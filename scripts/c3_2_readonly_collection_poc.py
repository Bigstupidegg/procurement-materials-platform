from __future__ import annotations

import os
from typing import Callable

try:
    from company_market_collector import (
        SHEET_COLUMNS,
        fetch_browser_quotes,
        fetch_yfinance_quotes,
    )
    from company_market_core import DataContractError, MarketQuote, require_success
except ModuleNotFoundError:  # imported as scripts.c3_2_readonly_collection_poc
    from scripts.company_market_collector import (
        SHEET_COLUMNS,
        fetch_browser_quotes,
        fetch_yfinance_quotes,
    )
    from scripts.company_market_core import DataContractError, MarketQuote, require_success


SENSITIVE_ENVIRONMENT_KEYS = (
    "GOOGLE_SHEET_ID",
    "GOOGLE_SERVICE_ACCOUNT_FILE",
    "CONTROLLED_WRITE_APPROVAL",
)


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
