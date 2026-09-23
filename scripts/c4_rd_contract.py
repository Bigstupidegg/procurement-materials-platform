"""C4-RD-4.1 source-neutral contracts, canonicalization, and immutable models.

This module is intentionally local-only.  It represents and validates contract
objects; it does not collect data, decide readiness, export, or persist data.

The supported trust boundary is ordinary construction and documented object APIs.
It is not a hostile-code sandbox and makes no claim that pure-Python objects resist
arbitrary same-process introspection, monkeypatching, or interpreter manipulation.
"""

from __future__ import annotations

from collections.abc import Mapping as ABCMapping
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
import hashlib
import json
import re
import unicodedata
from types import MappingProxyType
from typing import Any, Mapping, Sequence


CONTRACT_VERSION = "1.0.0"
CANONICAL_JSON_PROFILE = "C4_CANONICAL_JSON_V1@1.0.0"
HASH_PROFILE = "C4_HASH_PROFILE_V1@1.0.0"
RAW_BYTES_HASH_PROFILE = "C4_RAW_BYTES_HASH_V1@1.0.0"
EXCLUSION_CATALOG_VERSION = "1.1.0"

TRUST_STATES = (
    "REAL_ORIGIN_UNVERIFIED", "REAL_ORIGIN_VERIFIED", "SHADOW_UNRESOLVED",
    "LEGACY_UNVERIFIED", "TRUST_REJECTED",
)
QUALITY_STATUSES = ("NOT_ASSESSED", "PASS", "WARNING", "FAIL", "CONFLICT")
READINESS_STATES = (
    "REAL_RAW_UNVERIFIED", "REAL_SOURCE_DATE_VERIFIED", "REAL_AVAILABILITY_VERIFIED",
    "REAL_RESEARCH_READY", "REAL_BACKTEST_READY", "QUARANTINED", "REJECTED",
)
ELIGIBILITY_STATES = ("NOT_EVALUATED", "ELIGIBLE", "INELIGIBLE")
SOURCE_PERIOD_TYPES = ("DAILY_MARKET", "SNAPSHOT", "MONTH", "QUARTER", "OTHER_BOUNDED_PERIOD")
CALENDAR_ROLES = ("MARKET", "PUBLICATION", "LOCAL_OPERATIONAL", "SCHEDULER")
CALENDAR_ROLE_STATUSES = ("RESOLVED", "NOT_APPLICABLE", "UNVERIFIED", "CONFLICTING", "OUT_OF_RANGE")
AVAILABILITY_BASES = (
    "SOURCE_NATIVE_TIMESTAMP", "VERIFIED_ARCHIVE_TIMESTAMP", "VERIFIED_CHANNEL_TIMESTAMP",
    "FIRST_SUCCESSFUL_COLLECTION", "APPROVED_CONSERVATIVE_ESTIMATE", "UNVERIFIED",
)

EXCLUSION_REASON_CODES = (
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
)
STALE_REASON_ALIASES = frozenset({
    "AMBIGUOUS_SOURCE_MARKET_DATE", "SOURCE_AVAILABILITY_UNVERIFIED", "FUTURE_TIMESTAMP",
    "INCOMPLETE_LINEAGE", "STALE_SOURCE", "TARGET_HORIZON_NOT_ELAPSED",
    "DUPLICATE_OBSERVATION_ID", "INVALID_VALUE", "UNIT_UNAPPROVED", "CURRENCY_UNAPPROVED",
    "VINTAGE_NOT_PROVEN",
})

HASH_DOMAINS = frozenset({
    "OBSERVATION_ID", "OBSERVATION_CONTENT", "OBSERVATION_VERSION_ID",
    "OBSERVATION_VERSION_CONTENT", "ASSESSMENT_ID", "ASSESSMENT_CONTENT",
    "CALENDAR_CONTENT", "RULE_BUNDLE_CONTENT", "MANIFEST_CONTENT",
    "PIT_FEATURE_CONTENT", "PIT_DATASET_ROW_ID", "PIT_DATASET_ROW_CONTENT",
    "PREREGISTRATION_CONTENT",
})

SAFETY_FLAGS = MappingProxyType({
    "real_data_ingestion_allowed": False,
    "shadow_export_allowed": False,
    "research_dataset_allowed": False,
    "backtest_allowed": False,
    "production_allowed": False,
    "canonical_allowed": False,
    "deferred_allowed": False,
    "ml_allowed": False,
    "procurement_allowed": False,
})
PIT_ENABLED_SOURCES: tuple[str, ...] = ()
RESEARCH_ENABLED_SOURCES: tuple[str, ...] = ()
BACKTEST_ENABLED_SOURCES: tuple[str, ...] = ()
RD3_OPEN_BLOCKERS = (
    "RD3-LME-001", "RD3-LME-002", "RD3-LME-003",
    "RD3-SMM-001", "RD3-SMM-002", "RD3-SMM-003", "RD3-SMM-004",
    "RD3-YAHOO-001", "RD3-YAHOO-002", "RD3-YAHOO-003", "RD3-YAHOO-004",
    "RD3-BZ-001", "RD3-WB-001", "RD3-WB-002", "RD3-WB-003",
)

_RFC3339 = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})T(?P<time>\d{2}:\d{2}:\d{2})"
    r"(?P<fraction>\.\d{1,6})?(?P<zone>Z|[+-]\d{2}:\d{2})$"
)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ContractError(ValueError):
    """Raised when a frozen C4-RD contract invariant is violated."""


class ImmutableMapping(tuple, ABCMapping[str, Any]):
    """Final-ish immutable Mapping stored entirely as recursively frozen pairs."""

    __slots__ = ()

    def __new__(cls, value: Mapping[str, Any]) -> "ImmutableMapping":
        if cls is not ImmutableMapping:
            raise ContractError("ImmutableMapping subclasses are forbidden")
        if not isinstance(value, Mapping):
            raise ContractError("semantic mapping input must implement Mapping")
        frozen: list[tuple[str, Any]] = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractError("semantic mapping keys must be strings")
            frozen.append((key, _freeze(item, field_name=key)))
        return tuple.__new__(cls, frozen)

    def __init_subclass__(cls, **_kwargs: Any) -> None:
        raise TypeError("ImmutableMapping cannot be subclassed")

    def __getitem__(self, key: str) -> Any:
        for item_key, item_value in tuple.__iter__(self):
            if item_key == key:
                return item_value
        raise KeyError(key)

    def __iter__(self):
        return (key for key, _value in tuple.__iter__(self))

    def __len__(self) -> int:
        return tuple.__len__(self)

    def __deepcopy__(self, _memo: dict[int, Any]) -> "ImmutableMapping":
        return self


def _freeze(value: Any, *, field_name: str | None = None) -> Any:
    if field_name is not None and field_name.endswith("_at") and isinstance(value, str):
        return canonical_timestamp(value)
    if isinstance(value, Enum):
        return _freeze(value.value, field_name=field_name)
    if isinstance(value, Mapping):
        copied: dict[str, Any] = {}
        try:
            items = tuple(value.items())
        except Exception as exc:
            raise ContractError("semantic mapping could not be snapshotted") from exc
        for key, item in items:
            if not isinstance(key, str):
                raise ContractError("semantic mapping keys must be strings")
            copied[key] = _freeze(item, field_name=key)
        return MappingProxyType(copied)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        raise ContractError("generic set-like semantic input is forbidden")
    if value is None or isinstance(value, (str, bool, int, Decimal, date, datetime)):
        return value
    if isinstance(value, float):
        raise ContractError("binary float is forbidden")
    raise ContractError(f"unsupported mutable or semantic type: {type(value).__name__}")


def _plain(value: Any) -> Any:
    """Return a schema-facing plain payload with explicit contract nulls."""
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _plain(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return canonical_timestamp(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _reject_surrogates(value: str) -> None:
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ContractError("invalid Unicode: lone surrogate")


def parse_rfc3339(value: str) -> datetime:
    if not isinstance(value, str) or not _RFC3339.fullmatch(value):
        raise ContractError("timestamp must be timezone-aware RFC3339 with 0-6 fractional digits")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError("invalid RFC3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError("naive datetime is forbidden")
    return parsed


def canonical_timestamp(value: str | datetime) -> str:
    parsed = parse_rfc3339(value) if isinstance(value, str) else value
    if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError("naive datetime is forbidden")
    utc = parsed.astimezone(timezone.utc)
    base = utc.strftime("%Y-%m-%dT%H:%M:%S")
    fraction = f"{utc.microsecond:06d}".rstrip("0")
    return f"{base}.{fraction}Z" if fraction else f"{base}Z"


def parse_date(value: str) -> date:
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise ContractError("date must use YYYY-MM-DD only")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ContractError("invalid calendar date") from exc


def validate_source_period(
    source_period_type: str,
    source_market_date: str | None,
    source_period_start_date: str | None,
    source_period_end_date: str | None,
) -> None:
    if source_period_type not in (
        "DAILY_MARKET", "SNAPSHOT", "MONTH", "QUARTER", "OTHER_BOUNDED_PERIOD",
    ):
        raise ContractError("unsupported source_period_type")
    market = parse_date(source_market_date) if source_market_date is not None else None
    start = parse_date(source_period_start_date) if source_period_start_date is not None else None
    end = parse_date(source_period_end_date) if source_period_end_date is not None else None
    if source_period_type in {"DAILY_MARKET", "SNAPSHOT"}:
        if market is None or start is not None or end is not None:
            raise ContractError(f"{source_period_type} requires source_market_date and null period bounds")
        return
    if market is not None or start is None or end is None:
        raise ContractError(f"{source_period_type} requires null source_market_date and bounded dates")
    if start > end:
        raise ContractError("source period start must not follow end")
    if source_period_type == "MONTH":
        if start.day != 1:
            raise ContractError("MONTH must begin on first calendar day")
        next_month = date(start.year + (start.month == 12), 1 if start.month == 12 else start.month + 1, 1)
        if end != date.fromordinal(next_month.toordinal() - 1):
            raise ContractError("MONTH must use exact calendar-month bounds")
    if source_period_type == "QUARTER":
        if start.month not in {1, 4, 7, 10} or start.day != 1:
            raise ContractError("QUARTER must begin on an exact quarter boundary")
        if start.month == 10:
            next_quarter = date(start.year + 1, 1, 1)
        else:
            next_quarter = date(start.year, start.month + 3, 1)
        if end != date.fromordinal(next_quarter.toordinal() - 1):
            raise ContractError("QUARTER must use exact calendar-quarter bounds")


def parse_json_strict(value: str | bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in items:
            if key in result:
                raise ContractError(f"duplicate raw JSON key: {key}")
            result[key] = item
        return result

    try:
        return json.loads(value, object_pairs_hook=pairs, parse_float=Decimal, parse_constant=lambda x: (_ for _ in ()).throw(ContractError(f"invalid number: {x}")))
    except UnicodeDecodeError as exc:
        raise ContractError("JSON must be valid UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ContractError("invalid JSON") from exc


def _normalize(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        value = _plain(value)
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, datetime):
        return canonical_timestamp(value)
    if isinstance(value, date):
        return value.isoformat()
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        raise ContractError("binary float is forbidden")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ContractError("non-finite Decimal is forbidden")
        return value
    if isinstance(value, str):
        _reject_surrogates(value)
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for raw_key, item in value.items():
            if not isinstance(raw_key, str):
                raise ContractError("JSON object keys must be strings")
            _reject_surrogates(raw_key)
            key = unicodedata.normalize("NFC", raw_key)
            if key in normalized:
                raise ContractError(f"key collision after NFC normalization: {key}")
            normalized[key] = _normalize(item)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    raise ContractError(f"unsupported canonical type: {type(value).__name__}")


def _decimal_text(value: Decimal) -> str:
    if value.is_zero():
        return "0"
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _encode_canonical(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        return "[" + ",".join(_encode_canonical(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(
            f"{_encode_canonical(key)}:{_encode_canonical(value[key])}" for key in sorted(value)
        ) + "}"
    raise ContractError(f"unsupported normalized type: {type(value).__name__}")


def canonical_json_bytes(value: Any, *, profile: str = CANONICAL_JSON_PROFILE) -> bytes:
    if profile != "C4_CANONICAL_JSON_V1@1.0.0":
        raise ContractError("unsupported canonical JSON profile")
    return _encode_canonical(_normalize(value)).encode("utf-8")


def raw_payload_hash(payload: bytes, *, profile: str = RAW_BYTES_HASH_PROFILE) -> str:
    if profile != "C4_RAW_BYTES_HASH_V1@1.0.0":
        raise ContractError("unsupported raw-bytes hash profile")
    if not isinstance(payload, bytes):
        raise ContractError("raw payload must be exact bytes")
    return hashlib.sha256(payload).hexdigest()


def canonical_hash(
    domain: str,
    value: Any,
    *,
    contract_version: str = CONTRACT_VERSION,
    canonical_profile: str = CANONICAL_JSON_PROFILE,
    hash_profile: str = HASH_PROFILE,
) -> str:
    if domain not in {
        "OBSERVATION_ID", "OBSERVATION_CONTENT", "OBSERVATION_VERSION_ID",
        "OBSERVATION_VERSION_CONTENT", "ASSESSMENT_ID", "ASSESSMENT_CONTENT",
        "CALENDAR_CONTENT", "RULE_BUNDLE_CONTENT", "MANIFEST_CONTENT",
        "PIT_FEATURE_CONTENT", "PIT_DATASET_ROW_ID", "PIT_DATASET_ROW_CONTENT",
        "PREREGISTRATION_CONTENT",
    }:
        raise ContractError("unknown hash domain")
    if contract_version.split(".", 1)[0] != "1":
        raise ContractError("unsupported contract major version")
    if canonical_profile != "C4_CANONICAL_JSON_V1@1.0.0" or hash_profile != "C4_HASH_PROFILE_V1@1.0.0":
        raise ContractError("unsupported canonical/hash profile")
    prefix = (
        b"C4RD\0" + domain.encode("ascii") + b"\0CONTRACT=" + contract_version.encode("ascii")
        + b"\0CANON=" + canonical_profile.encode("ascii") + b"\0HASH=" + hash_profile.encode("ascii") + b"\0"
    )
    return hashlib.sha256(prefix + canonical_json_bytes(value, profile=canonical_profile)).hexdigest()


def _require_sha256(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ContractError(f"{field_name} must be lowercase SHA-256 hex")


def _require_non_empty(value: Any, field_name: str) -> None:
    if not isinstance(value, str) or value == "":
        raise ContractError(f"{field_name} must be a non-empty string")


def _freeze_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{field_name} must be a mapping")
    frozen = _freeze(value)
    if not isinstance(frozen, Mapping):
        raise ContractError(f"{field_name} must be a mapping")
    return frozen


def _validate_authorizations(value: Mapping[str, Any]) -> None:
    authorization_fields = (
        "real_data_ingestion_allowed",
        "shadow_export_allowed",
        "research_dataset_allowed",
        "backtest_allowed",
        "production_allowed",
        "canonical_allowed",
        "deferred_allowed",
        "ml_allowed",
        "procurement_allowed",
    )
    if len(value) != len(authorization_fields) or set(value) != set(authorization_fields):
        raise ContractError("manifest authorizations must contain the exact nine frozen fields")
    if any(type(value[name]) is not bool or value[name] is not False for name in authorization_fields):
        raise ContractError("every manifest authorization must be strict boolean false")


@dataclass(frozen=True, slots=True)
class CalendarAssignment:
    subject_id: str
    role: str
    status: str
    calendar_reference_id: str | None = None
    calendar_version: str | None = None
    calendar_hash: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.subject_id, "subject_id")
        if self.role not in ("MARKET", "PUBLICATION", "LOCAL_OPERATIONAL", "SCHEDULER") or self.status not in (
            "RESOLVED", "NOT_APPLICABLE", "UNVERIFIED", "CONFLICTING", "OUT_OF_RANGE",
        ):
            raise ContractError("invalid calendar role or status")
        refs = (self.calendar_reference_id, self.calendar_version, self.calendar_hash)
        if self.status == "RESOLVED" and any(item is None for item in refs):
            raise ContractError("RESOLVED calendar assignment requires all reference fields")
        if self.status == "NOT_APPLICABLE" and any(item is not None for item in refs):
            raise ContractError("NOT_APPLICABLE calendar assignment requires null references")
        if self.calendar_reference_id is not None:
            _require_non_empty(self.calendar_reference_id, "calendar_reference_id")
        if self.calendar_version is not None:
            _require_non_empty(self.calendar_version, "calendar_version")
        if self.calendar_hash is not None:
            _require_sha256(self.calendar_hash, "calendar_hash")

    def identity_projection(self) -> dict[str, Any]:
        return {"subject_id": self.subject_id, "calendar_role": self.role}

    def content_projection(self) -> dict[str, Any]:
        return _plain(self)


def normalize_calendar_assignments(items: Sequence[CalendarAssignment]) -> tuple[CalendarAssignment, ...]:
    assignments = tuple(items)
    if any(type(item) is not CalendarAssignment for item in assignments):
        raise ContractError("calendar assignments must be validated CalendarAssignment values")
    roles = ("MARKET", "PUBLICATION", "LOCAL_OPERATIONAL", "SCHEDULER")
    by_role = {item.role: item for item in assignments}
    if len(assignments) != 4 or len(by_role) != 4 or set(by_role) != set(roles):
        raise ContractError("exactly one assignment for each of the four calendar roles is required")
    return tuple(by_role[role] for role in roles)


@dataclass(frozen=True, slots=True)
class Observation:
    source_id: str
    source_record_identifier: str
    metric_id: str
    instrument_id: str
    source_period_type: str
    source_market_date: str | None
    source_period_start_date: str | None
    source_period_end_date: str | None
    calendar_assignments: tuple[CalendarAssignment, ...]
    data_origin: str = "SYNTHETIC_FIXTURE"
    operational_status: str = "SYNTHETIC_NON_OPERATIONAL"
    scheduler_execution_at: str | None = None
    local_business_date: str | None = None
    source_business_date: str | None = None
    scheduler_business_date: str | None = None
    source_publication_at: str | None = None
    collected_at: str | None = None
    observed_at: str | None = None
    source_available_at: str | None = None
    channel_available_at: str | None = None
    created_at: str | None = None
    semantic_data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("source_id", "source_record_identifier", "metric_id", "instrument_id"):
            _require_non_empty(getattr(self, name), name)
        validate_source_period(self.source_period_type, self.source_market_date, self.source_period_start_date, self.source_period_end_date)
        object.__setattr__(self, "calendar_assignments", normalize_calendar_assignments(self.calendar_assignments))
        object.__setattr__(self, "semantic_data", _freeze_mapping(self.semantic_data, "semantic_data"))
        for name in (
            "scheduler_execution_at", "source_publication_at", "collected_at", "observed_at",
            "source_available_at", "channel_available_at", "created_at",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, canonical_timestamp(value))
        for name in ("local_business_date", "source_business_date", "scheduler_business_date"):
            value = getattr(self, name)
            if value is not None:
                parse_date(value)
        if self.data_origin != "SYNTHETIC_FIXTURE" or self.operational_status != "SYNTHETIC_NON_OPERATIONAL":
            raise ContractError("RD-4.1 models accept synthetic non-operational construction only")

    def identity_projection(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_record_identifier": self.source_record_identifier,
            "metric_id": self.metric_id,
            "instrument_id": self.instrument_id,
            "source_period_type": self.source_period_type,
            "source_market_date": self.source_market_date,
            "source_period_start_date": self.source_period_start_date,
            "source_period_end_date": self.source_period_end_date,
        }

    @property
    def observation_id(self) -> str:
        return canonical_hash("OBSERVATION_ID", self.identity_projection())

    def content_projection(self) -> dict[str, Any]:
        return _plain(self)

    @property
    def content_hash(self) -> str:
        return canonical_hash("OBSERVATION_CONTENT", self.content_projection())


@dataclass(frozen=True, slots=True)
class ObservationVersion:
    observation_id: str
    source_version_or_release_key: str
    stable_version_key: str
    raw_payload_hash: str
    transformation_version: str
    parent_version_id: str | None = None
    revision_available_at: str | None = None
    collected_at: str | None = None
    observed_at: str | None = None
    created_at: str | None = None
    semantic_data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "semantic_data", _freeze_mapping(self.semantic_data, "semantic_data"))
        _require_sha256(self.observation_id, "observation_id")
        _require_sha256(self.raw_payload_hash, "raw_payload_hash")
        if not self.source_version_or_release_key or not self.stable_version_key or not self.transformation_version:
            raise ContractError("ObservationVersion identity strings must be explicit and non-empty")
        if self.transformation_version.lower() == "latest":
            raise ContractError("implicit/latest transformation version is forbidden")
        if self.parent_version_id is not None:
            _require_sha256(self.parent_version_id, "parent_version_id")
        for name in ("revision_available_at", "collected_at", "observed_at", "created_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, canonical_timestamp(value))

    def identity_projection(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "source_version_or_release_key": self.source_version_or_release_key,
            "stable_version_key": self.stable_version_key,
            "raw_payload_hash": self.raw_payload_hash,
            "transformation_version": self.transformation_version,
        }

    @property
    def observation_version_id(self) -> str:
        return canonical_hash("OBSERVATION_VERSION_ID", self.identity_projection())

    def content_projection(self) -> dict[str, Any]:
        return _plain(self)

    @property
    def content_hash(self) -> str:
        return canonical_hash("OBSERVATION_VERSION_CONTENT", self.content_projection())


@dataclass(frozen=True, slots=True)
class CalendarReference:
    calendar_reference_id: str
    calendar_version: str
    calendar_kind: str
    entries: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        for name in ("calendar_reference_id", "calendar_version", "calendar_kind"):
            _require_non_empty(getattr(self, name), name)
        object.__setattr__(self, "entries", tuple(_freeze_mapping(item, "calendar entry") for item in self.entries))

    def identity_projection(self) -> dict[str, Any]:
        return {"calendar_reference_id": self.calendar_reference_id, "calendar_version": self.calendar_version}

    def content_projection(self) -> dict[str, Any]:
        return _plain(self)

    @property
    def calendar_hash(self) -> str:
        return canonical_hash("CALENDAR_CONTENT", self.content_projection())


@dataclass(frozen=True, slots=True, kw_only=True)
class Assessment:
    subject_id: str
    assessment_role: str
    evaluation_at: str
    rule_bundle_version: str
    result: Mapping[str, Any]
    evidence: Mapping[str, Any] = field(default_factory=dict)
    assessed_at: str | None = None
    created_at: str | None = None
    assessment_type: str = field(default="ASSESSMENT", init=False)

    def __post_init__(self) -> None:
        _require_non_empty(self.assessment_role, "assessment_role")
        _require_non_empty(self.rule_bundle_version, "rule_bundle_version")
        object.__setattr__(self, "result", _freeze_mapping(self.result, "result"))
        object.__setattr__(self, "evidence", _freeze_mapping(self.evidence, "evidence"))
        _require_sha256(self.subject_id, "subject_id")
        object.__setattr__(self, "evaluation_at", canonical_timestamp(self.evaluation_at))
        if self.assessed_at is not None:
            object.__setattr__(self, "assessed_at", canonical_timestamp(self.assessed_at))
        if self.created_at is not None:
            object.__setattr__(self, "created_at", canonical_timestamp(self.created_at))

    def identity_projection(self) -> dict[str, Any]:
        return {
            "assessment_type": self.assessment_type,
            "subject_id": self.subject_id,
            "assessment_role": self.assessment_role,
            "evaluation_at": self.evaluation_at,
            "rule_bundle_version": self.rule_bundle_version,
        }

    @property
    def assessment_id(self) -> str:
        return canonical_hash("ASSESSMENT_ID", self.identity_projection())

    def content_projection(self) -> dict[str, Any]:
        return _plain(self)

    @property
    def content_hash(self) -> str:
        return canonical_hash("ASSESSMENT_CONTENT", self.content_projection())


@dataclass(frozen=True, slots=True, kw_only=True)
class AvailabilityAssessment(Assessment):
    availability_basis: str
    assessment_type: str = field(default="AVAILABILITY", init=False)

    def __post_init__(self, _base_post_init=Assessment.__post_init__) -> None:
        _base_post_init(self)
        if self.availability_basis not in (
            "SOURCE_NATIVE_TIMESTAMP", "VERIFIED_ARCHIVE_TIMESTAMP", "VERIFIED_CHANNEL_TIMESTAMP",
            "FIRST_SUCCESSFUL_COLLECTION", "APPROVED_CONSERVATIVE_ESTIMATE", "UNVERIFIED",
        ):
            raise ContractError("unsupported availability_basis")


@dataclass(frozen=True, slots=True, kw_only=True)
class LineageAssessment(Assessment):
    assessment_type: str = field(default="LINEAGE", init=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class TrustQualityAssessment(Assessment):
    trust_state: str
    quality_status: str
    reason_codes: tuple[str, ...]
    assessment_type: str = field(default="TRUST_QUALITY", init=False)

    def __post_init__(self, _base_post_init=Assessment.__post_init__) -> None:
        _base_post_init(self)
        if self.trust_state not in (
            "REAL_ORIGIN_UNVERIFIED", "REAL_ORIGIN_VERIFIED", "SHADOW_UNRESOLVED",
            "LEGACY_UNVERIFIED", "TRUST_REJECTED",
        ) or self.quality_status not in ("NOT_ASSESSED", "PASS", "WARNING", "FAIL", "CONFLICT"):
            raise ContractError("unsupported trust or quality state")
        object.__setattr__(self, "reason_codes", _validated_reason_codes(self.reason_codes))


@dataclass(frozen=True, slots=True, kw_only=True)
class PITEligibilityAssessment(Assessment):
    eligibility_state: str
    feature_available_at_max: str
    research_cutoff_at: str
    label_available_at: str
    evaluation_as_of_at: str
    reason_codes: tuple[str, ...]
    assessment_type: str = field(default="PIT_ELIGIBILITY", init=False)

    def __post_init__(self, _base_post_init=Assessment.__post_init__) -> None:
        _base_post_init(self)
        _require_eligibility_state(self.eligibility_state)
        for name in ("feature_available_at_max", "research_cutoff_at", "label_available_at", "evaluation_as_of_at"):
            object.__setattr__(self, name, canonical_timestamp(getattr(self, name)))
        validate_pit_structure(self.feature_available_at_max, self.research_cutoff_at, self.label_available_at, self.evaluation_as_of_at)
        object.__setattr__(self, "reason_codes", _validated_reason_codes(self.reason_codes))


@dataclass(frozen=True, slots=True, kw_only=True)
class ResearchEligibilityAssessment(Assessment):
    eligibility_state: str
    reason_codes: tuple[str, ...]
    assessment_type: str = field(default="RESEARCH_ELIGIBILITY", init=False)

    def __post_init__(self, _base_post_init=Assessment.__post_init__) -> None:
        _base_post_init(self)
        _require_eligibility_state(self.eligibility_state)
        object.__setattr__(self, "reason_codes", _validated_reason_codes(self.reason_codes))


@dataclass(frozen=True, slots=True, kw_only=True)
class BacktestRecordEligibilityAssessment(Assessment):
    eligibility_state: str
    reason_codes: tuple[str, ...]
    scope: str = field(default="RECORD", init=False)
    assessment_type: str = field(default="BACKTEST_RECORD_ELIGIBILITY", init=False)

    def __post_init__(self, _base_post_init=Assessment.__post_init__) -> None:
        _base_post_init(self)
        if self.assessment_type != "BACKTEST_RECORD_ELIGIBILITY" or self.scope != "RECORD":
            raise ContractError("backtest record assessment requires RECORD scope")
        _require_eligibility_state(self.eligibility_state)
        object.__setattr__(self, "reason_codes", _validated_reason_codes(self.reason_codes))


@dataclass(frozen=True, slots=True, kw_only=True)
class BacktestDatasetEligibilityAssessment(Assessment):
    eligibility_state: str
    reason_codes: tuple[str, ...]
    scope: str = field(default="DATASET", init=False)
    assessment_type: str = field(default="BACKTEST_DATASET_ELIGIBILITY", init=False)

    def __post_init__(self, _base_post_init=Assessment.__post_init__) -> None:
        _base_post_init(self)
        if self.assessment_type != "BACKTEST_DATASET_ELIGIBILITY" or self.scope != "DATASET":
            raise ContractError("backtest dataset assessment requires DATASET scope")
        _require_eligibility_state(self.eligibility_state)
        object.__setattr__(self, "reason_codes", _validated_reason_codes(self.reason_codes))


def _require_eligibility_state(value: str) -> None:
    if value not in ("NOT_EVALUATED", "ELIGIBLE", "INELIGIBLE"):
        raise ContractError("unsupported eligibility_state")


def _validated_reason_codes(values: Sequence[str]) -> tuple[str, ...]:
    result = tuple(values)
    authoritative_codes = (
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
    )
    if len(result) != len(set(result)) or any(item not in authoritative_codes for item in result):
        raise ContractError("reason_codes must be unique frozen catalog codes")
    return result


@dataclass(frozen=True, slots=True, kw_only=True)
class RuleBundle:
    rule_bundle_version: str
    contract_version: str
    exclusion_catalog_version: str
    exclusion_reasons: tuple["ExclusionReason", ...]
    rules: Mapping[str, Any]

    def __post_init__(self) -> None:
        _require_non_empty(self.rule_bundle_version, "rule_bundle_version")
        if self.contract_version != "1.0.0" or self.exclusion_catalog_version != "1.1.0":
            raise ContractError("unsupported rule-bundle contract/catalog version")
        normalized = normalize_exclusion_reasons(self.exclusion_reasons)
        if tuple(item.code for item in normalized) != (
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
            raise ContractError("rule bundle must contain the exact frozen exclusion catalog")
        object.__setattr__(self, "exclusion_reasons", normalized)
        object.__setattr__(self, "rules", _freeze_mapping(self.rules, "rules"))

    def identity_projection(self) -> dict[str, Any]:
        return {"rule_bundle_version": self.rule_bundle_version}

    def content_projection(self) -> dict[str, Any]:
        return _plain(self)

    @property
    def content_hash(self) -> str:
        return canonical_hash("RULE_BUNDLE_CONTENT", self.content_projection())


@dataclass(frozen=True, slots=True)
class ExclusionReason:
    code: str
    severity: str
    blocking_scope: str
    description: str
    remediation: str
    sort_order: int

    def __post_init__(self) -> None:
        if self.code not in (
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
        ) or self.code in {
            "AMBIGUOUS_SOURCE_MARKET_DATE", "SOURCE_AVAILABILITY_UNVERIFIED", "FUTURE_TIMESTAMP",
            "INCOMPLETE_LINEAGE", "STALE_SOURCE", "TARGET_HORIZON_NOT_ELAPSED",
            "DUPLICATE_OBSERVATION_ID", "INVALID_VALUE", "UNIT_UNAPPROVED", "CURRENCY_UNAPPROVED",
            "VINTAGE_NOT_PROVEN",
        }:
            raise ContractError("unknown or stale exclusion reason code")
        if not all((self.severity, self.blocking_scope, self.description, self.remediation)):
            raise ContractError("exclusion reason semantic fields must be non-empty")
        if type(self.sort_order) is not int or self.sort_order < 0:
            raise ContractError("sort_order must be a non-negative integer")

    def identity_projection(self) -> dict[str, Any]:
        return {"code": self.code}

    def content_projection(self) -> dict[str, Any]:
        return _plain(self)


def normalize_exclusion_reasons(items: Sequence[ExclusionReason]) -> tuple[ExclusionReason, ...]:
    """Normalize the frozen catalog by explicit sort order, then stable reason code."""
    reasons = tuple(items)
    if any(type(item) is not ExclusionReason for item in reasons):
        raise ContractError("exclusion reasons must be validated ExclusionReason values")
    codes = [item.code for item in reasons]
    if len(codes) != len(set(codes)):
        raise ContractError("duplicate exclusion reason code")
    return tuple(sorted(reasons, key=lambda item: (item.sort_order, item.code)))


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadinessManifest:
    manifest_kind: str
    semantic_subject_scope_hash: str
    evaluation_as_of_at: str
    contract_version: str
    rule_bundle_version: str
    entries: tuple[Mapping[str, Any], ...]
    authorizations: Mapping[str, bool]
    blocker_ids: tuple[str, ...] = RD3_OPEN_BLOCKERS
    report_generated_at: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.manifest_kind, "manifest_kind")
        _require_non_empty(self.rule_bundle_version, "rule_bundle_version")
        if self.contract_version != "1.0.0":
            raise ContractError("unsupported readiness manifest contract version")
        _require_sha256(self.semantic_subject_scope_hash, "semantic_subject_scope_hash")
        object.__setattr__(self, "evaluation_as_of_at", canonical_timestamp(self.evaluation_as_of_at))
        if self.report_generated_at is not None:
            object.__setattr__(self, "report_generated_at", canonical_timestamp(self.report_generated_at))
        frozen_authorizations = _freeze_mapping(self.authorizations, "authorizations")
        _validate_authorizations(frozen_authorizations)
        object.__setattr__(self, "authorizations", frozen_authorizations)
        blocker_ids = tuple(self.blocker_ids)
        authoritative_blockers = {
            "RD3-LME-001", "RD3-LME-002", "RD3-LME-003",
            "RD3-SMM-001", "RD3-SMM-002", "RD3-SMM-003", "RD3-SMM-004",
            "RD3-YAHOO-001", "RD3-YAHOO-002", "RD3-YAHOO-003", "RD3-YAHOO-004",
            "RD3-BZ-001", "RD3-WB-001", "RD3-WB-002", "RD3-WB-003",
        }
        if len(blocker_ids) != 15 or len(set(blocker_ids)) != len(blocker_ids) or set(blocker_ids) != authoritative_blockers:
            raise ContractError("manifest must contain the exact 15 open RD-3 blockers")
        object.__setattr__(self, "blocker_ids", tuple(sorted(blocker_ids)))
        if any(not isinstance(item, Mapping) for item in self.entries):
            raise ContractError("manifest entries must be mappings")
        normalized_entries = tuple(_freeze_mapping(item, "manifest entry") for item in sorted(self.entries, key=lambda item: (str(item.get("path", "")), str(item.get("hash", "")))))
        object.__setattr__(self, "entries", normalized_entries)

    def identity_projection(self) -> dict[str, Any]:
        return {
            "manifest_kind": self.manifest_kind,
            "semantic_subject_scope_hash": self.semantic_subject_scope_hash,
            "evaluation_as_of_at": self.evaluation_as_of_at,
            "contract_version": self.contract_version,
            "rule_bundle_version": self.rule_bundle_version,
        }

    @property
    def manifest_id(self) -> str:
        return canonical_hash("MANIFEST_CONTENT", {"identity": self.identity_projection()})

    def content_projection(self) -> dict[str, Any]:
        return _plain(self)

    @property
    def content_hash(self) -> str:
        return canonical_hash("MANIFEST_CONTENT", self.content_projection())


def validate_pit_structure(
    feature_available_at_max: str,
    research_cutoff_at: str,
    label_available_at: str,
    evaluation_as_of_at: str,
) -> None:
    feature = parse_rfc3339(feature_available_at_max)
    cutoff = parse_rfc3339(research_cutoff_at)
    label = parse_rfc3339(label_available_at)
    evaluation = parse_rfc3339(evaluation_as_of_at)
    if feature > cutoff:
        raise ContractError("feature_available_at_max must not follow research_cutoff_at")
    if label <= cutoff:
        raise ContractError("label_available_at must follow research_cutoff_at")
    if evaluation < label:
        raise ContractError("evaluation_as_of_at must not precede label_available_at")


def model_field_names(model_type: Any) -> tuple[str, ...]:
    """Return schema-facing fields stored directly by a public value class."""

    if not isinstance(model_type, type) or not is_dataclass(model_type):
        raise TypeError("not an RD-4.1 public model class")
    return tuple(item.name for item in fields(model_type))
