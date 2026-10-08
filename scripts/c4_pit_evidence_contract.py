"""C4-PIT-2B synthetic claim-first evidence contracts.

This module is deliberately pure and persistence-free.  It models evidence before
an ObservationVersion exists and exposes an explicit candidate materialization
boundary.  It does not enable PIT, acquire artifacts, access DuckDB, or write data.

The existing C4 canonical JSON and hash implementation remains authoritative.  PoC
hashes are namespaced inside the inert ``MANIFEST_CONTENT`` domain so this bounded
stage does not extend the frozen production hash-domain catalog.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import InitVar, dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from scripts.c4_rd_contract import (
    ContractError,
    ImmutableMapping,
    canonical_hash,
    canonical_timestamp,
    parse_date,
    parse_rfc3339,
    raw_payload_hash,
    validate_source_period,
)


EVIDENCE_CONTRACT_VERSION = "C4_PIT_EVIDENCE_CONTRACT_V1@1.0.0"
DAY_GRANULAR_POLICY_VERSION = "C4_DAY_GRANULAR_NEXT_DAY_V1@1.0.0"
STRICT_INTRADAY_POLICY_VERSION = "C4_STRICT_INTRADAY_V1@1.0.0"

EVIDENCE_SUBJECT_SCOPES = (
    "UNBOUND",
    "SOURCE_RELEASE_ONLY",
    "ATTACHMENT_BOUND",
    "RECORD_BOUND",
    "RECORD_VERSION_BOUND",
)
TEMPORAL_BOUND_TYPES = (
    "EXACT",
    "BY_DATE",
    "NOT_BEFORE",
    "NOT_LATER_THAN",
    "INTERVAL",
    "UNKNOWN",
)
TEMPORAL_PRECISIONS = ("EXACT_TIMESTAMP", "DATE_ONLY", "MONTH_ONLY", "UNKNOWN")
PIT_TEMPORAL_MODES = ("STRICT_INTRADAY_PIT", "DAY_GRANULAR_PIT")
POLICY_VERSION_BY_MODE = {
    "STRICT_INTRADAY_PIT": STRICT_INTRADAY_POLICY_VERSION,
    "DAY_GRANULAR_PIT": DAY_GRANULAR_POLICY_VERSION,
}
BINDING_STATES = (
    "EXACT_ARTIFACT_HASH_BOUND",
    "OFFICIAL_ATTACHMENT_BOUND",
    "OFFICIAL_RECORD_VALUE_BOUND",
    "SOURCE_RELEASE_BOUND",
    "SEMANTIC_VALUE_MATCH_ONLY",
    "UNBOUND",
)
EVIDENCE_AUTHORITIES = (
    "OFFICIAL_SOURCE_RELEASE",
    "OFFICIAL_ATTACHMENT",
    "OFFICIAL_RECORD",
    "SEMANTIC_MATCH_ONLY",
    "UNVERIFIED",
)
CLAIM_TYPES = (
    "SOURCE_RELEASE_PUBLICATION",
    "ARTIFACT_MEMBERSHIP",
    "RECORD_VALUE",
    "RECORD_VERSION",
)

_ALLOWED_BINDING_STATES_BY_SCOPE = {
    "UNBOUND": frozenset({"UNBOUND", "SEMANTIC_VALUE_MATCH_ONLY"}),
    "SOURCE_RELEASE_ONLY": frozenset({"SOURCE_RELEASE_BOUND", "SEMANTIC_VALUE_MATCH_ONLY"}),
    "ATTACHMENT_BOUND": frozenset(
        {
            "SOURCE_RELEASE_BOUND",
            "OFFICIAL_ATTACHMENT_BOUND",
            "EXACT_ARTIFACT_HASH_BOUND",
            "SEMANTIC_VALUE_MATCH_ONLY",
        }
    ),
    "RECORD_BOUND": frozenset(
        {
            "SOURCE_RELEASE_BOUND",
            "OFFICIAL_ATTACHMENT_BOUND",
            "EXACT_ARTIFACT_HASH_BOUND",
            "OFFICIAL_RECORD_VALUE_BOUND",
            "SEMANTIC_VALUE_MATCH_ONLY",
        }
    ),
    "RECORD_VERSION_BOUND": frozenset(
        {
            "SOURCE_RELEASE_BOUND",
            "OFFICIAL_ATTACHMENT_BOUND",
            "EXACT_ARTIFACT_HASH_BOUND",
            "OFFICIAL_RECORD_VALUE_BOUND",
            "SEMANTIC_VALUE_MATCH_ONLY",
        }
    ),
}

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_LOCATOR_KEYS = frozenset({"page", "table", "row", "label"})
_PERIOD_KEYS = frozenset(
    {
        "source_period_type",
        "source_market_date",
        "source_period_start_date",
        "source_period_end_date",
    }
)
_CANDIDATE_CONSTRUCTION_AUTHORITY = object()


class EvidenceContractError(ContractError):
    """Fail-closed C4-PIT-2B contract or validation error."""


def _require_non_empty(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise EvidenceContractError(f"{name} must be a non-empty string")


def _require_optional_non_empty(value: Any, name: str) -> None:
    if value is not None:
        _require_non_empty(value, name)


def _require_sha256(value: Any, name: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise EvidenceContractError(f"{name} must be lowercase SHA-256 hex")


def _require_decimal(value: Any, name: str) -> None:
    if type(value) is not Decimal or not value.is_finite():
        raise EvidenceContractError(f"{name} must be an exact finite Decimal")


def _immutable_mapping(value: Any, name: str) -> ImmutableMapping:
    if not isinstance(value, Mapping):
        raise EvidenceContractError(f"{name} must be a mapping")
    try:
        return ImmutableMapping(value)
    except ContractError as exc:
        raise EvidenceContractError(str(exc)) from exc


def _exact_keys(value: Mapping[str, Any], keys: frozenset[str], name: str) -> None:
    if set(value) != keys:
        raise EvidenceContractError(f"{name} must contain exactly {sorted(keys)}")


def _contract_hash(kind: str, projection: Mapping[str, Any]) -> str:
    """Use the repository hash authority without changing its frozen domain set."""

    return canonical_hash(
        "MANIFEST_CONTENT",
        {
            "contract_version": EVIDENCE_CONTRACT_VERSION,
            "contract_kind": kind,
            "projection": projection,
        },
    )


def _scope_rank(value: str) -> int:
    try:
        return EVIDENCE_SUBJECT_SCOPES.index(value)
    except ValueError as exc:
        raise EvidenceContractError("unsupported evidence subject scope") from exc


def _require_policy_compatibility(mode: str, policy_version: str | None) -> str:
    if mode not in PIT_TEMPORAL_MODES:
        raise EvidenceContractError("unsupported PIT temporal mode")
    expected = POLICY_VERSION_BY_MODE[mode]
    selected = expected if policy_version is None else policy_version
    if selected != expected:
        raise EvidenceContractError("PIT mode and policy version are incompatible")
    return selected


def _resolve_source_timezone(bound: "TemporalBound", explicit_timezone: str | None) -> str | None:
    if (
        bound.source_timezone is not None
        and explicit_timezone is not None
        and bound.source_timezone != explicit_timezone
    ):
        raise EvidenceContractError("conflicting source timezone authorities")
    return explicit_timezone or bound.source_timezone


def _validate_record_locator(value: Mapping[str, Any]) -> None:
    _exact_keys(value, _LOCATOR_KEYS, "record_locator")
    if any(not isinstance(value[key], str) or not value[key] for key in _LOCATOR_KEYS):
        raise EvidenceContractError("record_locator components must be non-empty strings")


def _validate_period(value: Mapping[str, Any]) -> None:
    _exact_keys(value, _PERIOD_KEYS, "claimed_period")
    validate_source_period(
        value["source_period_type"],
        value["source_market_date"],
        value["source_period_start_date"],
        value["source_period_end_date"],
    )


@dataclass(frozen=True, slots=True)
class TemporalBound:
    """Source temporal evidence that preserves its original precision."""

    bound_type: str
    precision: str
    original_value: str | tuple[str, str] | None
    source_timezone: str | None = None
    normalized_start: str | None = field(init=False, default=None)
    normalized_end: str | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        if self.bound_type not in TEMPORAL_BOUND_TYPES:
            raise EvidenceContractError("unsupported temporal bound type")
        if self.precision not in TEMPORAL_PRECISIONS:
            raise EvidenceContractError("unsupported temporal precision")
        _require_optional_non_empty(self.source_timezone, "source_timezone")
        if self.source_timezone is not None:
            try:
                ZoneInfo(self.source_timezone)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise EvidenceContractError("source_timezone must be an installed IANA timezone") from exc
        if self.bound_type == "UNKNOWN":
            if self.precision != "UNKNOWN" or self.original_value is not None:
                raise EvidenceContractError("UNKNOWN bound must retain UNKNOWN precision and null value")
            if self.source_timezone is not None:
                raise EvidenceContractError("UNKNOWN bound cannot carry timezone metadata")
            return
        if self.precision == "UNKNOWN":
            raise EvidenceContractError("known temporal bound cannot use UNKNOWN precision")
        if self.bound_type == "EXACT" and self.precision != "EXACT_TIMESTAMP":
            raise EvidenceContractError("EXACT bound requires EXACT_TIMESTAMP precision")
        if self.precision == "EXACT_TIMESTAMP" and self.bound_type == "BY_DATE":
            raise EvidenceContractError("BY_DATE cannot claim exact timestamp precision")

        if self.bound_type == "INTERVAL":
            if not isinstance(self.original_value, tuple) or len(self.original_value) != 2:
                raise EvidenceContractError("INTERVAL requires an exact two-value tuple")
            normalized = tuple(self._normalize(item) for item in self.original_value)
            if self._sort_key(normalized[0]) > self._sort_key(normalized[1]):
                raise EvidenceContractError("temporal interval start must not follow end")
            object.__setattr__(self, "normalized_start", normalized[0])
            object.__setattr__(self, "normalized_end", normalized[1])
            return
        if not isinstance(self.original_value, str) or not self.original_value:
            raise EvidenceContractError("known non-interval bound requires one original value")
        normalized_value = self._normalize(self.original_value)
        if self.bound_type in {"EXACT", "BY_DATE", "NOT_BEFORE"}:
            object.__setattr__(self, "normalized_start", normalized_value)
        if self.bound_type in {"EXACT", "BY_DATE", "NOT_LATER_THAN"}:
            object.__setattr__(self, "normalized_end", normalized_value)

    def _normalize(self, value: str) -> str:
        if not isinstance(value, str) or not value:
            raise EvidenceContractError("temporal evidence values must be non-empty strings")
        try:
            if self.precision == "EXACT_TIMESTAMP":
                # parse_rfc3339 rejects a missing timezone/offset.
                return canonical_timestamp(value)
            if self.precision == "DATE_ONLY":
                parse_date(value)
                return value
        except ContractError as exc:
            raise EvidenceContractError(str(exc)) from exc
        if self.precision == "MONTH_ONLY":
            if _MONTH.fullmatch(value) is None:
                raise EvidenceContractError("MONTH_ONLY value must use YYYY-MM")
            return value
        raise EvidenceContractError("unsupported temporal precision")

    def _sort_key(self, value: str) -> datetime | date | str:
        if self.precision == "EXACT_TIMESTAMP":
            return parse_rfc3339(value)
        if self.precision == "DATE_ONLY":
            return parse_date(value)
        return value

    def projection(self) -> dict[str, Any]:
        return {
            "bound_type": self.bound_type,
            "precision": self.precision,
            "original_value": self.original_value,
            "source_timezone": self.source_timezone,
            "normalized_start": self.normalized_start,
            "normalized_end": self.normalized_end,
        }


@dataclass(frozen=True, slots=True)
class TemporalEligibilityDecision:
    mode: str
    policy_version: str
    eligible: bool
    reason: str
    source_factual_timestamp: str | None
    eligible_source_local_date: str | None
    derived_comparison_boundary_at: str | None
    derived: bool
    source_timezone: str | None = None

    def __post_init__(self) -> None:
        if self.mode not in PIT_TEMPORAL_MODES:
            raise EvidenceContractError("unsupported PIT temporal mode")
        _require_non_empty(self.policy_version, "policy_version")
        if type(self.eligible) is not bool or type(self.derived) is not bool:
            raise EvidenceContractError("temporal decision booleans must be exact")
        _require_non_empty(self.reason, "reason")
        if self.source_factual_timestamp is not None:
            object.__setattr__(
                self,
                "source_factual_timestamp",
                canonical_timestamp(self.source_factual_timestamp),
            )
        if self.eligible_source_local_date is not None:
            parse_date(self.eligible_source_local_date)
        if self.derived_comparison_boundary_at is not None:
            object.__setattr__(
                self,
                "derived_comparison_boundary_at",
                canonical_timestamp(self.derived_comparison_boundary_at),
            )
        if self.derived and self.derived_comparison_boundary_at is None:
            raise EvidenceContractError("derived decision requires a comparison boundary")
        if self.source_timezone is not None:
            try:
                ZoneInfo(self.source_timezone)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise EvidenceContractError("source_timezone must be an installed IANA timezone") from exc
        if self.derived and self.source_timezone is None:
            raise EvidenceContractError("derived decision requires an explicit source timezone")
        if not self.derived and (
            self.eligible_source_local_date is not None
            or self.derived_comparison_boundary_at is not None
        ):
            raise EvidenceContractError("source facts and policy-derived boundaries must remain separate")


def evaluate_temporal_eligibility(
    bound: TemporalBound,
    *,
    cutoff_at: str,
    mode: str,
    source_timezone: str | None = None,
    policy_version: str | None = None,
) -> TemporalEligibilityDecision:
    """Evaluate a bound without converting policy output into a source fact."""

    if type(bound) is not TemporalBound:
        raise EvidenceContractError("bound must be an exact TemporalBound")
    cutoff = canonical_timestamp(cutoff_at)
    version = _require_policy_compatibility(mode, policy_version)
    resolved_timezone = _resolve_source_timezone(bound, source_timezone)

    if mode == "STRICT_INTRADAY_PIT":
        if bound.bound_type != "EXACT" or bound.precision != "EXACT_TIMESTAMP":
            return TemporalEligibilityDecision(
                mode, version, False, "EXACT_TIMESTAMP_REQUIRED", None, None, None, False
            )
        factual = bound.normalized_start
        assert factual is not None
        return TemporalEligibilityDecision(
            mode,
            version,
            parse_rfc3339(factual) <= parse_rfc3339(cutoff),
            "VISIBLE" if parse_rfc3339(factual) <= parse_rfc3339(cutoff) else "AFTER_CUTOFF",
            factual,
            None,
            None,
            False,
        )

    if bound.bound_type == "EXACT" and bound.precision == "EXACT_TIMESTAMP":
        factual = bound.normalized_start
        assert factual is not None
        visible = parse_rfc3339(factual) <= parse_rfc3339(cutoff)
        return TemporalEligibilityDecision(
            mode,
            version,
            visible,
            "VISIBLE" if visible else "AFTER_CUTOFF",
            factual,
            None,
            None,
            False,
        )
    if bound.bound_type != "BY_DATE" or bound.precision != "DATE_ONLY":
        return TemporalEligibilityDecision(
            mode, version, False, "QUALIFYING_DATE_OR_TIMESTAMP_REQUIRED", None, None, None, False
        )
    timezone_name = resolved_timezone
    if timezone_name is None:
        raise EvidenceContractError("DAY_GRANULAR_PIT requires an explicit source timezone")
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise EvidenceContractError("source_timezone must be an installed IANA timezone") from exc
    evidence_date = parse_date(bound.normalized_start or "")
    eligible_date = evidence_date + timedelta(days=1)
    local_boundary = datetime.combine(eligible_date, time.min, tzinfo=zone)
    comparison_boundary = canonical_timestamp(local_boundary)
    visible = parse_rfc3339(comparison_boundary) <= parse_rfc3339(cutoff)
    return TemporalEligibilityDecision(
        mode,
        version,
        visible,
        "VISIBLE" if visible else "CONSERVATIVE_NEXT_DAY_NOT_REACHED",
        None,
        eligible_date.isoformat(),
        comparison_boundary,
        True,
        timezone_name,
    )


@dataclass(frozen=True, slots=True)
class SourceRelease:
    source_id: str
    data_acquisition_channel: str
    historical_evidence_channel: str
    release_family: str
    official_release_id: str | None = None
    release_label: str | None = None
    declared_publication_bound: TemporalBound | None = None

    def __post_init__(self) -> None:
        for name in (
            "source_id",
            "data_acquisition_channel",
            "historical_evidence_channel",
            "release_family",
        ):
            _require_non_empty(getattr(self, name), name)
        _require_optional_non_empty(self.official_release_id, "official_release_id")
        _require_optional_non_empty(self.release_label, "release_label")
        if self.official_release_id is None and self.release_label is None:
            raise EvidenceContractError(
                "release without an official ID must expose an explicit weaker release label"
            )
        if self.declared_publication_bound is not None and type(self.declared_publication_bound) is not TemporalBound:
            raise EvidenceContractError("declared_publication_bound must be an exact TemporalBound")

    @property
    def identity_strength(self) -> str:
        return "OFFICIAL_RELEASE_ID" if self.official_release_id is not None else "WEAK_RELEASE_LABEL"

    def identity_projection(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "historical_evidence_channel": self.historical_evidence_channel,
            "release_family": self.release_family,
            "official_release_id": self.official_release_id,
            "release_label": self.release_label if self.official_release_id is None else None,
            "identity_strength": self.identity_strength,
        }

    @property
    def source_release_id(self) -> str:
        return _contract_hash("SOURCE_RELEASE_ID", self.identity_projection())

    def content_projection(self) -> dict[str, Any]:
        return {
            **self.identity_projection(),
            "data_acquisition_channel": self.data_acquisition_channel,
            "release_label": self.release_label,
            "declared_publication_bound": (
                None
                if self.declared_publication_bound is None
                else self.declared_publication_bound.projection()
            ),
        }

    @property
    def content_hash(self) -> str:
        return _contract_hash("SOURCE_RELEASE_CONTENT", self.content_projection())


@dataclass(frozen=True, slots=True)
class EvidenceArtifact:
    source_release_id: str
    artifact_role: str
    media_type: str
    official_locator: str
    payload_hash: str | None = None
    byte_size: int | None = None
    retrieved_at: str | None = None
    source_document_id: str | None = None

    def __post_init__(self) -> None:
        _require_sha256(self.source_release_id, "source_release_id")
        for name in ("artifact_role", "media_type", "official_locator"):
            _require_non_empty(getattr(self, name), name)
        _require_optional_non_empty(self.source_document_id, "source_document_id")
        if self.payload_hash is not None:
            _require_sha256(self.payload_hash, "payload_hash")
        if self.byte_size is not None and (type(self.byte_size) is not int or self.byte_size < 0):
            raise EvidenceContractError("byte_size must be an exact non-negative int")
        if self.payload_hash is None and self.byte_size is not None:
            raise EvidenceContractError("byte_size without payload_hash is insufficient artifact identity")
        if self.retrieved_at is not None:
            object.__setattr__(self, "retrieved_at", canonical_timestamp(self.retrieved_at))

    @classmethod
    def from_payload(
        cls,
        *,
        source_release_id: str,
        artifact_role: str,
        media_type: str,
        official_locator: str,
        payload: bytes,
        retrieved_at: str | None = None,
        source_document_id: str | None = None,
    ) -> "EvidenceArtifact":
        if type(payload) is not bytes:
            raise EvidenceContractError("artifact payload must be exact synthetic bytes")
        return cls(
            source_release_id=source_release_id,
            artifact_role=artifact_role,
            media_type=media_type,
            official_locator=official_locator,
            payload_hash=raw_payload_hash(payload),
            byte_size=len(payload),
            retrieved_at=retrieved_at,
            source_document_id=source_document_id,
        )

    def identity_projection(self) -> dict[str, Any]:
        return {
            "source_release_id": self.source_release_id,
            "artifact_role": self.artifact_role,
            "media_type": self.media_type,
            "official_locator": self.official_locator,
            "source_document_id": self.source_document_id,
            "payload_hash": self.payload_hash,
        }

    @property
    def artifact_id(self) -> str:
        return _contract_hash("EVIDENCE_ARTIFACT_ID", self.identity_projection())

    def content_projection(self) -> dict[str, Any]:
        return {
            **self.identity_projection(),
            "byte_size": self.byte_size,
            "retrieved_at": self.retrieved_at,
        }

    @property
    def content_hash(self) -> str:
        return _contract_hash("EVIDENCE_ARTIFACT_CONTENT", self.content_projection())


def validate_artifact_consistency(artifacts: Sequence[EvidenceArtifact]) -> None:
    """Reject one official locator resolving to contradictory known byte hashes."""

    seen: dict[str, str] = {}
    content_by_id: dict[str, str] = {}
    for artifact in tuple(artifacts):
        if type(artifact) is not EvidenceArtifact:
            raise EvidenceContractError("artifacts must contain exact EvidenceArtifact values")
        if artifact.payload_hash is None:
            artifact_content = content_by_id.setdefault(artifact.artifact_id, artifact.content_hash)
            if artifact_content != artifact.content_hash:
                raise EvidenceContractError("same artifact ID has contradictory canonical content")
        else:
            key = artifact.official_locator
            previous = seen.setdefault(key, artifact.payload_hash)
            if previous != artifact.payload_hash:
                raise EvidenceContractError("same official artifact locator has contradictory payload hashes")
            artifact_content = content_by_id.setdefault(artifact.artifact_id, artifact.content_hash)
            if artifact_content != artifact.content_hash:
                raise EvidenceContractError("same artifact ID has contradictory canonical content")


def _require_exact_artifact_member(
    artifact: EvidenceArtifact,
    artifacts: Sequence[EvidenceArtifact],
) -> None:
    values = tuple(artifacts)
    validate_artifact_consistency(values)
    matches = [item for item in values if item.artifact_id == artifact.artifact_id]
    if not matches:
        raise EvidenceContractError("supplied artifact is absent from validated collection")
    if any(item.content_hash != artifact.content_hash for item in matches):
        raise EvidenceContractError("supplied artifact content does not match validated collection")


@dataclass(frozen=True, slots=True)
class EvidenceClaim:
    source_release_id: str
    artifact_id: str | None
    claim_type: str
    subject_scope: str
    claimed_period: Mapping[str, Any]
    claimed_value: Decimal | None
    unit: str | None
    currency: str | None
    temporal_bound: TemporalBound
    evidence_authority: str
    safe_locator: Mapping[str, Any]
    transformation_version: str

    def __post_init__(self) -> None:
        _require_sha256(self.source_release_id, "source_release_id")
        if self.artifact_id is not None:
            _require_sha256(self.artifact_id, "artifact_id")
        if self.claim_type not in CLAIM_TYPES:
            raise EvidenceContractError("unsupported evidence claim type")
        _scope_rank(self.subject_scope)
        period = _immutable_mapping(self.claimed_period, "claimed_period")
        _validate_period(period)
        object.__setattr__(self, "claimed_period", period)
        if self.claimed_value is not None:
            _require_decimal(self.claimed_value, "claimed_value")
        _require_optional_non_empty(self.unit, "unit")
        _require_optional_non_empty(self.currency, "currency")
        if any(item is not None for item in (self.claimed_value, self.unit, self.currency)) and any(
            item is None for item in (self.claimed_value, self.unit, self.currency)
        ):
            raise EvidenceContractError("record value, unit, and currency must be supplied together")
        if type(self.temporal_bound) is not TemporalBound:
            raise EvidenceContractError("temporal_bound must be an exact TemporalBound")
        if self.evidence_authority not in EVIDENCE_AUTHORITIES:
            raise EvidenceContractError("unsupported evidence authority")
        locator = _immutable_mapping(self.safe_locator, "safe_locator")
        object.__setattr__(self, "safe_locator", locator)
        _require_non_empty(self.transformation_version, "transformation_version")
        if self.transformation_version.lower() == "latest":
            raise EvidenceContractError("implicit/latest transformation version is forbidden")

    def identity_projection(self) -> dict[str, Any]:
        return {
            "source_release_id": self.source_release_id,
            "artifact_id": self.artifact_id,
            "claim_type": self.claim_type,
            "subject_scope": self.subject_scope,
            "claimed_period": self.claimed_period,
            "safe_locator": self.safe_locator,
            "transformation_version": self.transformation_version,
        }

    @property
    def claim_id(self) -> str:
        return _contract_hash("EVIDENCE_CLAIM_ID", self.identity_projection())

    def content_projection(self) -> dict[str, Any]:
        return {
            **self.identity_projection(),
            "claimed_value": self.claimed_value,
            "unit": self.unit,
            "currency": self.currency,
            "temporal_bound": self.temporal_bound.projection(),
            "evidence_authority": self.evidence_authority,
        }

    @property
    def content_hash(self) -> str:
        return _contract_hash("EVIDENCE_CLAIM_CONTENT", self.content_projection())


@dataclass(frozen=True, slots=True)
class EvidenceSubjectBinding:
    claim_id: str
    subject_scope: str
    binding_states: tuple[str, ...]
    source_release_id: str | None = None
    artifact_id: str | None = None
    expected_artifact_hash: str | None = None
    record_locator: Mapping[str, Any] | None = None
    source_id: str | None = None
    instrument_id: str | None = None
    metric_id: str | None = None
    source_record_identifier: str | None = None
    observation_id: str | None = None
    historical_vintage_claim_id: str | None = None
    historical_vintage_content_hash: str | None = None
    target_release_record_key: str | None = None

    def __post_init__(self) -> None:
        _require_sha256(self.claim_id, "claim_id")
        rank = _scope_rank(self.subject_scope)
        if not isinstance(self.binding_states, tuple) or not self.binding_states:
            raise EvidenceContractError("binding_states must be a non-empty tuple")
        if any(item not in BINDING_STATES for item in self.binding_states):
            raise EvidenceContractError("unsupported binding state")
        normalized_states = tuple(item for item in BINDING_STATES if item in self.binding_states)
        if len(normalized_states) != len(self.binding_states):
            raise EvidenceContractError("binding states must be unique")
        object.__setattr__(self, "binding_states", normalized_states)
        for name in (
            "source_release_id",
            "artifact_id",
            "expected_artifact_hash",
            "observation_id",
            "historical_vintage_claim_id",
            "historical_vintage_content_hash",
        ):
            value = getattr(self, name)
            if value is not None:
                _require_sha256(value, name)
        for name in ("source_id", "instrument_id", "metric_id", "source_record_identifier"):
            _require_optional_non_empty(getattr(self, name), name)
        _require_optional_non_empty(self.target_release_record_key, "target_release_record_key")
        locator = None
        if self.record_locator is not None:
            locator = _immutable_mapping(self.record_locator, "record_locator")
            _validate_record_locator(locator)
            object.__setattr__(self, "record_locator", locator)

        release_fields = (self.source_release_id,)
        artifact_fields = (self.artifact_id,)
        record_fields = (
            locator,
            self.source_id,
            self.instrument_id,
            self.metric_id,
            self.source_record_identifier,
        )
        version_fields = (
            self.observation_id,
            self.historical_vintage_claim_id,
            self.historical_vintage_content_hash,
            self.target_release_record_key,
        )
        allowed_states = _ALLOWED_BINDING_STATES_BY_SCOPE[self.subject_scope]
        if any(item not in allowed_states for item in self.binding_states):
            raise EvidenceContractError("binding state is incompatible with evidence subject scope")
        if rank == 0:
            if any(item is not None for item in release_fields + artifact_fields + record_fields + version_fields):
                raise EvidenceContractError("UNBOUND binding cannot carry narrowed subject fields")
            if self.binding_states not in (("SEMANTIC_VALUE_MATCH_ONLY",), ("UNBOUND",)):
                raise EvidenceContractError("UNBOUND scope requires an unbound or semantic-only state")
        elif rank == 1:
            if self.source_release_id is None or "SOURCE_RELEASE_BOUND" not in self.binding_states:
                raise EvidenceContractError("SOURCE_RELEASE_ONLY requires explicit release binding")
            if any(item is not None for item in artifact_fields + record_fields + version_fields):
                raise EvidenceContractError("source-release binding cannot imply attachment or record binding")
        elif rank == 2:
            if (
                self.source_release_id is None
                or self.artifact_id is None
                or "SOURCE_RELEASE_BOUND" not in self.binding_states
                or "OFFICIAL_ATTACHMENT_BOUND" not in self.binding_states
            ):
                raise EvidenceContractError("ATTACHMENT_BOUND requires explicit release and attachment states")
            if any(item is not None for item in record_fields + version_fields):
                raise EvidenceContractError("attachment binding cannot imply record binding")
        else:
            if (
                self.source_release_id is None
                or self.artifact_id is None
                or any(item is None for item in record_fields)
                or "SOURCE_RELEASE_BOUND" not in self.binding_states
                or "OFFICIAL_ATTACHMENT_BOUND" not in self.binding_states
                or "OFFICIAL_RECORD_VALUE_BOUND" not in self.binding_states
            ):
                raise EvidenceContractError("record scope requires explicit release, attachment, and record binding")
            if rank == 3 and any(item is not None for item in version_fields):
                raise EvidenceContractError("record binding does not imply record-version binding")
            if rank == 4 and any(item is None for item in version_fields):
                raise EvidenceContractError("RECORD_VERSION_BOUND requires explicit observation and vintage IDs")
        if "EXACT_ARTIFACT_HASH_BOUND" in self.binding_states and (
            self.artifact_id is None or self.expected_artifact_hash is None
        ):
            raise EvidenceContractError("exact artifact binding requires artifact ID and expected hash")
        if "UNBOUND" in self.binding_states and len(self.binding_states) != 1:
            raise EvidenceContractError("UNBOUND cannot be combined with other binding states")

    def identity_projection(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "subject_scope": self.subject_scope,
            "source_release_id": self.source_release_id,
            "artifact_id": self.artifact_id,
            "record_locator": self.record_locator,
            "source_id": self.source_id,
            "instrument_id": self.instrument_id,
            "metric_id": self.metric_id,
            "source_record_identifier": self.source_record_identifier,
            "observation_id": self.observation_id,
            "historical_vintage_claim_id": self.historical_vintage_claim_id,
            "target_release_record_key": self.target_release_record_key,
        }

    @property
    def binding_id(self) -> str:
        return _contract_hash("EVIDENCE_SUBJECT_BINDING_ID", self.identity_projection())

    def content_projection(self) -> dict[str, Any]:
        return {
            **self.identity_projection(),
            "binding_states": self.binding_states,
            "expected_artifact_hash": self.expected_artifact_hash,
            "historical_vintage_content_hash": self.historical_vintage_content_hash,
        }

    @property
    def content_hash(self) -> str:
        return _contract_hash("EVIDENCE_SUBJECT_BINDING_CONTENT", self.content_projection())


def validate_subject_binding(
    binding: EvidenceSubjectBinding,
    *,
    claim: EvidenceClaim,
    release: SourceRelease,
    artifact: EvidenceArtifact | None,
) -> None:
    """Validate a requested narrowing; constructors never promote it implicitly."""

    if type(binding) is not EvidenceSubjectBinding:
        raise EvidenceContractError("binding must be an exact EvidenceSubjectBinding")
    if type(claim) is not EvidenceClaim or type(release) is not SourceRelease:
        raise EvidenceContractError("claim and release must use exact contract types")
    if binding.claim_id != claim.claim_id:
        raise EvidenceContractError("binding claim does not match evidence claim")
    if claim.source_release_id != release.source_release_id:
        raise EvidenceContractError("claim source release does not match supplied release")
    if _scope_rank(binding.subject_scope) < _scope_rank(claim.subject_scope):
        raise EvidenceContractError("binding cannot be broader than the claim's declared subject scope")
    if binding.source_release_id is not None and binding.source_release_id != release.source_release_id:
        raise EvidenceContractError("binding source release mismatch")
    if binding.artifact_id is not None:
        if artifact is None or type(artifact) is not EvidenceArtifact:
            raise EvidenceContractError("attachment binding requires the exact supplied artifact")
        if artifact.source_release_id != release.source_release_id:
            raise EvidenceContractError("artifact source release mismatch")
        if binding.artifact_id != artifact.artifact_id or claim.artifact_id != artifact.artifact_id:
            raise EvidenceContractError("artifact identity mismatch")
        if "EXACT_ARTIFACT_HASH_BOUND" in binding.binding_states:
            if artifact.payload_hash is None or artifact.payload_hash != binding.expected_artifact_hash:
                raise EvidenceContractError("artifact payload hash does not match exact binding")
    if _scope_rank(binding.subject_scope) >= _scope_rank("RECORD_BOUND") and (
        claim.safe_locator != binding.record_locator
    ):
        raise EvidenceContractError("record claim locator does not match explicit record binding")


def validate_binding_consistency(bindings: Sequence[EvidenceSubjectBinding]) -> None:
    """Reject one binding identity resolving to contradictory canonical content."""

    content_by_id: dict[str, str] = {}
    for binding in tuple(bindings):
        if type(binding) is not EvidenceSubjectBinding:
            raise EvidenceContractError(
                "bindings must contain exact EvidenceSubjectBinding values"
            )
        previous = content_by_id.setdefault(binding.binding_id, binding.content_hash)
        if previous != binding.content_hash:
            raise EvidenceContractError(
                "same binding ID has contradictory canonical content"
            )


def _require_exact_binding_member(
    binding: EvidenceSubjectBinding,
    bindings: Sequence[EvidenceSubjectBinding],
) -> None:
    values = tuple(bindings)
    validate_binding_consistency(values)
    matches = [item for item in values if item.binding_id == binding.binding_id]
    if not matches:
        raise EvidenceContractError("supplied binding is absent from validated collection")
    if any(item.content_hash != binding.content_hash for item in matches):
        raise EvidenceContractError(
            "supplied binding content does not match validated collection"
        )


@dataclass(frozen=True, slots=True)
class HistoricalVintageClaim:
    observation_id: str
    source_id: str
    instrument_id: str
    metric_id: str
    source_record_identifier: str
    source_period: Mapping[str, Any]
    source_release_id: str
    release_record_key: str
    semantic_value: Decimal
    unit: str
    currency: str
    record_locator: Mapping[str, Any]
    evidence_claim_ids: tuple[str, ...]
    evidence_claim_content_hashes: tuple[str, ...]
    binding_ids: tuple[str, ...]
    binding_content_hashes: tuple[str, ...]
    temporal_bound: TemporalBound
    evidence_authority: str
    transformation_version: str
    parent_vintage_claim_id: str | None = None

    def __post_init__(self) -> None:
        _require_sha256(self.observation_id, "observation_id")
        _require_sha256(self.source_release_id, "source_release_id")
        for name in (
            "source_id",
            "instrument_id",
            "metric_id",
            "source_record_identifier",
            "release_record_key",
            "unit",
            "currency",
            "transformation_version",
        ):
            _require_non_empty(getattr(self, name), name)
        if self.transformation_version.lower() == "latest":
            raise EvidenceContractError("implicit/latest transformation version is forbidden")
        period = _immutable_mapping(self.source_period, "source_period")
        _validate_period(period)
        object.__setattr__(self, "source_period", period)
        _require_decimal(self.semantic_value, "semantic_value")
        locator = _immutable_mapping(self.record_locator, "record_locator")
        _validate_record_locator(locator)
        object.__setattr__(self, "record_locator", locator)
        for name in (
            "evidence_claim_ids",
            "evidence_claim_content_hashes",
            "binding_ids",
            "binding_content_hashes",
        ):
            values = getattr(self, name)
            if not isinstance(values, tuple) or not values:
                raise EvidenceContractError(f"{name} must be a non-empty tuple")
            if len(values) != len(set(values)):
                raise EvidenceContractError(f"{name} must not contain duplicates")
            for value in values:
                _require_sha256(value, name)
        if len(self.evidence_claim_ids) != len(self.evidence_claim_content_hashes):
            raise EvidenceContractError("evidence claim IDs and content hashes must align")
        if len(self.binding_ids) != len(self.binding_content_hashes):
            raise EvidenceContractError("binding IDs and content hashes must align")
        if type(self.temporal_bound) is not TemporalBound:
            raise EvidenceContractError("temporal_bound must be an exact TemporalBound")
        if self.evidence_authority not in EVIDENCE_AUTHORITIES:
            raise EvidenceContractError("unsupported evidence authority")
        if self.parent_vintage_claim_id is not None:
            _require_sha256(self.parent_vintage_claim_id, "parent_vintage_claim_id")
            if self.parent_vintage_claim_id == self.vintage_claim_id:
                raise EvidenceContractError("historical vintage cannot be its own parent")

    def identity_projection(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "instrument_id": self.instrument_id,
            "metric_id": self.metric_id,
            "source_record_identifier": self.source_record_identifier,
            "source_period": self.source_period,
            "source_release_id": self.source_release_id,
            "release_record_key": self.release_record_key,
        }

    @property
    def vintage_claim_id(self) -> str:
        return _contract_hash("HISTORICAL_VINTAGE_CLAIM_ID", self.identity_projection())

    def content_projection(self) -> dict[str, Any]:
        return {
            **self.identity_projection(),
            "observation_id": self.observation_id,
            "semantic_value": self.semantic_value,
            "unit": self.unit,
            "currency": self.currency,
            "record_locator": self.record_locator,
            "evidence_claim_ids": self.evidence_claim_ids,
            "evidence_claim_content_hashes": self.evidence_claim_content_hashes,
            "binding_ids": self.binding_ids,
            "binding_content_hashes": self.binding_content_hashes,
            "temporal_bound": self.temporal_bound.projection(),
            "evidence_authority": self.evidence_authority,
            "transformation_version": self.transformation_version,
            "parent_vintage_claim_id": self.parent_vintage_claim_id,
        }

    @property
    def content_hash(self) -> str:
        return _contract_hash("HISTORICAL_VINTAGE_CLAIM_CONTENT", self.content_projection())


def _temporal_order_key(bound: TemporalBound) -> tuple[str, datetime | date]:
    if bound.bound_type == "EXACT" and bound.precision == "EXACT_TIMESTAMP":
        assert bound.normalized_start is not None
        return ("EXACT_TIMESTAMP", parse_rfc3339(bound.normalized_start))
    if bound.bound_type == "BY_DATE" and bound.precision == "DATE_ONLY":
        assert bound.normalized_start is not None
        return ("DATE_ONLY", parse_date(bound.normalized_start))
    raise EvidenceContractError("revision temporal bounds cannot be safely ordered")


def _validated_lineage_chain(
    vintages: Sequence[HistoricalVintageClaim],
) -> tuple[HistoricalVintageClaim, ...]:
    values = tuple(vintages)
    if not values:
        return ()
    by_id: dict[str, HistoricalVintageClaim] = {}
    for item in values:
        if type(item) is not HistoricalVintageClaim:
            raise EvidenceContractError("lineage must contain exact HistoricalVintageClaim values")
        if item.vintage_claim_id in by_id:
            raise EvidenceContractError("duplicate historical vintage identity")
        by_id[item.vintage_claim_id] = item
    children: dict[str, list[HistoricalVintageClaim]] = {
        item.vintage_claim_id: [] for item in values
    }
    roots: list[HistoricalVintageClaim] = []
    for item in values:
        parent_id = item.parent_vintage_claim_id
        if parent_id is None:
            roots.append(item)
            continue
        parent = by_id.get(parent_id)
        if parent is None:
            raise EvidenceContractError("historical vintage parent does not exist")
        if parent.observation_id != item.observation_id:
            raise EvidenceContractError("historical vintage parent belongs to another Observation")
        children[parent_id].append(item)

    if len({item.observation_id for item in values}) != 1:
        raise EvidenceContractError("revision lineage must contain exactly one Observation")

    for start in values:
        visited: set[str] = set()
        cursor: HistoricalVintageClaim | None = start
        while cursor is not None:
            if cursor.vintage_claim_id in visited:
                raise EvidenceContractError("historical vintage revision lineage contains a cycle")
            visited.add(cursor.vintage_claim_id)
            cursor = (
                None
                if cursor.parent_vintage_claim_id is None
                else by_id[cursor.parent_vintage_claim_id]
            )

    if len(roots) != 1:
        raise EvidenceContractError("revision lineage has disconnected competing roots")
    if any(len(items) > 1 for items in children.values()):
        raise EvidenceContractError("revision lineage has ambiguous competing sibling revisions")
    for item in values:
        if item.parent_vintage_claim_id is None:
            continue
        parent = by_id[item.parent_vintage_claim_id]
        parent_key = _temporal_order_key(parent.temporal_bound)
        child_key = _temporal_order_key(item.temporal_bound)
        if parent_key[0] != child_key[0]:
            raise EvidenceContractError("revision temporal bounds use incomparable precision")
        if (
            parent_key[0] == "DATE_ONLY"
            and parent.temporal_bound.source_timezone != item.temporal_bound.source_timezone
        ):
            raise EvidenceContractError("revision date bounds use conflicting timezone authorities")
        if child_key[1] <= parent_key[1]:
            raise EvidenceContractError("child revision availability must be strictly later than parent")
    chain: list[HistoricalVintageClaim] = []
    cursor: HistoricalVintageClaim | None = roots[0]
    while cursor is not None:
        chain.append(cursor)
        next_items = children[cursor.vintage_claim_id]
        cursor = next_items[0] if next_items else None
    if len(chain) != len(values):
        raise EvidenceContractError("revision lineage is not one explicit supersession path")
    return tuple(chain)


def validate_revision_lineage(vintages: Sequence[HistoricalVintageClaim]) -> None:
    _validated_lineage_chain(vintages)


def select_visible_vintage(
    vintages: Sequence[HistoricalVintageClaim],
    *,
    cutoff_at: str,
    mode: str,
    source_timezone: str | None = None,
    policy_version: str | None = None,
) -> HistoricalVintageClaim | None:
    chain = _validated_lineage_chain(vintages)
    selected: HistoricalVintageClaim | None = None
    invisible_seen = False
    for item in chain:
        decision = evaluate_temporal_eligibility(
            item.temporal_bound,
            cutoff_at=cutoff_at,
            mode=mode,
            source_timezone=source_timezone,
            policy_version=policy_version,
        )
        if decision.eligible:
            if invisible_seen:
                raise EvidenceContractError("visible revision follows an invisible parent")
            selected = item
        else:
            invisible_seen = True
    return selected


@dataclass(frozen=True, slots=True)
class MaterializationContract:
    mode: str
    policy_version: str
    transformation_version: str
    source_timezone: str | None = None

    def __post_init__(self) -> None:
        _require_policy_compatibility(self.mode, self.policy_version)
        _require_non_empty(self.transformation_version, "transformation_version")
        if self.transformation_version.lower() == "latest":
            raise EvidenceContractError("implicit/latest transformation version is forbidden")
        _require_optional_non_empty(self.source_timezone, "source_timezone")
        if self.source_timezone is not None:
            try:
                ZoneInfo(self.source_timezone)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise EvidenceContractError("source_timezone must be an installed IANA timezone") from exc
        if self.mode == "DAY_GRANULAR_PIT" and self.source_timezone is None:
            raise EvidenceContractError("day-granular materialization requires a source timezone")


@dataclass(frozen=True, slots=True)
class ObservationVersionCandidate:
    observation_id: str
    source_release_id: str
    source_version_or_release_key: str
    stable_version_key: str
    artifact_payload_hash: str
    transformation_version: str
    parent_vintage_claim_id: str | None
    historical_vintage_claim_id: str
    evidence_claim_id: str
    binding_id: str
    binding_content_hash: str
    semantic_data: Mapping[str, Any]
    temporal_bound: TemporalBound
    policy_decision: TemporalEligibilityDecision
    source_factual_revision_available_at: str | None
    _construction_authority: InitVar[object | None] = None

    def __post_init__(self, _construction_authority: object | None) -> None:
        if _construction_authority is not _CANDIDATE_CONSTRUCTION_AUTHORITY:
            raise EvidenceContractError(
                "candidate must be created by explicit validated materialization"
            )
        for name in (
            "observation_id",
            "source_release_id",
            "artifact_payload_hash",
            "historical_vintage_claim_id",
            "evidence_claim_id",
            "binding_id",
            "binding_content_hash",
        ):
            _require_sha256(getattr(self, name), name)
        if self.parent_vintage_claim_id is not None:
            _require_sha256(self.parent_vintage_claim_id, "parent_vintage_claim_id")
        for name in (
            "source_version_or_release_key",
            "stable_version_key",
            "transformation_version",
        ):
            _require_non_empty(getattr(self, name), name)
        object.__setattr__(
            self,
            "semantic_data",
            _immutable_mapping(self.semantic_data, "semantic_data"),
        )
        if type(self.temporal_bound) is not TemporalBound:
            raise EvidenceContractError("candidate temporal bound type mismatch")
        if type(self.policy_decision) is not TemporalEligibilityDecision or not self.policy_decision.eligible:
            raise EvidenceContractError("candidate requires an eligible exact policy decision")
        _require_policy_compatibility(
            self.policy_decision.mode,
            self.policy_decision.policy_version,
        )
        if self.source_factual_revision_available_at is not None:
            object.__setattr__(
                self,
                "source_factual_revision_available_at",
                canonical_timestamp(self.source_factual_revision_available_at),
            )
        if self.temporal_bound.precision != "EXACT_TIMESTAMP" and self.source_factual_revision_available_at is not None:
            raise EvidenceContractError("non-exact evidence cannot populate factual revision_available_at")
        if self.policy_decision.derived and self.source_factual_revision_available_at is not None:
            raise EvidenceContractError("policy-derived boundary cannot be stored as source factual time")
        if self.temporal_bound.precision == "EXACT_TIMESTAMP":
            if (
                self.temporal_bound.bound_type != "EXACT"
                or self.policy_decision.derived
                or self.policy_decision.source_factual_timestamp
                != self.temporal_bound.normalized_start
                or self.source_factual_revision_available_at
                != self.temporal_bound.normalized_start
            ):
                raise EvidenceContractError("candidate exact-time decision contradicts source evidence")
        elif self.temporal_bound.precision == "DATE_ONLY":
            decision_timezone = self.policy_decision.source_timezone
            expected_local_date: str | None = None
            expected_boundary: str | None = None
            if decision_timezone is not None and self.temporal_bound.normalized_start is not None:
                if (
                    self.temporal_bound.source_timezone is not None
                    and self.temporal_bound.source_timezone != decision_timezone
                ):
                    raise EvidenceContractError("candidate uses conflicting source timezone authorities")
                next_date = parse_date(self.temporal_bound.normalized_start) + timedelta(days=1)
                expected_local_date = next_date.isoformat()
                expected_boundary = canonical_timestamp(
                    datetime.combine(next_date, time.min, tzinfo=ZoneInfo(decision_timezone))
                )
            if (
                self.temporal_bound.bound_type != "BY_DATE"
                or self.policy_decision.mode != "DAY_GRANULAR_PIT"
                or not self.policy_decision.derived
                or self.policy_decision.source_factual_timestamp is not None
                or self.policy_decision.eligible_source_local_date != expected_local_date
                or self.policy_decision.derived_comparison_boundary_at != expected_boundary
            ):
                raise EvidenceContractError("candidate day-granular decision contradicts source evidence")
        else:
            raise EvidenceContractError("candidate temporal evidence is not materializable")

    def content_projection(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "source_release_id": self.source_release_id,
            "source_version_or_release_key": self.source_version_or_release_key,
            "stable_version_key": self.stable_version_key,
            "artifact_payload_hash": self.artifact_payload_hash,
            "transformation_version": self.transformation_version,
            "parent_vintage_claim_id": self.parent_vintage_claim_id,
            "historical_vintage_claim_id": self.historical_vintage_claim_id,
            "evidence_claim_id": self.evidence_claim_id,
            "binding_id": self.binding_id,
            "binding_content_hash": self.binding_content_hash,
            "semantic_data": self.semantic_data,
            "temporal_bound": self.temporal_bound.projection(),
            "policy_decision": {
                "mode": self.policy_decision.mode,
                "policy_version": self.policy_decision.policy_version,
                "eligible": self.policy_decision.eligible,
                "reason": self.policy_decision.reason,
                "source_factual_timestamp": self.policy_decision.source_factual_timestamp,
                "eligible_source_local_date": self.policy_decision.eligible_source_local_date,
                "derived_comparison_boundary_at": self.policy_decision.derived_comparison_boundary_at,
                "derived": self.policy_decision.derived,
                "source_timezone": self.policy_decision.source_timezone,
            },
            "source_factual_revision_available_at": self.source_factual_revision_available_at,
        }

    @property
    def candidate_hash(self) -> str:
        return _contract_hash("OBSERVATION_VERSION_CANDIDATE", self.content_projection())


def materialize_observation_version_candidate(
    vintage: HistoricalVintageClaim,
    *,
    claim: EvidenceClaim,
    binding: EvidenceSubjectBinding,
    release: SourceRelease,
    artifact: EvidenceArtifact,
    all_vintages: Sequence[HistoricalVintageClaim],
    all_artifacts: Sequence[EvidenceArtifact],
    all_bindings: Sequence[EvidenceSubjectBinding],
    cutoff_at: str,
    contract: MaterializationContract,
) -> ObservationVersionCandidate:
    """Explicitly validate and project one candidate; never persist or mutate."""

    for value, expected, name in (
        (vintage, HistoricalVintageClaim, "vintage"),
        (claim, EvidenceClaim, "claim"),
        (binding, EvidenceSubjectBinding, "binding"),
        (release, SourceRelease, "release"),
        (artifact, EvidenceArtifact, "artifact"),
        (contract, MaterializationContract, "contract"),
    ):
        if type(value) is not expected:
            raise EvidenceContractError(f"{name} must use the exact contract type")
    artifact_values = tuple(all_artifacts)
    vintage_values = tuple(all_vintages)
    _require_exact_artifact_member(artifact, artifact_values)
    _require_exact_binding_member(binding, all_bindings)
    validate_revision_lineage(vintage_values)
    validate_subject_binding(binding, claim=claim, release=release, artifact=artifact)
    matching_vintages = [
        item for item in vintage_values if item.vintage_claim_id == vintage.vintage_claim_id
    ]
    if not matching_vintages:
        raise EvidenceContractError("materialized vintage is absent from validated lineage")
    if matching_vintages[0].content_hash != vintage.content_hash:
        raise EvidenceContractError("materialized vintage content does not match validated lineage")
    if vintage.source_release_id != release.source_release_id:
        raise EvidenceContractError("vintage source release mismatch")
    claim_references = dict(
        zip(
            vintage.evidence_claim_ids,
            vintage.evidence_claim_content_hashes,
            strict=True,
        )
    )
    if claim_references.get(claim.claim_id) != claim.content_hash:
        raise EvidenceContractError("evidence claim content does not match validated vintage reference")
    if binding.subject_scope not in {"RECORD_BOUND", "RECORD_VERSION_BOUND"}:
        raise EvidenceContractError("materialization requires explicit record binding")
    if binding.subject_scope == "RECORD_BOUND":
        binding_references = dict(
            zip(vintage.binding_ids, vintage.binding_content_hashes, strict=True)
        )
        if binding_references.get(binding.binding_id) != binding.content_hash:
            raise EvidenceContractError("binding content does not match validated vintage reference")
    else:
        if (
            binding.observation_id != vintage.observation_id
            or binding.historical_vintage_claim_id != vintage.vintage_claim_id
            or binding.historical_vintage_content_hash != vintage.content_hash
            or binding.target_release_record_key != vintage.release_record_key
        ):
            raise EvidenceContractError("record-version binding target does not match historical vintage")
    required_states = {
        "SOURCE_RELEASE_BOUND",
        "OFFICIAL_ATTACHMENT_BOUND",
        "OFFICIAL_RECORD_VALUE_BOUND",
        "EXACT_ARTIFACT_HASH_BOUND",
    }
    if not required_states.issubset(binding.binding_states):
        raise EvidenceContractError("materialization binding lacks required objective facets")
    if artifact.payload_hash is None or binding.expected_artifact_hash != artifact.payload_hash:
        raise EvidenceContractError("materialization requires the exact supplied artifact hash")
    if (
        binding.source_id,
        binding.instrument_id,
        binding.metric_id,
        binding.source_record_identifier,
        binding.record_locator,
    ) != (
        vintage.source_id,
        vintage.instrument_id,
        vintage.metric_id,
        vintage.source_record_identifier,
        vintage.record_locator,
    ):
        raise EvidenceContractError("record binding does not match historical vintage subject")
    if (
        claim.claimed_value,
        claim.unit,
        claim.currency,
        claim.claimed_period,
        claim.safe_locator,
        claim.temporal_bound,
    ) != (
        vintage.semantic_value,
        vintage.unit,
        vintage.currency,
        vintage.source_period,
        vintage.record_locator,
        vintage.temporal_bound,
    ):
        raise EvidenceContractError("evidence claim content does not match historical vintage")
    if contract.transformation_version != vintage.transformation_version or (
        claim.transformation_version != vintage.transformation_version
    ):
        raise EvidenceContractError("deterministic transformation version mismatch")
    visible = select_visible_vintage(
        vintage_values,
        cutoff_at=cutoff_at,
        mode=contract.mode,
        source_timezone=contract.source_timezone,
        policy_version=contract.policy_version,
    )
    if visible is None or visible.vintage_claim_id != vintage.vintage_claim_id:
        raise EvidenceContractError("historical vintage is not the unique visible candidate at cutoff")
    decision = evaluate_temporal_eligibility(
        vintage.temporal_bound,
        cutoff_at=cutoff_at,
        mode=contract.mode,
        source_timezone=contract.source_timezone,
        policy_version=contract.policy_version,
    )
    if not decision.eligible:
        raise EvidenceContractError("historical vintage is not eligible under materialization policy")
    factual_revision = (
        decision.source_factual_timestamp
        if vintage.temporal_bound.precision == "EXACT_TIMESTAMP"
        else None
    )
    return ObservationVersionCandidate(
        observation_id=vintage.observation_id,
        source_release_id=vintage.source_release_id,
        source_version_or_release_key=vintage.release_record_key,
        stable_version_key=vintage.vintage_claim_id,
        artifact_payload_hash=artifact.payload_hash,
        transformation_version=vintage.transformation_version,
        parent_vintage_claim_id=vintage.parent_vintage_claim_id,
        historical_vintage_claim_id=vintage.vintage_claim_id,
        evidence_claim_id=claim.claim_id,
        binding_id=binding.binding_id,
        binding_content_hash=binding.content_hash,
        semantic_data={
            "value": vintage.semantic_value,
            "unit": vintage.unit,
            "currency": vintage.currency,
        },
        temporal_bound=vintage.temporal_bound,
        policy_decision=decision,
        source_factual_revision_available_at=factual_revision,
        _construction_authority=_CANDIDATE_CONSTRUCTION_AUTHORITY,
    )
