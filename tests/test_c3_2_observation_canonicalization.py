from __future__ import annotations

from datetime import date
import unittest

from scripts.c3_2_deferred_assembly import REQUIRED_MATERIALS, assemble_deferred_canonical_business_date
from scripts.c3_2_observation_canonicalization import (
    YAHOO_CONFIRMED, YAHOO_UNCONFIRMED, RawObservation, canonicalize_daily_observations,
    classify_yahoo_close, confirm_yahoo_history_close,
)


TARGET = "2026-08-31"


def observation(material_id: str, *, price: float = 100.0, kind: str | None = None, observed_at: str = "2026-08-31T16:00:00+08:00") -> RawObservation:
    yahoo = material_id in {"BRENT_FUT", "SILVER_FUT", "GOLD_FUT"}
    return RawObservation(
        record_id=f"{material_id}-{observed_at}", material_id=material_id,
        source_id=("YFINANCE_" + material_id if yahoo else "SMM" if material_id == "CU_SMM_CATHODE" else "LME"),
        source_date=TARGET, price=price, currency="USD", unit="unit",
        market_type="FUTURE" if yahoo else "SPOT", observation_at=observed_at,
        observation_kind=kind or (YAHOO_CONFIRMED if yahoo else "DAILY_SNAPSHOT"),
    )


class ObservationCanonicalizationTests(unittest.TestCase):
    def test_yahoo_intraday_is_not_a_daily_canonical_value(self):
        intraday = observation("BRENT_FUT", kind=YAHOO_UNCONFIRMED, observed_at="2026-08-31T10:00:00+08:00")
        result = canonicalize_daily_observations(TARGET, [intraday])
        self.assertEqual(result.status, "CANONICALIZATION_INCOMPLETE")
        self.assertIn("YAHOO_DAILY_CLOSE_UNCONFIRMED", result.failure_reason)

    def test_yahoo_final_close_becomes_the_only_canonical_value(self):
        intraday = observation("BRENT_FUT", price=90.0, kind=YAHOO_UNCONFIRMED, observed_at="2026-08-31T10:00:00+08:00")
        final = observation("BRENT_FUT", price=91.0, kind=YAHOO_CONFIRMED, observed_at="2026-09-01T10:00:00+08:00")
        result = canonicalize_daily_observations(TARGET, [intraday, final])
        self.assertEqual(result.status, "CANONICALIZATION_COMPLETE")
        self.assertEqual(result.canonical_records[0].observation.price, 91.0)
        self.assertEqual(result.canonical_records[0].canonical_reason, YAHOO_CONFIRMED)

    def test_next_day_confirmed_close_may_differ_from_intraday_without_conflict(self):
        intraday = observation("GOLD_FUT", price=100.0, kind=YAHOO_UNCONFIRMED)
        confirmed = observation("GOLD_FUT", price=101.0, kind=YAHOO_CONFIRMED, observed_at="2026-09-01T10:00:00+08:00")
        result = canonicalize_daily_observations(TARGET, [intraday, confirmed])
        self.assertEqual(result.status, "CANONICALIZATION_COMPLETE")
        self.assertEqual(result.canonical_records[0].observation.price, 101.0)

    def test_conflicting_final_closes_require_human_review(self):
        first = observation("GOLD_FUT", price=100.0, observed_at="2026-08-31T16:00:00+08:00")
        second = observation("GOLD_FUT", price=101.0, observed_at="2026-08-31T17:00:00+08:00")
        result = canonicalize_daily_observations(TARGET, [first, second])
        self.assertEqual(result.status, "HUMAN_REVIEW_REQUIRED")
        self.assertEqual(result.conflict_materials, ("GOLD_FUT",))

    def test_yahoo_finality_needs_next_day_historical_close(self):
        self.assertEqual(classify_yahoo_close(TARGET, evaluated_on=date(2026, 8, 31), historical_date_present=True, close_parseable=True), YAHOO_UNCONFIRMED)
        self.assertEqual(classify_yahoo_close(TARGET, evaluated_on=date(2026, 9, 1), historical_date_present=False, close_parseable=True), YAHOO_UNCONFIRMED)
        self.assertEqual(classify_yahoo_close(TARGET, evaluated_on=date(2026, 9, 1), historical_date_present=True, close_parseable=True), YAHOO_CONFIRMED)

    def test_historical_adapter_requires_the_target_date_and_parseable_close(self):
        self.assertEqual(confirm_yahoo_history_close(TARGET, evaluated_on=date(2026, 9, 1), history_rows=[("2026-08-30", 100.0)]), YAHOO_UNCONFIRMED)
        self.assertEqual(confirm_yahoo_history_close(TARGET, evaluated_on=date(2026, 9, 1), history_rows=[(TARGET, "not-a-close")]), YAHOO_UNCONFIRMED)
        self.assertEqual(confirm_yahoo_history_close(TARGET, evaluated_on=date(2026, 9, 1), history_rows=[(TARGET, 101.0)]), YAHOO_CONFIRMED)

    def test_same_value_versions_are_a_safe_duplicate(self):
        first = observation("SILVER_FUT", price=100.0, observed_at="2026-08-31T16:00:00+08:00")
        second = observation("SILVER_FUT", price=100.0, observed_at="2026-08-31T17:00:00+08:00")
        result = canonicalize_daily_observations(TARGET, [first, second])
        self.assertEqual(result.status, "CANONICALIZATION_COMPLETE")
        self.assertEqual(result.duplicate_same_count, 1)
        self.assertEqual(result.canonical_records[0].observation.record_id, second.record_id)

    def test_deferred_assembly_accepts_canonical_records_only(self):
        result = canonicalize_daily_observations(TARGET, [observation(material) for material in REQUIRED_MATERIALS])
        assembly = assemble_deferred_canonical_business_date(TARGET, result.canonical_records)
        self.assertEqual(assembly.status, "ASSEMBLY_COMPLETE")
        self.assertEqual(len(assembly.canonical_records), 11)

    def test_deferred_assembly_rejects_noncanonical_observations(self):
        assembly = assemble_deferred_canonical_business_date(TARGET, [observation("BRENT_FUT")])
        self.assertEqual(assembly.status, "ASSEMBLY_INCOMPLETE")
        self.assertEqual(assembly.failure_reason, "NON_CANONICAL_RECORD")


if __name__ == "__main__":
    unittest.main()
