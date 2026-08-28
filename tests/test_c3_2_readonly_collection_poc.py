from __future__ import annotations

from contextlib import redirect_stdout
import io
import os
import unittest
from unittest.mock import patch

from scripts.c3_2_readonly_collection_poc import (
    SHEET_COLUMNS,
    classify_failure,
    log_source_date_metadata,
    run_collection,
)
from scripts.company_market_collector import make_quote


def successful_quote(key: str, value: float):
    return make_quote(
        key=key,
        name=key,
        source="test",
        instrument=key,
        term="test",
        quote_type="test",
        currency="test",
        unit="test",
        value=value,
    )


class ReadOnlyCollectionPocTests(unittest.TestCase):
    def test_success_reports_statuses_without_raw_values_or_sheet_access(self):
        quotes = {key: successful_quote(key, 91000.25 + index) for index, key in enumerate(SHEET_COLUMNS)}
        browser_quotes = {key: quotes[key] for key in SHEET_COLUMNS[:8]}
        finance_quotes = {key: quotes[key] for key in SHEET_COLUMNS[8:]}

        output = io.StringIO()
        with patch.dict(
            os.environ,
            {
                "GOOGLE_SHEET_ID": "must-be-removed",
                "GOOGLE_SERVICE_ACCOUNT_FILE": "must-be-removed.json",
                "CONTROLLED_WRITE_APPROVAL": "must-be-removed",
                "ALLOW_GOOGLE_SHEET_WRITE": "1",
            },
            clear=False,
        ), redirect_stdout(output):
            result = run_collection(lambda: browser_quotes, lambda: finance_quotes)
            self.assertNotIn("GOOGLE_SHEET_ID", os.environ)
            self.assertNotIn("GOOGLE_SERVICE_ACCOUNT_FILE", os.environ)
            self.assertNotIn("CONTROLLED_WRITE_APPROVAL", os.environ)
            self.assertEqual(os.environ["ALLOW_GOOGLE_SHEET_WRITE"], "0")

        text = output.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("C3_2_1_RESULT=PASS usable_quotes=11/11", text)
        self.assertIn("google_sheet_write=DISABLED", text)
        self.assertNotIn("91000.25", text)
        self.assertNotIn("must-be-removed", text)

    def test_missing_or_failed_quote_fails_closed(self):
        quotes = {key: successful_quote(key, 1000.0) for key in SHEET_COLUMNS}
        failed_key = "lead_lme_cash"
        quotes[failed_key] = make_quote(
            key=failed_key,
            name=failed_key,
            source="London Metal Exchange",
            instrument="Lead",
            term="Cash",
            quote_type="OFFER",
            currency="USD",
            unit="USD/MT",
            value=None,
            error="semantic table timeout",
        )

        output = io.StringIO()
        with redirect_stdout(output):
            result = run_collection(
                lambda: {key: quotes[key] for key in SHEET_COLUMNS[:8]},
                lambda: {key: quotes[key] for key in SHEET_COLUMNS[8:]},
            )

        self.assertEqual(result, 1)
        self.assertIn("source_column=lead_lme_cash status=ERROR", output.getvalue())
        self.assertIn("classification=EXTERNAL_SERVICE_FAILURE", output.getvalue())
        self.assertIn("C3_2_1_RESULT=FAIL", output.getvalue())

    def test_failure_classification_is_diagnostic_only(self):
        self.assertEqual(classify_failure("403 Forbidden"), "AUTH_FAILURE")
        self.assertEqual(classify_failure("ChromeDriver failed"), "ENVIRONMENT_FAILURE")
        self.assertEqual(classify_failure("request timeout"), "EXTERNAL_SERVICE_FAILURE")
        self.assertEqual(classify_failure("contract check failed"), "CODE_FAILURE")

    def test_source_date_log_exposes_dates_but_not_quote_values(self):
        quotes = {key: successful_quote(key, 1866.25) for key in SHEET_COLUMNS}
        for key in ("brent_yfinance", "silver_yfinance", "gold_yfinance"):
            quotes[key] = quotes[key].__class__(**{**quotes[key].__dict__, "observed_at": "2026-08-28"})
        output = io.StringIO()
        with redirect_stdout(output):
            log_source_date_metadata(quotes)
        text = output.getvalue()
        self.assertIn("source_date source=LME market_date=UNAVAILABLE", text)
        self.assertIn("source_date source=yfinance_BZ=F market_date=2026-08-28", text)
        self.assertNotIn("1866.25", text)


if __name__ == "__main__":
    unittest.main()
