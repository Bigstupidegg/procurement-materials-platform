"""C4-RD-7.4B deterministic preregistration core contract.

This module validates synthetic RD-6 ``PITDatasetResult`` authority and builds
an immutable, in-memory declaration of a Direction research protocol.  It does
not approve, persist, execute, backtest, forecast, calculate labels/metrics, or
access external data.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass
from decimal import Decimal
import re
from types import MappingProxyType
from typing import Any
import unicodedata

from scripts.c4_rd_contract import (
    BACKTEST_ENABLED_SOURCES,
    CONTRACT_VERSION,
    PIT_ENABLED_SOURCES,
    RD3_OPEN_BLOCKERS,
    RESEARCH_ENABLED_SOURCES,
    SAFETY_FLAGS,
    ContractError,
    canonical_hash,
    canonical_json_bytes,
)
from scripts.c4_rd_pit_dataset import PITDatasetResult


HORIZONS = ("P7D", "P14D", "P28D")
_QUESTION_KEYS = (
    "objective",
    "population_scope",
    "intervention_or_method",
    "comparator",
    "outcome",
)
_FEATURE_DEFINITION_KEYS = (
    "feature_definition_id",
    "feature_definition_version",
    "value_key",
    "requirement",
)
_LABEL_KEYS = ("label_definition_id", "label_horizon", "label_reference_id")
_REQUEST_KEYS = (
    "dataset_contract_version",
    "research_subject_id",
    "observation_version_id",
    "research_cutoff_at",
    "feature_set_version",
    "feature_computation_profile_version",
    "cutoff_policy_version",
    "feature_definitions",
    "label_specification",
    "execution_mode",
)
_EXPECTED_BLOCKERS = (
    "RD3-LME-001", "RD3-LME-002", "RD3-LME-003",
    "RD3-SMM-001", "RD3-SMM-002", "RD3-SMM-003", "RD3-SMM-004",
    "RD3-YAHOO-001", "RD3-YAHOO-002", "RD3-YAHOO-003", "RD3-YAHOO-004",
    "RD3-BZ-001", "RD3-WB-001", "RD3-WB-002", "RD3-WB-003",
)
_SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


class PreregistrationError(ContractError):
    """Base error for the preregistration contract."""


class PreregistrationInvalidProtocol(PreregistrationError):
    """Raised for malformed caller-supplied research protocol input."""


class PreregistrationHardFail(PreregistrationError):
    """Raised when supplied dataset authority cannot be trusted."""


def _plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _plain(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _freeze(value: Any, error_type: type[PreregistrationError]) -> Any:
    if isinstance(value, Mapping):
        try:
            items = tuple(value.items())
        except Exception as exc:
            raise error_type("mapping input could not be snapshotted") from exc
        copied: dict[str, Any] = {}
        for key, item in items:
            if not isinstance(key, str):
                raise error_type("mapping keys must be strings")
            copied[key] = _freeze(item, error_type)
        return MappingProxyType(copied)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item, error_type) for item in value)
    if isinstance(value, (set, frozenset)):
        raise error_type("set-like input is forbidden")
    if isinstance(value, float):
        raise error_type("binary float is forbidden")
    if value is None or isinstance(value, (str, bool, int, Decimal)):
        return value
    raise error_type(f"unsupported input type: {type(value).__name__}")


def _require_nonblank(value: Any, name: str, error_type: type[PreregistrationError]) -> str:
    if not isinstance(value, str) or not value or value.isspace():
        raise error_type(f"{name} must be a non-whitespace string")
    return value


def _validate_semver(value: Any) -> str:
    value = _require_nonblank(value, "preregistration_version", PreregistrationInvalidProtocol)
    matched = _SEMVER.fullmatch(value)
    if matched is None:
        raise PreregistrationInvalidProtocol("preregistration_version must be strict SemVer 2.0.0")
    prerelease = matched.group(4)
    if prerelease is not None:
        for identifier in prerelease.split("."):
            if identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0"):
                raise PreregistrationInvalidProtocol("numeric prerelease identifier has a leading zero")
    return value


def _semantic_equal(first: Any, second: Any) -> bool:
    return canonical_json_bytes(_plain(first)) == canonical_json_bytes(_plain(second))


@dataclass(frozen=True, slots=True)
class ResearchQuestion:
    objective: str
    population_scope: str
    intervention_or_method: str
    comparator: str
    outcome: str

    def __post_init__(self) -> None:
        for name in _QUESTION_KEYS:
            _require_nonblank(getattr(self, name), name, PreregistrationInvalidProtocol)

    def semantic_projection(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in _QUESTION_KEYS}


@dataclass(frozen=True, slots=True)
class HorizonPlanEntry:
    forecast_horizon: str
    role: str

    def semantic_projection(self) -> dict[str, Any]:
        return {"forecast_horizon": self.forecast_horizon, "role": self.role}


@dataclass(frozen=True, slots=True)
class FeatureDefinitionBinding:
    feature_definition_id: str
    feature_definition_version: str
    value_key: str
    requirement: str

    def semantic_projection(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in _FEATURE_DEFINITION_KEYS}


@dataclass(frozen=True, slots=True)
class FeatureBinding:
    feature_set_version: str
    feature_computation_profile_version: str
    feature_definitions: tuple[FeatureDefinitionBinding, ...]

    def semantic_projection(self) -> dict[str, Any]:
        return {
            "feature_set_version": self.feature_set_version,
            "feature_computation_profile_version": self.feature_computation_profile_version,
            "feature_definitions": [item.semantic_projection() for item in self.feature_definitions],
        }


@dataclass(frozen=True, slots=True)
class DatasetBinding:
    forecast_horizon: str
    dataset_identity: str
    dataset_manifest_type: str
    dataset_manifest_version: str
    dataset_contract_version: str
    feature_set_version: str
    feature_computation_profile_version: str
    cutoff_policy_version: str
    label_definition_id: str
    label_reference_id: str

    def semantic_projection(self) -> dict[str, Any]:
        return {
            "forecast_horizon": self.forecast_horizon,
            "dataset_identity": self.dataset_identity,
            "dataset_manifest_type": self.dataset_manifest_type,
            "dataset_manifest_version": self.dataset_manifest_version,
            "dataset_contract_version": self.dataset_contract_version,
            "feature_set_version": self.feature_set_version,
            "feature_computation_profile_version": self.feature_computation_profile_version,
            "cutoff_policy_version": self.cutoff_policy_version,
            "label_definition_id": self.label_definition_id,
            "label_reference_id": self.label_reference_id,
        }


@dataclass(frozen=True, slots=True)
class LabelProtocol:
    label_definition_id: str
    target_computation_rule: str
    target_availability_rule: str
    missing_label_policy: str
    direction_semantics: Mapping[str, str]
    flat_evaluation_policy: str

    def semantic_projection(self) -> dict[str, Any]:
        return {
            "label_definition_id": self.label_definition_id,
            "target_computation_rule": self.target_computation_rule,
            "target_availability_rule": self.target_availability_rule,
            "missing_label_policy": self.missing_label_policy,
            "direction_semantics": _plain(self.direction_semantics),
            "flat_evaluation_policy": self.flat_evaluation_policy,
        }


@dataclass(frozen=True, slots=True)
class BaselineDefinition:
    baseline_id: str
    baseline_version: str
    required_inputs: str
    lookback_rule: str
    missing_data_behavior: str
    output_definition: str

    def semantic_projection(self) -> dict[str, Any]:
        return {
            "baseline_id": self.baseline_id,
            "baseline_version": self.baseline_version,
            "required_inputs": self.required_inputs,
            "lookback_rule": self.lookback_rule,
            "missing_data_behavior": self.missing_data_behavior,
            "output_definition": self.output_definition,
        }


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    metric_id: str
    metric_version: str
    aggregation_scope: str | None = None

    def semantic_projection(self) -> dict[str, Any]:
        result = {"metric_id": self.metric_id, "metric_version": self.metric_version}
        if self.aggregation_scope is not None:
            result["aggregation_scope"] = self.aggregation_scope
        return result


@dataclass(frozen=True, slots=True)
class MetricPlan:
    primary: tuple[MetricDefinition, ...]
    secondary: tuple[MetricDefinition, ...]
    diagnostic: tuple[MetricDefinition, ...]

    def semantic_projection(self) -> dict[str, Any]:
        return {
            "primary": [item.semantic_projection() for item in self.primary],
            "secondary": [item.semantic_projection() for item in self.secondary],
            "diagnostic": [item.semantic_projection() for item in self.diagnostic],
        }


@dataclass(frozen=True, slots=True)
class EvaluationProtocol:
    evaluation_unit_fields: tuple[str, ...]
    temporal_policy: str
    model_training_mode: str
    calibration_policy: str
    final_evaluation_universe: str
    candidate_abstention_policy: str
    candidate_required_coverage: Decimal
    paired_comparison_policy: str
    primary_horizon: str
    primary_total_min: int
    primary_up_min: int
    primary_down_min: int
    per_subject_total_min: int
    per_subject_up_min: int
    per_subject_down_min: int
    practical_superiority_floor: Decimal
    baseline_success_policy: str
    bootstrap_method: str
    bootstrap_replicates: int
    bootstrap_block_length_origins: int
    confidence_level: Decimal
    bootstrap_seed_rule: str
    multi_subject_bootstrap_policy: str
    pit_invariant_declarations: tuple[str, ...]
    forbidden_practices: tuple[str, ...]
    result_states: tuple[str, ...]

    def semantic_projection(self) -> dict[str, Any]:
        return {item.name: _plain(getattr(self, item.name)) for item in fields(self)}


@dataclass(frozen=True, slots=True)
class ExclusionRule:
    rule_id: str
    rule_version: str
    behavior: str

    def semantic_projection(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "behavior": self.behavior,
        }


@dataclass(frozen=True, slots=True)
class ExclusionPolicy:
    rules: tuple[ExclusionRule, ...]
    forbidden_substitutions: tuple[str, ...]

    def semantic_projection(self) -> dict[str, Any]:
        return {
            "rules": [item.semantic_projection() for item in self.rules],
            "forbidden_substitutions": list(self.forbidden_substitutions),
        }


@dataclass(frozen=True, slots=True)
class ReportingPolicy:
    mandatory_reporting_fields: tuple[str, ...]
    null_result_policy: str
    failure_result_policy: str

    def semantic_projection(self) -> dict[str, Any]:
        return {
            "mandatory_reporting_fields": list(self.mandatory_reporting_fields),
            "null_result_policy": self.null_result_policy,
            "failure_result_policy": self.failure_result_policy,
        }


@dataclass(frozen=True, slots=True)
class PreregistrationProtocol:
    contract_version: str
    preregistration_version: str
    research_protocol_id: str
    research_question: ResearchQuestion
    research_task: str
    research_subjects: tuple[str, ...]
    horizon_plan: tuple[HorizonPlanEntry, ...]
    dataset_bindings: tuple[DatasetBinding, ...]
    feature_binding: FeatureBinding
    label_protocol: LabelProtocol
    baseline_plan: tuple[BaselineDefinition, ...]
    metric_plan: MetricPlan
    evaluation_protocol: EvaluationProtocol
    exclusion_policy: ExclusionPolicy
    reporting_policy: ReportingPolicy
    authorization_snapshot: Mapping[str, Any]

    def __post_init__(self) -> None:
        canonical_json_bytes(self.semantic_projection())

    def semantic_projection(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "preregistration_version": self.preregistration_version,
            "research_protocol_id": self.research_protocol_id,
            "research_question": self.research_question.semantic_projection(),
            "research_task": self.research_task,
            "research_subjects": list(self.research_subjects),
            "horizon_plan": [item.semantic_projection() for item in self.horizon_plan],
            "dataset_bindings": [item.semantic_projection() for item in self.dataset_bindings],
            "feature_binding": self.feature_binding.semantic_projection(),
            "label_protocol": self.label_protocol.semantic_projection(),
            "baseline_plan": [item.semantic_projection() for item in self.baseline_plan],
            "metric_plan": self.metric_plan.semantic_projection(),
            "evaluation_protocol": self.evaluation_protocol.semantic_projection(),
            "exclusion_policy": self.exclusion_policy.semantic_projection(),
            "reporting_policy": self.reporting_policy.semantic_projection(),
            "authorization_snapshot": _plain(self.authorization_snapshot),
        }

    @property
    def preregistration_identity(self) -> str:
        return canonical_hash("PREREGISTRATION_CONTENT", self.semantic_projection())


@dataclass(frozen=True, slots=True)
class PreregistrationResult:
    protocol: PreregistrationProtocol
    preregistration_identity: str
    validation_status: str
    diagnostics: tuple[Mapping[str, Any], ...] = ()
    runtime_metadata: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.protocol) is not PreregistrationProtocol:
            raise PreregistrationHardFail("result protocol type mismatch")
        if self.validation_status != "VALID":
            raise PreregistrationHardFail("successful result status must be VALID")
        if self.preregistration_identity != self.protocol.preregistration_identity:
            raise PreregistrationHardFail("result identity does not match protocol")
        object.__setattr__(self, "diagnostics", tuple(_freeze(item, PreregistrationHardFail) for item in self.diagnostics))
        object.__setattr__(self, "runtime_metadata", _freeze(self.runtime_metadata, PreregistrationInvalidProtocol))


def _authorization_snapshot() -> Mapping[str, Any]:
    if len(SAFETY_FLAGS) != 9 or any(value is not False for value in SAFETY_FLAGS.values()):
        raise PreregistrationHardFail("authorization flags differ from the frozen v1 boundary")
    if PIT_ENABLED_SOURCES != () or RESEARCH_ENABLED_SOURCES != () or BACKTEST_ENABLED_SOURCES != ():
        raise PreregistrationHardFail("enabled-source tuples differ from the frozen v1 boundary")
    if RD3_OPEN_BLOCKERS != _EXPECTED_BLOCKERS:
        raise PreregistrationHardFail("RD-3 blockers differ from the frozen v1 boundary")
    return _freeze({
        "safety_flags": dict(SAFETY_FLAGS),
        "pit_enabled_sources": PIT_ENABLED_SOURCES,
        "research_enabled_sources": RESEARCH_ENABLED_SOURCES,
        "backtest_enabled_sources": BACKTEST_ENABLED_SOURCES,
        "rd3_open_blockers": RD3_OPEN_BLOCKERS,
    }, PreregistrationHardFail)


def _research_question(value: Any) -> ResearchQuestion:
    if type(value) is ResearchQuestion:
        return value
    if not isinstance(value, Mapping):
        raise PreregistrationInvalidProtocol("research_question must be a mapping")
    frozen = _freeze(value, PreregistrationInvalidProtocol)
    if set(frozen) != set(_QUESTION_KEYS):
        raise PreregistrationInvalidProtocol("research_question must contain exactly five frozen fields")
    return ResearchQuestion(**{name: frozen[name] for name in _QUESTION_KEYS})


def _research_subjects(values: Any) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)) or not values:
        raise PreregistrationInvalidProtocol("research_subjects must be a non-empty list or tuple")
    normalized: list[str] = []
    for value in tuple(values):
        value = _require_nonblank(value, "research subject", PreregistrationInvalidProtocol)
        normalized.append(unicodedata.normalize("NFC", value))
    if len(normalized) != len(set(normalized)):
        raise PreregistrationInvalidProtocol("duplicate research subject after NFC normalization")
    return tuple(sorted(normalized))


def _feature_definitions(value: Any) -> tuple[FeatureDefinitionBinding, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise PreregistrationHardFail("feature_definitions must be a non-empty ordered sequence")
    result: list[FeatureDefinitionBinding] = []
    identities: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != set(_FEATURE_DEFINITION_KEYS):
            raise PreregistrationHardFail("feature definition binding shape mismatch")
        values = {
            name: _require_nonblank(item[name], name, PreregistrationHardFail)
            for name in _FEATURE_DEFINITION_KEYS
        }
        if values["requirement"] not in {"MANDATORY", "OPTIONAL"}:
            raise PreregistrationHardFail("feature requirement mismatch")
        if values["feature_definition_id"] in identities:
            raise PreregistrationHardFail("duplicate feature definition")
        identities.add(values["feature_definition_id"])
        result.append(FeatureDefinitionBinding(**values))
    return tuple(result)


def _validate_result_authority(result: PITDatasetResult, authorization: Mapping[str, Any]) -> None:
    if tuple(row.row_id for row in result.rows) != result.manifest.ordered_row_ids:
        raise PreregistrationHardFail("PIT rows do not reconcile to manifest row IDs")
    if tuple(row.row_content_hash for row in result.rows) != result.manifest.ordered_row_content_hashes:
        raise PreregistrationHardFail("PIT rows do not reconcile to manifest row hashes")
    if not _semantic_equal(result.manifest.authorization_snapshot, authorization):
        raise PreregistrationHardFail("PIT manifest authorization snapshot mismatch")
    for row in result.rows:
        if row.operational_status != "SYNTHETIC_NON_OPERATIONAL":
            raise PreregistrationHardFail("PIT row is not synthetic non-operational")


def _scope_analysis(
    horizon: str,
    result: PITDatasetResult,
) -> tuple[str, str, tuple[bytes, ...], tuple[FeatureDefinitionBinding, ...], frozenset[str]]:
    scope = result.manifest.request_scope
    if not scope:
        raise PreregistrationHardFail("PIT request_scope must not be empty")
    definitions: tuple[FeatureDefinitionBinding, ...] | None = None
    definition_identities: set[str] = set()
    reference_identities: set[str] = set()
    subjects: set[str] = set()
    reduced: list[bytes] = []
    for entry in scope:
        if not isinstance(entry, Mapping) or set(entry) != set(_REQUEST_KEYS):
            raise PreregistrationHardFail("PIT request_scope entry shape mismatch")
        if entry["execution_mode"] != "SYNTHETIC_NON_OPERATIONAL":
            raise PreregistrationHardFail("PIT request is not synthetic non-operational")
        if entry["feature_set_version"] != result.manifest.feature_set_version:
            raise PreregistrationHardFail("request feature_set_version mismatch")
        if entry["feature_computation_profile_version"] != result.manifest.feature_computation_profile_version:
            raise PreregistrationHardFail("request feature computation profile mismatch")
        entry_definitions = _feature_definitions(entry["feature_definitions"])
        if definitions is None:
            definitions = entry_definitions
        elif entry_definitions != definitions:
            raise PreregistrationHardFail("feature definition sequence mismatch")
        label = entry["label_specification"]
        if not isinstance(label, Mapping) or set(label) != set(_LABEL_KEYS):
            raise PreregistrationHardFail("label specification shape mismatch")
        for name in _LABEL_KEYS:
            _require_nonblank(label[name], name, PreregistrationHardFail)
        if label["label_horizon"] != horizon:
            raise PreregistrationHardFail("label horizon does not match dataset binding")
        definition_identities.add(label["label_definition_id"])
        reference_identities.add(label["label_reference_id"])
        subject = _require_nonblank(entry["research_subject_id"], "research_subject_id", PreregistrationHardFail)
        subjects.add(unicodedata.normalize("NFC", subject))
        reduced.append(canonical_json_bytes({key: _plain(entry[key]) for key in _REQUEST_KEYS if key != "label_specification"}))
    if len(definition_identities) != 1:
        raise PreregistrationHardFail("one label_definition_id is required within each horizon")
    if len(reference_identities) != 1:
        raise PreregistrationHardFail("one label_reference_id is required within each horizon")
    if definitions is None:
        raise PreregistrationHardFail("feature definition authority is absent")
    return (
        next(iter(definition_identities)),
        next(iter(reference_identities)),
        tuple(sorted(reduced)),
        definitions,
        frozenset(subjects),
    )


def _fixed_baselines() -> tuple[BaselineDefinition, ...]:
    values = (
        BaselineDefinition(
            "last_observation_direction", "1.0.0",
            "TWO_LATEST_PIT_SAFE_OBSERVATIONS",
            "LATEST_TWO_DISTINCT_SOURCE_DATES_AT_OR_BEFORE_ORIGIN",
            "INSUFFICIENT_DATA", "SIGN_LATEST_MINUS_PREVIOUS",
        ),
        BaselineDefinition(
            "moving_average_trend", "1.0.0",
            "AT_LEAST_TWO_PIT_SAFE_OBSERVATIONS_IN_INCLUSIVE_HORIZON_WINDOW",
            "SOURCE_DATE_IN_[ORIGIN_MINUS_H,ORIGIN]",
            "INSUFFICIENT_DATA", "SIGN_ORIGIN_MINUS_ARITHMETIC_MEAN_WINDOW",
        ),
        BaselineDefinition(
            "momentum_direction", "1.0.0",
            "EXACT_ORIGIN_MINUS_H_PIT_SAFE_OBSERVATION",
            "EXACT_SOURCE_DATE_ORIGIN_MINUS_H",
            "INSUFFICIENT_DATA", "SIGN_ORIGIN_MINUS_EXACT_LAG",
        ),
    )
    return tuple(sorted(values, key=lambda item: (item.baseline_id, item.baseline_version)))


def _fixed_metric_plan() -> MetricPlan:
    secondary = tuple(sorted((
        MetricDefinition("accuracy", "1.0.0"),
        MetricDefinition("macro_f1", "1.0.0"),
    ), key=lambda item: (item.metric_id, item.metric_version)))
    diagnostic = tuple(
        MetricDefinition(metric_id, "1.0.0")
        for metric_id in sorted((
            "actual_distribution", "confusion_matrix", "evaluation_coverage",
            "exclusion_reason_counts", "f1_down", "f1_up", "precision_down",
            "precision_up", "prediction_distribution", "recall_down", "recall_up",
        ))
    )
    roles = (
        tuple((item.metric_id, item.metric_version) for item in (MetricDefinition("balanced_accuracy", "1.0.0", "SUBJECT_MACRO"),)),
        tuple((item.metric_id, item.metric_version) for item in secondary),
        tuple((item.metric_id, item.metric_version) for item in diagnostic),
    )
    flattened = tuple(identity for role in roles for identity in role)
    if len(flattened) != len(set(flattened)):
        raise PreregistrationHardFail("metric identity appears in multiple roles")
    return MetricPlan(
        primary=(MetricDefinition("balanced_accuracy", "1.0.0", "SUBJECT_MACRO"),),
        secondary=secondary,
        diagnostic=diagnostic,
    )


def _fixed_evaluation_protocol() -> EvaluationProtocol:
    return EvaluationProtocol(
        evaluation_unit_fields=(
            "research_subject_id", "research_cutoff_at", "forecast_horizon", "label_definition_id",
        ),
        temporal_policy="CHRONOLOGICAL_WALK_FORWARD_NO_RANDOMIZATION",
        model_training_mode="NONE",
        calibration_policy="NONE_FOR_V1",
        final_evaluation_universe="ALL_PREREGISTERED_ELIGIBLE_ORIGINS",
        candidate_abstention_policy="FORBIDDEN",
        candidate_required_coverage=Decimal("1"),
        paired_comparison_policy="PER_BASELINE_MATCHED_ATOMIC_UNIVERSE",
        primary_horizon="P14D",
        primary_total_min=120,
        primary_up_min=40,
        primary_down_min=40,
        per_subject_total_min=30,
        per_subject_up_min=10,
        per_subject_down_min=10,
        practical_superiority_floor=Decimal("0.02"),
        baseline_success_policy="ALL_REQUIRED_BASELINES_MUST_PASS",
        bootstrap_method="MOVING_BLOCK_BOOTSTRAP",
        bootstrap_replicates=2000,
        bootstrap_block_length_origins=28,
        confidence_level=Decimal("0.95"),
        bootstrap_seed_rule="DERIVE_DETERMINISTIC_INTEGER_FROM_PREREGISTRATION_IDENTITY",
        multi_subject_bootstrap_policy="WITHIN_SUBJECT_CONTIGUOUS_BLOCKS_THEN_UNWEIGHTED_SUBJECT_MEAN",
        pit_invariant_declarations=(
            "feature_available_at_max <= cutoff_at",
            "label_available_at > cutoff_at",
            "evaluation_as_of_at >= label_available_at",
        ),
        forbidden_practices=(
            "RANDOM_SHUFFLE", "RANDOM_K_FOLD", "FUTURE_AWARE_SCALING",
            "FULL_HISTORY_FIT", "FUTURE_INFORMED_FEATURE_SELECTION",
        ),
        result_states=(
            "EVIDENCE_SUPPORTS_INCREMENTAL_VALUE", "NO_CLEAR_INCREMENTAL_VALUE",
            "INSUFFICIENT_EVIDENCE", "INVALID_EVALUATION",
        ),
    )


def _fixed_exclusion_policy() -> ExclusionPolicy:
    rules = tuple(sorted((
        ExclusionRule("MISSING_EXACT_HORIZON_LABEL", "1.0.0", "EXCLUDE_EVALUATION_UNIT"),
        ExclusionRule("ZERO_RETURN_NON_DIRECTIONAL", "1.0.0", "EXCLUDE_BINARY_PRIMARY_KEEP_ACCOUNTING"),
        ExclusionRule("BASELINE_INSUFFICIENT_DATA", "1.0.0", "EXCLUDE_ONLY_AFFECTED_BASELINE_PAIRED_COMPARISON"),
        ExclusionRule("CANDIDATE_PREDICTION_FAILURE", "1.0.0", "KEEP_SAMPLE_AND_FAIL_COVERAGE_GATE"),
        ExclusionRule("PIT_INVARIANT_VIOLATION", "1.0.0", "INVALID_EVALUATION"),
        ExclusionRule("EXTRAORDINARY_EVENT", "1.0.0", "KEEP_PRIMARY_ALLOW_DIAGNOSTIC_SUBGROUP_ONLY"),
    ), key=lambda item: (item.rule_id, item.rule_version)))
    return ExclusionPolicy(
        rules=rules,
        forbidden_substitutions=(
            "NEAREST_DATE_SUBSTITUTION", "FORWARD_FILL", "BACKFILL",
            "INTERPOLATION", "MANUAL_LABEL_FILL", "POST_HOC_EXCLUSION",
        ),
    )


def _fixed_reporting_policy() -> ReportingPolicy:
    return ReportingPolicy(
        mandatory_reporting_fields=(
            "preregistration_identity", "dataset_identities", "research_task", "subjects", "horizon_roles",
            "requested_units", "eligible_units", "label_available_units", "baseline_pairable_units",
            "candidate_prediction_units", "evaluated_units", "excluded_units", "exclusion_reasons",
            "primary_metrics", "secondary_metrics", "diagnostics", "per_subject", "per_horizon",
            "per_class", "baseline_comparison", "delta_vs_baseline", "confidence_interval",
            "negative_results", "null_results", "failed_results",
        ),
        null_result_policy="MANDATORY_PRESERVE_AND_REPORT",
        failure_result_policy="MANDATORY_PRESERVE_AND_REPORT",
    )


def build_preregistration_protocol(
    *,
    preregistration_version: str,
    research_protocol_id: str,
    research_question: Mapping[str, Any] | ResearchQuestion,
    research_subjects: Sequence[str],
    dataset_results_by_horizon: Mapping[str, PITDatasetResult],
    runtime_metadata: Mapping[str, Any] | None = None,
) -> PreregistrationResult:
    """Validate RD-6 authority and build the frozen RD-7 v1 protocol."""

    version = _validate_semver(preregistration_version)
    protocol_id = _require_nonblank(
        research_protocol_id, "research_protocol_id", PreregistrationInvalidProtocol,
    )
    question = _research_question(research_question)
    subjects = _research_subjects(research_subjects)
    if not isinstance(dataset_results_by_horizon, Mapping):
        raise PreregistrationHardFail("dataset_results_by_horizon must be a mapping")
    try:
        result_items = tuple(dataset_results_by_horizon.items())
    except Exception as exc:
        raise PreregistrationHardFail("dataset authority mapping could not be snapshotted") from exc
    results = dict(result_items)
    if set(results) != set(HORIZONS):
        raise PreregistrationHardFail("dataset authority requires exactly P7D, P14D, and P28D")
    if any(type(results[horizon]) is not PITDatasetResult for horizon in HORIZONS):
        raise PreregistrationHardFail("each horizon authority must be an exact PITDatasetResult")

    authorization = _authorization_snapshot()
    analyses: dict[str, tuple[str, str, tuple[bytes, ...], tuple[FeatureDefinitionBinding, ...], frozenset[str]]] = {}
    for horizon in HORIZONS:
        _validate_result_authority(results[horizon], authorization)
        analyses[horizon] = _scope_analysis(horizon, results[horizon])

    first = results[HORIZONS[0]].manifest
    aligned_fields = (
        "feature_set_version", "feature_computation_profile_version", "cutoff_policy_version",
        "rule_bundle_bindings", "source_profile_bindings", "authorization_snapshot",
        "include_count", "exclude_count", "quarantine_count",
        "exclusion_reason_summary", "quarantine_reason_summary",
    )
    for horizon in HORIZONS[1:]:
        manifest = results[horizon].manifest
        for name in aligned_fields:
            if not _semantic_equal(getattr(manifest, name), getattr(first, name)):
                raise PreregistrationHardFail(f"cross-horizon manifest mismatch: {name}")
    if not all(analyses[horizon][2] == analyses[HORIZONS[0]][2] for horizon in HORIZONS[1:]):
        raise PreregistrationHardFail("cross-horizon reduced request universe mismatch")
    if not all(results[horizon].manifest.ordered_row_ids == first.ordered_row_ids for horizon in HORIZONS[1:]):
        raise PreregistrationHardFail("cross-horizon ordered row-ID universe mismatch")
    if not all(analyses[horizon][3] == analyses[HORIZONS[0]][3] for horizon in HORIZONS[1:]):
        raise PreregistrationHardFail("cross-horizon feature definition sequence mismatch")
    expected_subjects = frozenset(subjects)
    if any(analyses[horizon][4] != expected_subjects for horizon in HORIZONS):
        raise PreregistrationHardFail("manifest subject universe does not match research_subjects")
    label_definitions = {analyses[horizon][0] for horizon in HORIZONS}
    if len(label_definitions) != 1:
        raise PreregistrationHardFail("label_definition_id must match across horizons")

    dataset_bindings = tuple(
        DatasetBinding(
            forecast_horizon=horizon,
            dataset_identity=results[horizon].dataset_identity,
            dataset_manifest_type=results[horizon].manifest.manifest_type,
            dataset_manifest_version=results[horizon].manifest.manifest_version,
            dataset_contract_version=results[horizon].manifest.dataset_contract_version,
            feature_set_version=results[horizon].manifest.feature_set_version,
            feature_computation_profile_version=results[horizon].manifest.feature_computation_profile_version,
            cutoff_policy_version=results[horizon].manifest.cutoff_policy_version,
            label_definition_id=analyses[horizon][0],
            label_reference_id=analyses[horizon][1],
        )
        for horizon in HORIZONS
    )
    feature_binding = FeatureBinding(
        feature_set_version=first.feature_set_version,
        feature_computation_profile_version=first.feature_computation_profile_version,
        feature_definitions=analyses[HORIZONS[0]][3],
    )
    protocol = PreregistrationProtocol(
        contract_version=CONTRACT_VERSION,
        preregistration_version=version,
        research_protocol_id=protocol_id,
        research_question=question,
        research_task="DIRECTION",
        research_subjects=subjects,
        horizon_plan=(
            HorizonPlanEntry("P7D", "SECONDARY"),
            HorizonPlanEntry("P14D", "PRIMARY"),
            HorizonPlanEntry("P28D", "SECONDARY"),
        ),
        dataset_bindings=dataset_bindings,
        feature_binding=feature_binding,
        label_protocol=LabelProtocol(
            label_definition_id=next(iter(label_definitions)),
            target_computation_rule="SIGN_OF_EXACT_HORIZON_RETURN",
            target_availability_rule="LABEL_AVAILABLE_STRICTLY_AFTER_CUTOFF",
            missing_label_policy="EXCLUDE_FROM_EVALUATION",
            direction_semantics=MappingProxyType({
                "UP": "RETURN_GT_ZERO", "DOWN": "RETURN_LT_ZERO", "FLAT": "RETURN_EQ_ZERO",
            }),
            flat_evaluation_policy="ZERO_RETURN_NON_DIRECTIONAL_EXCLUDE_PRIMARY_KEEP_ACCOUNTING",
        ),
        baseline_plan=_fixed_baselines(),
        metric_plan=_fixed_metric_plan(),
        evaluation_protocol=_fixed_evaluation_protocol(),
        exclusion_policy=_fixed_exclusion_policy(),
        reporting_policy=_fixed_reporting_policy(),
        authorization_snapshot=authorization,
    )
    identity = protocol.preregistration_identity
    return PreregistrationResult(
        protocol=protocol,
        preregistration_identity=identity,
        validation_status="VALID",
        diagnostics=(),
        runtime_metadata={} if runtime_metadata is None else runtime_metadata,
    )
