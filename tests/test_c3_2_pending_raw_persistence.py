from __future__ import annotations

from datetime import datetime
import os
import unittest
from unittest.mock import patch

from scripts.c3_2_pending_raw_persistence import (
    ALLOW_PENDING_RAW_WRITE, MARKET_RAW_COLUMNS, build_pending_rows, run_pending_raw_persistence_pilot,
)
from scripts.company_market_collector import make_quote


NOW = datetime.fromisoformat("2026-08-31T10:00:00+08:00")


def quote(key, *, value=100.0, date="2026-08-31", status="SUCCESS"):
    return make_quote(key=key, name=key, source="test", instrument=key, term="Spot", quote_type="Average",
                      currency="CNY" if key == "smm_electrolytic_copper" else "USD",
                      unit="CENT/OZ" if key == "silver_yfinance" else "USD/MT", value=value if status == "SUCCESS" else None,
                      observed_at=date if status == "SUCCESS" else None, error="missing" if status != "SUCCESS" else None)


def valid_quotes():
    return {
        "smm_electrolytic_copper": quote("smm_electrolytic_copper"), "brent_yfinance": quote("brent_yfinance"),
        "silver_yfinance": quote("silver_yfinance", value=200.0), "gold_yfinance": quote("gold_yfinance", value=300.0),
    }


class PendingRawPersistenceTests(unittest.TestCase):
    def _run(self, quotes=None, existing=(), *, gate="1", readback=True):
        stored = list(existing)
        written = []
        audits = []
        def reader(_id, _credential):
            return MARKET_RAW_COLUMNS, list(stored)
        def writer(_id, _credential, rows):
            written.extend(rows)
            if readback:
                stored.extend(rows)
        with patch.dict(os.environ, {ALLOW_PENDING_RAW_WRITE: gate, "ALLOW_GOOGLE_SHEET_WRITE": "1", "CONTROLLED_WRITE_APPROVAL": "bad"}, clear=False):
            result = run_pending_raw_persistence_pilot(
                sheet_id="not-logged", credential_file="not-logged", quote_fetcher=lambda: quotes or valid_quotes(),
                raw_reader=reader, raw_writer=writer, audit_writer=lambda *_args: audits.append(_args[-1]), now=NOW,
            )
            self.assertEqual(os.environ["ALLOW_GOOGLE_SHEET_WRITE"], "0")
            self.assertNotIn("CONTROLLED_WRITE_APPROVAL", os.environ)
        return result, written, audits

    def test_default_gate_fails_closed_without_market_raw_write(self):
        result, written, audits = self._run(gate="0")
        self.assertEqual(result.final_status, "FAIL_CLOSED")
        self.assertEqual(written, [])
        self.assertEqual(result.appended_count, 0)
        self.assertEqual(audits[0][15], "FALSE")

    def test_enabled_gate_writes_only_pending_rows_with_blank_business_date(self):
        result, written, audits = self._run()
        self.assertEqual(result.appended_count, 4)
        self.assertEqual(len(written), 4)
        self.assertTrue(all(row[1] == "" and row[19] == "INTERNAL_OPERATIONAL" for row in written))
        self.assertEqual(audits[0][15], "FALSE")
        self.assertEqual(audits[0][16], 4)
        self.assertEqual(audits[0][22], "TRUE")

    def test_smm_missing_date_or_price_is_not_written(self):
        samples = valid_quotes()
        samples["smm_electrolytic_copper"] = quote("smm_electrolytic_copper", status="ERROR")
        result, written, _ = self._run(samples)
        self.assertEqual(result.appended_count, 3)
        self.assertIn("CU_SMM_CATHODE", result.missing_materials)
        self.assertEqual(result.error_code, "SMM_SNAPSHOT_MISSING")
        self.assertNotIn("CU_SMM_CATHODE", [row[2] for row in written])

    def test_yahoo_and_silver_multiplier_contract_are_preserved(self):
        rows, missing = build_pending_rows(valid_quotes(), run_id="run", now=NOW)
        self.assertEqual(missing, ())
        silver = next(row for row in rows if row[2] == "SILVER_FUT")
        self.assertEqual(silver[5], 200.0)
        self.assertEqual(silver[7], "CENT/OZ")

    def test_same_value_duplicate_is_not_appended_twice(self):
        rows, _ = build_pending_rows(valid_quotes(), run_id="old", now=NOW)
        result, written, _ = self._run(existing=[rows[0]])
        self.assertEqual(result.duplicate_same_count, 1)
        self.assertEqual(result.appended_count, 3)
        self.assertNotIn("CU_SMM_CATHODE", [row[2] for row in written])

    def test_conflict_stops_all_new_rows_and_requires_human_review(self):
        rows, _ = build_pending_rows(valid_quotes(), run_id="old", now=NOW)
        conflict = list(rows[0]); conflict[5] = 999.0
        result, written, audits = self._run(existing=[tuple(conflict)])
        self.assertEqual(result.final_status, "HUMAN_REVIEW_REQUIRED")
        self.assertEqual(written, [])
        self.assertEqual(audits[0][20], "DUPLICATE_CONFLICT")

    def test_partial_collection_writes_only_valid_rows(self):
        samples = valid_quotes()
        samples.pop("gold_yfinance")
        result, written, _ = self._run(samples)
        self.assertEqual(result.appended_count, 3)
        self.assertEqual(len(written), 3)
        self.assertIn("GOLD_FUT", result.missing_materials)

    def test_readback_mismatch_fails_closed_and_audits(self):
        result, written, audits = self._run(readback=False)
        self.assertEqual(result.final_status, "FAIL_CLOSED")
        self.assertEqual(result.error_code, "READBACK_MISMATCH")
        self.assertEqual(result.appended_count, 0)
        self.assertEqual(audits[0][17], "MISMATCH")

    def test_collection_failure_still_appends_redacted_audit_without_raw_write(self):
        stored, audits = [], []
        with patch.dict(os.environ, {ALLOW_PENDING_RAW_WRITE: "1"}, clear=False):
            result = run_pending_raw_persistence_pilot(
                sheet_id="not-logged", credential_file="not-logged",
                quote_fetcher=lambda: (_ for _ in ()).throw(RuntimeError("provider failure")),
                raw_reader=lambda *_args: (MARKET_RAW_COLUMNS, stored),
                raw_writer=lambda *_args: self.fail("must not write"),
                audit_writer=lambda *_args: audits.append(_args[-1]), now=NOW,
            )
        self.assertEqual(result.error_code, "COLLECTION_FAILURE")
        self.assertEqual(result.appended_count, 0)
        self.assertEqual(len(audits), 1)


if __name__ == "__main__":
    unittest.main()
