"""Pure C3.2 observation versioning and daily canonicalization PoC.

This module has no Google Sheet client and never writes observations.  It
models the proposed append-only observation layer before any schema migration.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable

try:
    from company_market_collector import normalize_market_date
except ModuleNotFoundError:  # imported as scripts.c3_2_observation_canonicalization
    from scripts.company_market_collector import normalize_market_date


YAHOO_MATERIALS = frozenset({"BRENT_FUT", "SILVER_FUT", "GOLD_FUT"})
DAILY_SNAPSHOT_SOURCES = frozenset({"LME", "SMM"})


@dataclass(frozen=True)
class RawObservation:
    """One immutable source observation proposed for append-only persistence."""

    record_id: str
    material_id: str
    source_id: str
    source_date: str
    price: float
    currency: str
    unit: str
    market_type: str
    observation_at: str
    observation_kind: str
    source_status: str = "SUCCESS"
    date_parse_status: str = "PARSED"


@dataclass(frozen=True)
class CanonicalDailyRecord:
    """A selected immutable observation, suitable for deferred assembly only."""

    observation: RawObservation
    canonical_status: str
    canonical_reason: str


@dataclass(frozen=True)
class CanonicalizationResult:
    status: str
    target_date: str | None
    canonical_records: tuple[CanonicalDailyRecord, ...]
    duplicate_same_count: int = 0
    conflict_materials: tuple[str, ...] = ()
    failure_reason: str | None = None


def observation_version_key(observation: RawObservation) -> tuple[str, str, str, str]:
    """Natural version key; record_id remains the immutable physical primary key."""
    return (
        normalize_market_date(observation.source_date) or "",
        observation.material_id,
        observation.source_id,
        observation.observation_at,
    )


def _numeric_text(value: object) -> str:
    try:
        return format(Decimal(str(value)), "f").rstrip("0").rstrip(".") or "0"
    except (InvalidOperation, ValueError):
        return str(value).strip()


def _valid(observation: RawObservation, target_date: str) -> bool:
    return (
        bool(observation.record_id)
        and normalize_market_date(observation.source_date) == target_date
        and observation.source_status in {"SUCCESS", "RETRY_SUCCESS"}
        and observation.date_parse_status in {"PASS", "MATCH", "PARSED"}
    )


def _same_value(left: RawObservation, right: RawObservation) -> bool:
    return (
        left.source_id == right.source_id
        and left.currency == right.currency
        and left.unit == right.unit
        and left.market_type == right.market_type
        and _numeric_text(left.price) == _numeric_text(right.price)
    )


def _timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def canonicalize_daily_observations(
    target_date: str, observations: Iterable[RawObservation]
) -> CanonicalizationResult:
    """Fail closed unless one governed canonical observation exists per material.

    Yahoo observations require an explicit FINAL_DAILY_CLOSE marker.  Intraday
    observations remain usable as audit history but cannot become daily values.
    """
    target = normalize_market_date(target_date)
    if not target:
        return CanonicalizationResult("CANONICALIZATION_INCOMPLETE", None, (), failure_reason="INVALID_TARGET_DATE")
    grouped: dict[str, list[RawObservation]] = {}
    for observation in observations:
        if normalize_market_date(observation.source_date) == target:
            grouped.setdefault(observation.material_id, []).append(observation)

    canonical: list[CanonicalDailyRecord] = []
    duplicate_same = 0
    conflicts: list[str] = []
    missing_final: list[str] = []
    for material_id, candidates in grouped.items():
        valid = [item for item in candidates if _valid(item, target)]
        if material_id in YAHOO_MATERIALS:
            valid = [item for item in valid if item.observation_kind == "FINAL_DAILY_CLOSE"]
            if not valid:
                missing_final.append(material_id)
                continue
        elif not valid:
            continue
        if any(_timestamp(item.observation_at) is None for item in valid):
            return CanonicalizationResult("CANONICALIZATION_INCOMPLETE", target, (), failure_reason="INVALID_OBSERVATION_TIMESTAMP")
        valid.sort(key=lambda item: _timestamp(item.observation_at))
        chosen = valid[-1]
        older = valid[:-1]
        if any(not _same_value(chosen, item) for item in older):
            conflicts.append(material_id)
            continue
        duplicate_same += len(older)
        reason = "YAHOO_FINAL_DAILY_CLOSE" if material_id in YAHOO_MATERIALS else "DAILY_SNAPSHOT"
        canonical.append(CanonicalDailyRecord(chosen, "CANONICAL", reason))
    if conflicts:
        return CanonicalizationResult("HUMAN_REVIEW_REQUIRED", target, (), duplicate_same, tuple(sorted(conflicts)), "CONFLICTING_OBSERVATION")
    if missing_final:
        return CanonicalizationResult("CANONICALIZATION_INCOMPLETE", target, (), duplicate_same, (), "FINAL_DAILY_CLOSE_MISSING:" + ",".join(sorted(missing_final)))
    return CanonicalizationResult("CANONICALIZATION_COMPLETE", target, tuple(canonical), duplicate_same)
