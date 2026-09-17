"""Local-only JSON Schema validator for the inert C4-RD-4.1 contracts."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable

from scripts.c4_rd_contract import (
    ContractError,
    parse_json_strict,
    validate_pit_structure,
    validate_source_period,
)


SCHEMA_DIRECTORY = Path(__file__).resolve().parents[1] / "schemas" / "c4_rd"
SCHEMA_FILES = (
    "c4_common.schema.json",
    "c4_observation.schema.json",
    "c4_observation_version.schema.json",
    "c4_calendar_reference.schema.json",
    "c4_availability_assessment.schema.json",
    "c4_lineage_assessment.schema.json",
    "c4_trust_quality_assessment.schema.json",
    "c4_pit_eligibility.schema.json",
    "c4_research_eligibility.schema.json",
    "c4_backtest_eligibility.schema.json",
    "c4_rule_bundle.schema.json",
    "c4_readiness_manifest.schema.json",
    "c4_export_contract.schema.json",
)


class SchemaValidationError(ValueError):
    """Fail-closed schema loading or selection error."""


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    instance_path: str
    schema_path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {
            "instance_path": self.instance_path,
            "schema_path": self.schema_path,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class ValidationResult:
    valid: bool
    schema_id: str
    errors: tuple[ValidationIssue, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"valid": self.valid, "schema_id": self.schema_id, "errors": [item.as_dict() for item in self.errors]}


def _path(parts: Any) -> str:
    values = list(parts)
    if not values:
        return "$"
    return "$" + "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in values)


class LocalSchemaValidator:
    """Loads an explicit schema allowlist and never performs remote retrieval."""

    def __init__(self, schema_directory: Path = SCHEMA_DIRECTORY) -> None:
        self.schema_directory = schema_directory.resolve()
        self._schemas: dict[str, Mapping[str, Any]] = {}
        resources: list[tuple[str, Resource[Any]]] = []
        for filename in (
            "c4_common.schema.json",
            "c4_observation.schema.json",
            "c4_observation_version.schema.json",
            "c4_calendar_reference.schema.json",
            "c4_availability_assessment.schema.json",
            "c4_lineage_assessment.schema.json",
            "c4_trust_quality_assessment.schema.json",
            "c4_pit_eligibility.schema.json",
            "c4_research_eligibility.schema.json",
            "c4_backtest_eligibility.schema.json",
            "c4_rule_bundle.schema.json",
            "c4_readiness_manifest.schema.json",
            "c4_export_contract.schema.json",
        ):
            path = (self.schema_directory / filename).resolve()
            if path.parent != self.schema_directory:
                raise SchemaValidationError("schema path escaped local directory")
            try:
                schema = parse_json_strict(path.read_bytes())
            except (OSError, ContractError) as exc:
                raise SchemaValidationError(f"cannot load approved local schema {filename}: {exc}") from exc
            if not isinstance(schema, dict):
                raise SchemaValidationError(f"schema {filename} must be an object")
            schema_id = schema.get("$id")
            expected_suffix = ":1.0.0"
            if not isinstance(schema_id, str) or not schema_id.startswith("urn:c4-rd:schema:") or not schema_id.endswith(expected_suffix):
                raise SchemaValidationError(f"unsupported schema ID/version in {filename}")
            if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
                raise SchemaValidationError(f"unsupported schema draft in {filename}")
            Draft202012Validator.check_schema(schema)
            if schema_id in self._schemas:
                raise SchemaValidationError(f"duplicate schema ID: {schema_id}")
            self._schemas[schema_id] = schema
            resources.append((schema_id, Resource.from_contents(schema)))
        self._registry = Registry().with_resources(resources)

    @property
    def schema_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._schemas))

    def validate(self, schema_id: str, instance: Any) -> ValidationResult:
        if schema_id not in self._schemas:
            raise SchemaValidationError("unknown or unsupported schema ID")
        validator = Draft202012Validator(
            self._schemas[schema_id], registry=self._registry, format_checker=FormatChecker()
        )
        try:
            issues = [
                ValidationIssue(_path(error.absolute_path), _path(error.absolute_schema_path), error.message)
                for error in validator.iter_errors(instance)
            ]
        except Unresolvable as exc:
            raise SchemaValidationError("schema reference cannot be resolved from the local registry") from exc
        if not issues:
            issues.extend(self._semantic_issues(schema_id, instance))
        issues.sort(key=lambda item: (item.instance_path, item.schema_path, item.message))
        return ValidationResult(not issues, schema_id, tuple(issues))

    def _semantic_issues(self, schema_id: str, instance: Any) -> list[ValidationIssue]:
        if not isinstance(instance, dict):
            return []
        issues: list[ValidationIssue] = []
        try:
            if schema_id.endswith(":c4-observation:1.0.0"):
                validate_source_period(
                    instance["source_period_type"], instance["source_market_date"],
                    instance["source_period_start_date"], instance["source_period_end_date"],
                )
                roles = [item["role"] for item in instance["calendar_assignments"]]
                if len(roles) != len(set(roles)):
                    raise ContractError("calendar roles must not be duplicated")
                if set(roles) != {"MARKET", "PUBLICATION", "LOCAL_OPERATIONAL", "SCHEDULER"}:
                    raise ContractError("all four calendar roles are required")
            elif schema_id.endswith(":c4-pit-eligibility:1.0.0"):
                validate_pit_structure(
                    instance["feature_available_at_max"], instance["research_cutoff_at"],
                    instance["label_available_at"], instance["evaluation_as_of_at"],
                )
            elif schema_id.endswith(":c4-rule-bundle:1.0.0"):
                codes = [item["code"] for item in instance["exclusion_reasons"]]
                if tuple(codes) != (
                    "MISSING_SOURCE_MARKET_DATE", "AMBIGUOUS_SOURCE_DATE", "MISSING_SOURCE_AVAILABLE_AT",
                    "AVAILABILITY_UNVERIFIED", "UNKNOWN_SOURCE_TIMEZONE", "CALENDAR_UNRESOLVED",
                    "UNSCHEDULED_CLOSURE_UNRESOLVED", "AVAILABILITY_PRECEDES_SOURCE_DATE",
                    "FUTURE_TIMESTAMP_OUT_OF_TOLERANCE", "LINEAGE_INCOMPLETE", "REVISION_HISTORY_UNAVAILABLE",
                    "SOURCE_DATA_STALE", "FEATURE_AVAILABLE_AFTER_CUTOFF", "LABEL_AVAILABLE_AT_OR_BEFORE_CUTOFF",
                    "LABEL_HORIZON_NOT_ELAPSED", "DUPLICATE_OBSERVATION_ID_CONFLICT",
                    "CONFLICTING_OBSERVATION_VERSION", "INVALID_NUMERIC_VALUE", "UNIT_NOT_APPROVED",
                    "CURRENCY_NOT_APPROVED", "TRUST_STATE_NOT_APPROVED", "LEGACY_UNVERIFIED_FORBIDDEN",
                    "SHADOW_EXPORT_NOT_AUTHORIZED", "HISTORICAL_VINTAGE_NOT_PROVEN", "INVALID_SOURCE_PERIOD",
                    "SOURCE_PERIOD_TYPE_MISMATCH", "REQUIRED_CALENDAR_ROLE_MISSING",
                    "SOURCE_VENUE_MAPPING_CONFLICT", "CONTINUOUS_CONTRACT_MAPPING_UNRESOLVED",
                    "SOURCE_LICENSING_BLOCKED",
                ):
                    raise ContractError("exclusion reason catalog must exactly match frozen version 1.1.0 order")
        except (ContractError, KeyError, TypeError) as exc:
            issues.append(ValidationIssue("$", "$semantic", str(exc)))
        return issues


def load_json_instance(path: Path) -> Any:
    """Read a local JSON instance without writes or network access."""
    return parse_json_strict(path.read_bytes())


def validate_local(schema_id: str, instance: Any) -> ValidationResult:
    return LocalSchemaValidator().validate(schema_id, instance)
