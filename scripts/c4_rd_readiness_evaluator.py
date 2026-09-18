"""C4-RD-4.2 deterministic, local-only readiness evaluation.

The evaluator judges already-supplied C4-RD-4.1 value objects.  It performs no
collection, network access, persistence, export, promotion, prediction, or use
of ambient current time.  Source profiles are inert rule representations that
can only add restrictions to the frozen core contract.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from scripts.c4_rd_contract import (
    BACKTEST_ENABLED_SOURCES,
    CONTRACT_VERSION,
    EXCLUSION_REASON_CODES,
    PIT_ENABLED_SOURCES,
    RD3_OPEN_BLOCKERS,
    RESEARCH_ENABLED_SOURCES,
    AvailabilityAssessment,
    CalendarReference,
    ContractError,
    LineageAssessment,
    Observation,
    ObservationVersion,
    RuleBundle,
    TrustQualityAssessment,
    canonical_timestamp,
    parse_rfc3339,
)


EVALUATOR_VERSION = "C4_RD_4_2_EVALUATOR_V1@1.0.0"
EVALUATION_ROLES = ("PIT", "RESEARCH", "BACKTEST")
SOURCE_PROFILE_VERSION = "1.0.0"


class EvaluationInputError(ContractError):
    """Raised when an evaluation cannot safely enter the decision procedure."""


@dataclass(frozen=True, slots=True)
class BlockerRule:
    blocker_id: str
    reason_code: str
    roles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SourceProfile:
    """Inert restrictions for one explicitly approved source interpretation."""

    profile_id: str
    profile_version: str
    accepted_source_ids: tuple[str, ...]
    required_calendar_roles: tuple[str, ...]
    blocker_rules: tuple[BlockerRule, ...]
    accepted_instrument_ids: tuple[str, ...] = ()


def _rule(blocker_id: str, reason_code: str, *roles: str) -> BlockerRule:
    return BlockerRule(blocker_id, reason_code, tuple(roles))


_ALL_ROLES = EVALUATION_ROLES
_HISTORICAL_ROLES = ("RESEARCH", "BACKTEST")

SOURCE_PROFILES: Mapping[str, SourceProfile] = MappingProxyType({
    "LME": SourceProfile(
        "LME", SOURCE_PROFILE_VERSION, ("LME",), ("MARKET",),
        (
            _rule("RD3-LME-001", "AVAILABILITY_UNVERIFIED", *_ALL_ROLES),
            _rule("RD3-LME-002", "SOURCE_LICENSING_BLOCKED", *_ALL_ROLES),
            _rule("RD3-LME-003", "HISTORICAL_VINTAGE_NOT_PROVEN", *_HISTORICAL_ROLES),
        ),
    ),
    "SMM": SourceProfile(
        "SMM", SOURCE_PROFILE_VERSION, ("SMM",), ("PUBLICATION",),
        (
            _rule("RD3-SMM-001", "AVAILABILITY_UNVERIFIED", *_ALL_ROLES),
            _rule("RD3-SMM-002", "CALENDAR_UNRESOLVED", *_ALL_ROLES),
            _rule("RD3-SMM-003", "SOURCE_LICENSING_BLOCKED", *_ALL_ROLES),
            _rule("RD3-SMM-004", "HISTORICAL_VINTAGE_NOT_PROVEN", *_HISTORICAL_ROLES),
        ),
    ),
    "YAHOO": SourceProfile(
        "YAHOO", SOURCE_PROFILE_VERSION, ("YAHOO",), ("MARKET",),
        (
            _rule("RD3-YAHOO-001", "SOURCE_LICENSING_BLOCKED", *_ALL_ROLES),
            _rule("RD3-YAHOO-002", "CONTINUOUS_CONTRACT_MAPPING_UNRESOLVED", *_ALL_ROLES),
            _rule("RD3-YAHOO-003", "HISTORICAL_VINTAGE_NOT_PROVEN", *_HISTORICAL_ROLES),
            _rule("RD3-YAHOO-004", "AMBIGUOUS_SOURCE_DATE", *_ALL_ROLES),
        ),
    ),
    "BZ": SourceProfile(
        "BZ", SOURCE_PROFILE_VERSION, ("YAHOO",), (),
        (_rule("RD3-BZ-001", "SOURCE_VENUE_MAPPING_CONFLICT", *_ALL_ROLES),),
        ("BZ=F",),
    ),
    "WORLD_BANK": SourceProfile(
        "WORLD_BANK", SOURCE_PROFILE_VERSION, ("WORLD_BANK",), ("PUBLICATION",),
        (
            _rule("RD3-WB-001", "AVAILABILITY_UNVERIFIED", *_ALL_ROLES),
            _rule("RD3-WB-002", "HISTORICAL_VINTAGE_NOT_PROVEN", *_HISTORICAL_ROLES),
            _rule("RD3-WB-003", "SOURCE_LICENSING_BLOCKED", *_ALL_ROLES),
        ),
    ),
})


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    evidence_kind: str
    evidence_id: str
    content_hash: str

    def as_dict(self) -> dict[str, str]:
        return {
            "evidence_kind": self.evidence_kind,
            "evidence_id": self.evidence_id,
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True, slots=True)
class _SemanticEligibilityDecision:
    passed: bool
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReadinessEvaluationResult:
    observation_id: str
    observation_version_id: str
    evaluation_role: str
    cutoff_at: str
    evaluation_as_of_at: str
    label_available_at: str | None
    readiness_state: str
    eligibility_state: str
    reason_codes: tuple[str, ...]
    blocker_ids: tuple[str, ...]
    evidence_references: tuple[EvidenceReference, ...]
    contract_version: str
    evaluator_version: str
    rule_bundle_version: str
    rule_bundle_hash: str
    source_profile_id: str
    source_profile_version: str
    observation_content_hash: str
    observation_version_content_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject": {
                "observation_id": self.observation_id,
                "observation_version_id": self.observation_version_id,
            },
            "context": {
                "evaluation_role": self.evaluation_role,
                "cutoff_at": self.cutoff_at,
                "evaluation_as_of_at": self.evaluation_as_of_at,
                "label_available_at": self.label_available_at,
            },
            "result": {
                "readiness_state": self.readiness_state,
                "eligibility_state": self.eligibility_state,
            },
            "diagnostics": {
                "reason_codes": list(self.reason_codes),
                "blocker_ids": list(self.blocker_ids),
            },
            "evidence_references": [item.as_dict() for item in self.evidence_references],
            "rules": {
                "contract_version": self.contract_version,
                "evaluator_version": self.evaluator_version,
                "rule_bundle_version": self.rule_bundle_version,
                "rule_bundle_hash": self.rule_bundle_hash,
                "source_profile_id": self.source_profile_id,
                "source_profile_version": self.source_profile_version,
            },
            "audit": {
                "observation_content_hash": self.observation_content_hash,
                "observation_version_content_hash": self.observation_version_content_hash,
            },
        }


def _require_exact(value: Any, expected_type: type, name: str) -> None:
    if type(value) is not expected_type:
        raise EvaluationInputError(f"{name} must be an exact validated {expected_type.__name__}")


def _timestamp(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise EvaluationInputError(f"{name} must be an explicit timezone-aware RFC3339 string")
    try:
        return canonical_timestamp(value)
    except ContractError as exc:
        raise EvaluationInputError(f"invalid {name}: {exc}") from exc


def _snapshot_sequence(values: Sequence[Any], name: str) -> tuple[Any, ...]:
    if isinstance(values, (str, bytes)):
        raise EvaluationInputError(f"{name} must be a sequence of validated values")
    try:
        return tuple(values)
    except (TypeError, RuntimeError) as exc:
        raise EvaluationInputError(f"{name} could not be snapshotted") from exc


def _validate_rule_bundle(rule_bundle: RuleBundle) -> None:
    _require_exact(rule_bundle, RuleBundle, "rule_bundle")
    config = rule_bundle.rules.get("rd4_2")
    if not isinstance(config, Mapping):
        raise EvaluationInputError("RuleBundle.rules.rd4_2 must be an inert mapping")
    if config.get("evaluator_version") != EVALUATOR_VERSION:
        raise EvaluationInputError("unsupported RD-4.2 evaluator version")
    blockers = config.get("rd3_open_blockers")
    if not isinstance(blockers, tuple) or blockers != RD3_OPEN_BLOCKERS:
        raise EvaluationInputError("RuleBundle must preserve the exact ordered 15 OPEN RD-3 blockers")
    allowlists = (
        ("pit_enabled_sources", PIT_ENABLED_SOURCES),
        ("research_enabled_sources", RESEARCH_ENABLED_SOURCES),
        ("backtest_enabled_sources", BACKTEST_ENABLED_SOURCES),
    )
    for name, authoritative in allowlists:
        value = config.get(name)
        if not isinstance(value, tuple) or value != authoritative or value:
            raise EvaluationInputError(f"RuleBundle {name} must remain the authoritative empty tuple")


def _validate_reserved_identity(observation: Observation, version: ObservationVersion) -> None:
    expected_observation = observation.identity_projection()
    expected_version = version.identity_projection()
    sources: tuple[tuple[str, Mapping[str, Any], Mapping[str, Any]], ...] = (
        ("observation.semantic_data", observation.semantic_data, expected_observation),
        ("observation_version.semantic_data", version.semantic_data, {**expected_observation, **expected_version}),
    )
    for location, semantic_data, expected in sources:
        declared = semantic_data.get("stable_identity", semantic_data)
        if not isinstance(declared, Mapping):
            raise EvaluationInputError(f"{location}.stable_identity must be a mapping when supplied")
        for name, expected_value in expected.items():
            if name in declared and declared[name] != expected_value:
                raise EvaluationInputError(f"contradictory stable identity at {location}.{name}")


def _validate_assessment(
    value: Any,
    expected_type: type,
    name: str,
    observation_version_id: str,
    rule_bundle_version: str,
    evaluation_as_of_at: str,
) -> None:
    _require_exact(value, expected_type, name)
    if value.subject_id != observation_version_id:
        raise EvaluationInputError(f"{name} subject does not match observation_version_id")
    if value.rule_bundle_version != rule_bundle_version:
        raise EvaluationInputError(f"{name} rule bundle version mismatch")
    if parse_rfc3339(value.evaluation_at) > parse_rfc3339(evaluation_as_of_at):
        raise EvaluationInputError(f"{name} was evaluated after evaluation_as_of_at")


def _calendar_status(
    observation: Observation,
    calendar_references: tuple[CalendarReference, ...],
    required_roles: tuple[str, ...],
) -> tuple[set[str], list[EvidenceReference]]:
    reasons: set[str] = set()
    references_by_id: dict[str, CalendarReference] = {}
    for reference in calendar_references:
        _require_exact(reference, CalendarReference, "calendar_reference")
        if reference.calendar_reference_id in references_by_id:
            raise EvaluationInputError("duplicate calendar_reference_id in evaluation input")
        references_by_id[reference.calendar_reference_id] = reference
    assignments = {item.role: item for item in observation.calendar_assignments}
    used: list[EvidenceReference] = []
    for role in required_roles:
        assignment = assignments[role]
        if assignment.status != "RESOLVED" or assignment.calendar_reference_id is None:
            reasons.update(("REQUIRED_CALENDAR_ROLE_MISSING", "CALENDAR_UNRESOLVED"))
            continue
        reference = references_by_id.get(assignment.calendar_reference_id)
        if reference is None:
            reasons.update(("REQUIRED_CALENDAR_ROLE_MISSING", "CALENDAR_UNRESOLVED"))
            continue
        if (
            reference.calendar_version != assignment.calendar_version
            or reference.calendar_hash != assignment.calendar_hash
        ):
            reasons.add("CALENDAR_UNRESOLVED")
            continue
        used.append(EvidenceReference("CALENDAR", reference.calendar_reference_id, reference.calendar_hash))
    return reasons, used


def _ordered(values: set[str], authority: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(item for item in authority if item in values)


def _evaluate_semantic_rule_path(
    *,
    evaluation_role: str,
    source_date_verified: bool,
    availability_verified: bool,
    feature_available_at_max: str | None,
    cutoff_at: str,
    evaluation_as_of_at: str,
    label_available_at: str | None,
    other_reason_codes: Sequence[str] = (),
) -> _SemanticEligibilityDecision:
    """Evaluate deterministic source-neutral rules without operational authority."""

    if evaluation_role not in EVALUATION_ROLES:
        raise EvaluationInputError("unsupported evaluation_role")
    if type(source_date_verified) is not bool or type(availability_verified) is not bool:
        raise EvaluationInputError("semantic evidence flags must be exact booleans")
    supplied_reasons = _snapshot_sequence(other_reason_codes, "other_reason_codes")
    if any(item not in EXCLUSION_REASON_CODES for item in supplied_reasons):
        raise EvaluationInputError("semantic reason codes must use the frozen exclusion catalog")

    cutoff = _timestamp(cutoff_at, "cutoff_at")
    evaluation_as_of = _timestamp(evaluation_as_of_at, "evaluation_as_of_at")
    if parse_rfc3339(evaluation_as_of) < parse_rfc3339(cutoff):
        raise EvaluationInputError("evaluation_as_of_at must not precede cutoff_at")
    label = _timestamp(label_available_at, "label_available_at") if label_available_at is not None else None

    reasons = set(supplied_reasons)
    if not source_date_verified:
        reasons.add("MISSING_SOURCE_MARKET_DATE")

    if feature_available_at_max is None:
        reasons.update(("MISSING_SOURCE_AVAILABLE_AT", "AVAILABILITY_UNVERIFIED"))
    else:
        feature_available_at = _timestamp(feature_available_at_max, "feature_available_at_max")
        if not availability_verified:
            reasons.add("AVAILABILITY_UNVERIFIED")
        elif parse_rfc3339(feature_available_at) > parse_rfc3339(cutoff):
            reasons.add("FEATURE_AVAILABLE_AFTER_CUTOFF")

    if evaluation_role == "BACKTEST":
        if label is None:
            reasons.add("LABEL_HORIZON_NOT_ELAPSED")
        else:
            if parse_rfc3339(label) <= parse_rfc3339(cutoff):
                reasons.add("LABEL_AVAILABLE_AT_OR_BEFORE_CUTOFF")
            if parse_rfc3339(evaluation_as_of) < parse_rfc3339(label):
                reasons.add("LABEL_HORIZON_NOT_ELAPSED")

    ordered_reasons = _ordered(reasons, EXCLUSION_REASON_CODES)
    return _SemanticEligibilityDecision(not ordered_reasons, ordered_reasons)


def _profile_for(
    source_profile_id: str,
    source_profile_version: str,
    observation: Observation,
) -> SourceProfile | None:
    if not isinstance(source_profile_id, str) or not source_profile_id:
        raise EvaluationInputError("source_profile_id must be explicit")
    if not isinstance(source_profile_version, str) or not source_profile_version:
        raise EvaluationInputError("source_profile_version must be explicit")
    profile = SOURCE_PROFILES.get(source_profile_id)
    if profile is None or source_profile_version != profile.profile_version:
        return None
    if observation.source_id not in profile.accepted_source_ids:
        return None
    if profile.accepted_instrument_ids and observation.instrument_id not in profile.accepted_instrument_ids:
        return None
    return profile


def evaluate_readiness(
    *,
    observation: Observation,
    observation_version: ObservationVersion,
    rule_bundle: RuleBundle,
    evaluation_role: str,
    cutoff_at: str,
    evaluation_as_of_at: str,
    source_profile_id: str,
    source_profile_version: str,
    availability_assessment: AvailabilityAssessment | None = None,
    calendar_references: Sequence[CalendarReference] = (),
    lineage_assessment: LineageAssessment | None = None,
    trust_quality_assessment: TrustQualityAssessment | None = None,
    label_available_at: str | None = None,
    contract_version: str = CONTRACT_VERSION,
) -> ReadinessEvaluationResult:
    """Judge supplied evidence without acquiring, mutating, or persisting data."""

    if contract_version != CONTRACT_VERSION:
        raise EvaluationInputError("unsupported evaluation contract version")
    _require_exact(observation, Observation, "observation")
    _require_exact(observation_version, ObservationVersion, "observation_version")
    _validate_rule_bundle(rule_bundle)
    if observation_version.observation_id != observation.observation_id:
        raise EvaluationInputError("ObservationVersion/Observation identity mismatch")
    _validate_reserved_identity(observation, observation_version)
    if evaluation_role not in EVALUATION_ROLES:
        raise EvaluationInputError("unsupported evaluation_role")
    cutoff = _timestamp(cutoff_at, "cutoff_at")
    evaluation_as_of = _timestamp(evaluation_as_of_at, "evaluation_as_of_at")
    if parse_rfc3339(evaluation_as_of) < parse_rfc3339(cutoff):
        raise EvaluationInputError("evaluation_as_of_at must not precede cutoff_at")
    label = _timestamp(label_available_at, "label_available_at") if label_available_at is not None else None
    calendar_values = _snapshot_sequence(calendar_references, "calendar_references")

    version_id = observation_version.observation_version_id
    evidence: list[EvidenceReference] = []
    reasons: set[str] = set()
    semantic_reasons: set[str] = set()
    blockers: set[str] = set()

    profile = _profile_for(source_profile_id, source_profile_version, observation)
    required_calendar_roles = profile.required_calendar_roles if profile is not None else ()
    if profile is None:
        reasons.add("TRUST_STATE_NOT_APPROVED")
        semantic_reasons.add("TRUST_STATE_NOT_APPROVED")
    else:
        for blocker_rule in profile.blocker_rules:
            if evaluation_role in blocker_rule.roles:
                blockers.add(blocker_rule.blocker_id)
                reasons.add(blocker_rule.reason_code)

    # BZ=F never inherits a guessed venue through the broader Yahoo profile.
    if observation.instrument_id == "BZ=F":
        blockers.add("RD3-BZ-001")
        reasons.add("SOURCE_VENUE_MAPPING_CONFLICT")

    calendar_reasons, calendar_evidence = _calendar_status(
        observation, calendar_values, required_calendar_roles
    )
    reasons.update(calendar_reasons)
    semantic_reasons.update(calendar_reasons)
    evidence.extend(calendar_evidence)

    readiness_state = "REAL_SOURCE_DATE_VERIFIED"
    feature_available_at: str | None = None
    availability_verified = False
    if availability_assessment is None:
        pass
    else:
        _validate_assessment(
            availability_assessment, AvailabilityAssessment, "availability_assessment",
            version_id, rule_bundle.rule_bundle_version, evaluation_as_of,
        )
        evidence.append(EvidenceReference(
            "AVAILABILITY", availability_assessment.assessment_id,
            availability_assessment.content_hash,
        ))
        supplied_available_at = availability_assessment.result.get("feature_available_at_max")
        if supplied_available_at is not None:
            feature_available_at = _timestamp(supplied_available_at, "feature_available_at_max")
            availability_verified = availability_assessment.availability_basis != "UNVERIFIED"
            if availability_verified and parse_rfc3339(feature_available_at) <= parse_rfc3339(cutoff):
                readiness_state = "REAL_AVAILABILITY_VERIFIED"

    lineage_complete = False
    historical_vintage_proven = False
    if lineage_assessment is None:
        reasons.add("LINEAGE_INCOMPLETE")
        semantic_reasons.add("LINEAGE_INCOMPLETE")
        if evaluation_role in _HISTORICAL_ROLES:
            reasons.add("HISTORICAL_VINTAGE_NOT_PROVEN")
            semantic_reasons.add("HISTORICAL_VINTAGE_NOT_PROVEN")
    else:
        _validate_assessment(
            lineage_assessment, LineageAssessment, "lineage_assessment",
            version_id, rule_bundle.rule_bundle_version, evaluation_as_of,
        )
        evidence.append(EvidenceReference(
            "LINEAGE", lineage_assessment.assessment_id, lineage_assessment.content_hash
        ))
        lineage_complete = lineage_assessment.result.get("lineage_complete") is True
        historical_vintage_proven = lineage_assessment.result.get("historical_vintage_proven") is True
        if not lineage_complete:
            reasons.add("LINEAGE_INCOMPLETE")
            semantic_reasons.add("LINEAGE_INCOMPLETE")
        if evaluation_role in _HISTORICAL_ROLES and not historical_vintage_proven:
            reasons.add("HISTORICAL_VINTAGE_NOT_PROVEN")
            semantic_reasons.add("HISTORICAL_VINTAGE_NOT_PROVEN")

    trust_approved = False
    if trust_quality_assessment is None:
        reasons.add("TRUST_STATE_NOT_APPROVED")
        semantic_reasons.add("TRUST_STATE_NOT_APPROVED")
    else:
        _validate_assessment(
            trust_quality_assessment, TrustQualityAssessment, "trust_quality_assessment",
            version_id, rule_bundle.rule_bundle_version, evaluation_as_of,
        )
        evidence.append(EvidenceReference(
            "TRUST_QUALITY", trust_quality_assessment.assessment_id,
            trust_quality_assessment.content_hash,
        ))
        trust_approved = (
            trust_quality_assessment.trust_state == "REAL_ORIGIN_VERIFIED"
            and trust_quality_assessment.quality_status in ("PASS", "WARNING")
        )
        if trust_quality_assessment.trust_state == "LEGACY_UNVERIFIED":
            reasons.add("LEGACY_UNVERIFIED_FORBIDDEN")
            semantic_reasons.add("LEGACY_UNVERIFIED_FORBIDDEN")
        if trust_quality_assessment.trust_state == "SHADOW_UNRESOLVED":
            reasons.add("SHADOW_EXPORT_NOT_AUTHORIZED")
        if not trust_approved:
            reasons.add("TRUST_STATE_NOT_APPROVED")
            semantic_reasons.add("TRUST_STATE_NOT_APPROVED")
        if trust_quality_assessment.trust_state == "TRUST_REJECTED":
            readiness_state = "REJECTED"
        elif trust_quality_assessment.quality_status == "CONFLICT":
            readiness_state = "QUARANTINED"

    enabled_by_role = {
        "PIT": PIT_ENABLED_SOURCES,
        "RESEARCH": RESEARCH_ENABLED_SOURCES,
        "BACKTEST": BACKTEST_ENABLED_SOURCES,
    }
    operationally_authorized = source_profile_id in enabled_by_role[evaluation_role]
    if not operationally_authorized:
        reasons.add("SHADOW_EXPORT_NOT_AUTHORIZED")

    source_date_verified = (
        observation.source_market_date is not None
        or (
            observation.source_period_start_date is not None
            and observation.source_period_end_date is not None
        )
    )
    semantic_decision = _evaluate_semantic_rule_path(
        evaluation_role=evaluation_role,
        source_date_verified=source_date_verified,
        availability_verified=availability_verified,
        feature_available_at_max=feature_available_at,
        cutoff_at=cutoff,
        evaluation_as_of_at=evaluation_as_of,
        label_available_at=label,
        other_reason_codes=tuple(semantic_reasons),
    )
    reasons.update(semantic_decision.reason_codes)

    # Operational eligibility remains impossible while all allowlists are empty
    # and the selected source profile retains any OPEN RD-3 blocker.
    eligibility_state = (
        "ELIGIBLE"
        if semantic_decision.passed and operationally_authorized and not blockers
        else "INELIGIBLE"
    )

    ordered_evidence = tuple(sorted(
        evidence, key=lambda item: (item.evidence_kind, item.evidence_id, item.content_hash)
    ))
    return ReadinessEvaluationResult(
        observation_id=observation.observation_id,
        observation_version_id=version_id,
        evaluation_role=evaluation_role,
        cutoff_at=cutoff,
        evaluation_as_of_at=evaluation_as_of,
        label_available_at=label,
        readiness_state=readiness_state,
        eligibility_state=eligibility_state,
        reason_codes=_ordered(reasons, EXCLUSION_REASON_CODES),
        blocker_ids=_ordered(blockers, RD3_OPEN_BLOCKERS),
        evidence_references=ordered_evidence,
        contract_version=contract_version,
        evaluator_version=EVALUATOR_VERSION,
        rule_bundle_version=rule_bundle.rule_bundle_version,
        rule_bundle_hash=rule_bundle.content_hash,
        source_profile_id=source_profile_id,
        source_profile_version=source_profile_version,
        observation_content_hash=observation.content_hash,
        observation_version_content_hash=observation_version.content_hash,
    )
