from __future__ import annotations

from datetime import date
import os
import unittest
from unittest.mock import patch

from scripts.c3_2_observation_canonicalization import YAHOO_UNCONFIRMED
from scripts.c3_2_shadow_observation_store import (
    build_shadow_observation_row,
    enforce_shadow_write_safety,
    plan_shadow_observation_append,
)
from scripts.company_market_core import MarketQuote


def quote(*, value: float = 90.0, observed_at: str = "2026-09-01") -> MarketQuote:
    return MarketQuote(
        key="brent_yfinance", name="Brent", source="Yahoo Finance / yfinance", instrument="BZ=F",
        term="Continuous Futures", quote_type="Close", currency="USD", unit="USD/bbl", value=value,
        fetched_at="2026-09-01T10:00:00+08:00", observed_at=observed_at, status="SUCCESS",
    )


class ShadowObservationStoreTests(unittest.TestCase):
    def test_same_day_yahoo_row_is_unconfirmed_and_deterministic(self):
        first = build_shadow_observation_row("brent_yfinance", quote(), evaluated_on=date(2026, 9, 1), collected_at="2026-09-01T10:00:00+08:00")
        second = build_shadow_observation_row("brent_yfinance", quote(), evaluated_on=date(2026, 9, 1), collected_at="2026-09-01T11:00:00+08:00")
        self.assertEqual(first[22], second[22])
        self.assertEqual(first[24], YAHOO_UNCONFIRMED)

    def test_plan_is_idempotent_for_same_value_reread(self):
        row = build_shadow_observation_row("brent_yfinance", quote(), evaluated_on=date(2026, 9, 1), collected_at="2026-09-01T10:00:00+08:00")
        reread = build_shadow_observation_row("brent_yfinance", quote(), evaluated_on=date(2026, 9, 1), collected_at="2026-09-01T11:00:00+08:00")
        plan = plan_shadow_observation_append([reread], [row])
        self.assertEqual((plan.status, len(plan.rows), plan.duplicate_same_count), ("READY", 0, 1))

    def test_id_collision_with_different_value_fails_closed(self):
        row = build_shadow_observation_row("brent_yfinance", quote(), evaluated_on=date(2026, 9, 1), collected_at="2026-09-01T10:00:00+08:00")
        changed = list(row)
        changed[5] = "999"
        plan = plan_shadow_observation_append([row], [changed])
        self.assertEqual((plan.status, plan.failure_reason), ("FAIL_CLOSED", "OBSERVATION_ID_VALUE_MISMATCH"))

    def test_shadow_safety_forces_formal_write_gate_off(self):
        with patch.dict(os.environ, {"ALLOW_GOOGLE_SHEET_WRITE": "1", "ALLOW_PENDING_RAW_WRITE": "1", "CONTROLLED_WRITE_APPROVAL": "x"}, clear=False):
            enforce_shadow_write_safety()
            self.assertEqual(os.environ["ALLOW_GOOGLE_SHEET_WRITE"], "0")
            self.assertEqual(os.environ["ALLOW_PENDING_RAW_WRITE"], "0")
            self.assertNotIn("CONTROLLED_WRITE_APPROVAL", os.environ)


if __name__ == "__main__":
    unittest.main()
