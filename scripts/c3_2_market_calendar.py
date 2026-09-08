"""Pure C3.2-6 date-role and source-readiness policy.

The policy never fetches data and never writes a Sheet.  A source market date
must come from source metadata; scheduler and local dates are observability
context only and can never manufacture a canonical target.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Collection, Iterable
from zoneinfo import ZoneInfo

try:
    from company_market_collector import normalize_market_date
except ModuleNotFoundError:
    from scripts.company_market_collector import normalize_market_date


TAIPEI = ZoneInfo("Asia/Taipei")
YAHOO_UNCONFIRMED = "YAHOO_UNCONFIRMED"
YAHOO_CONFIRMED = "YAHOO_DAILY_CLOSE_CONFIRMED"
DAILY_SNAPSHOT = "DAILY_SNAPSHOT"
VALID_SOURCE_STATUSES = frozenset({"SUCCESS", "RETRY_SUCCESS"})
VALID_DATE_PARSE_STATUSES = frozenset({"PASS", "MATCH", "PARSED"})


@dataclass(frozen=True)
class ExecutionDateContext:
    scheduler_execution_at: str
    local_calendar_date: str
    local_business_date: str | None
    local_calendar_status: str
    canonical_target_basis: str = "EXPLICIT_SOURCE_MARKET_DATE_ONLY"


@dataclass(frozen=True)
class SourceReadiness:
    source_group: str | None
    source_market_date: str | None
    source_availability_date: str | None
    canonical_target_date: str | None
    status: str
    reason: str


def execution_date_context(
    execution_at: datetime, *, local_holidays: Collection[str] | None = None
) -> ExecutionDateContext:
    """Separate actual execution time from an independently governed local date.

    Without an approved holiday set a Taipei weekday remains unverified and is
    not asserted to be a local business date.  This never blocks source
    collection and never influences canonical targeting.
    """
    if execution_at.tzinfo is None:
        raise ValueError("Scheduler execution time must be timezone-aware.")
    local = execution_at.astimezone(TAIPEI)
    local_date = local.date().isoformat()
    if local.weekday() >= 5:
        business_date = None
        status = "WEEKEND"
    elif local_holidays is None:
        business_date = None
        status = "WEEKDAY_HOLIDAY_STATUS_UNVERIFIED"
    elif local_date in local_holidays:
        business_date = None
        status = "HOLIDAY"
    else:
        business_date = local_date
        status = "BUSINESS_DAY"
    return ExecutionDateContext(
        local.isoformat(timespec="seconds"), local_date, business_date, status
    )


def explicit_weekday_source_date(value: object) -> str | None:
    """Return an explicit ISO source date only when it is not a weekend label.

    A weekday is necessary, not sufficient, evidence of a source market day.
    Regional holidays are handled by source publication/readiness evidence;
    this function never fills a missing holiday date from a nearby session.
    """
    parsed = normalize_market_date(value)
    if not parsed:
        return None
    try:
        return parsed if date.fromisoformat(parsed).weekday() < 5 else None
    except ValueError:
        return None


def source_group(source_id: object) -> str | None:
    value = str(source_id or "").upper()
    if value == "LME" or value.startswith("LME_"):
        return "LME"
    if value == "SMM" or value.startswith("SMM_"):
        return "SMM"
    if value.startswith("YFINANCE_"):
        return "YAHOO"
    return None


def _availability_date(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(TAIPEI)
        return parsed.date().isoformat()
    except ValueError:
        return normalize_market_date(text)


def assess_source_readiness(observation: object) -> SourceReadiness:
    """Assess one stored observation without inferring any missing date."""
    group = source_group(getattr(observation, "source_id", ""))
    market_date = normalize_market_date(getattr(observation, "source_date", ""))
    availability_date = _availability_date(getattr(observation, "observation_at", ""))
    if group is None:
        return SourceReadiness(None, market_date, availability_date, None, "NOT_GOVERNED", "UNKNOWN_SOURCE")
    if explicit_weekday_source_date(market_date) is None:
        return SourceReadiness(group, market_date, availability_date, None, "INVALID", "NON_BUSINESS_OR_INVALID_SOURCE_DATE")
    if getattr(observation, "source_status", "") not in VALID_SOURCE_STATUSES:
        return SourceReadiness(group, market_date, availability_date, None, "INVALID", "SOURCE_STATUS_NOT_READY")
    if getattr(observation, "date_parse_status", "") not in VALID_DATE_PARSE_STATUSES:
        return SourceReadiness(group, market_date, availability_date, None, "INVALID", "SOURCE_DATE_NOT_PARSED")
    if availability_date is None:
        return SourceReadiness(group, market_date, None, None, "PENDING", "AVAILABILITY_DATE_UNAVAILABLE")
    if availability_date < market_date:
        return SourceReadiness(group, market_date, availability_date, None, "INVALID", "AVAILABILITY_PRECEDES_MARKET_DATE")

    kind = getattr(observation, "observation_kind", "")
    if group == "YAHOO":
        if kind == YAHOO_UNCONFIRMED:
            return SourceReadiness(group, market_date, availability_date, None, "PENDING", "YAHOO_HISTORICAL_CLOSE_NOT_CONFIRMED")
        if kind != YAHOO_CONFIRMED:
            return SourceReadiness(group, market_date, availability_date, None, "INVALID", "YAHOO_KIND_NOT_GOVERNED")
        if availability_date <= market_date:
            return SourceReadiness(group, market_date, availability_date, None, "INVALID", "YAHOO_CONFIRMATION_NOT_LATER_DAY")
    elif kind != DAILY_SNAPSHOT:
        return SourceReadiness(group, market_date, availability_date, None, "INVALID", "SNAPSHOT_KIND_NOT_GOVERNED")

    return SourceReadiness(group, market_date, availability_date, market_date, "READY", "EXPLICIT_SOURCE_DATE_AND_MATURE_KIND")


def governed_open_target_dates(observations: Iterable[object]) -> list[str]:
    """Return only governed Shadow weekday dates for later re-evaluation."""
    return sorted({
        source_date
        for observation in observations
        if str(getattr(observation, "record_id", "")).startswith("shadow-")
        and source_group(getattr(observation, "source_id", "")) is not None
        and (source_date := explicit_weekday_source_date(getattr(observation, "source_date", "")))
    })
