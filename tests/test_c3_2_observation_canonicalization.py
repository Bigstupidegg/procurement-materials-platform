from __future__ import annotations

import unittest

from scripts.c3_2_deferred_assembly import REQUIRED_MATERIALS, assemble_deferred_canonical_business_date
from scripts.c3_2_observation_canonicalization import RawObservation, canonicalize_daily_observations


TARGET = "2026-08-31"


def observation(material_id: str, *, price: float = 100.0, kind: str | None = None, observed_at: str = "2026-08-31T16:00:00+08:00") -> RawObservation:
    yahoo = material_id in {"BRENT_FUT", "SILVER_FUT", "GOLD_FUT"}
    return RawObservation(
        record_id=f"{material_id}-{observed_at}", material_id=material_id,
        source_id=("YFINANCE_" + material_id if yahoo else "SMM" if material_id == "CU_SMM_CATHODE" else "LME"),
        source_date=TARGET, price=price, currency="USD", unit="unit",
        market_type="FUTURE" if yahoo else "SPOT", observation_at=observed_at,
        observation_kind=kind or ("FINAL_DAILY_CLOSE" if yahoo else "DAILY_SNAPSHOT"),
    )


class ObservationCanonicalizationTests(unittest.TestCase):
    def test_yahoo_intraday_is_not_a_daily_canonical_value(self):
        intraday = observation("BRENT_FUT", kind="INTRADAY", observed_at="2026-08-31T10:00:00+08:00")
        result = canonicalize_daily_observations(TARGET, [intraday])
        self.assertEqual(result.status, "CANONICALIZATION_INCOMPLETE")
        self.assertIn("FINAL_DAILY_CLOSE_MISSING", result.failure_reason)

    def test_yahoo_final_close_becomes_the_only_canonical_value(self):
        intraday = observation("BRENT_FUT", price=90.0, kind="INTRADAY", observed_at="2026-08-31T10:00:00+08:00")
        final = observation("BRENT_FUT", price=91.0, kind="FINAL_DAILY_CLOSE", observed_at="2026-08-31T16:00:00+08:00")
        result = canonicalize_daily_observations(TARGET, [intraday, final])
        self.assertEqual(result.status, "CANONICALIZATION_COMPLETE")
        self.assertEqual(result.canonical_records[0].observation.price, 91.0)
        self.assertEqual(result.canonical_records[0].canonical_reason, "YAHOO_FINAL_DAILY_CLOSE")

    def test_conflicting_final_closes_require_human_review(self):
        first = observation("GOLD_FUT", price=100.0, observed_at="2026-08-31T16:00:00+08:00")
        second = observation("GOLD_FUT", price=101.0, observed_at="2026-08-31T17:00:00+08:00")
        result = canonicalize_daily_observations(TARGET, [first, second])
        self.assertEqual(result.status, "HUMAN_REVIEW_REQUIRED")
        self.assertEqual(result.conflict_materials, ("GOLD_FUT",))

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
