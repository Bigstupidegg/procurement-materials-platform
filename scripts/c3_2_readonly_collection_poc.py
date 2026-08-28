from __future__ import annotations

import os
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

try:
    from company_market_collector import (
        SHEET_COLUMNS,
        fetch_browser_quotes,
        fetch_yfinance_quotes,
        normalize_market_date,
    )
    from company_market_core import DataContractError, MarketQuote, require_success
except ModuleNotFoundError:  # imported as scripts.c3_2_readonly_collection_poc
    from scripts.company_market_collector import (
        SHEET_COLUMNS,
        fetch_browser_quotes,
        fetch_yfinance_quotes,
        normalize_market_date,
    )
    from scripts.company_market_core import DataContractError, MarketQuote, require_success


SENSITIVE_ENVIRONMENT_KEYS = (
    "GOOGLE_SHEET_ID",
    "GOOGLE_SERVICE_ACCOUNT_FILE",
    "CONTROLLED_WRITE_APPROVAL",
)

TAIPEI = ZoneInfo("Asia/Taipei")
SOURCE_DATE_GROUPS = {
    "LME": (
        "copper_lme_cash", "copper_lme_3m", "aluminium_lme_cash", "lead_lme_cash",
        "nickel_lme_cash", "tin_lme_cash", "zinc_lme_cash",
    ),
    "SMM": ("smm_electrolytic_copper",),
    "yfinance_BZ=F": ("brent_yfinance",),
    "yfinance_SI=F": ("silver_yfinance",),
    "yfinance_GC=F": ("gold_yfinance",),
}


def enforce_read_only_environment() -> None:
    for key in SENSITIVE_ENVIRONMENT_KEYS:
        os.environ.pop(key, None)
    os.environ["ALLOW_GOOGLE_SHEET_WRITE"] = "0"


def classify_failure(message: str) -> str:
    normalized = message.lower()
    if any(term in normalized for term in ("401", "403", "unauthorized", "forbidden", "permission")):
        return "AUTH_FAILURE"
    if any(term in normalized for term in ("chromedriver", "chrome", "selenium", "browser")):
        return "ENVIRONMENT_FAILURE"
    if any(term in normalized for term in ("timeout", "timed out", "connection", "http", "dns")):
        return "EXTERNAL_SERVICE_FAILURE"
    return "CODE_FAILURE" if "contract check failed" in normalized else "EXTERNAL_SERVICE_FAILURE"


def log_source_date_metadata(quotes: dict[str, MarketQuote]) -> None:
    """Emit date-only source metadata for the read-only schedule decision gate."""
    execution_at = datetime.now(TAIPEI).isoformat(timespec="seconds")
    execution_date = execution_at[:10]
    grouped_dates = {
        source: {normalize_market_date(quotes[key].observed_at) for key in keys if key in quotes}
        for source, keys in SOURCE_DATE_GROUPS.items()
    }
    resolved_dates = {date for dates in grouped_dates.values() for date in dates if date}
    print(f"source_date_execution_taipei={execution_at}")
    for source, dates in grouped_dates.items():
        usable = sorted(date for date in dates if date)
        source_date = usable[0] if len(usable) == 1 else "MIXED" if usable else "UNAVAILABLE"
        parsed = source_date not in {"MIXED", "UNAVAILABLE"}
        differs = parsed and any(other != source_date for other in resolved_dates)
        print(
            f"source_date source={source} market_date={source_date} parsed={parsed} "
            f"equals_taipei_execution_date={source_date == execution_date if parsed else 'UNKNOWN'} "
            f"differs_from_other_sources={differs if parsed else 'UNKNOWN'}"
        )


def run_collection(
    browser_fetcher: Callable[[], dict[str, MarketQuote]] = fetch_browser_quotes,
    finance_fetcher: Callable[[], dict[str, MarketQuote]] = fetch_yfinance_quotes,
) -> int:
    enforce_read_only_environment()

    quotes = browser_fetcher()
    quotes.update(finance_fetcher())

    missing = [key for key in SHEET_COLUMNS if key not in quotes]
    unexpected = [key for key in quotes if key not in SHEET_COLUMNS]
    if missing or unexpected:
        print(f"C3_2_1_RESULT=FAIL classification=CODE_FAILURE")
        print(f"contract=quote_key_set missing={missing!r} unexpected={unexpected!r}")
        return 1

    for key in SHEET_COLUMNS:
        quote = quotes[key]
        line = f"source_column={key} status={quote.status} attempts={quote.attempts}"
        if quote.ok:
            print(line)
        else:
            classification = classify_failure(quote.error or "unknown collection failure")
            print(f"{line} classification={classification} diagnostic={quote.error or 'unknown'}")

    log_source_date_metadata(quotes)

    try:
        require_success(quotes, SHEET_COLUMNS)
    except DataContractError as exc:
        print(f"C3_2_1_RESULT=FAIL classification={classify_failure(str(exc))}")
        print("google_sheet_write=DISABLED audit_persistence=DISABLED raw_values_logged=NO")
        return 1

    print(f"C3_2_1_RESULT=PASS usable_quotes={len(SHEET_COLUMNS)}/{len(SHEET_COLUMNS)}")
    print("google_sheet_write=DISABLED audit_persistence=DISABLED raw_values_logged=NO")
    return 0


def main() -> int:
    print("C3.2-1 GitHub Actions read-only collection PoC")
    return run_collection()


if __name__ == "__main__":
    raise SystemExit(main())
