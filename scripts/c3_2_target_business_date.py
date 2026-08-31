"""Pure, fail-closed Target Business Date resolution for the C3.2 Pilot.

This module deliberately neither fetches quotes nor writes Sheets.  A target
date is eligible only when each source group supplies explicit available dates
and every collected quote later proves that exact date.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Mapping

try:
    from company_market_collector import SHEET_COLUMNS, normalize_market_date
    from company_market_core import MarketQuote
except ModuleNotFoundError:  # imported as scripts.c3_2_target_business_date
    from scripts.company_market_collector import SHEET_COLUMNS, normalize_market_date
    from scripts.company_market_core import MarketQuote


@dataclass(frozen=True)
class TargetBusinessDateResolution:
    status: str
    target_business_date: str | None
    common_dates: tuple[str, ...]
    source_latest_dates: Mapping[str, str | None]
    failure_reason: str | None = None


@dataclass(frozen=True)
class TargetDateQuoteVerification:
    status: str
    target_business_date: str | None
    failure_reason: str | None = None


def _explicit_dates(dates: Iterable[object]) -> set[str]:
    """Keep only dates explicitly parsed from source-provided metadata."""
    return {parsed for item in dates if (parsed := normalize_market_date(item))}


def resolve_target_business_date(
    lme_available_dates: Iterable[object],
    smm_available_dates: Iterable[object],
    yahoo_available_dates: Iterable[object],
) -> TargetBusinessDateResolution:
    """Return the newest real intersection, never an inferred calendar date."""
    available = {
        "LME": _explicit_dates(lme_available_dates),
        "SMM": _explicit_dates(smm_available_dates),
        "Yahoo": _explicit_dates(yahoo_available_dates),
    }
    latest = {source: max(dates) if dates else None for source, dates in available.items()}
    common = tuple(sorted(set.intersection(*available.values())))
    if not common:
        return TargetBusinessDateResolution(
            "NO_COMMON_DATE", None, common, latest, "No explicit common source date."
        )
    return TargetBusinessDateResolution("RESOLVED", common[-1], common, latest)


def historical_fetch_supported(source_capabilities: Mapping[str, bool]) -> TargetDateQuoteVerification:
    """Fail closed until every source group can retrieve a true requested date."""
    missing = tuple(sorted(source for source, supported in source_capabilities.items() if not supported))
    if missing:
        return TargetDateQuoteVerification(
            "HISTORICAL_FETCH_UNSUPPORTED", None, ",".join(missing)
        )
    return TargetDateQuoteVerification("HISTORICAL_FETCH_SUPPORTED", None)


def verify_target_date_quotes(
    quotes: Mapping[str, MarketQuote], target_business_date: str | None
) -> TargetDateQuoteVerification:
    """Accept only 11 successful quotes whose explicit source dates equal T*."""
    if target_business_date is None:
        return TargetDateQuoteVerification("NO_COMMON_DATE", None, "Target date is unavailable.")
    # Validates the supplied target itself; no execution-time or calendar inference is used.
    try:
        date.fromisoformat(target_business_date)
    except ValueError:
        return TargetDateQuoteVerification("NO_COMMON_DATE", None, "Target date is not ISO-parseable.")
    for key in SHEET_COLUMNS:
        quote = quotes.get(key)
        if quote is None or not quote.ok:
            return TargetDateQuoteVerification("COLLECTION_INCOMPLETE", None, key)
        if normalize_market_date(quote.observed_at) != target_business_date:
            return TargetDateQuoteVerification("DATE_MISMATCH", None, key)
    return TargetDateQuoteVerification("MATCH", target_business_date)
