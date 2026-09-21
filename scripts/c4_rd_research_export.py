"""C4-RD-5 deterministic, local-only research export decisions.

This module consumes already-constructed RD-4.1 objects and an RD-4.2
readiness result.  It does not acquire data, persist output, execute research
or backtests, generate predictions, or make procurement decisions.

``semantic_export_passed`` is deliberately source-neutral.  It describes
whether supplied synthetic evidence passes the core research rules.  Final
``INCLUDE_CANDIDATE`` additionally requires the frozen operational controls,
approved source allowlist, an eligible RD-4.2 result, and no open blocker.
Those operational requirements cannot currently pass.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
import re
from types import MappingProxyType
from typing import Any

from scripts.c4_rd_contract import (
    CANONICAL_JSON_PROFILE,
    CONTRACT_VERSION,
    EXCLUSION_REASON_CODES,
    HASH_PROFILE,
    READINESS_STATES,
    ELIGIBILITY_STATES,
    RESEARCH_ENABLED_SOURCES,
    RD3_OPEN_BLOCKERS,
    SAFETY_FLAGS,
    AvailabilityAssessment,
    CalendarReference,
    ContractError,
    LineageAssessment,
    Observation,
    ObservationVersion,
    TrustQualityAssessment,
    canonical_hash,
    canonical_json_bytes,
    canonical_timestamp,
    parse_rfc3339,
)
from scripts.c4_rd_readiness_evaluator import (
    EVALUATOR_VERSION,
    SOURCE_PROFILES,
    SOURCE_PROFILE_VERSION,
    EvidenceReference,
    ReadinessEvaluationResult,
)


EXPORT_CONTRACT_VERSION = "C4_RD_5_RESEARCH_EXPORT_V1@1.0.0"
EXPORT_DISPOSITIONS = ("INCLUDE_CANDIDATE", "EXCLUDE", "QUARANTINE")

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CONFLICTING_RD4_REASONS = frozenset({
    "DUPLICATE_OBSERVATION_ID_CONFLICT",
    "CONFLICTING_OBSERVATION_VERSION",
})
_LOCAL_REASON_ORDER = (
    "SOURCE_PROFILE_NOT_APPROVED",
    "SOURCE_DATE_NOT_VERIFIED",
    "AVAILABILITY_EVIDENCE_MISSING",
    "AVAILABILITY_NOT_VERIFIED",
    "CONSERVATIVE_AVAILABILITY_PIT_DISABLED",
    "LINEAGE_EVIDENCE_MISSING",
    "LINEAGE_NOT_COMPLETE",
    "TRUST_EVIDENCE_MISSING",
    "TRUST_NOT_APPROVED",
    "REQUIRED_CALENDAR_EVIDENCE_MISSING",
    "RD4_SEMANTIC_REASON_PRESENT",
    "RESEARCH_OPERATION_NOT_AUTHORIZED",
    "RESEARCH_SOURCE_NOT_ENABLED",
    "RD4_RESEARCH_ELIGIBILITY_NOT_ELIGIBLE",
    "OPEN_RD3_BLOCKER",
    "READINESS_QUARANTINED",
    "READINESS_REJECTED",
    "EVIDENCE_REFERENCE_CONFLICT",
    "CALENDAR_EVIDENCE_CONFLICT",
    "REVISION_LINEAGE_CONFLICT",
)
_REASON_ORDER = EXCLUSION_REASON_CODES + _LOCAL_REASON_ORDER


class ResearchExportInputError(ContractError):
    """Raised when an input cannot safely enter the RD-5 decision procedure."""


def _require_exact(value: Any, expected_type: type, name: str) -> None:
    if type(value) is not expected_type:
        raise ResearchExportInputError(
            f"{name} must be an exact validated {expected_type.__name__}"
        )


def _require_non_empty(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ResearchExportInputError(f"{name} must be an explicit non-empty string")


def _require_sha256(value: Any, name: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ResearchExportInputError(f"{name} must be lowercase SHA-256 hex")


def _timestamp(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise ResearchExportInputError(
            f"{name} must be an explicit timezone-aware RFC3339 string"
        )
    try:
        return canonical_timestamp(value)
    except ContractError as exc:
        raise ResearchExportInputError(f"invalid {name}: {exc}") from exc


def _snapshot_sequence(values: Sequence[Any], name: str) -> tuple[Any, ...]:
    if isinstance(values, (str, bytes)):
        raise ResearchExportInputError(f"{name} must be a sequence")
    try:
        return tuple(values)
    except (TypeError, RuntimeError) as exc:
        raise ResearchExportInputError(f"{name} could not be snapshotted") from exc


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        try:
            items = tuple(value.items())
        except Exception as exc:
            raise ResearchExportInputError("mapping input could not be snapshotted") from exc
        frozen: dict[str, Any] = {}
        for key, item in items:
            if not isinstance(key, str):
                raise ResearchExportInputError("mapping keys must be strings")
            frozen[key] = _freeze(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        raise ResearchExportInputError("set-like semantic input is forbidden")
    if value is None or isinstance(value, (str, bool, int, Decimal)):
        return value
    if isinstance(value, float):
        raise ResearchExportInputError("binary float is forbidden")
    raise ResearchExportInputError(f"unsupported semantic type: {type(value).__name__}")


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _ordered(values: set[str]) -> tuple[str, ...]:
    authority = _REASON_ORDER
    unknown = values.difference(authority)
    if unknown:
        raise ResearchExportInputError(f"unknown RD-5 reason code: {sorted(unknown)[0]}")
    return tuple(item for item in authority if item in values)


def _ordered_blockers(values: set[str]) -> tuple[str, ...]:
    unknown = values.difference(RD3_OPEN_BLOCKERS)
    if unknown:
        raise ResearchExportInputError(f"unknown RD-3 blocker: {sorted(unknown)[0]}")
    return tuple(item for item in RD3_OPEN_BLOCKERS if item in values)


def _distribution(values: Sequence[str], authority: tuple[str, ...]) -> Mapping[str, int]:
    counts = Counter(values)
    return MappingProxyType({item: counts[item] for item in authority if counts[item]})


def _profile_is_valid(result: ReadinessEvaluationResult, observation: Observation) -> bool:
    profile = SOURCE_PROFILES.get(result.source_profile_id)
    if profile is None or result.source_profile_version != profile.profile_version:
        return False
    if observation.source_id not in profile.accepted_source_ids:
        return False
    if profile.accepted_instrument_ids and observation.instrument_id not in profile.accepted_instrument_ids:
        return False
    return True


def _profile_operational_reason_codes(result: ReadinessEvaluationResult) -> frozenset[str]:
    blocker_reasons: dict[str, str] = {}
    for profile in SOURCE_PROFILES.values():
        for rule in profile.blocker_rules:
            blocker_reasons[rule.blocker_id] = rule.reason_code
    return frozenset(
        blocker_reasons[item]
        for item in result.blocker_ids
        if item in blocker_reasons
    )


def _validate_evaluation_result(
    result: ReadinessEvaluationResult,
    observation: Observation,
    version: ObservationVersion,
    cutoff: str,
) -> None:
    _require_exact(result, ReadinessEvaluationResult, "evaluation_result")
    if result.observation_id != observation.observation_id:
        raise ResearchExportInputError("evaluation/Observation identity mismatch")
    if result.observation_version_id != version.observation_version_id:
        raise ResearchExportInputError("evaluation/ObservationVersion identity mismatch")
    if version.observation_id != observation.observation_id:
        raise ResearchExportInputError("ObservationVersion/Observation identity mismatch")
    if result.observation_content_hash != observation.content_hash:
        raise ResearchExportInputError("evaluation/Observation content-hash mismatch")
    if result.observation_version_content_hash != version.content_hash:
        raise ResearchExportInputError("evaluation/ObservationVersion content-hash mismatch")
    if result.evaluation_role != "RESEARCH":
        raise ResearchExportInputError("RD-5 requires an RD-4.2 RESEARCH evaluation")
    if result.contract_version != CONTRACT_VERSION:
        raise ResearchExportInputError("unsupported RD-4.1 contract version")
    if result.evaluator_version != EVALUATOR_VERSION:
        raise ResearchExportInputError("unsupported RD-4.2 evaluator version")
    if result.cutoff_at != cutoff:
        raise ResearchExportInputError("evaluation cutoff does not match research cutoff")
    if result.readiness_state not in READINESS_STATES:
        raise ResearchExportInputError("unsupported readiness_state")
    if result.eligibility_state not in ELIGIBILITY_STATES:
        raise ResearchExportInputError("unsupported eligibility_state")
    if len(result.reason_codes) != len(set(result.reason_codes)) or any(
        item not in EXCLUSION_REASON_CODES for item in result.reason_codes
    ):
        raise ResearchExportInputError("invalid or duplicate RD-4.2 reason code")
    if tuple(item for item in EXCLUSION_REASON_CODES if item in result.reason_codes) != result.reason_codes:
        raise ResearchExportInputError("RD-4.2 reason codes are not in authoritative order")
    blockers = set(result.blocker_ids)
    if len(blockers) != len(result.blocker_ids):
        raise ResearchExportInputError("duplicate RD-3 blocker")
    if _ordered_blockers(blockers) != result.blocker_ids:
        raise ResearchExportInputError("RD-3 blockers are not in authoritative order")
    for name in (
        "rule_bundle_version", "source_profile_id", "source_profile_version"
    ):
        _require_non_empty(getattr(result, name), f"evaluation_result.{name}")
    for name in (
        "rule_bundle_hash", "observation_content_hash", "observation_version_content_hash"
    ):
        _require_sha256(getattr(result, name), f"evaluation_result.{name}")
    _timestamp(result.evaluation_as_of_at, "evaluation_result.evaluation_as_of_at")


def _validate_assessment(
    value: Any,
    expected_type: type,
    name: str,
    version_id: str,
    result: ReadinessEvaluationResult,
) -> None:
    _require_exact(value, expected_type, name)
    if value.subject_id != version_id:
        raise ResearchExportInputError(f"{name} subject mismatch")
    if value.rule_bundle_version != result.rule_bundle_version:
        raise ResearchExportInputError(f"{name} rule bundle mismatch")
    if parse_rfc3339(value.evaluation_at) > parse_rfc3339(result.evaluation_as_of_at):
        raise ResearchExportInputError(f"{name} postdates evaluation_as_of_at")


def _evidence_references(
    result: ReadinessEvaluationResult,
) -> dict[tuple[str, str], EvidenceReference]:
    references: dict[tuple[str, str], EvidenceReference] = {}
    for reference in result.evidence_references:
        _require_exact(reference, EvidenceReference, "evaluation evidence reference")
        _require_non_empty(reference.evidence_kind, "evidence_kind")
        _require_non_empty(reference.evidence_id, "evidence_id")
        _require_sha256(reference.content_hash, "evidence content_hash")
        key = (reference.evidence_kind, reference.evidence_id)
        if key in references:
            raise ResearchExportInputError("duplicate evaluation evidence reference")
        references[key] = reference
    return references


def _reference_status(
    references: Mapping[tuple[str, str], EvidenceReference],
    kind: str,
    evidence_id: str,
    content_hash: str,
) -> str:
    reference = references.get((kind, evidence_id))
    if reference is None:
        return "MISSING"
    return "MATCH" if reference.content_hash == content_hash else "CONFLICT"


@dataclass(frozen=True, slots=True)
class _RD42AuthoritySnapshot:
    """Private immutable binding to the exact accepted RD-4.2 result."""

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
    evaluator_version: str
    rule_bundle_version: str
    rule_bundle_hash: str
    source_profile_id: str
    source_profile_version: str
    observation_content_hash: str
    observation_version_content_hash: str
    evidence_references: tuple[tuple[str, str, str], ...]
    evaluation_snapshot_hash: str

    @classmethod
    def from_result(cls, result: ReadinessEvaluationResult) -> "_RD42AuthoritySnapshot":
        _require_exact(result, ReadinessEvaluationResult, "evaluation_result")
        if result.contract_version != CONTRACT_VERSION:
            raise ResearchExportInputError("authority evaluation contract version mismatch")
        evidence = tuple(
            (item.evidence_kind, item.evidence_id, item.content_hash)
            for item in sorted(
                result.evidence_references,
                key=lambda item: (item.evidence_kind, item.evidence_id, item.content_hash),
            )
        )
        return cls(
            observation_id=result.observation_id,
            observation_version_id=result.observation_version_id,
            evaluation_role=result.evaluation_role,
            cutoff_at=result.cutoff_at,
            evaluation_as_of_at=result.evaluation_as_of_at,
            label_available_at=result.label_available_at,
            readiness_state=result.readiness_state,
            eligibility_state=result.eligibility_state,
            reason_codes=result.reason_codes,
            blocker_ids=result.blocker_ids,
            evaluator_version=result.evaluator_version,
            rule_bundle_version=result.rule_bundle_version,
            rule_bundle_hash=result.rule_bundle_hash,
            source_profile_id=result.source_profile_id,
            source_profile_version=result.source_profile_version,
            observation_content_hash=result.observation_content_hash,
            observation_version_content_hash=result.observation_version_content_hash,
            evidence_references=evidence,
            evaluation_snapshot_hash=canonical_hash(
                "MANIFEST_CONTENT",
                {"rd4_2_evaluation_result": result.as_dict()},
            ),
        )

    def __post_init__(self) -> None:
        for name in (
            "observation_id", "observation_version_id", "rule_bundle_hash",
            "observation_content_hash", "observation_version_content_hash",
            "evaluation_snapshot_hash",
        ):
            _require_sha256(getattr(self, name), f"authority_snapshot.{name}")
        if self.evaluation_role != "RESEARCH":
            raise ResearchExportInputError("authority snapshot must represent a RESEARCH evaluation")
        if self.readiness_state not in READINESS_STATES:
            raise ResearchExportInputError("authority snapshot has unsupported readiness_state")
        if self.eligibility_state not in ELIGIBILITY_STATES:
            raise ResearchExportInputError("authority snapshot has unsupported eligibility_state")
        if tuple(item for item in EXCLUSION_REASON_CODES if item in self.reason_codes) != self.reason_codes:
            raise ResearchExportInputError("authority snapshot reason codes are not authoritative")
        if _ordered_blockers(set(self.blocker_ids)) != self.blocker_ids:
            raise ResearchExportInputError("authority snapshot blockers are not authoritative")
        if self.evaluator_version != EVALUATOR_VERSION:
            raise ResearchExportInputError("authority snapshot evaluator identity mismatch")
        if not self.rule_bundle_version:
            raise ResearchExportInputError("authority snapshot rule bundle version is missing")
        if not self.source_profile_id or not self.source_profile_version:
            raise ResearchExportInputError("authority snapshot source profile identity is missing")
        object.__setattr__(self, "cutoff_at", _timestamp(self.cutoff_at, "authority_snapshot.cutoff_at"))
        object.__setattr__(
            self,
            "evaluation_as_of_at",
            _timestamp(self.evaluation_as_of_at, "authority_snapshot.evaluation_as_of_at"),
        )
        if self.label_available_at is not None:
            object.__setattr__(
                self,
                "label_available_at",
                _timestamp(self.label_available_at, "authority_snapshot.label_available_at"),
            )
        for kind, evidence_id, content_hash in self.evidence_references:
            _require_non_empty(kind, "authority_snapshot.evidence_kind")
            _require_non_empty(evidence_id, "authority_snapshot.evidence_id")
            _require_sha256(content_hash, "authority_snapshot.evidence_content_hash")
        canonical_json_bytes(self.content_projection())

    def content_projection(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "observation_version_id": self.observation_version_id,
            "evaluation_role": self.evaluation_role,
            "cutoff_at": self.cutoff_at,
            "evaluation_as_of_at": self.evaluation_as_of_at,
            "label_available_at": self.label_available_at,
            "readiness_state": self.readiness_state,
            "eligibility_state": self.eligibility_state,
            "reason_codes": list(self.reason_codes),
            "blocker_ids": list(self.blocker_ids),
            "evaluator_version": self.evaluator_version,
            "rule_bundle_version": self.rule_bundle_version,
            "rule_bundle_hash": self.rule_bundle_hash,
            "source_profile_id": self.source_profile_id,
            "source_profile_version": self.source_profile_version,
            "observation_content_hash": self.observation_content_hash,
            "observation_version_content_hash": self.observation_version_content_hash,
            "evidence_references": [
                {
                    "evidence_kind": kind,
                    "evidence_id": evidence_id,
                    "content_hash": content_hash,
                }
                for kind, evidence_id, content_hash in self.evidence_references
            ],
            "evaluation_snapshot_hash": self.evaluation_snapshot_hash,
        }

    @property
    def content_hash(self) -> str:
        return canonical_hash(
            "MANIFEST_CONTENT",
            {"rd4_2_authority_snapshot": self.content_projection()},
        )


@dataclass(frozen=True, slots=True)
class ResearchExportDecision:
    export_contract_version: str
    disposition: str
    semantic_export_passed: bool
    operationally_authorized: bool
    source_id: str
    instrument_id: str
    source_version_or_release_key: str
    stable_version_key: str
    raw_payload_hash: str
    transformation_version: str
    source_period: Mapping[str, Any]
    temporal_context: Mapping[str, Any]
    export_reason_codes: tuple[str, ...]
    evidence_references: tuple[Mapping[str, str], ...]
    research_payload: Mapping[str, Any]
    _authority_snapshot: _RD42AuthoritySnapshot = field(repr=False)

    def __post_init__(self) -> None:
        if self.export_contract_version != EXPORT_CONTRACT_VERSION:
            raise ResearchExportInputError("unsupported export contract version")
        if self.disposition not in EXPORT_DISPOSITIONS:
            raise ResearchExportInputError("unsupported export disposition")
        if type(self.semantic_export_passed) is not bool or type(self.operationally_authorized) is not bool:
            raise ResearchExportInputError("decision booleans must be exact booleans")
        _require_sha256(self.raw_payload_hash, "raw_payload_hash")
        for name in (
            "source_id", "instrument_id", "source_version_or_release_key",
            "stable_version_key", "transformation_version",
        ):
            _require_non_empty(getattr(self, name), name)
        object.__setattr__(self, "source_period", _freeze(self.source_period))
        object.__setattr__(self, "temporal_context", _freeze(self.temporal_context))
        object.__setattr__(self, "research_payload", _freeze(self.research_payload))
        object.__setattr__(
            self,
            "evidence_references",
            tuple(_freeze(item) for item in self.evidence_references),
        )
        if _ordered(set(self.export_reason_codes)) != self.export_reason_codes:
            raise ResearchExportInputError("export reasons are not unique authoritative values")
        if not set(self.rd4_reason_codes).issubset(self.export_reason_codes):
            raise ResearchExportInputError("decision must preserve every RD-4.2 reason")
        self._validate_authority_binding()
        authoritative_operation = (
            SAFETY_FLAGS["research_dataset_allowed"] is True
            and self.source_profile_id in RESEARCH_ENABLED_SOURCES
        )
        if self.operationally_authorized is not authoritative_operation:
            raise ResearchExportInputError("operational authorization does not match frozen controls")
        conflict_reasons = {
            "READINESS_QUARANTINED",
            "EVIDENCE_REFERENCE_CONFLICT",
            "CALENDAR_EVIDENCE_CONFLICT",
            "REVISION_LINEAGE_CONFLICT",
        }
        has_conflict = bool(conflict_reasons.intersection(self.export_reason_codes))
        if (self.disposition == "QUARANTINE") is not has_conflict:
            raise ResearchExportInputError("quarantine disposition and conflict diagnostics disagree")
        if self.disposition == "INCLUDE_CANDIDATE" and (
            not self.semantic_export_passed
            or not self.operationally_authorized
            or self.eligibility_state != "ELIGIBLE"
            or self.blocker_ids
        ):
            raise ResearchExportInputError("INCLUDE_CANDIDATE requirements are not satisfied")
        canonical_json_bytes(self.content_projection())

    def _validate_authority_binding(self) -> None:
        _require_exact(self._authority_snapshot, _RD42AuthoritySnapshot, "authority_snapshot")
        snapshot = self._authority_snapshot
        bindings = (
            (
                "research_cutoff_at",
                self.temporal_context.get("research_cutoff_at"),
                snapshot.cutoff_at,
            ),
            (
                "evaluation_as_of_at",
                self.temporal_context.get("evaluation_as_of_at"),
                snapshot.evaluation_as_of_at,
            ),
        )
        for name, actual, authoritative in bindings:
            if actual != authoritative:
                raise ResearchExportInputError(f"decision {name} does not match bound RD-4.2 authority")
        evidence = tuple(
            (
                item.get("evidence_kind"),
                item.get("evidence_id"),
                item.get("content_hash"),
            )
            for item in self.evidence_references
        )
        if evidence != snapshot.evidence_references:
            raise ResearchExportInputError("decision evidence references do not match bound RD-4.2 authority")

    @property
    def observation_id(self) -> str:
        return self._authority_snapshot.observation_id

    @property
    def observation_version_id(self) -> str:
        return self._authority_snapshot.observation_version_id

    @property
    def readiness_state(self) -> str:
        return self._authority_snapshot.readiness_state

    @property
    def eligibility_state(self) -> str:
        return self._authority_snapshot.eligibility_state

    @property
    def rd4_reason_codes(self) -> tuple[str, ...]:
        return self._authority_snapshot.reason_codes

    @property
    def blocker_ids(self) -> tuple[str, ...]:
        return self._authority_snapshot.blocker_ids

    @property
    def evaluator_version(self) -> str:
        return self._authority_snapshot.evaluator_version

    @property
    def rule_bundle_version(self) -> str:
        return self._authority_snapshot.rule_bundle_version

    @property
    def rule_bundle_hash(self) -> str:
        return self._authority_snapshot.rule_bundle_hash

    @property
    def source_profile_id(self) -> str:
        return self._authority_snapshot.source_profile_id

    @property
    def source_profile_version(self) -> str:
        return self._authority_snapshot.source_profile_version

    @property
    def observation_content_hash(self) -> str:
        return self._authority_snapshot.observation_content_hash

    @property
    def observation_version_content_hash(self) -> str:
        return self._authority_snapshot.observation_version_content_hash

    @property
    def authority_snapshot_hash(self) -> str:
        return self._authority_snapshot.content_hash

    @property
    def ordering_key(self) -> tuple[str, str, str, str]:
        return (
            self.source_id,
            self.instrument_id,
            self.observation_id,
            self.observation_version_id,
        )

    def content_projection(self) -> dict[str, Any]:
        return {
            "export_contract_version": self.export_contract_version,
            "disposition": self.disposition,
            "semantic_export_passed": self.semantic_export_passed,
            "operationally_authorized": self.operationally_authorized,
            "identity": {
                "observation_id": self.observation_id,
                "observation_version_id": self.observation_version_id,
                "source_id": self.source_id,
                "instrument_id": self.instrument_id,
                "source_version_or_release_key": self.source_version_or_release_key,
                "stable_version_key": self.stable_version_key,
                "raw_payload_hash": self.raw_payload_hash,
                "transformation_version": self.transformation_version,
            },
            "source_period": _plain(self.source_period),
            "temporal_context": _plain(self.temporal_context),
            "evaluation": {
                "readiness_state": self.readiness_state,
                "eligibility_state": self.eligibility_state,
                "rd4_reason_codes": list(self.rd4_reason_codes),
                "export_reason_codes": list(self.export_reason_codes),
                "blocker_ids": list(self.blocker_ids),
                "evaluator_version": self.evaluator_version,
                "rule_bundle_version": self.rule_bundle_version,
                "rule_bundle_hash": self.rule_bundle_hash,
                "source_profile_id": self.source_profile_id,
                "source_profile_version": self.source_profile_version,
                "authority_snapshot_hash": self.authority_snapshot_hash,
            },
            "lineage": {
                "evidence_references": [_plain(item) for item in self.evidence_references],
                "observation_content_hash": self.observation_content_hash,
                "observation_version_content_hash": self.observation_version_content_hash,
                "rd4_2_authority_snapshot": self._authority_snapshot.content_projection(),
            },
            "research_payload": _plain(self.research_payload),
        }

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.content_projection())

    @property
    def content_hash(self) -> str:
        return canonical_hash("MANIFEST_CONTENT", self.content_projection())

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision_content_hash": self.content_hash,
            "content": self.content_projection(),
        }


def _bind_authoritative_evaluations(
    decisions: tuple[ResearchExportDecision, ...],
    authoritative_evaluations: Sequence[ReadinessEvaluationResult],
) -> tuple[ReadinessEvaluationResult, ...]:
    """Match decisions to independently supplied RD-4.2 authority and compare all fields."""

    evaluations = _snapshot_sequence(
        authoritative_evaluations,
        "authoritative_evaluations",
    )
    if any(type(item) is not ReadinessEvaluationResult for item in evaluations):
        raise ResearchExportInputError(
            "authoritative_evaluations must contain exact ReadinessEvaluationResult values"
        )

    by_identity: dict[tuple[str, str], ReadinessEvaluationResult] = {}
    for evaluation in evaluations:
        identity = (evaluation.observation_id, evaluation.observation_version_id)
        if identity in by_identity:
            raise ResearchExportInputError("duplicate authoritative evaluation identity")
        by_identity[identity] = evaluation

    decision_identities = tuple(
        (item.observation_id, item.observation_version_id)
        for item in decisions
    )
    if len(decision_identities) != len(set(decision_identities)):
        raise ResearchExportInputError("duplicate record decision")

    expected_identities = set(decision_identities)
    supplied_identities = set(by_identity)
    if expected_identities - supplied_identities:
        raise ResearchExportInputError("missing authoritative evaluation")
    if supplied_identities - expected_identities:
        raise ResearchExportInputError("extra authoritative evaluation")

    ordered = tuple(by_identity[identity] for identity in decision_identities)
    for decision, evaluation in zip(decisions, ordered, strict=True):
        authoritative_snapshot = _RD42AuthoritySnapshot.from_result(evaluation)
        if decision._authority_snapshot != authoritative_snapshot:
            raise ResearchExportInputError(
                "decision does not match independently supplied RD-4.2 authority"
            )
    return ordered


@dataclass(frozen=True, slots=True)
class ResearchExportManifest:
    export_contract_version: str
    research_cutoff_at: str
    evaluation_context: Mapping[str, Any]
    rule_bundle_version: str
    rule_bundle_hash: str
    evaluator_version: str
    decisions: tuple[ResearchExportDecision, ...]
    authoritative_evaluations: tuple[ReadinessEvaluationResult, ...] = field(repr=False)
    included_candidate_count: int
    excluded_count: int
    quarantined_count: int
    reason_code_distribution: Mapping[str, int]
    blocker_distribution: Mapping[str, int]
    authorizations: Mapping[str, bool]
    research_enabled_sources: tuple[str, ...]
    rd3_open_blockers: tuple[str, ...]
    canonical_json_profile: str = CANONICAL_JSON_PROFILE
    hash_profile: str = HASH_PROFILE

    def __post_init__(self) -> None:
        if self.export_contract_version != EXPORT_CONTRACT_VERSION:
            raise ResearchExportInputError("unsupported export contract version")
        object.__setattr__(self, "research_cutoff_at", _timestamp(self.research_cutoff_at, "research_cutoff_at"))
        object.__setattr__(self, "evaluation_context", _freeze(self.evaluation_context))
        if not self.evaluation_context:
            raise ResearchExportInputError("evaluation_context must be explicit and non-empty")
        decisions = tuple(self.decisions)
        if not decisions or any(type(item) is not ResearchExportDecision for item in decisions):
            raise ResearchExportInputError("manifest requires validated RD-5 decisions")
        for item in decisions:
            item._validate_authority_binding()
        if decisions != tuple(sorted(decisions, key=lambda item: item.ordering_key)):
            raise ResearchExportInputError("manifest decisions must use deterministic ordering")
        evaluations = _bind_authoritative_evaluations(
            decisions,
            self.authoritative_evaluations,
        )
        object.__setattr__(self, "decisions", decisions)
        object.__setattr__(self, "authoritative_evaluations", evaluations)
        object.__setattr__(self, "reason_code_distribution", _freeze(self.reason_code_distribution))
        object.__setattr__(self, "blocker_distribution", _freeze(self.blocker_distribution))
        object.__setattr__(self, "authorizations", _freeze(self.authorizations))
        _require_non_empty(self.rule_bundle_version, "rule_bundle_version")
        _require_sha256(self.rule_bundle_hash, "rule_bundle_hash")
        _require_non_empty(self.evaluator_version, "evaluator_version")
        if self.canonical_json_profile != CANONICAL_JSON_PROFILE or self.hash_profile != HASH_PROFILE:
            raise ResearchExportInputError("frozen canonicalization/hash profile mismatch")
        if self.research_enabled_sources != RESEARCH_ENABLED_SOURCES:
            raise ResearchExportInputError("research enabled-source authority mismatch")
        if self.rd3_open_blockers != RD3_OPEN_BLOCKERS:
            raise ResearchExportInputError("RD-3 blocker authority mismatch")
        if dict(self.authorizations) != dict(SAFETY_FLAGS):
            raise ResearchExportInputError("authorization authority mismatch")
        if any(item.temporal_context["research_cutoff_at"] != self.research_cutoff_at for item in decisions):
            raise ResearchExportInputError("decision research cutoff mismatch")
        if any(
            item.rule_bundle_version != self.rule_bundle_version
            or item.rule_bundle_hash != self.rule_bundle_hash
            or item.evaluator_version != self.evaluator_version
            for item in decisions
        ):
            raise ResearchExportInputError("manifest decision evaluation context mismatch")
        counts = Counter(item.disposition for item in decisions)
        if (
            self.included_candidate_count != counts["INCLUDE_CANDIDATE"]
            or self.excluded_count != counts["EXCLUDE"]
            or self.quarantined_count != counts["QUARANTINE"]
            or len(decisions)
            != self.included_candidate_count + self.excluded_count + self.quarantined_count
        ):
            raise ResearchExportInputError("manifest disposition counts do not reconcile")
        expected_reasons = _distribution(
            tuple(reason for item in decisions for reason in item.export_reason_codes),
            _REASON_ORDER,
        )
        expected_blockers = _distribution(
            tuple(blocker for item in decisions for blocker in item.blocker_ids),
            RD3_OPEN_BLOCKERS,
        )
        if dict(self.reason_code_distribution) != dict(expected_reasons):
            raise ResearchExportInputError("manifest reason distribution does not reconcile")
        if dict(self.blocker_distribution) != dict(expected_blockers):
            raise ResearchExportInputError("manifest blocker distribution does not reconcile")
        canonical_json_bytes(self.content_projection())

    def content_projection(self) -> dict[str, Any]:
        return {
            "export_contract_version": self.export_contract_version,
            "research_cutoff_at": self.research_cutoff_at,
            "evaluation_context": _plain(self.evaluation_context),
            "rules": {
                "rule_bundle_version": self.rule_bundle_version,
                "rule_bundle_hash": self.rule_bundle_hash,
                "evaluator_version": self.evaluator_version,
                "canonical_json_profile": self.canonical_json_profile,
                "hash_profile": self.hash_profile,
            },
            "decisions": [
                {"content_hash": item.content_hash, "content": item.content_projection()}
                for item in self.decisions
            ],
            "authoritative_evaluations": [
                {
                    "observation_id": item.observation_id,
                    "observation_version_id": item.observation_version_id,
                    "evaluation_role": item.evaluation_role,
                    "evaluation_snapshot_hash": canonical_hash(
                        "MANIFEST_CONTENT",
                        {"rd4_2_evaluation_result": item.as_dict()},
                    ),
                }
                for item in self.authoritative_evaluations
            ],
            "counts": {
                "total": len(self.decisions),
                "include_candidate": self.included_candidate_count,
                "exclude": self.excluded_count,
                "quarantine": self.quarantined_count,
            },
            "reason_code_distribution": _plain(self.reason_code_distribution),
            "blocker_distribution": _plain(self.blocker_distribution),
            "stable_input_hashes": [item.content_hash for item in self.decisions],
            "authoritative_input_hashes": [
                canonical_hash(
                    "MANIFEST_CONTENT",
                    {"rd4_2_evaluation_result": item.as_dict()},
                )
                for item in self.authoritative_evaluations
            ],
            "authorizations": _plain(self.authorizations),
            "research_enabled_sources": list(self.research_enabled_sources),
            "rd3_open_blockers": list(self.rd3_open_blockers),
        }

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.content_projection())

    @property
    def content_hash(self) -> str:
        return canonical_hash("MANIFEST_CONTENT", self.content_projection())

    def as_dict(self) -> dict[str, Any]:
        return {
            "manifest_content_hash": self.content_hash,
            "content": self.content_projection(),
        }


def build_research_export_decision(
    *,
    observation: Observation,
    observation_version: ObservationVersion,
    evaluation_result: ReadinessEvaluationResult,
    research_cutoff_at: str,
    research_payload: Mapping[str, Any],
    availability_assessment: AvailabilityAssessment | None = None,
    lineage_assessment: LineageAssessment | None = None,
    trust_quality_assessment: TrustQualityAssessment | None = None,
    calendar_references: Sequence[CalendarReference] = (),
    export_contract_version: str = EXPORT_CONTRACT_VERSION,
) -> ResearchExportDecision:
    """Build one immutable decision without performing I/O or execution."""

    if export_contract_version != EXPORT_CONTRACT_VERSION:
        raise ResearchExportInputError("unsupported export contract version")
    _require_exact(observation, Observation, "observation")
    _require_exact(observation_version, ObservationVersion, "observation_version")
    cutoff = _timestamp(research_cutoff_at, "research_cutoff_at")
    _validate_evaluation_result(evaluation_result, observation, observation_version, cutoff)
    if not isinstance(research_payload, Mapping):
        raise ResearchExportInputError("research_payload must be a mapping")
    frozen_payload = _freeze(research_payload)
    canonical_json_bytes(frozen_payload)
    calendar_values = _snapshot_sequence(calendar_references, "calendar_references")
    if any(type(item) is not CalendarReference for item in calendar_values):
        raise ResearchExportInputError("calendar_references must contain exact CalendarReference values")

    references = _evidence_references(evaluation_result)
    semantic_reasons: set[str] = set()
    export_reasons: set[str] = set(evaluation_result.reason_codes)
    conflicts: set[str] = set()

    profile_valid = _profile_is_valid(evaluation_result, observation)
    profile = SOURCE_PROFILES.get(evaluation_result.source_profile_id) if profile_valid else None
    if not profile_valid:
        semantic_reasons.add("SOURCE_PROFILE_NOT_APPROVED")

    source_date_verified = observation.source_market_date is not None or (
        observation.source_period_start_date is not None
        and observation.source_period_end_date is not None
    )
    if not source_date_verified:
        semantic_reasons.add("SOURCE_DATE_NOT_VERIFIED")

    feature_available_at: str | None = None
    if availability_assessment is None:
        semantic_reasons.add("AVAILABILITY_EVIDENCE_MISSING")
    else:
        _validate_assessment(
            availability_assessment,
            AvailabilityAssessment,
            "availability_assessment",
            observation_version.observation_version_id,
            evaluation_result,
        )
        feature_value = availability_assessment.result.get("feature_available_at_max")
        if feature_value is None:
            semantic_reasons.add("AVAILABILITY_EVIDENCE_MISSING")
        else:
            feature_available_at = _timestamp(feature_value, "feature_available_at_max")
            if parse_rfc3339(feature_available_at) > parse_rfc3339(cutoff):
                semantic_reasons.add("FEATURE_AVAILABLE_AFTER_CUTOFF")
        if availability_assessment.availability_basis == "APPROVED_CONSERVATIVE_ESTIMATE":
            semantic_reasons.add("CONSERVATIVE_AVAILABILITY_PIT_DISABLED")
        elif availability_assessment.availability_basis == "UNVERIFIED":
            semantic_reasons.add("AVAILABILITY_NOT_VERIFIED")
        status = _reference_status(
            references,
            "AVAILABILITY",
            availability_assessment.assessment_id,
            availability_assessment.content_hash,
        )
        if status == "MISSING":
            semantic_reasons.add("AVAILABILITY_EVIDENCE_MISSING")
        elif status == "CONFLICT":
            conflicts.add("EVIDENCE_REFERENCE_CONFLICT")

    lineage_complete = False
    historical_vintage_proven = False
    if lineage_assessment is None:
        semantic_reasons.add("LINEAGE_EVIDENCE_MISSING")
    else:
        _validate_assessment(
            lineage_assessment,
            LineageAssessment,
            "lineage_assessment",
            observation_version.observation_version_id,
            evaluation_result,
        )
        lineage_complete = lineage_assessment.result.get("lineage_complete") is True
        historical_vintage_proven = lineage_assessment.result.get("historical_vintage_proven") is True
        if not lineage_complete:
            semantic_reasons.add("LINEAGE_NOT_COMPLETE")
        if not historical_vintage_proven:
            semantic_reasons.add("HISTORICAL_VINTAGE_NOT_PROVEN")
        if lineage_assessment.result.get("lineage_conflict") is True:
            conflicts.add("REVISION_LINEAGE_CONFLICT")
        status = _reference_status(
            references,
            "LINEAGE",
            lineage_assessment.assessment_id,
            lineage_assessment.content_hash,
        )
        if status == "MISSING":
            semantic_reasons.add("LINEAGE_EVIDENCE_MISSING")
        elif status == "CONFLICT":
            conflicts.add("EVIDENCE_REFERENCE_CONFLICT")

    if trust_quality_assessment is None:
        semantic_reasons.add("TRUST_EVIDENCE_MISSING")
    else:
        _validate_assessment(
            trust_quality_assessment,
            TrustQualityAssessment,
            "trust_quality_assessment",
            observation_version.observation_version_id,
            evaluation_result,
        )
        trust_approved = (
            trust_quality_assessment.trust_state == "REAL_ORIGIN_VERIFIED"
            and trust_quality_assessment.quality_status in ("PASS", "WARNING")
        )
        if not trust_approved:
            semantic_reasons.add("TRUST_NOT_APPROVED")
        if trust_quality_assessment.quality_status == "CONFLICT":
            conflicts.add("EVIDENCE_REFERENCE_CONFLICT")
        status = _reference_status(
            references,
            "TRUST_QUALITY",
            trust_quality_assessment.assessment_id,
            trust_quality_assessment.content_hash,
        )
        if status == "MISSING":
            semantic_reasons.add("TRUST_EVIDENCE_MISSING")
        elif status == "CONFLICT":
            conflicts.add("EVIDENCE_REFERENCE_CONFLICT")

    calendars_by_id: dict[str, CalendarReference] = {}
    for reference in calendar_values:
        if reference.calendar_reference_id in calendars_by_id:
            raise ResearchExportInputError("duplicate calendar_reference_id")
        calendars_by_id[reference.calendar_reference_id] = reference
    assignments = {item.role: item for item in observation.calendar_assignments}
    required_roles = profile.required_calendar_roles if profile is not None else ()
    for role in required_roles:
        assignment = assignments[role]
        if assignment.status == "CONFLICTING":
            conflicts.add("CALENDAR_EVIDENCE_CONFLICT")
            continue
        if assignment.status != "RESOLVED" or assignment.calendar_reference_id is None:
            semantic_reasons.add("REQUIRED_CALENDAR_EVIDENCE_MISSING")
            continue
        reference = calendars_by_id.get(assignment.calendar_reference_id)
        if reference is None:
            semantic_reasons.add("REQUIRED_CALENDAR_EVIDENCE_MISSING")
            continue
        if (
            reference.calendar_version != assignment.calendar_version
            or reference.calendar_hash != assignment.calendar_hash
        ):
            conflicts.add("CALENDAR_EVIDENCE_CONFLICT")
            continue
        status = _reference_status(
            references,
            "CALENDAR",
            reference.calendar_reference_id,
            reference.calendar_hash,
        )
        if status == "MISSING":
            semantic_reasons.add("REQUIRED_CALENDAR_EVIDENCE_MISSING")
        elif status == "CONFLICT":
            conflicts.add("CALENDAR_EVIDENCE_CONFLICT")

    operational_rd4_reasons = _profile_operational_reason_codes(evaluation_result) | {
        "SHADOW_EXPORT_NOT_AUTHORIZED"
    }
    if any(item not in operational_rd4_reasons for item in evaluation_result.reason_codes):
        semantic_reasons.add("RD4_SEMANTIC_REASON_PRESENT")

    if evaluation_result.readiness_state == "QUARANTINED":
        conflicts.add("READINESS_QUARANTINED")
    elif evaluation_result.readiness_state == "REJECTED":
        semantic_reasons.add("READINESS_REJECTED")
    if _CONFLICTING_RD4_REASONS.intersection(evaluation_result.reason_codes):
        conflicts.add("REVISION_LINEAGE_CONFLICT")
    if observation_version.parent_version_id == observation_version.observation_version_id:
        conflicts.add("REVISION_LINEAGE_CONFLICT")

    semantic_export_passed = not semantic_reasons and not conflicts
    source_enabled = evaluation_result.source_profile_id in RESEARCH_ENABLED_SOURCES
    operationally_authorized = (
        SAFETY_FLAGS["research_dataset_allowed"] is True and source_enabled
    )

    if not operationally_authorized:
        export_reasons.add("RESEARCH_OPERATION_NOT_AUTHORIZED")
    if not source_enabled:
        export_reasons.add("RESEARCH_SOURCE_NOT_ENABLED")
    if evaluation_result.eligibility_state != "ELIGIBLE":
        export_reasons.add("RD4_RESEARCH_ELIGIBILITY_NOT_ELIGIBLE")
    if evaluation_result.blocker_ids:
        export_reasons.add("OPEN_RD3_BLOCKER")
    export_reasons.update(semantic_reasons)
    export_reasons.update(conflicts)

    if conflicts:
        disposition = "QUARANTINE"
    elif (
        semantic_export_passed
        and operationally_authorized
        and profile_valid
        and evaluation_result.eligibility_state == "ELIGIBLE"
        and not evaluation_result.blocker_ids
    ):
        disposition = "INCLUDE_CANDIDATE"
    else:
        disposition = "EXCLUDE"

    evidence_payload = tuple(
        {
            "evidence_kind": item.evidence_kind,
            "evidence_id": item.evidence_id,
            "content_hash": item.content_hash,
        }
        for item in sorted(
            evaluation_result.evidence_references,
            key=lambda item: (item.evidence_kind, item.evidence_id, item.content_hash),
        )
    )
    authority_snapshot = _RD42AuthoritySnapshot.from_result(evaluation_result)
    return ResearchExportDecision(
        export_contract_version=export_contract_version,
        disposition=disposition,
        semantic_export_passed=semantic_export_passed,
        operationally_authorized=operationally_authorized,
        source_id=observation.source_id,
        instrument_id=observation.instrument_id,
        source_version_or_release_key=observation_version.source_version_or_release_key,
        stable_version_key=observation_version.stable_version_key,
        raw_payload_hash=observation_version.raw_payload_hash,
        transformation_version=observation_version.transformation_version,
        source_period={
            "source_period_type": observation.source_period_type,
            "source_market_date": observation.source_market_date,
            "represented_period_start": observation.source_period_start_date,
            "represented_period_end": observation.source_period_end_date,
            "source_publication_at": observation.source_publication_at,
        },
        temporal_context={
            "source_available_at": observation.source_available_at,
            "feature_available_at_max": feature_available_at,
            "research_cutoff_at": cutoff,
            "evaluation_as_of_at": evaluation_result.evaluation_as_of_at,
        },
        export_reason_codes=_ordered(export_reasons),
        evidence_references=evidence_payload,
        research_payload=frozen_payload,
        _authority_snapshot=authority_snapshot,
    )


def build_research_export_manifest(
    decisions: Sequence[ResearchExportDecision],
    authoritative_evaluations: Sequence[ReadinessEvaluationResult],
    *,
    research_cutoff_at: str,
    evaluation_context: Mapping[str, Any],
    export_contract_version: str = EXPORT_CONTRACT_VERSION,
) -> ResearchExportManifest:
    """Build an order-independent, deterministic in-memory manifest."""

    if export_contract_version != EXPORT_CONTRACT_VERSION:
        raise ResearchExportInputError("unsupported export contract version")
    cutoff = _timestamp(research_cutoff_at, "research_cutoff_at")
    if not isinstance(evaluation_context, Mapping):
        raise ResearchExportInputError("evaluation_context must be a mapping")
    frozen_context = _freeze(evaluation_context)
    if not frozen_context:
        raise ResearchExportInputError("evaluation_context must be explicit and non-empty")
    values = _snapshot_sequence(decisions, "decisions")
    if not values or any(type(item) is not ResearchExportDecision for item in values):
        raise ResearchExportInputError("decisions must contain validated RD-5 decisions")
    ordered = tuple(sorted(values, key=lambda item: item.ordering_key))
    ordered_evaluations = _bind_authoritative_evaluations(
        ordered,
        authoritative_evaluations,
    )
    if any(item.temporal_context["research_cutoff_at"] != cutoff for item in ordered):
        raise ResearchExportInputError("decision research cutoff mismatch")
    version_ids = tuple(item.observation_version_id for item in ordered)
    if len(version_ids) != len(set(version_ids)):
        raise ResearchExportInputError("duplicate record decision")
    rule_versions = {item.rule_bundle_version for item in ordered_evaluations}
    rule_hashes = {item.rule_bundle_hash for item in ordered_evaluations}
    evaluator_versions = {item.evaluator_version for item in ordered_evaluations}
    if len(rule_versions) != 1 or len(rule_hashes) != 1 or len(evaluator_versions) != 1:
        raise ResearchExportInputError("manifest decisions require one evaluation context")
    dispositions = Counter(item.disposition for item in ordered)
    reasons = tuple(reason for item in ordered for reason in item.export_reason_codes)
    blockers = tuple(blocker for item in ordered for blocker in item.blocker_ids)
    return ResearchExportManifest(
        export_contract_version=export_contract_version,
        research_cutoff_at=cutoff,
        evaluation_context=frozen_context,
        rule_bundle_version=next(iter(rule_versions)),
        rule_bundle_hash=next(iter(rule_hashes)),
        evaluator_version=next(iter(evaluator_versions)),
        decisions=ordered,
        authoritative_evaluations=ordered_evaluations,
        included_candidate_count=dispositions["INCLUDE_CANDIDATE"],
        excluded_count=dispositions["EXCLUDE"],
        quarantined_count=dispositions["QUARANTINE"],
        reason_code_distribution=_distribution(reasons, _REASON_ORDER),
        blocker_distribution=_distribution(blockers, RD3_OPEN_BLOCKERS),
        authorizations=SAFETY_FLAGS,
        research_enabled_sources=RESEARCH_ENABLED_SOURCES,
        rd3_open_blockers=RD3_OPEN_BLOCKERS,
    )
