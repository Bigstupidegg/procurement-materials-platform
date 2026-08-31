from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime
import io
import os
import unittest
from unittest.mock import patch

from scripts.c3_2_pilot_dry_run import (
    COMPANY_MAIN_LAYOUT,
    RUN_AUDIT_COLUMNS,
    build_run_audit_row,
    evaluate_pilot_dry_run,
    persist_redacted_status,
    run_pilot_dry_run,
)
from scripts.company_market_collector import COMPANY_MAIN_LAYOUT, SHEET_COLUMNS, make_quote


def quotes(date_text: str = "2026-08-28"):
    return {
        key: make_quote(key=key, name=key, source="test", instrument=key, term="Cash", quote_type="OFFER", currency="USD", unit="USD/MT", value=1000.0, observed_at=date_text)
        for key in SHEET_COLUMNS
    }


class PilotDryRunTests(unittest.TestCase):
    def _run_inputs(self, date_text="2026-08-28"):
        sample = quotes(date_text)
        return (
            lambda: {key: sample[key] for key in SHEET_COLUMNS[:8]},
            lambda: {key: sample[key] for key in SHEET_COLUMNS[8:]},
            lambda _sheet_id, _credential, _worksheet: (COMPANY_MAIN_LAYOUT, []),
        )

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
            result = run_pilot_dry_run(sheet_id="not-logged", credential_file="not-logged.json", worksheet_name="Sheet1", browser_fetcher=lambda: {key: quote for key, quote in quotes().items() if key in SHEET_COLUMNS[:8]}, finance_fetcher=lambda: {key: quote for key, quote in quotes().items() if key in SHEET_COLUMNS[8:]}, sheet_reader=reader, status_writer=lambda status: (statuses.append(status.safe_dict()) or "local-status.json"), audit_writer=lambda *_args: None)
            self.assertEqual(result, 0)
            self.assertEqual(os.environ["ALLOW_GOOGLE_SHEET_WRITE"], "0")
        self.assertEqual(captured["sheet_id"], "not-logged")
        self.assertNotIn("1000.0", str(statuses[0]))
        self.assertNotIn("not-logged", str(statuses[0]))

    def test_date_mismatch_appends_redacted_audit_without_market_writes(self):
        browser, finance, reader = self._run_inputs()
        sample = quotes("2026-08-28")
        sample["gold_yfinance"] = make_quote(key="gold_yfinance", name="gold", source="test", instrument="gold", term="Close", quote_type="Close", currency="USD", unit="USD", value=1000.0, observed_at="2026-08-27")
        audit_rows = []
        with redirect_stdout(io.StringIO()):
            result = run_pilot_dry_run(
                sheet_id="id-not-logged", credential_file="credential-not-logged", worksheet_name="Sheet1",
                browser_fetcher=lambda: {key: sample[key] for key in SHEET_COLUMNS[:8]},
                finance_fetcher=lambda: {key: sample[key] for key in SHEET_COLUMNS[8:]},
                sheet_reader=reader, status_writer=lambda _status: "local-status.json",
                audit_writer=lambda _id, _credential, row: audit_rows.append(row),
            )
        self.assertEqual(result, 1)
        self.assertEqual(len(audit_rows), 1)
        row = audit_rows[0]
        self.assertEqual(len(row), len(RUN_AUDIT_COLUMNS))
        self.assertEqual(row[9], "MISMATCH")
        self.assertEqual(row[10], "")
        self.assertEqual(row[15:17], ("FALSE", 0))
        self.assertEqual(row[22], "FALSE")
        self.assertNotIn("1000.0", str(row))
        self.assertNotIn("id-not-logged", str(row))

    def test_collector_failure_still_audits_and_audit_failure_is_explicit(self):
        audit_rows = []
        with redirect_stdout(io.StringIO()):
            result = run_pilot_dry_run(
                sheet_id="id", credential_file="credential", worksheet_name="Sheet1",
                browser_fetcher=lambda: (_ for _ in ()).throw(RuntimeError("upstream")),
                finance_fetcher=lambda: {}, sheet_reader=lambda *_args: (COMPANY_MAIN_LAYOUT, []),
                status_writer=lambda _status: "local-status.json",
                audit_writer=lambda _id, _credential, row: audit_rows.append(row),
            )
        self.assertEqual(result, 1)
        self.assertEqual(audit_rows[0][8], "FAIL")
        self.assertEqual(audit_rows[0][20], "COLLECTION_OR_INSPECTION_FAILURE")

        browser, finance, reader = self._run_inputs()
        with redirect_stdout(io.StringIO()):
            result = run_pilot_dry_run(
                sheet_id="id", credential_file="credential", worksheet_name="Sheet1",
                browser_fetcher=browser, finance_fetcher=finance, sheet_reader=reader,
                status_writer=lambda _status: "local-status.json",
                audit_writer=lambda *_args: (_ for _ in ()).throw(RuntimeError("audit unavailable")),
            )
        self.assertEqual(result, 1)

    def test_duplicate_run_ids_are_distinct_and_audit_is_not_market_data(self):
        result = evaluate_pilot_dry_run(quotes(), COMPANY_MAIN_LAYOUT, [])
        first = build_run_audit_row(result, started_at="a", finished_at="b", success_count=11)
        second = build_run_audit_row(result, started_at="c", finished_at="d", success_count=11)
        self.assertNotEqual(first[0], second[0])
        self.assertEqual(first[15:17], ("FALSE", 0))
        self.assertNotIn("1000.0", str(first))


if __name__ == "__main__":
    unittest.main()
