"""Pure C4-RD-7.5B preregistration governance contracts.

This module binds immutable synthetic approval and change records to exact
``PreregistrationProtocol`` instances.  It performs validation only: it does
not persist evidence, contact GitHub, execute a backtest, or grant operational
authority.
"""

from __future__ import annotations

from dataclasses import InitVar, dataclass
import re
from typing import Any

from scripts.c4_rd_contract import ContractError, canonical_timestamp, parse_rfc3339
from scripts.c4_rd_preregistration import PreregistrationProtocol


GOVERNANCE_CONTRACT_VERSION = "1.0.0"

APPROVAL_STATES = ("PENDING", "APPROVED", "REJECTED", "SUPERSEDED")
OPERATIONAL_STATUSES = ("SYNTHETIC_NON_OPERATIONAL", "OPERATIONAL_VERIFIED")
APPROVAL_EVIDENCE_TYPES = (
    "GITHUB_PR_MERGE",
    "GITHUB_PR_CLOSED_UNMERGED",
    "SYNTHETIC_FIXTURE",
)
CHANGE_CLASSIFICATIONS = (
    "RESEARCH_QUESTION",
    "DATASET",
    "FEATURE",
    "LABEL",
    "BASELINE",
    "METRIC",
    "EVALUATION",
    "EXCLUSION",
    "REPORTING",
    "ADMINISTRATIVE",
)

PENDING, APPROVED, REJECTED, SUPERSEDED = APPROVAL_STATES
SYNTHETIC_NON_OPERATIONAL, OPERATIONAL_VERIFIED = OPERATIONAL_STATUSES
GITHUB_PR_MERGE, GITHUB_PR_CLOSED_UNMERGED, SYNTHETIC_FIXTURE = APPROVAL_EVIDENCE_TYPES
(
    RESEARCH_QUESTION,
    DATASET,
    FEATURE,
    LABEL,
    BASELINE,
    METRIC,
    EVALUATION,
    EXCLUSION,
    REPORTING,
    ADMINISTRATIVE,
) = CHANGE_CLASSIFICATIONS

_GOVERNANCE_CONSTRUCTION_TOKEN = object()
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_GITHUB_APPROVAL_PR_URL = re.compile(
    r"^https://github\.com/Bigstupidegg/procurement-materials-platform/pull/[1-9][0-9]*$"
)
_SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_SECTION_CLASSIFICATIONS = {
    "research_question": RESEARCH_QUESTION,
    "research_task": RESEARCH_QUESTION,
    "research_subjects": RESEARCH_QUESTION,
    "dataset_bindings": DATASET,
    "authorization_snapshot": DATASET,
    "feature_binding": FEATURE,
    "horizon_plan": LABEL,
    "label_protocol": LABEL,
    "baseline_plan": BASELINE,
    "metric_plan": METRIC,
    "evaluation_protocol": EVALUATION,
    "exclusion_policy": EXCLUSION,
    "reporting_policy": REPORTING,
}
_PROTOCOL_INVARIANT_KEYS = {"research_protocol_id", "contract_version"}
_PROTOCOL_VERSION_KEY = "preregistration_version"
_LEGAL_APPROVAL_SEQUENCES = {
    (PENDING,),
    (PENDING, APPROVED),
    (PENDING, REJECTED),
    (PENDING, APPROVED, SUPERSEDED),
    (APPROVED,),
    (APPROVED, SUPERSEDED),
    (REJECTED,),
}


class PreregistrationGovernanceError(ContractError):
    """Base error for the preregistration governance contract."""


class InvalidApprovalRecord(PreregistrationGovernanceError):
    """Raised when an approval record violates the frozen state matrix."""


class InvalidApprovalHistory(PreregistrationGovernanceError):
    """Raised when ordered approval records do not form a legal lifecycle."""


class InvalidChangeRecord(PreregistrationGovernanceError):
    """Raised when a change record is untruthful or malformed."""


class InvalidChangeSet(PreregistrationGovernanceError):
    """Raised when a transition's change records are incomplete or inconsistent."""


class FormalBacktestNotAuthorized(PreregistrationGovernanceError):
    """Raised when a governance record does not authorize a formal backtest."""


def _require_nonblank(value: Any, name: str, error_type: type[PreregistrationGovernanceError]) -> str:
    if type(value) is not str or not value or value.isspace():
        raise error_type(f"{name} must be a non-whitespace string")
    return value


def _strict_timestamp(
    value: Any,
    name: str,
    error_type: type[PreregistrationGovernanceError],
):
    if type(value) is not str or not value.endswith("Z"):
        raise error_type(f"{name} must be canonical RFC3339 UTC Z text")
    try:
        parsed = parse_rfc3339(value)
        if canonical_timestamp(value) != value:
            raise error_type(f"{name} must be canonical RFC3339 UTC Z text")
    except ContractError as exc:
        if isinstance(exc, error_type):
            raise
        raise error_type(f"{name} must be canonical RFC3339 UTC Z text") from exc
    return parsed


def _parse_semver(
    value: Any,
    name: str,
    error_type: type[PreregistrationGovernanceError],
) -> tuple[int, int, int, tuple[str, ...] | None]:
    if type(value) is not str:
        raise error_type(f"{name} must be strict SemVer 2.0.0")
    matched = _SEMVER.fullmatch(value)
    if matched is None:
        raise error_type(f"{name} must be strict SemVer 2.0.0")
    prerelease_text = matched.group(4)
    prerelease = None if prerelease_text is None else tuple(prerelease_text.split("."))
    if prerelease is not None:
        for identifier in prerelease:
            if identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0"):
                raise error_type(f"{name} has a numeric prerelease identifier with a leading zero")
    return int(matched.group(1)), int(matched.group(2)), int(matched.group(3)), prerelease


def _compare_semver(
    left: Any,
    right: Any,
    error_type: type[PreregistrationGovernanceError],
) -> int:
    """Return -1, 0, or 1 using strict SemVer 2.0.0 precedence."""

    left_value = _parse_semver(left, "previous_preregistration_version", error_type)
    right_value = _parse_semver(right, "new_preregistration_version", error_type)
    if left_value[:3] != right_value[:3]:
        return -1 if left_value[:3] < right_value[:3] else 1
    left_pre, right_pre = left_value[3], right_value[3]
    if left_pre is None or right_pre is None:
        if left_pre is right_pre:
            return 0
        return 1 if left_pre is None else -1
    for left_id, right_id in zip(left_pre, right_pre):
        if left_id == right_id:
            continue
        left_numeric, right_numeric = left_id.isdigit(), right_id.isdigit()
        if left_numeric and right_numeric:
            return -1 if int(left_id) < int(right_id) else 1
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return -1 if left_id < right_id else 1
    if len(left_pre) == len(right_pre):
        return 0
    return -1 if len(left_pre) < len(right_pre) else 1


def _require_protocol(
    value: Any,
    name: str,
    error_type: type[PreregistrationGovernanceError],
) -> PreregistrationProtocol:
    if type(value) is not PreregistrationProtocol:
        raise error_type(f"{name} must be an exact trusted PreregistrationProtocol")
    return value


def _validate_identity(value: Any, name: str, error_type: type[PreregistrationGovernanceError]) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise error_type(f"{name} must be a lowercase PREREGISTRATION_CONTENT identity")
    return value


@dataclass(frozen=True, slots=True)
class PreregistrationApprovalRecord:
    governance_contract_version: str
    preregistration_identity: str
    preregistration_version: str
    approval_state: str
    approved_protocol_hash: str
    approved_at: str | None
    approval_evidence_type: str | None
    approval_evidence_reference: str | None
    merge_commit_sha: str | None
    operational_status: str
    rejection_reason: str | None
    superseded_by_preregistration_identity: str | None
    superseded_at: str | None
    supersession_evidence_type: str | None
    supersession_evidence_reference: str | None
    supersession_merge_commit_sha: str | None
    recorded_at: str
    _construction_token: InitVar[object] = None

    def __post_init__(self, _construction_token: object) -> None:
        if _construction_token is not _GOVERNANCE_CONSTRUCTION_TOKEN:
            raise InvalidApprovalRecord(
                "PreregistrationApprovalRecord must be created by an approved governance builder"
            )
        _validate_approval_record(self)


def _validate_approval_record(record: PreregistrationApprovalRecord) -> None:
    if record.governance_contract_version != GOVERNANCE_CONTRACT_VERSION:
        raise InvalidApprovalRecord("governance_contract_version mismatch")
    identity = _validate_identity(
        record.preregistration_identity, "preregistration_identity", InvalidApprovalRecord
    )
    if record.approved_protocol_hash != identity:
        raise InvalidApprovalRecord("approved_protocol_hash must equal preregistration_identity")
    _parse_semver(record.preregistration_version, "preregistration_version", InvalidApprovalRecord)
    if record.approval_state not in APPROVAL_STATES:
        raise InvalidApprovalRecord("unsupported approval_state")
    if record.operational_status not in OPERATIONAL_STATUSES:
        raise InvalidApprovalRecord("unsupported operational_status")
    recorded = _strict_timestamp(record.recorded_at, "recorded_at", InvalidApprovalRecord)

    if record.approval_state == PENDING:
        if record.operational_status != SYNTHETIC_NON_OPERATIONAL or any(
            value is not None
            for value in (
                record.approved_at,
                record.approval_evidence_type,
                record.approval_evidence_reference,
                record.merge_commit_sha,
                record.rejection_reason,
                record.superseded_by_preregistration_identity,
                record.superseded_at,
                record.supersession_evidence_type,
                record.supersession_evidence_reference,
                record.supersession_merge_commit_sha,
            )
        ):
            raise InvalidApprovalRecord("PENDING field matrix mismatch")
        return

    if record.approval_evidence_type not in APPROVAL_EVIDENCE_TYPES:
        raise InvalidApprovalRecord("approval evidence type is invalid")
    _require_nonblank(
        record.approval_evidence_reference,
        "approval_evidence_reference",
        InvalidApprovalRecord,
    )
    if record.operational_status == SYNTHETIC_NON_OPERATIONAL:
        if record.approval_evidence_type != SYNTHETIC_FIXTURE or record.merge_commit_sha is not None:
            raise InvalidApprovalRecord("synthetic approval evidence matrix mismatch")
    else:
        if record.approval_state != APPROVED:
            raise InvalidApprovalRecord("operational evidence may only create an APPROVED record")
        if record.approval_evidence_type != GITHUB_PR_MERGE:
            raise InvalidApprovalRecord("operational approval requires GITHUB_PR_MERGE evidence")
        if (
            type(record.approval_evidence_reference) is not str
            or _GITHUB_APPROVAL_PR_URL.fullmatch(record.approval_evidence_reference) is None
        ):
            raise InvalidApprovalRecord(
                "operational approval requires a canonical fixed-repository GitHub PR URL"
            )
        if type(record.merge_commit_sha) is not str or _GIT_SHA.fullmatch(record.merge_commit_sha) is None:
            raise InvalidApprovalRecord("operational approval requires a lowercase merge commit SHA")

    if record.approval_state == REJECTED:
        if record.approved_at is not None:
            raise InvalidApprovalRecord("REJECTED requires approved_at to be null")
        _require_nonblank(record.rejection_reason, "rejection_reason", InvalidApprovalRecord)
        if any(
            value is not None
            for value in (
                record.superseded_by_preregistration_identity,
                record.superseded_at,
                record.supersession_evidence_type,
                record.supersession_evidence_reference,
                record.supersession_merge_commit_sha,
            )
        ):
            raise InvalidApprovalRecord("REJECTED must not contain supersession fields")
        return

    approved = _strict_timestamp(record.approved_at, "approved_at", InvalidApprovalRecord)
    if record.rejection_reason is not None:
        raise InvalidApprovalRecord("approved lifecycle records require null rejection_reason")

    if record.approval_state == APPROVED:
        if any(
            value is not None
            for value in (
                record.superseded_by_preregistration_identity,
                record.superseded_at,
                record.supersession_evidence_type,
                record.supersession_evidence_reference,
                record.supersession_merge_commit_sha,
            )
        ):
            raise InvalidApprovalRecord("APPROVED must not contain supersession fields")
        if recorded < approved:
            raise InvalidApprovalRecord("recorded_at must not precede approved_at")
        return

    superseding_identity = _validate_identity(
        record.superseded_by_preregistration_identity,
        "superseded_by_preregistration_identity",
        InvalidApprovalRecord,
    )
    if superseding_identity == identity:
        raise InvalidApprovalRecord("a protocol cannot supersede itself")
    superseded = _strict_timestamp(record.superseded_at, "superseded_at", InvalidApprovalRecord)
    if record.supersession_evidence_type not in APPROVAL_EVIDENCE_TYPES:
        raise InvalidApprovalRecord("supersession evidence type is invalid")
    _require_nonblank(
        record.supersession_evidence_reference,
        "supersession_evidence_reference",
        InvalidApprovalRecord,
    )
    if record.operational_status == SYNTHETIC_NON_OPERATIONAL and (
        record.supersession_evidence_type != SYNTHETIC_FIXTURE
        or record.supersession_merge_commit_sha is not None
    ):
        raise InvalidApprovalRecord("synthetic supersession evidence matrix mismatch")
    if not approved < superseded:
        raise InvalidApprovalRecord("approved_at must precede superseded_at")
    if recorded < superseded:
        raise InvalidApprovalRecord("recorded_at must not precede superseded_at")


def build_synthetic_preregistration_approval_record(
    *,
    protocol: PreregistrationProtocol,
    approval_state: str,
    recorded_at: str,
    approved_at: str | None = None,
    fixture_evidence_reference: str | None = None,
    rejection_reason: str | None = None,
    superseded_by_protocol: PreregistrationProtocol | None = None,
    superseded_at: str | None = None,
    supersession_fixture_evidence_reference: str | None = None,
) -> PreregistrationApprovalRecord:
    """Build a synthetic record that cannot grant operational authority."""

    old = _require_protocol(protocol, "protocol", InvalidApprovalRecord)
    if approval_state not in APPROVAL_STATES:
        raise InvalidApprovalRecord("unsupported approval_state")
    identity = old.preregistration_identity
    common = {
        "governance_contract_version": GOVERNANCE_CONTRACT_VERSION,
        "preregistration_identity": identity,
        "preregistration_version": old.preregistration_version,
        "approval_state": approval_state,
        "approved_protocol_hash": identity,
        "operational_status": SYNTHETIC_NON_OPERATIONAL,
        "recorded_at": recorded_at,
        "_construction_token": _GOVERNANCE_CONSTRUCTION_TOKEN,
    }
    if approval_state == PENDING:
        return PreregistrationApprovalRecord(
            **common,
            approved_at=approved_at,
            approval_evidence_type=None,
            approval_evidence_reference=fixture_evidence_reference,
            merge_commit_sha=None,
            rejection_reason=rejection_reason,
            superseded_by_preregistration_identity=None,
            superseded_at=superseded_at,
            supersession_evidence_type=None,
            supersession_evidence_reference=supersession_fixture_evidence_reference,
            supersession_merge_commit_sha=None,
        )
    if approval_state == REJECTED:
        return PreregistrationApprovalRecord(
            **common,
            approved_at=approved_at,
            approval_evidence_type=SYNTHETIC_FIXTURE,
            approval_evidence_reference=fixture_evidence_reference,
            merge_commit_sha=None,
            rejection_reason=rejection_reason,
            superseded_by_preregistration_identity=None,
            superseded_at=superseded_at,
            supersession_evidence_type=None,
            supersession_evidence_reference=supersession_fixture_evidence_reference,
            supersession_merge_commit_sha=None,
        )
    if approval_state == APPROVED:
        return PreregistrationApprovalRecord(
            **common,
            approved_at=approved_at,
            approval_evidence_type=SYNTHETIC_FIXTURE,
            approval_evidence_reference=fixture_evidence_reference,
            merge_commit_sha=None,
            rejection_reason=rejection_reason,
            superseded_by_preregistration_identity=None,
            superseded_at=superseded_at,
            supersession_evidence_type=None,
            supersession_evidence_reference=supersession_fixture_evidence_reference,
            supersession_merge_commit_sha=None,
        )

    new = _require_protocol(
        superseded_by_protocol, "superseded_by_protocol", InvalidApprovalRecord
    )
    if old.research_protocol_id != new.research_protocol_id:
        raise InvalidApprovalRecord("supersession cannot switch research_protocol_id")
    if old.contract_version != new.contract_version:
        raise InvalidApprovalRecord("supersession cannot switch protocol contract_version")
    if old.preregistration_identity == new.preregistration_identity:
        raise InvalidApprovalRecord("supersession requires a different protocol identity")
    if _compare_semver(
        old.preregistration_version, new.preregistration_version, InvalidApprovalRecord
    ) >= 0:
        raise InvalidApprovalRecord("superseding protocol version must have higher SemVer precedence")
    return PreregistrationApprovalRecord(
        **common,
        approved_at=approved_at,
        approval_evidence_type=SYNTHETIC_FIXTURE,
        approval_evidence_reference=fixture_evidence_reference,
        merge_commit_sha=None,
        rejection_reason=rejection_reason,
        superseded_by_preregistration_identity=new.preregistration_identity,
        superseded_at=superseded_at,
        supersession_evidence_type=SYNTHETIC_FIXTURE,
        supersession_evidence_reference=supersession_fixture_evidence_reference,
        supersession_merge_commit_sha=None,
    )


def validate_preregistration_approval_history(
    records: list[PreregistrationApprovalRecord] | tuple[PreregistrationApprovalRecord, ...],
) -> None:
    """Validate an ordered, identity-specific approval lifecycle."""

    if not isinstance(records, (list, tuple)) or not records:
        raise InvalidApprovalHistory("approval history must be a non-empty list or tuple")
    snapshot = tuple(records)
    if any(type(record) is not PreregistrationApprovalRecord for record in snapshot):
        raise InvalidApprovalHistory("approval history contains an invalid record type")
    binding = (
        snapshot[0].governance_contract_version,
        snapshot[0].preregistration_identity,
        snapshot[0].preregistration_version,
        snapshot[0].approved_protocol_hash,
    )
    if binding[0] != GOVERNANCE_CONTRACT_VERSION:
        raise InvalidApprovalHistory("governance_contract_version mismatch")
    for record in snapshot:
        if (
            record.governance_contract_version,
            record.preregistration_identity,
            record.preregistration_version,
            record.approved_protocol_hash,
        ) != binding:
            raise InvalidApprovalHistory("approval history protocol binding mismatch")
    sequence = tuple(record.approval_state for record in snapshot)
    if sequence not in _LEGAL_APPROVAL_SEQUENCES:
        raise InvalidApprovalHistory("illegal approval lifecycle")
    recorded_instants = tuple(
        _strict_timestamp(record.recorded_at, "recorded_at", InvalidApprovalHistory)
        for record in snapshot
    )
    if any(current <= previous for previous, current in zip(recorded_instants, recorded_instants[1:])):
        raise InvalidApprovalHistory("recorded_at must be strictly increasing")
    if APPROVED in sequence and SUPERSEDED in sequence:
        approved = snapshot[sequence.index(APPROVED)]
        superseded = snapshot[sequence.index(SUPERSEDED)]
        preserved_fields = (
            "approved_at",
            "approval_evidence_type",
            "approval_evidence_reference",
            "merge_commit_sha",
            "approved_protocol_hash",
            "preregistration_identity",
            "preregistration_version",
        )
        if any(
            getattr(approved, name) != getattr(superseded, name)
            for name in preserved_fields
        ):
            raise InvalidApprovalHistory("SUPERSEDED rewrites original approval evidence")


@dataclass(frozen=True, slots=True)
class PreregistrationChangeRecord:
    governance_contract_version: str
    research_protocol_id: str
    previous_preregistration_identity: str
    previous_preregistration_version: str
    new_preregistration_identity: str
    new_preregistration_version: str
    change_classification: str
    change_reason: str
    recorded_at: str
    _construction_token: InitVar[object] = None

    def __post_init__(self, _construction_token: object) -> None:
        if _construction_token is not _GOVERNANCE_CONSTRUCTION_TOKEN:
            raise InvalidChangeRecord(
                "PreregistrationChangeRecord must be created by the approved governance builder"
            )
        _validate_change_record_shape(self, InvalidChangeRecord)


def _validate_change_record_shape(
    record: PreregistrationChangeRecord,
    error_type: type[PreregistrationGovernanceError],
) -> None:
    if record.governance_contract_version != GOVERNANCE_CONTRACT_VERSION:
        raise error_type("governance_contract_version mismatch")
    _require_nonblank(record.research_protocol_id, "research_protocol_id", error_type)
    _validate_identity(
        record.previous_preregistration_identity,
        "previous_preregistration_identity",
        error_type,
    )
    _validate_identity(
        record.new_preregistration_identity,
        "new_preregistration_identity",
        error_type,
    )
    _parse_semver(record.previous_preregistration_version, "previous version", error_type)
    _parse_semver(record.new_preregistration_version, "new version", error_type)
    if record.change_classification not in CHANGE_CLASSIFICATIONS:
        raise error_type("unsupported change_classification")
    _require_nonblank(record.change_reason, "change_reason", error_type)
    _strict_timestamp(record.recorded_at, "recorded_at", error_type)


def _changed_classifications(
    previous_protocol: PreregistrationProtocol,
    new_protocol: PreregistrationProtocol,
    error_type: type[PreregistrationGovernanceError],
) -> frozenset[str]:
    previous = previous_protocol.semantic_projection()
    new = new_protocol.semantic_projection()
    if previous_protocol.research_protocol_id != new_protocol.research_protocol_id:
        raise error_type("protocol transition cannot switch research_protocol_id")
    if previous_protocol.contract_version != new_protocol.contract_version:
        raise error_type("protocol transition cannot switch contract_version")
    changed_keys = {
        key
        for key in set(previous) | set(new)
        if key not in _PROTOCOL_INVARIANT_KEYS | {_PROTOCOL_VERSION_KEY}
        and (key not in previous or key not in new or previous[key] != new[key])
    }
    unknown = changed_keys - set(_SECTION_CLASSIFICATIONS)
    if unknown:
        raise error_type("protocol transition contains an unclassified semantic section")
    return frozenset(_SECTION_CLASSIFICATIONS[key] for key in changed_keys)


def _validate_protocol_transition(
    previous_protocol: Any,
    new_protocol: Any,
    classification: str,
    error_type: type[PreregistrationGovernanceError],
) -> tuple[PreregistrationProtocol, PreregistrationProtocol, frozenset[str]]:
    previous = _require_protocol(previous_protocol, "previous_protocol", error_type)
    new = _require_protocol(new_protocol, "new_protocol", error_type)
    actual = _changed_classifications(previous, new, error_type)
    if classification == ADMINISTRATIVE:
        if actual or previous.preregistration_identity != new.preregistration_identity or (
            previous.preregistration_version != new.preregistration_version
        ):
            raise error_type("ADMINISTRATIVE cannot hide a protocol semantic change")
        return previous, new, actual
    if classification not in CHANGE_CLASSIFICATIONS:
        raise error_type("unsupported change_classification")
    if not actual:
        raise error_type("semantic change record requires a substantive semantic change")
    if previous.preregistration_identity == new.preregistration_identity:
        raise error_type("semantic change requires a new preregistration identity")
    if previous.preregistration_version == new.preregistration_version:
        raise error_type("semantic change requires a new preregistration version")
    if _compare_semver(
        previous.preregistration_version,
        new.preregistration_version,
        error_type,
    ) >= 0:
        raise error_type("new preregistration version must have higher SemVer precedence")
    if classification not in actual:
        raise error_type("change_classification is not present in the semantic diff")
    return previous, new, actual


def build_preregistration_change_record(
    *,
    previous_protocol: PreregistrationProtocol,
    new_protocol: PreregistrationProtocol,
    change_classification: str,
    change_reason: str,
    recorded_at: str,
) -> PreregistrationChangeRecord:
    """Build one truthful change record for an exact trusted protocol pair."""

    previous, new, _actual = _validate_protocol_transition(
        previous_protocol,
        new_protocol,
        change_classification,
        InvalidChangeRecord,
    )
    return PreregistrationChangeRecord(
        governance_contract_version=GOVERNANCE_CONTRACT_VERSION,
        research_protocol_id=previous.research_protocol_id,
        previous_preregistration_identity=previous.preregistration_identity,
        previous_preregistration_version=previous.preregistration_version,
        new_preregistration_identity=new.preregistration_identity,
        new_preregistration_version=new.preregistration_version,
        change_classification=change_classification,
        change_reason=change_reason,
        recorded_at=recorded_at,
        _construction_token=_GOVERNANCE_CONSTRUCTION_TOKEN,
    )


def build_operational_preregistration_approval_record(
    *,
    protocol: PreregistrationProtocol,
    verified_evidence: Any,
    recorded_at: str,
) -> PreregistrationApprovalRecord:
    """Derive an operational approval record from verified GitHub evidence.

    ``OPERATIONAL_VERIFIED`` means only that the Human approval evidence chain
    was verified against GitHub.  It does not enable backtests, real data, or
    any production, canonical, or deferred write path.
    """

    from scripts.c4_rd_preregistration_approval_evidence import (
        VerifiedGitHubApprovalEvidence,
    )

    trusted = _require_protocol(protocol, "protocol", InvalidApprovalRecord)
    if type(verified_evidence) is not VerifiedGitHubApprovalEvidence:
        raise InvalidApprovalRecord(
            "verified_evidence must be exact VerifiedGitHubApprovalEvidence"
        )
    if verified_evidence.artifact_preregistration_identity != trusted.preregistration_identity:
        raise InvalidApprovalRecord("verified evidence protocol identity mismatch")
    return PreregistrationApprovalRecord(
        governance_contract_version=GOVERNANCE_CONTRACT_VERSION,
        preregistration_identity=trusted.preregistration_identity,
        preregistration_version=trusted.preregistration_version,
        approval_state=APPROVED,
        approved_protocol_hash=trusted.preregistration_identity,
        approved_at=verified_evidence.merged_at,
        approval_evidence_type=GITHUB_PR_MERGE,
        approval_evidence_reference=verified_evidence.pr_url,
        merge_commit_sha=verified_evidence.merge_commit_sha,
        operational_status=OPERATIONAL_VERIFIED,
        rejection_reason=None,
        superseded_by_preregistration_identity=None,
        superseded_at=None,
        supersession_evidence_type=None,
        supersession_evidence_reference=None,
        supersession_merge_commit_sha=None,
        recorded_at=recorded_at,
        _construction_token=_GOVERNANCE_CONSTRUCTION_TOKEN,
    )


def validate_preregistration_change_set(
    *,
    previous_protocol: PreregistrationProtocol,
    new_protocol: PreregistrationProtocol,
    records: list[PreregistrationChangeRecord] | tuple[PreregistrationChangeRecord, ...],
) -> None:
    """Require an exact, complete record set for a protocol transition."""

    previous = _require_protocol(previous_protocol, "previous_protocol", InvalidChangeSet)
    new = _require_protocol(new_protocol, "new_protocol", InvalidChangeSet)
    if not isinstance(records, (list, tuple)):
        raise InvalidChangeSet("records must be a list or tuple")
    snapshot = tuple(records)
    if any(type(record) is not PreregistrationChangeRecord for record in snapshot):
        raise InvalidChangeSet("change set contains an invalid record type")
    actual = _changed_classifications(previous, new, InvalidChangeSet)
    expected_binding = (
        GOVERNANCE_CONTRACT_VERSION,
        previous.research_protocol_id,
        previous.preregistration_identity,
        previous.preregistration_version,
        new.preregistration_identity,
        new.preregistration_version,
    )
    for record in snapshot:
        _validate_change_record_shape(record, InvalidChangeSet)
        binding = (
            record.governance_contract_version,
            record.research_protocol_id,
            record.previous_preregistration_identity,
            record.previous_preregistration_version,
            record.new_preregistration_identity,
            record.new_preregistration_version,
        )
        if binding != expected_binding:
            raise InvalidChangeSet("change record protocol binding mismatch")

    classifications = tuple(record.change_classification for record in snapshot)
    if actual:
        if previous.preregistration_identity == new.preregistration_identity:
            raise InvalidChangeSet("semantic change requires a new preregistration identity")
        if previous.preregistration_version == new.preregistration_version or _compare_semver(
            previous.preregistration_version,
            new.preregistration_version,
            InvalidChangeSet,
        ) >= 0:
            raise InvalidChangeSet("semantic change requires a higher preregistration version")
        if ADMINISTRATIVE in classifications:
            raise InvalidChangeSet("ADMINISTRATIVE cannot be mixed with semantic changes")
        if len(classifications) != len(set(classifications)):
            raise InvalidChangeSet("duplicate change classification")
        if set(classifications) != set(actual):
            raise InvalidChangeSet("change set does not exactly cover the semantic diff")
        return

    if previous.preregistration_identity != new.preregistration_identity or (
        previous.preregistration_version != new.preregistration_version
    ):
        raise InvalidChangeSet("version-only or identity-only transition is forbidden")
    if classifications != (ADMINISTRATIVE,):
        raise InvalidChangeSet("pure administrative event requires exactly one ADMINISTRATIVE record")


def require_formal_backtest_authorization(
    *,
    approval_record: PreregistrationApprovalRecord,
    backtest_started_at: str,
) -> None:
    """Fail closed unless verified approval strictly predates a formal backtest."""

    if type(approval_record) is not PreregistrationApprovalRecord:
        raise FormalBacktestNotAuthorized("an exact approval record is required")
    started = _strict_timestamp(
        backtest_started_at,
        "backtest_started_at",
        FormalBacktestNotAuthorized,
    )
    if approval_record.operational_status != OPERATIONAL_VERIFIED:
        raise FormalBacktestNotAuthorized("approval is not operationally verified")
    if approval_record.approval_state == APPROVED:
        approved = _strict_timestamp(
            approval_record.approved_at,
            "approved_at",
            FormalBacktestNotAuthorized,
        )
        if approved < started:
            return
    elif approval_record.approval_state == SUPERSEDED:
        approved = _strict_timestamp(
            approval_record.approved_at,
            "approved_at",
            FormalBacktestNotAuthorized,
        )
        superseded = _strict_timestamp(
            approval_record.superseded_at,
            "superseded_at",
            FormalBacktestNotAuthorized,
        )
        if approved < started < superseded:
            return
    raise FormalBacktestNotAuthorized("formal backtest is not authorized")


__all__ = (
    "GOVERNANCE_CONTRACT_VERSION",
    "APPROVAL_STATES",
    "OPERATIONAL_STATUSES",
    "APPROVAL_EVIDENCE_TYPES",
    "CHANGE_CLASSIFICATIONS",
    "PENDING",
    "APPROVED",
    "REJECTED",
    "SUPERSEDED",
    "SYNTHETIC_NON_OPERATIONAL",
    "OPERATIONAL_VERIFIED",
    "GITHUB_PR_MERGE",
    "GITHUB_PR_CLOSED_UNMERGED",
    "SYNTHETIC_FIXTURE",
    "RESEARCH_QUESTION",
    "DATASET",
    "FEATURE",
    "LABEL",
    "BASELINE",
    "METRIC",
    "EVALUATION",
    "EXCLUSION",
    "REPORTING",
    "ADMINISTRATIVE",
    "PreregistrationGovernanceError",
    "InvalidApprovalRecord",
    "InvalidApprovalHistory",
    "InvalidChangeRecord",
    "InvalidChangeSet",
    "FormalBacktestNotAuthorized",
    "PreregistrationApprovalRecord",
    "PreregistrationChangeRecord",
    "build_synthetic_preregistration_approval_record",
    "build_operational_preregistration_approval_record",
    "validate_preregistration_approval_history",
    "build_preregistration_change_record",
    "validate_preregistration_change_set",
    "require_formal_backtest_authorization",
)
