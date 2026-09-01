from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from scripts.c3_2_observation_migration import (
    MIGRATION_VERSION, V2_COLUMNS, build_legacy_backfill_plan, enforce_phase_a_safety,
    reconcile_legacy_backfill,
)
from scripts.c3_2_pending_raw_persistence import MARKET_RAW_COLUMNS


def raw_row(record_id: str, price: str = "100"):
    values = [""] * len(MARKET_RAW_COLUMNS)
    values[0] = record_id
    values[2] = "BRENT_FUT"
    values[3] = "YFINANCE_BZ=F"
    values[4] = "2026/08/31"
    values[5] = price
    values[7] = "USD/bbl"
    values[8] = "FUTURE"
    values[9] = "2026-08-31T16:00:00+08:00"
    return values


class ObservationMigrationTests(unittest.TestCase):
    def test_backfill_is_deterministic_and_preserves_raw_columns(self):
        plan_a = build_legacy_backfill_plan(MARKET_RAW_COLUMNS, [raw_row("legacy-1")])
        plan_b = build_legacy_backfill_plan(MARKET_RAW_COLUMNS, [raw_row("legacy-1")])
        self.assertEqual(plan_a, plan_b)
        migrated = plan_a.expected_rows[0]
        self.assertEqual(migrated[:len(MARKET_RAW_COLUMNS)], tuple(raw_row("legacy-1")))
        self.assertEqual(migrated[len(MARKET_RAW_COLUMNS) + 1], "")
        self.assertEqual(migrated[len(MARKET_RAW_COLUMNS) + 2], "LEGACY_UNVERIFIED")
        self.assertEqual(migrated[len(MARKET_RAW_COLUMNS) + 3], "LEGACY_UNVERIFIED")
        self.assertEqual(migrated[-1], MIGRATION_VERSION)

    def test_source_row_makes_identical_legacy_observations_unique(self):
        plan = build_legacy_backfill_plan(MARKET_RAW_COLUMNS, [raw_row("same"), raw_row("same")])
        ids = [row[len(MARKET_RAW_COLUMNS)] for row in plan.expected_rows]
        self.assertEqual(len(set(ids)), 2)

    def test_reconciliation_allows_safe_idempotent_rerun(self):
        plan = build_legacy_backfill_plan(MARKET_RAW_COLUMNS, [raw_row("one"), raw_row("two")])
        initial = reconcile_legacy_backfill(plan, V2_COLUMNS, [])
        self.assertEqual((initial.status, initial.append_count), ("READY", 2))
        rerun = reconcile_legacy_backfill(plan, V2_COLUMNS, plan.expected_rows)
        self.assertEqual((rerun.status, rerun.matched_count, rerun.append_count), ("READY", 2, 0))

    def test_mismatched_or_unknown_rows_fail_closed(self):
        plan = build_legacy_backfill_plan(MARKET_RAW_COLUMNS, [raw_row("one")])
        changed = list(plan.expected_rows[0]); changed[5] = "101"
        self.assertEqual(reconcile_legacy_backfill(plan, V2_COLUMNS, [changed]).failure_reason, "OBSERVATION_VALUE_MISMATCH")
        unknown = list(plan.expected_rows[0]); unknown[len(MARKET_RAW_COLUMNS)] = "legacy-unknown"
        self.assertEqual(reconcile_legacy_backfill(plan, V2_COLUMNS, [unknown]).failure_reason, "UNEXPECTED_OBSERVATION_ID")

    def test_shadow_observation_does_not_block_legacy_reconciliation(self):
        plan = build_legacy_backfill_plan(MARKET_RAW_COLUMNS, [raw_row("one")])
        shadow = list(plan.expected_rows[0]); shadow[len(MARKET_RAW_COLUMNS)] = "shadow-confirmed"
        result = reconcile_legacy_backfill(plan, V2_COLUMNS, [plan.expected_rows[0], shadow])
        self.assertEqual((result.status, result.matched_count, result.append_count), ("READY", 1, 0))

    def test_duplicate_observation_id_fails_closed(self):
        plan = build_legacy_backfill_plan(MARKET_RAW_COLUMNS, [raw_row("one")])
        self.assertEqual(reconcile_legacy_backfill(plan, V2_COLUMNS, [plan.expected_rows[0], plan.expected_rows[0]]).failure_reason, "OBSERVATION_ID_NOT_UNIQUE")

    def test_phase_a_forces_all_write_gates_off(self):
        with patch.dict(os.environ, {"ALLOW_GOOGLE_SHEET_WRITE": "1", "ALLOW_PENDING_RAW_WRITE": "1", "CONTROLLED_WRITE_APPROVAL": "approval"}, clear=False):
            enforce_phase_a_safety()
            self.assertEqual(os.environ["ALLOW_GOOGLE_SHEET_WRITE"], "0")
            self.assertEqual(os.environ["ALLOW_PENDING_RAW_WRITE"], "0")
            self.assertNotIn("CONTROLLED_WRITE_APPROVAL", os.environ)


if __name__ == "__main__":
    unittest.main()
