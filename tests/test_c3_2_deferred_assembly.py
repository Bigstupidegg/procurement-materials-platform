from __future__ import annotations

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

from scripts.c3_2_deferred_assembly import (
    ALLOW_PENDING_RAW_WRITE,
    REQUIRED_MATERIALS,
    PendingSnapshot,
    assemble_deferred_business_date,
    enforce_pending_raw_write_disabled,
    pending_raw_write_enabled,
)
from scripts.company_market_collector import YFINANCE_SPECS


TARGET = "2026-08-31"


def record(material_id: str, source_date: str = TARGET, *, price: float = 100.0, source_id: str = "free-source"):
    return PendingSnapshot(
        material_id=material_id, source_id=source_id, source_date=source_date, price=price,
        currency="USD", unit="unit", market_type="spot", collected_at="2026-08-31T10:00:00+08:00",
        source_status="SUCCESS", date_parse_status="PARSED", run_id="test-run",
        collector_version="C3.2", data_classification="INTERNAL_OPERATIONAL",
    )


def non_lme_records():
    return [record(material) for material in REQUIRED_MATERIALS if "LME" not in material]


class DeferredAssemblyTests(unittest.TestCase):
    def test_day_t_waits_for_delayed_lme_after_smm_and_yahoo_snapshots(self):
        result = assemble_deferred_business_date(TARGET, non_lme_records())
        self.assertEqual(result.status, "WAITING_FOR_LME_DELAYED_DATA")
        self.assertIsNone(result.business_date)

    def test_day_t_plus_one_assembles_all_real_target_date_records(self):
        result = assemble_deferred_business_date(TARGET, [record(material) for material in REQUIRED_MATERIALS])
        self.assertEqual(result.status, "ASSEMBLY_COMPLETE")
        self.assertEqual(result.business_date, TARGET)
        self.assertEqual(len(result.canonical_records), 11)

    def test_missing_smm_never_uses_a_later_smm_snapshot(self):
        records = [record(material) for material in REQUIRED_MATERIALS if material != "CU_SMM_CATHODE"]
        result = assemble_deferred_business_date(TARGET, records)
        self.assertEqual(result.status, "SMM_SNAPSHOT_MISSING")
        self.assertIsNone(result.business_date)

    def test_yahoo_target_date_record_can_complete_assembly(self):
        records = [record(material) for material in REQUIRED_MATERIALS]
        self.assertEqual(assemble_deferred_business_date(TARGET, records).status, "ASSEMBLY_COMPLETE")

    def test_ten_of_eleven_is_incomplete(self):
        result = assemble_deferred_business_date(TARGET, [record(material) for material in REQUIRED_MATERIALS[:-1]])
        self.assertEqual(result.status, "ASSEMBLY_INCOMPLETE")

    def test_invalid_source_status_or_date_parse_status_fails_closed(self):
        records = [record(material) for material in REQUIRED_MATERIALS]
        records[0] = replace(records[0], source_status="ERROR")
        self.assertEqual(assemble_deferred_business_date(TARGET, records).status, "ASSEMBLY_INCOMPLETE")
        records[0] = replace(records[0], source_status="SUCCESS", date_parse_status="UNRESOLVED")
        self.assertEqual(assemble_deferred_business_date(TARGET, records).status, "ASSEMBLY_INCOMPLETE")

    def test_wrong_source_date_fails_closed(self):
        records = [record(material) for material in REQUIRED_MATERIALS]
        records[-1] = record(REQUIRED_MATERIALS[-1], "2026-08-28")
        self.assertEqual(assemble_deferred_business_date(TARGET, records).status, "DATE_MISMATCH")

    def test_duplicate_same_value_is_safe_but_conflict_requires_human_review(self):
        records = [record(material) for material in REQUIRED_MATERIALS]
        same = assemble_deferred_business_date(TARGET, records + [record("GOLD_FUT")])
        self.assertEqual(same.status, "ASSEMBLY_COMPLETE")
        self.assertEqual(same.duplicate_status, "DUPLICATE_SAME_VALUE")
        conflict = assemble_deferred_business_date(TARGET, records + [record("GOLD_FUT", price=101.0)])
        self.assertEqual(conflict.status, "HUMAN_REVIEW_REQUIRED")
        self.assertEqual(conflict.duplicate_status, "DUPLICATE_CONFLICT")

    def test_execution_date_and_weekends_do_not_change_business_date(self):
        result = assemble_deferred_business_date(TARGET, [record(material) for material in REQUIRED_MATERIALS])
        self.assertEqual(result.business_date, TARGET)

    def test_silver_contract_multiplier_is_preserved(self):
        self.assertEqual(YFINANCE_SPECS["silver_yfinance"][2], 100.0)

    def test_pending_and_sheet_write_gates_are_forced_off_without_persistence(self):
        with patch.dict(os.environ, {ALLOW_PENDING_RAW_WRITE: "1", "ALLOW_GOOGLE_SHEET_WRITE": "1", "CONTROLLED_WRITE_APPROVAL": "approval"}, clear=False):
            enforce_pending_raw_write_disabled()
            self.assertEqual(os.environ[ALLOW_PENDING_RAW_WRITE], "0")
            self.assertEqual(os.environ["ALLOW_GOOGLE_SHEET_WRITE"], "0")
            self.assertNotIn("CONTROLLED_WRITE_APPROVAL", os.environ)
            self.assertFalse(pending_raw_write_enabled())


if __name__ == "__main__":
    unittest.main()
