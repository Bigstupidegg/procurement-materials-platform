from __future__ import annotations

from datetime import datetime
import unittest

from scripts.c3_2_target_business_date import (
    historical_fetch_supported,
    resolve_target_business_date,
    verify_target_date_quotes,
)
from scripts.company_market_collector import SHEET_COLUMNS, YFINANCE_SPECS, make_quote


def quote(key: str, observed_at: str, *, value: float = 1.0):
    return make_quote(
        key=key, name=key, source="test", instrument=key, term="test",
        quote_type="test", currency="test", unit="test", value=value,
        observed_at=observed_at,
    )


class TargetBusinessDateResolverTests(unittest.TestCase):
    def test_newest_explicit_common_date_can_be_older_than_latest(self):
        result = resolve_target_business_date(
            ["2026-08-28"], ["2026-08-28", "2026-08-31"], ["2026-08-28", "2026-08-31"]
        )
        self.assertEqual(result.status, "RESOLVED")
        self.assertEqual(result.target_business_date, "2026-08-28")
        self.assertEqual(result.source_latest_dates["SMM"], "2026-08-31")

    def test_selects_latest_common_date_when_latest_dates_all_differ(self):
        result = resolve_target_business_date(
            ["2026-08-27", "2026-08-28"],
            ["2026-08-26", "2026-08-28", "2026-08-31"],
            ["2026-08-25", "2026-08-28", "2026-08-29"],
        )
        self.assertEqual(result.target_business_date, "2026-08-28")
        self.assertEqual(result.common_dates, ("2026-08-28",))

    def test_empty_intersection_never_fabricates_a_date(self):
        result = resolve_target_business_date(["2026-08-28"], ["2026-08-31"], ["2026-08-29"])
        self.assertEqual(result.status, "NO_COMMON_DATE")
        self.assertIsNone(result.target_business_date)

    def test_execution_date_and_weekends_do_not_influence_resolution(self):
        result = resolve_target_business_date(["2026-08-28"], ["2026-08-28"], ["2026-08-28"])
        self.assertEqual(result.target_business_date, "2026-08-28")
        self.assertNotEqual(result.target_business_date, datetime(2026, 8, 31).date().isoformat())

    def test_historical_fetch_capability_fails_closed(self):
        result = historical_fetch_supported({"LME": False, "SMM": False, "Yahoo": True})
        self.assertEqual(result.status, "HISTORICAL_FETCH_UNSUPPORTED")
        self.assertIsNone(result.target_business_date)

    def test_all_eleven_quotes_must_prove_target_date(self):
        quotes = {key: quote(key, "2026-08-28") for key in SHEET_COLUMNS}
        self.assertEqual(verify_target_date_quotes(quotes, "2026-08-28").status, "MATCH")
        quotes["gold_yfinance"] = quote("gold_yfinance", "2026-08-31")
        self.assertEqual(verify_target_date_quotes(quotes, "2026-08-28").status, "DATE_MISMATCH")

    def test_missing_quote_fails_closed(self):
        quotes = {key: quote(key, "2026-08-28") for key in SHEET_COLUMNS[:-1]}
        self.assertEqual(verify_target_date_quotes(quotes, "2026-08-28").status, "COLLECTION_INCOMPLETE")

    def test_si_f_unit_conversion_contract_is_unchanged(self):
        self.assertEqual(YFINANCE_SPECS["silver_yfinance"][2], 100.0)


if __name__ == "__main__":
    unittest.main()
