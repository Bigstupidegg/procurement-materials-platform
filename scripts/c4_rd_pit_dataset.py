"""C4-RD-6.4B deterministic, in-memory PIT dataset builder.

The builder consumes already-constructed RD-4.1 values, an independently
supplied RD-4.2 readiness result, and the RD-5 decision bound to that result.
It performs no acquisition, persistence, backtesting, forecasting, or
operational research execution.  The only positive path currently supported
is explicitly ``SYNTHETIC_NON_OPERATIONAL``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
import re
from types import MappingProxyType
from typing import Any

from scripts.c4_rd_contract import (
    BACKTEST_ENABLED_SOURCES,
    CONTRACT_VERSION,
    PIT_ENABLED_SOURCES,
    RD3_OPEN_BLOCKERS,
    RESEARCH_ENABLED_SOURCES,
    SAFETY_FLAGS,
    Observation,
    ObservationVersion,
    canonical_hash,
    canonical_json_bytes,
    canonical_timestamp,
    parse_rfc3339,
    ContractError,
)
from scripts.c4_rd_readiness_evaluator import (
    EVALUATOR_VERSION,
    ReadinessEvaluationResult,
)
from scripts.c4_rd_research_export import (
    EXPORT_CONTRACT_VERSION,
    ResearchExportDecision,
    ResearchExportInputError,
    build_research_export_manifest,
)


DATASET_CONTRACT_VERSION = "C4_RD_6_PIT_DATASET_V1@1.0.0"
FEATURE_COMPUTATION_PROFILE_VERSION = "C4_RD_6_FEATURE_COMPUTATION_V1@1.0.0"
CUTOFF_POLICY_VERSION = "C4_RD_6_LATEST_KNOWN_AT_CUTOFF_V1@1.0.0"
DATASET_MANIFEST_TYPE = "C4_RD6_PIT_DATASET"
DATASET_MANIFEST_VERSION = "1.0.0"

FEATURE_REQUIREMENTS = ("MANDATORY", "OPTIONAL")
VALUE_STATES = ("PRESENT", "EXPLICIT_MISSING")
EXECUTION_MODES = ("SYNTHETIC_NON_OPERATIONAL", "REAL_OPERATIONAL")

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REALIZED_LABEL_KEYS = frozenset({
    "realized_label",
    "realized_value",
    "realized_future_label",
    "future_return",
    "future_target_price",
    "target_price",
    "realized_future_volatility",
    "realized_volatility",
    "post_cutoff_evaluation_result",
})


class PITDatasetError(ContractError):
    """Raised when an RD-6 dataset request cannot be trusted."""


class PITDatasetHardFail(PITDatasetError):
    """Raised when the whole dataset build must stop."""


def _require_non_empty(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise PITDatasetHardFail(f"{name} must be a non-empty string")


def _require_sha256(value: Any, name: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise PITDatasetHardFail(f"{name} must be lowercase SHA-256 hex")


def _timestamp(value: Any, name: str) -> str:
    if not isinstance(value, (str, datetime)):
        raise PITDatasetHardFail(f"{name} must be an RFC3339 timestamp")
    try:
        return canonical_timestamp(value)
    except ContractError as exc:
        raise PITDatasetHardFail(f"invalid {name}") from exc


def _freeze(value: Any, *, field_name: str | None = None) -> Any:
    if field_name is not None and field_name.endswith("_at") and isinstance(value, str):
        return _timestamp(value, field_name)
    if isinstance(value, Mapping):
        copied: dict[str, Any] = {}
        try:
            items = tuple(value.items())
        except Exception as exc:
            raise PITDatasetHardFail("semantic mapping could not be snapshotted") from exc
        for key, item in items:
            if not isinstance(key, str):
                raise PITDatasetHardFail("semantic mapping keys must be strings")
            copied[key] = _freeze(item, field_name=key)
        return MappingProxyType(copied)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        raise PITDatasetHardFail("set-like semantic input is forbidden")
    if value is None or isinstance(value, (str, bool, int, Decimal, date, datetime)):
        return value
    if isinstance(value, float):
        raise PITDatasetHardFail("binary float is forbidden")
    raise PITDatasetHardFail(f"unsupported semantic type: {type(value).__name__}")


def _plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _plain(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, datetime):
        return canonical_timestamp(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _snapshot_sequence(values: Sequence[Any], name: str) -> tuple[Any, ...]:
    if not isinstance(values, (list, tuple)):
        raise PITDatasetHardFail(f"{name} must be a snapshottable sequence")
    return tuple(values)


def _forbidden_label_key(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = key.casefold()
            if normalized in _REALIZED_LABEL_KEYS or normalized.startswith("realized_"):
                return key
            nested = _forbidden_label_key(item)
            if nested is not None:
                return nested
    elif isinstance(value, (list, tuple)):
        for item in value:
            nested = _forbidden_label_key(item)
            if nested is not None:
                return nested
    return None


def _authorization_snapshot() -> Mapping[str, Any]:
    return _freeze({
        "safety_flags": dict(SAFETY_FLAGS),
        "pit_enabled_sources": PIT_ENABLED_SOURCES,
        "research_enabled_sources": RESEARCH_ENABLED_SOURCES,
        "backtest_enabled_sources": BACKTEST_ENABLED_SOURCES,
        "rd3_open_blockers": RD3_OPEN_BLOCKERS,
    })


@dataclass(frozen=True, slots=True)
class PITFeatureDefinition:
    feature_definition_id: str
    feature_definition_version: str
    value_key: str
    requirement: str

    def __post_init__(self) -> None:
        for name in ("feature_definition_id", "feature_definition_version", "value_key"):
            _require_non_empty(getattr(self, name), name)
        if self.feature_definition_version.casefold() == "latest":
            raise PITDatasetHardFail("implicit/latest feature definition version is forbidden")
        if self.requirement not in FEATURE_REQUIREMENTS:
            raise PITDatasetHardFail("unsupported feature requirement")


@dataclass(frozen=True, slots=True)
class PITDatasetRequest:
    research_subject_id: str
    observation_version_id: str
    research_cutoff_at: str
    feature_set_version: str
    feature_definitions: tuple[PITFeatureDefinition, ...]
    label_specification: Mapping[str, Any]
    execution_mode: str = "SYNTHETIC_NON_OPERATIONAL"
    dataset_contract_version: str = DATASET_CONTRACT_VERSION
    feature_computation_profile_version: str = FEATURE_COMPUTATION_PROFILE_VERSION
    cutoff_policy_version: str = CUTOFF_POLICY_VERSION
    runtime_metadata: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        if self.dataset_contract_version != DATASET_CONTRACT_VERSION:
            raise PITDatasetHardFail("unsupported dataset contract version")
        if self.feature_computation_profile_version != FEATURE_COMPUTATION_PROFILE_VERSION:
            raise PITDatasetHardFail("unsupported feature computation profile")
        if self.cutoff_policy_version != CUTOFF_POLICY_VERSION:
            raise PITDatasetHardFail("unsupported cutoff policy")
        _require_non_empty(self.research_subject_id, "research_subject_id")
        _require_sha256(self.observation_version_id, "observation_version_id")
        _require_non_empty(self.feature_set_version, "feature_set_version")
        if self.feature_set_version.casefold() == "latest":
            raise PITDatasetHardFail("implicit/latest feature set version is forbidden")
        object.__setattr__(
            self,
            "research_cutoff_at",
            _timestamp(self.research_cutoff_at, "research_cutoff_at"),
        )
        definitions = tuple(self.feature_definitions)
        if not definitions or any(type(item) is not PITFeatureDefinition for item in definitions):
            raise PITDatasetHardFail("feature_definitions must contain frozen definitions")
        identities = tuple(item.feature_definition_id for item in definitions)
        if len(identities) != len(set(identities)):
            raise PITDatasetHardFail("duplicate feature definition")
        object.__setattr__(self, "feature_definitions", definitions)
        if self.execution_mode not in EXECUTION_MODES:
            raise PITDatasetHardFail("unsupported execution mode")
        frozen_label = _freeze(self.label_specification)
        if not isinstance(frozen_label, Mapping) or not frozen_label:
            raise PITDatasetHardFail("label_specification must be an explicit mapping")
        forbidden = _forbidden_label_key(frozen_label)
        if forbidden is not None:
            raise PITDatasetHardFail(f"realized label field is forbidden: {forbidden}")
        object.__setattr__(self, "label_specification", frozen_label)
        object.__setattr__(self, "runtime_metadata", _freeze(self.runtime_metadata))
        canonical_json_bytes(self.semantic_projection())

    def semantic_projection(self) -> dict[str, Any]:
        return {
            "dataset_contract_version": self.dataset_contract_version,
            "research_subject_id": self.research_subject_id,
            "observation_version_id": self.observation_version_id,
            "research_cutoff_at": self.research_cutoff_at,
            "feature_set_version": self.feature_set_version,
            "feature_computation_profile_version": self.feature_computation_profile_version,
            "cutoff_policy_version": self.cutoff_policy_version,
            "feature_definitions": [_plain(item) for item in self.feature_definitions],
            "label_specification": _plain(self.label_specification),
            "execution_mode": self.execution_mode,
        }


@dataclass(frozen=True, slots=True)
class PITFeatureCandidate:
    research_subject_id: str
    feature_definition_id: str
    observation: Observation
    observation_version: ObservationVersion
    evaluation_result: ReadinessEvaluationResult
    research_export_decision: ResearchExportDecision
    runtime_metadata: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        _require_non_empty(self.research_subject_id, "research_subject_id")
        _require_non_empty(self.feature_definition_id, "feature_definition_id")
        object.__setattr__(self, "runtime_metadata", _freeze(self.runtime_metadata))


@dataclass(frozen=True, slots=True)
class PITFeatureSnapshot:
    feature_definition_id: str
    feature_definition_version: str
    feature_computation_profile_version: str
    research_cutoff_at: str
    value_state: str
    value: Any
    source_observation_id: str | None
    source_observation_version_id: str | None
    source_observed_at: str | None
    source_available_at: str | None
    source_profile_id: str | None
    source_profile_version: str | None
    evidence_refs: tuple[Mapping[str, str], ...]
    rd4_evaluation_hash: str | None
    rd5_decision_hash: str | None
    authority_binding_ref: str | None

    def __post_init__(self) -> None:
        for name in (
            "feature_definition_id",
            "feature_definition_version",
            "feature_computation_profile_version",
        ):
            _require_non_empty(getattr(self, name), name)
        object.__setattr__(
            self,
            "research_cutoff_at",
            _timestamp(self.research_cutoff_at, "research_cutoff_at"),
        )
        if self.value_state not in VALUE_STATES:
            raise PITDatasetHardFail("unsupported feature value_state")
        object.__setattr__(self, "value", _freeze(self.value))
        object.__setattr__(
            self,
            "evidence_refs",
            tuple(_freeze(item) for item in self.evidence_refs),
        )
        source_fields = (
            "source_observation_id",
            "source_observation_version_id",
            "source_observed_at",
            "source_available_at",
            "source_profile_id",
            "source_profile_version",
            "rd4_evaluation_hash",
            "rd5_decision_hash",
            "authority_binding_ref",
        )
        if self.value_state == "EXPLICIT_MISSING":
            if self.value is not None or any(getattr(self, name) is not None for name in source_fields):
                raise PITDatasetHardFail("explicit missing feature must not fabricate source lineage")
            if self.evidence_refs:
                raise PITDatasetHardFail("explicit missing feature must not fabricate evidence")
        else:
            if self.value is None:
                raise PITDatasetHardFail("present feature value must not be null")
            for name in source_fields:
                value = getattr(self, name)
                if value is None:
                    raise PITDatasetHardFail(f"present feature requires {name}")
            for name in (
                "source_observation_id",
                "source_observation_version_id",
                "rd4_evaluation_hash",
                "rd5_decision_hash",
                "authority_binding_ref",
            ):
                _require_sha256(getattr(self, name), name)
            object.__setattr__(
                self,
                "source_observed_at",
                _timestamp(self.source_observed_at, "source_observed_at"),
            )
            object.__setattr__(
                self,
                "source_available_at",
                _timestamp(self.source_available_at, "source_available_at"),
            )
        canonical_json_bytes(self.content_projection())

    def content_projection(self) -> dict[str, Any]:
        return {
            "feature_definition_id": self.feature_definition_id,
            "feature_definition_version": self.feature_definition_version,
            "feature_computation_profile_version": self.feature_computation_profile_version,
            "research_cutoff_at": self.research_cutoff_at,
            "value_state": self.value_state,
            "value": _plain(self.value),
            "source_observation_id": self.source_observation_id,
            "source_observation_version_id": self.source_observation_version_id,
            "source_observed_at": self.source_observed_at,
            "source_available_at": self.source_available_at,
            "source_profile_id": self.source_profile_id,
            "source_profile_version": self.source_profile_version,
            "evidence_refs": [_plain(item) for item in self.evidence_refs],
            "rd4_evaluation_hash": self.rd4_evaluation_hash,
            "rd5_decision_hash": self.rd5_decision_hash,
            "authority_binding_ref": self.authority_binding_ref,
        }

    @property
    def feature_content_hash(self) -> str:
        return canonical_hash("PIT_FEATURE_CONTENT", self.content_projection())

    def as_dict(self) -> dict[str, Any]:
        return {
            "feature_content_hash": self.feature_content_hash,
            "content": self.content_projection(),
        }


@dataclass(frozen=True, slots=True)
class PITDatasetRow:
    dataset_contract_version: str
    research_subject_id: str
    observation_version_id: str
    research_cutoff_at: str
    feature_set_version: str
    feature_computation_profile_version: str
    cutoff_policy_version: str
    features: tuple[PITFeatureSnapshot, ...]
    feature_available_at_max: str
    rd4_authority_bindings: tuple[Mapping[str, str], ...]
    rd5_authority_bindings: tuple[Mapping[str, str], ...]
    rule_bundle_bindings: tuple[Mapping[str, str], ...]
    source_profile_bindings: tuple[Mapping[str, str], ...]
    label_specification: Mapping[str, Any]
    authorization_snapshot: Mapping[str, Any]
    operational_status: str = "SYNTHETIC_NON_OPERATIONAL"

    def __post_init__(self) -> None:
        if self.dataset_contract_version != DATASET_CONTRACT_VERSION:
            raise PITDatasetHardFail("row contract version mismatch")
        _require_non_empty(self.research_subject_id, "research_subject_id")
        _require_sha256(self.observation_version_id, "observation_version_id")
        for name in (
            "feature_set_version",
            "feature_computation_profile_version",
            "cutoff_policy_version",
        ):
            _require_non_empty(getattr(self, name), name)
        object.__setattr__(
            self,
            "research_cutoff_at",
            _timestamp(self.research_cutoff_at, "research_cutoff_at"),
        )
        object.__setattr__(
            self,
            "feature_available_at_max",
            _timestamp(self.feature_available_at_max, "feature_available_at_max"),
        )
        if parse_rfc3339(self.feature_available_at_max) > parse_rfc3339(self.research_cutoff_at):
            raise PITDatasetHardFail("FEATURE_AVAILABLE_AFTER_CUTOFF")
        feature_values = tuple(self.features)
        if not feature_values or any(type(item) is not PITFeatureSnapshot for item in feature_values):
            raise PITDatasetHardFail("row features must contain validated snapshots")
        feature_ids = tuple(item.feature_definition_id for item in feature_values)
        if len(feature_ids) != len(set(feature_ids)):
            raise PITDatasetHardFail("duplicate row feature")
        if any(item.research_cutoff_at != self.research_cutoff_at for item in feature_values):
            raise PITDatasetHardFail("feature cutoff does not match row cutoff")
        object.__setattr__(self, "features", feature_values)
        for name in (
            "rd4_authority_bindings",
            "rd5_authority_bindings",
            "rule_bundle_bindings",
            "source_profile_bindings",
        ):
            object.__setattr__(self, name, tuple(_freeze(item) for item in getattr(self, name)))
        object.__setattr__(self, "label_specification", _freeze(self.label_specification))
        object.__setattr__(self, "authorization_snapshot", _freeze(self.authorization_snapshot))
        if _plain(self.authorization_snapshot) != _plain(_authorization_snapshot()):
            raise PITDatasetHardFail("authorization snapshot does not match frozen controls")
        if self.operational_status != "SYNTHETIC_NON_OPERATIONAL":
            raise PITDatasetHardFail("dataset rows must remain synthetic non-operational")
        canonical_json_bytes(self.content_projection())

    def identity_projection(self) -> dict[str, Any]:
        return {
            "dataset_contract_version": self.dataset_contract_version,
            "research_subject_id": self.research_subject_id,
            "observation_version_id": self.observation_version_id,
            "research_cutoff_at": self.research_cutoff_at,
            "feature_set_version": self.feature_set_version,
            "feature_computation_profile_version": self.feature_computation_profile_version,
            "cutoff_policy_version": self.cutoff_policy_version,
        }

    @property
    def row_id(self) -> str:
        return canonical_hash("PIT_DATASET_ROW_ID", self.identity_projection())

    def content_projection(self) -> dict[str, Any]:
        return {
            "row_id": self.row_id,
            "identity": self.identity_projection(),
            "features": [item.as_dict() for item in self.features],
            "feature_available_at_max": self.feature_available_at_max,
            "rd4_authority_bindings": [_plain(item) for item in self.rd4_authority_bindings],
            "rd5_authority_bindings": [_plain(item) for item in self.rd5_authority_bindings],
            "rule_bundle_bindings": [_plain(item) for item in self.rule_bundle_bindings],
            "source_profile_bindings": [_plain(item) for item in self.source_profile_bindings],
            "label_specification": _plain(self.label_specification),
            "authorization_snapshot": _plain(self.authorization_snapshot),
            "operational_status": self.operational_status,
        }

    @property
    def row_content_hash(self) -> str:
        return canonical_hash("PIT_DATASET_ROW_CONTENT", self.content_projection())

    @property
    def ordering_key(self) -> tuple[str, str, str]:
        return (self.research_cutoff_at, self.research_subject_id, self.row_id)

    def as_dict(self) -> dict[str, Any]:
        return {"row_content_hash": self.row_content_hash, "content": self.content_projection()}


@dataclass(frozen=True, slots=True)
class PITDatasetManifest:
    manifest_type: str
    manifest_version: str
    dataset_contract_version: str
    feature_set_version: str
    feature_computation_profile_version: str
    cutoff_policy_version: str
    rule_bundle_bindings: tuple[Mapping[str, str], ...]
    source_profile_bindings: tuple[Mapping[str, str], ...]
    authorization_snapshot: Mapping[str, Any]
    ordered_row_ids: tuple[str, ...]
    ordered_row_content_hashes: tuple[str, ...]
    include_count: int
    exclude_count: int
    quarantine_count: int
    exclusion_reason_summary: Mapping[str, int]
    quarantine_reason_summary: Mapping[str, int]

    def __post_init__(self) -> None:
        if self.manifest_type != DATASET_MANIFEST_TYPE or self.manifest_version != DATASET_MANIFEST_VERSION:
            raise PITDatasetHardFail("unsupported PIT dataset manifest type/version")
        if self.dataset_contract_version != DATASET_CONTRACT_VERSION:
            raise PITDatasetHardFail("manifest contract version mismatch")
        if self.feature_computation_profile_version != FEATURE_COMPUTATION_PROFILE_VERSION:
            raise PITDatasetHardFail("manifest computation profile mismatch")
        if self.cutoff_policy_version != CUTOFF_POLICY_VERSION:
            raise PITDatasetHardFail("manifest cutoff policy mismatch")
        _require_non_empty(self.feature_set_version, "feature_set_version")
        for name in ("rule_bundle_bindings", "source_profile_bindings"):
            object.__setattr__(self, name, tuple(_freeze(item) for item in getattr(self, name)))
        object.__setattr__(self, "authorization_snapshot", _freeze(self.authorization_snapshot))
        if _plain(self.authorization_snapshot) != _plain(_authorization_snapshot()):
            raise PITDatasetHardFail("manifest authorization snapshot mismatch")
        if len(self.ordered_row_ids) != len(self.ordered_row_content_hashes):
            raise PITDatasetHardFail("manifest row identity/content cardinality mismatch")
        if self.include_count != len(self.ordered_row_ids):
            raise PITDatasetHardFail("manifest include count mismatch")
        for value in self.ordered_row_ids + self.ordered_row_content_hashes:
            _require_sha256(value, "manifest row hash")
        if any(type(value) is not int or value < 0 for value in (
            self.include_count, self.exclude_count, self.quarantine_count
        )):
            raise PITDatasetHardFail("manifest counts must be non-negative integers")
        object.__setattr__(self, "exclusion_reason_summary", _freeze(self.exclusion_reason_summary))
        object.__setattr__(self, "quarantine_reason_summary", _freeze(self.quarantine_reason_summary))
        canonical_json_bytes(self.content_projection())

    def content_projection(self) -> dict[str, Any]:
        return {
            "manifest_type": self.manifest_type,
            "manifest_version": self.manifest_version,
            "dataset_contract_version": self.dataset_contract_version,
            "feature_set_version": self.feature_set_version,
            "feature_computation_profile_version": self.feature_computation_profile_version,
            "cutoff_policy_version": self.cutoff_policy_version,
            "rule_bundle_bindings": [_plain(item) for item in self.rule_bundle_bindings],
            "source_profile_bindings": [_plain(item) for item in self.source_profile_bindings],
            "authorization_snapshot": _plain(self.authorization_snapshot),
            "ordered_row_ids": list(self.ordered_row_ids),
            "ordered_row_content_hashes": list(self.ordered_row_content_hashes),
            "include_count": self.include_count,
            "exclude_count": self.exclude_count,
            "quarantine_count": self.quarantine_count,
            "exclusion_reason_summary": _plain(self.exclusion_reason_summary),
            "quarantine_reason_summary": _plain(self.quarantine_reason_summary),
        }

    @property
    def manifest_hash(self) -> str:
        return canonical_hash("MANIFEST_CONTENT", self.content_projection())

    @property
    def dataset_identity(self) -> str:
        return self.manifest_hash

    def as_dict(self) -> dict[str, Any]:
        return {"manifest_hash": self.manifest_hash, "content": self.content_projection()}


@dataclass(frozen=True, slots=True)
class PITDatasetResult:
    rows: tuple[PITDatasetRow, ...]
    manifest: PITDatasetManifest
    diagnostics: tuple[Mapping[str, Any], ...]

    def __post_init__(self) -> None:
        rows = tuple(self.rows)
        if any(type(item) is not PITDatasetRow for item in rows):
            raise PITDatasetHardFail("result rows must be validated PITDatasetRow values")
        if rows != tuple(sorted(rows, key=lambda item: item.ordering_key)):
            raise PITDatasetHardFail("result rows are not in deterministic order")
        if tuple(item.row_id for item in rows) != self.manifest.ordered_row_ids:
            raise PITDatasetHardFail("result row IDs do not reconcile to manifest")
        if tuple(item.row_content_hash for item in rows) != self.manifest.ordered_row_content_hashes:
            raise PITDatasetHardFail("result row hashes do not reconcile to manifest")
        object.__setattr__(self, "rows", rows)
        object.__setattr__(self, "diagnostics", tuple(_freeze(item) for item in self.diagnostics))

    @property
    def dataset_identity(self) -> str:
        return self.manifest.manifest_hash

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_identity": self.dataset_identity,
            "manifest": self.manifest.as_dict(),
            "rows": [item.as_dict() for item in self.rows],
            "diagnostics": [_plain(item) for item in self.diagnostics],
        }


def _validate_authority(candidate: PITFeatureCandidate) -> None:
    if type(candidate.observation) is not Observation:
        raise PITDatasetHardFail("candidate Observation type mismatch")
    if type(candidate.observation_version) is not ObservationVersion:
        raise PITDatasetHardFail("candidate ObservationVersion type mismatch")
    if type(candidate.evaluation_result) is not ReadinessEvaluationResult:
        raise PITDatasetHardFail("candidate RD-4.2 authority type mismatch")
    if type(candidate.research_export_decision) is not ResearchExportDecision:
        raise PITDatasetHardFail("candidate RD-5 authority type mismatch")
    observation = candidate.observation
    version = candidate.observation_version
    evaluation = candidate.evaluation_result
    decision = candidate.research_export_decision
    checks = (
        (version.observation_id, observation.observation_id, "ObservationVersion/Observation"),
        (evaluation.observation_id, observation.observation_id, "RD-4/Observation"),
        (evaluation.observation_version_id, version.observation_version_id, "RD-4/ObservationVersion"),
        (decision.observation_id, observation.observation_id, "RD-5/Observation"),
        (decision.observation_version_id, version.observation_version_id, "RD-5/ObservationVersion"),
        (evaluation.observation_content_hash, observation.content_hash, "RD-4 observation content"),
        (evaluation.observation_version_content_hash, version.content_hash, "RD-4 version content"),
        (decision.observation_content_hash, observation.content_hash, "RD-5 observation content"),
        (decision.observation_version_content_hash, version.content_hash, "RD-5 version content"),
        (decision.source_id, observation.source_id, "RD-5 source"),
        (decision.instrument_id, observation.instrument_id, "RD-5 instrument"),
        (
            decision.source_version_or_release_key,
            version.source_version_or_release_key,
            "RD-5 source release key",
        ),
        (decision.stable_version_key, version.stable_version_key, "RD-5 stable version key"),
        (decision.raw_payload_hash, version.raw_payload_hash, "RD-5 raw payload"),
        (decision.transformation_version, version.transformation_version, "RD-5 transformation"),
        (evaluation.rule_bundle_hash, decision.rule_bundle_hash, "RD-4/RD-5 rule bundle"),
        (evaluation.source_profile_id, decision.source_profile_id, "RD-4/RD-5 source profile"),
        (evaluation.source_profile_version, decision.source_profile_version, "RD-4/RD-5 profile version"),
    )
    for actual, expected, name in checks:
        if actual != expected:
            raise PITDatasetHardFail(f"AUTHORITY_BINDING_MISMATCH: {name}")
    expected_source_period = {
        "source_period_type": observation.source_period_type,
        "source_market_date": observation.source_market_date,
        "represented_period_start": observation.source_period_start_date,
        "represented_period_end": observation.source_period_end_date,
        "source_publication_at": observation.source_publication_at,
    }
    if _plain(decision.source_period) != expected_source_period:
        raise PITDatasetHardFail("AUTHORITY_BINDING_MISMATCH: RD-5 source period")
    if decision.temporal_context.get("source_available_at") != observation.source_available_at:
        raise PITDatasetHardFail("AUTHORITY_BINDING_MISMATCH: RD-5 source availability")
    if evaluation.contract_version != CONTRACT_VERSION or evaluation.evaluator_version != EVALUATOR_VERSION:
        raise PITDatasetHardFail("unsupported RD-4 authority version")
    if decision.export_contract_version != EXPORT_CONTRACT_VERSION:
        raise PITDatasetHardFail("unsupported RD-5 authority version")
    try:
        build_research_export_manifest(
            (decision,),
            (evaluation,),
            research_cutoff_at=evaluation.cutoff_at,
            evaluation_context={
                "purpose": "C4_RD6_PIT_AUTHORITY_BINDING",
                "dataset_contract_version": DATASET_CONTRACT_VERSION,
            },
        )
    except (ResearchExportInputError, ContractError) as exc:
        raise PITDatasetHardFail("AUTHORITY_BINDING_MISMATCH: RD-5/RD-4 authority") from exc


def _candidate_snapshot(
    request: PITDatasetRequest,
    definition: PITFeatureDefinition,
    candidate: PITFeatureCandidate,
) -> PITFeatureSnapshot:
    observation = candidate.observation
    version = candidate.observation_version
    evaluation = candidate.evaluation_result
    decision = candidate.research_export_decision
    available = decision.temporal_context.get("feature_available_at_max")
    if available is None:
        raise PITDatasetHardFail("candidate has no authoritative feature availability")
    available_at = _timestamp(available, "feature_available_at_max")
    if observation.source_available_at is not None and observation.source_available_at != available_at:
        raise PITDatasetHardFail("AUTHORITY_BINDING_MISMATCH: source availability")
    observed = version.observed_at or observation.observed_at
    if observed is None:
        raise PITDatasetHardFail("candidate has no source_observed_at")
    if definition.value_key not in version.semantic_data:
        raise PITDatasetHardFail("candidate version does not contain the defined feature value")
    value = version.semantic_data[definition.value_key]
    if value is None:
        raise PITDatasetHardFail("candidate feature value is null")
    rd4_hash = canonical_hash(
        "MANIFEST_CONTENT",
        {"rd4_2_evaluation_result": evaluation.as_dict()},
    )
    return PITFeatureSnapshot(
        feature_definition_id=definition.feature_definition_id,
        feature_definition_version=definition.feature_definition_version,
        feature_computation_profile_version=request.feature_computation_profile_version,
        research_cutoff_at=request.research_cutoff_at,
        value_state="PRESENT",
        value=value,
        source_observation_id=observation.observation_id,
        source_observation_version_id=version.observation_version_id,
        source_observed_at=observed,
        source_available_at=available_at,
        source_profile_id=evaluation.source_profile_id,
        source_profile_version=evaluation.source_profile_version,
        evidence_refs=decision.evidence_references,
        rd4_evaluation_hash=rd4_hash,
        rd5_decision_hash=decision.content_hash,
        authority_binding_ref=decision.authority_snapshot_hash,
    )


def _missing_snapshot(
    request: PITDatasetRequest,
    definition: PITFeatureDefinition,
) -> PITFeatureSnapshot:
    return PITFeatureSnapshot(
        feature_definition_id=definition.feature_definition_id,
        feature_definition_version=definition.feature_definition_version,
        feature_computation_profile_version=request.feature_computation_profile_version,
        research_cutoff_at=request.research_cutoff_at,
        value_state="EXPLICIT_MISSING",
        value=None,
        source_observation_id=None,
        source_observation_version_id=None,
        source_observed_at=None,
        source_available_at=None,
        source_profile_id=None,
        source_profile_version=None,
        evidence_refs=(),
        rd4_evaluation_hash=None,
        rd5_decision_hash=None,
        authority_binding_ref=None,
    )


def _diagnostic(
    request: PITDatasetRequest,
    classification: str,
    reason: str,
    *,
    feature_definition_id: str | None = None,
) -> Mapping[str, Any]:
    return _freeze({
        "classification": classification,
        "reason": reason,
        "research_cutoff_at": request.research_cutoff_at,
        "research_subject_id": request.research_subject_id,
        "observation_version_id": request.observation_version_id,
        "feature_definition_id": feature_definition_id,
    })


def _select_feature(
    request: PITDatasetRequest,
    definition: PITFeatureDefinition,
    candidates: tuple[PITFeatureCandidate, ...],
) -> tuple[PITFeatureSnapshot | None, str | None]:
    relevant = tuple(
        item for item in candidates
        if item.research_subject_id == request.research_subject_id
        and item.feature_definition_id == definition.feature_definition_id
        and item.evaluation_result.cutoff_at == request.research_cutoff_at
    )
    visible: list[tuple[PITFeatureCandidate, str]] = []
    cutoff = parse_rfc3339(request.research_cutoff_at)
    for item in relevant:
        available = item.research_export_decision.temporal_context.get("feature_available_at_max")
        if available is None:
            continue
        available_at = _timestamp(available, "feature_available_at_max")
        if parse_rfc3339(available_at) <= cutoff:
            visible.append((item, available_at))
    if not visible:
        if definition.requirement == "MANDATORY":
            return None, "MANDATORY_FEATURE_UNAVAILABLE"
        return _missing_snapshot(request, definition), None

    semantic = tuple(
        (item, available_at)
        for item, available_at in visible
        if item.research_export_decision.semantic_export_passed
        and item.research_export_decision.disposition != "QUARANTINE"
    )
    if not semantic:
        return None, "CANDIDATE_AUTHORITY_OR_QUALITY_INSUFFICIENT"
    latest = max(parse_rfc3339(available_at) for _item, available_at in semantic)
    latest_items = tuple(
        item for item, available_at in semantic
        if parse_rfc3339(available_at) == latest
    )
    snapshots = tuple(_candidate_snapshot(request, definition, item) for item in latest_items)
    unique: dict[tuple[str, str], PITFeatureSnapshot] = {}
    for snapshot in snapshots:
        identity = (snapshot.source_observation_version_id or "", snapshot.feature_content_hash)
        unique[identity] = snapshot
    if len(unique) != 1:
        raise PITDatasetHardFail("AMBIGUOUS_AUTHORITATIVE_CANDIDATE")
    return next(iter(unique.values())), None


def _build_row(
    request: PITDatasetRequest,
    candidates: tuple[PITFeatureCandidate, ...],
) -> tuple[PITDatasetRow | None, Mapping[str, Any] | None]:
    if request.execution_mode != "SYNTHETIC_NON_OPERATIONAL":
        return None, _diagnostic(request, "ROW_EXCLUDE", "OPERATIONAL_RESEARCH_NOT_AUTHORIZED")
    snapshots: list[PITFeatureSnapshot] = []
    for definition in request.feature_definitions:
        snapshot, reason = _select_feature(request, definition, candidates)
        if reason is not None:
            classification = (
                "QUARANTINE"
                if reason == "CANDIDATE_AUTHORITY_OR_QUALITY_INSUFFICIENT"
                else "ROW_EXCLUDE"
            )
            return None, _diagnostic(
                request,
                classification,
                reason,
                feature_definition_id=definition.feature_definition_id,
            )
        if snapshot is None:
            raise PITDatasetHardFail("feature selection returned no snapshot or reason")
        snapshots.append(snapshot)
    present = tuple(item for item in snapshots if item.value_state == "PRESENT")
    if not present:
        return None, _diagnostic(request, "ROW_EXCLUDE", "NO_PRESENT_FEATURE")
    available_max = max(
        (item.source_available_at for item in present if item.source_available_at is not None),
        key=parse_rfc3339,
    )
    if parse_rfc3339(available_max) > parse_rfc3339(request.research_cutoff_at):
        raise PITDatasetHardFail("FEATURE_AVAILABLE_AFTER_CUTOFF")

    rd4_bindings = tuple(sorted(({
        "feature_definition_id": item.feature_definition_id,
        "evaluation_hash": item.rd4_evaluation_hash,
    } for item in present), key=lambda item: item["feature_definition_id"]))
    rd5_bindings = tuple(sorted(({
        "feature_definition_id": item.feature_definition_id,
        "decision_hash": item.rd5_decision_hash,
        "authority_binding_ref": item.authority_binding_ref,
    } for item in present), key=lambda item: item["feature_definition_id"]))
    relevant = tuple(
        item for item in candidates
        if item.research_subject_id == request.research_subject_id
        and item.evaluation_result.cutoff_at == request.research_cutoff_at
        and any(
            snapshot.source_observation_version_id == item.observation_version.observation_version_id
            for snapshot in present
        )
    )
    rule_bindings = tuple(sorted({
        (
            item.evaluation_result.rule_bundle_version,
            item.evaluation_result.rule_bundle_hash,
        )
        for item in relevant
    }))
    profile_bindings = tuple(sorted({
        (
            item.evaluation_result.source_profile_id,
            item.evaluation_result.source_profile_version,
        )
        for item in relevant
    }))
    return PITDatasetRow(
        dataset_contract_version=request.dataset_contract_version,
        research_subject_id=request.research_subject_id,
        observation_version_id=request.observation_version_id,
        research_cutoff_at=request.research_cutoff_at,
        feature_set_version=request.feature_set_version,
        feature_computation_profile_version=request.feature_computation_profile_version,
        cutoff_policy_version=request.cutoff_policy_version,
        features=tuple(snapshots),
        feature_available_at_max=available_max,
        rd4_authority_bindings=rd4_bindings,
        rd5_authority_bindings=rd5_bindings,
        rule_bundle_bindings=tuple({"rule_bundle_version": version, "rule_bundle_hash": hash_value} for version, hash_value in rule_bindings),
        source_profile_bindings=tuple({"source_profile_id": profile, "source_profile_version": version} for profile, version in profile_bindings),
        label_specification=request.label_specification,
        authorization_snapshot=_authorization_snapshot(),
    ), None


def _summary(diagnostics: tuple[Mapping[str, Any], ...], classification: str) -> Mapping[str, int]:
    counts = Counter(
        str(item["reason"])
        for item in diagnostics
        if item["classification"] == classification
    )
    return MappingProxyType(dict(sorted(counts.items())))


def build_pit_dataset(
    requests: Sequence[PITDatasetRequest],
    candidates: Sequence[PITFeatureCandidate],
) -> PITDatasetResult:
    """Build a deterministic PIT dataset without I/O or operational execution."""

    request_values = _snapshot_sequence(requests, "requests")
    candidate_values = _snapshot_sequence(candidates, "candidates")
    if not request_values or any(type(item) is not PITDatasetRequest for item in request_values):
        raise PITDatasetHardFail("requests must contain PITDatasetRequest values")
    if any(type(item) is not PITFeatureCandidate for item in candidate_values):
        raise PITDatasetHardFail("candidates must contain PITFeatureCandidate values")
    contexts = {
        (
            item.dataset_contract_version,
            item.feature_set_version,
            item.feature_computation_profile_version,
            item.cutoff_policy_version,
        )
        for item in request_values
    }
    if len(contexts) != 1:
        raise PITDatasetHardFail("dataset requests require one frozen dataset context")
    for candidate in candidate_values:
        _validate_authority(candidate)

    built_rows: list[PITDatasetRow] = []
    diagnostics: list[Mapping[str, Any]] = []
    for request in sorted(
        request_values,
        key=lambda item: (
            item.research_cutoff_at,
            item.research_subject_id,
            item.observation_version_id,
        ),
    ):
        row, diagnostic = _build_row(request, candidate_values)
        if row is not None:
            built_rows.append(row)
        if diagnostic is not None:
            diagnostics.append(diagnostic)

    by_id: dict[str, PITDatasetRow] = {}
    first_request_by_id: dict[str, PITDatasetRequest] = {}
    for row in built_rows:
        existing = by_id.get(row.row_id)
        if existing is None:
            by_id[row.row_id] = row
            first_request_by_id[row.row_id] = next(
                item for item in request_values
                if item.research_subject_id == row.research_subject_id
                and item.observation_version_id == row.observation_version_id
                and item.research_cutoff_at == row.research_cutoff_at
            )
            continue
        if existing.row_content_hash != row.row_content_hash:
            raise PITDatasetHardFail("ROW_ID_CONTENT_CONFLICT")
        diagnostics.append(_diagnostic(
            first_request_by_id[row.row_id],
            "DEDUPLICATED",
            "DUPLICATE_ROW_SAME_CONTENT",
        ))

    ordered_rows = tuple(sorted(by_id.values(), key=lambda item: item.ordering_key))
    ordered_diagnostics = tuple(sorted(
        diagnostics,
        key=lambda item: (
            str(item["research_cutoff_at"]),
            str(item["research_subject_id"]),
            str(item["classification"]),
            str(item["reason"]),
            str(item["feature_definition_id"] or ""),
        ),
    ))
    context = next(iter(contexts))
    rule_bindings = tuple(sorted({
        tuple(sorted(_plain(binding).items()))
        for row in ordered_rows for binding in row.rule_bundle_bindings
    }))
    profile_bindings = tuple(sorted({
        tuple(sorted(_plain(binding).items()))
        for row in ordered_rows for binding in row.source_profile_bindings
    }))
    manifest = PITDatasetManifest(
        manifest_type=DATASET_MANIFEST_TYPE,
        manifest_version=DATASET_MANIFEST_VERSION,
        dataset_contract_version=context[0],
        feature_set_version=context[1],
        feature_computation_profile_version=context[2],
        cutoff_policy_version=context[3],
        rule_bundle_bindings=tuple(dict(item) for item in rule_bindings),
        source_profile_bindings=tuple(dict(item) for item in profile_bindings),
        authorization_snapshot=_authorization_snapshot(),
        ordered_row_ids=tuple(item.row_id for item in ordered_rows),
        ordered_row_content_hashes=tuple(item.row_content_hash for item in ordered_rows),
        include_count=len(ordered_rows),
        exclude_count=sum(item["classification"] == "ROW_EXCLUDE" for item in ordered_diagnostics),
        quarantine_count=sum(item["classification"] == "QUARANTINE" for item in ordered_diagnostics),
        exclusion_reason_summary=_summary(ordered_diagnostics, "ROW_EXCLUDE"),
        quarantine_reason_summary=_summary(ordered_diagnostics, "QUARANTINE"),
    )
    return PITDatasetResult(ordered_rows, manifest, ordered_diagnostics)
