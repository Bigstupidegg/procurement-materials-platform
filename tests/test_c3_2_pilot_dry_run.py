from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime
import io
import os
import unittest
from unittest.mock import patch

from scripts.c3_2_pilot_dry_run import evaluate_pilot_dry_run, persist_redacted_status, run_pilot_dry_run
from scripts.company_market_collector import COMPANY_MAIN_LAYOUT, SHEET_COLUMNS, make_quote


def quotes(date_text: str = "2026-08-28"):
    return {
        key: make_quote(key=key, name=key, source="test", instrument=key, term="Cash", quote_type="OFFER", currency="USD", unit="USD/MT", value=1000.0, observed_at=date_text)
        for key in SHEET_COLUMNS
    }


class PilotDryRunTests(unittest.TestCase):
    def test_date_mismatch_is_pending_and_never_write_eligible(self):
        sample = quotes()
        sample["gold_yfinance"] = make_quote(key="gold_yfinance", name="gold", source="test", instrument="gold", term="Cash", quote_type="Close", currency="USD", unit="USD", value=1000.0, observed_at="2026-08-27")
        with redirect_stdout(io.StringIO()):
            result = evaluate_pilot_dry_run(sample, COMPANY_MAIN_LAYOUT, [])
        self.assertEqual(result.final_status, "DATE_ALIGNMENT_PENDING")
        self.assertIsNone(result.target_business_date)
        self.assertEqual(result.safe_dict()["google_sheet_write"], "DISABLED")

    def test_duplicate_and_unsafe_rows_fail_closed(self):
        with redirect_stdout(io.StringIO()):
            duplicate = evaluate_pilot_dry_run(quotes(), COMPANY_MAIN_LAYOUT, [["2026/08/28"]])
            unsafe = evaluate_pilot_dry_run(quotes(), COMPANY_MAIN_LAYOUT, [["", "occupied"]])
        self.assertEqual(duplicate.final_status, "FAIL_CLOSED")
        self.assertEqual(duplicate.duplicate_status, "DUPLICATE")
        self.assertEqual(unsafe.row_safety_status, "FAIL")

    def test_missing_quote_and_anomaly_fail_closed(self):
        missing = quotes()
        missing["lead_lme_cash"] = make_quote(key="lead_lme_cash", name="lead", source="test", instrument="lead", term="Cash", quote_type="OFFER", currency="USD", unit="USD", value=None, error="missing")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(evaluate_pilot_dry_run(missing, COMPANY_MAIN_LAYOUT, []).final_status, "FAIL_CLOSED")
            anomalous = evaluate_pilot_dry_run(quotes(), COMPANY_MAIN_LAYOUT, [["2026/08/27"] + [1.0] * 11])
        self.assertEqual(anomalous.anomaly_status, "FAIL")

    def test_redacted_status_excludes_raw_values_and_write_is_forced_off(self):
        captured = {}
        def reader(sheet_id, credential_file, worksheet_name):
            captured.update(sheet_id=sheet_id, credential_file=credential_file, worksheet_name=worksheet_name)
            return COMPANY_MAIN_LAYOUT, []
        statuses = []
        with redirect_stdout(io.StringIO()), patch.dict(os.environ, {"ALLOW_GOOGLE_SHEET_WRITE": "1"}, clear=False):
            result = run_pilot_dry_run(sheet_id="not-logged", credential_file="not-logged.json", worksheet_name="Sheet1", browser_fetcher=lambda: {key: quote for key, quote in quotes().items() if key in SHEET_COLUMNS[:8]}, finance_fetcher=lambda: {key: quote for key, quote in quotes().items() if key in SHEET_COLUMNS[8:]}, sheet_reader=reader, status_writer=lambda status: (statuses.append(status.safe_dict()) or "local-status.json"))
            self.assertEqual(result, 0)
            self.assertEqual(os.environ["ALLOW_GOOGLE_SHEET_WRITE"], "0")
        self.assertEqual(captured["sheet_id"], "not-logged")
        self.assertNotIn("1000.0", str(statuses[0]))
        self.assertNotIn("not-logged", str(statuses[0]))


if __name__ == "__main__":
    unittest.main()
