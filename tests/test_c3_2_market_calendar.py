from __future__ import annotations

from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from scripts.c3_2_market_calendar import (
    assess_source_readiness,
    execution_date_context,
    governed_open_target_dates,
)
from scripts.c3_2_observation_canonicalization import RawObservation


def observation(
    source_id: str,
    source_date: str,
    kind: str,
    *,
    observation_at: str = "2026-09-07T08:05:04+08:00",
    record_id: str = "shadow-test",
) -> RawObservation:
    return RawObservation(
        record_id, "CU_LME_CASH", source_id, source_date, 100.0, "USD", "USD/MT",
        "SPOT", observation_at, kind, "SUCCESS", "PARSED",
    )


class MarketCalendarTests(unittest.TestCase):
    def test_scheduler_execution_and_local_business_date_are_separate(self):
        execution = execution_date_context(datetime(2026, 9, 7, 8, 5, tzinfo=ZoneInfo("Asia/Taipei")))
        self.assertEqual(execution.scheduler_execution_at, "2026-09-07T08:05:00+08:00")
        self.assertEqual(execution.local_calendar_date, "2026-09-07")
        self.assertIsNone(execution.local_business_date)
        self.assertEqual(execution.local_calendar_status, "WEEKDAY_HOLIDAY_STATUS_UNVERIFIED")
        self.assertEqual(execution.canonical_target_basis, "EXPLICIT_SOURCE_MARKET_DATE_ONLY")

    def test_authoritative_local_holiday_can_fail_closed(self):
        execution = datetime(2026, 9, 7, 16, 30, tzinfo=ZoneInfo("Asia/Taipei"))
        holiday = execution_date_context(execution, local_holidays={"2026-09-07"})
        business = execution_date_context(execution, local_holidays=set())
        self.assertEqual((holiday.local_calendar_status, holiday.local_business_date), ("HOLIDAY", None))
        self.assertEqual((business.local_calendar_status, business.local_business_date), ("BUSINESS_DAY", "2026-09-07"))

    def test_2026_09_07_recovery_never_targets_sunday_2026_09_06(self):
        sunday = observation("LME_CASH_OFFER", "2026-09-06", "DAILY_SNAPSHOT")
        self.assertEqual(assess_source_readiness(sunday).status, "INVALID")
        self.assertEqual(governed_open_target_dates([sunday]), [])

    def test_smm_and_delayed_lme_use_explicit_market_and_availability_dates(self):
        smm = observation("SMM_1_COPPER_CATHODE", "2026-09-07", "DAILY_SNAPSHOT")
        lme = observation("LME_CASH_OFFER", "2026-09-04", "DAILY_SNAPSHOT")
        self.assertEqual(assess_source_readiness(smm).canonical_target_date, "2026-09-07")
        lme_status = assess_source_readiness(lme)
        self.assertEqual(lme_status.source_availability_date, "2026-09-07")
        self.assertEqual(lme_status.canonical_target_date, "2026-09-04")

    def test_yahoo_requires_later_exact_date_confirmation(self):
        pending = observation("YFINANCE_BZ=F", "2026-09-04", "YAHOO_UNCONFIRMED")
        same_day_confirmed = observation(
            "YFINANCE_BZ=F", "2026-09-04", "YAHOO_DAILY_CLOSE_CONFIRMED",
            observation_at="2026-09-04T23:00:00+08:00",
        )
        mature = observation("YFINANCE_BZ=F", "2026-09-04", "YAHOO_DAILY_CLOSE_CONFIRMED")
        self.assertEqual(assess_source_readiness(pending).status, "PENDING")
        self.assertEqual(assess_source_readiness(same_day_confirmed).status, "INVALID")
        self.assertEqual(assess_source_readiness(mature).canonical_target_date, "2026-09-04")

    def test_missing_holiday_source_date_is_never_inferred_from_availability(self):
        missing = observation("SMM_1_COPPER_CATHODE", "", "DAILY_SNAPSHOT")
        readiness = assess_source_readiness(missing)
        self.assertEqual(readiness.status, "INVALID")
        self.assertIsNone(readiness.canonical_target_date)
        self.assertEqual(governed_open_target_dates([missing]), [])


if __name__ == "__main__":
    unittest.main()
