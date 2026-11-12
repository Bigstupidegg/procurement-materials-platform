from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_signals.py"
spec = importlib.util.spec_from_file_location("build_signals", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def rules():
    return {
        "primarySource": "WORLD_BANK_PINK_SHEET",
        "comparisonSource": "FRED",
        "policy": {
            "worldBankDeterminesTrend": True,
            "fredUsedForCorroborationOnly": True,
            "automaticSupplierPriceDecision": False,
            "note": "test",
        },
        "thresholds": {
            "strongDecrease3MonthPercent": -8.0,
            "decrease3MonthPercent": -3.0,
            "decrease6MonthPercent": -5.0,
            "strongIncrease3MonthPercent": 8.0,
            "increase3MonthPercent": 3.0,
            "increase6MonthPercent": 5.0,
            "shortTermReversal1MonthPercent": 3.0,
            "rangeLowPercentile": 20.0,
            "rangeHighPercentile": 80.0,
            "sourceHighMeanAbsoluteDifferencePercent": 1.0,
            "sourceMediumMeanAbsoluteDifferencePercent": 3.0,
        },
    }


def observations(values):
    result = []
    year, month = 2024, 1
    for value in values:
        result.append({"period": f"{year:04d}-{month:02d}", "value": value})
        month += 1
        if month == 13:
            month = 1
            year += 1
    return result


def material():
    return {
        "id": "copper",
        "nameZh": "銅",
        "nameEn": "Copper",
        "currency": "USD",
        "unit": "公噸(MT)",
    }


def wb_series(values):
    obs = observations(values)
    return {
        "currency": "USD",
        "displayUnit": "公噸(MT)",
        "sourceUnit": "($/mt)",
        "latestPeriod": obs[-1]["period"],
        "observations": obs,
    }


def comparable(latest_pct=0.2, mean_pct=0.3):
    return {
        "comparisonAvailable": True,
        "latestDifferencePercentVsWorldBank": latest_pct,
        "recent12MonthMeanAbsoluteDifferencePercent": mean_pct,
    }


class SignalTests(unittest.TestCase):
    def test_strong_decrease_creates_reduction_signal(self):
        values = [120, 119, 118, 117, 116, 114, 112, 110, 108, 105, 102, 98, 94]
        result = module.build_material_signal(
            material(), wb_series(values), comparable(), rules()["thresholds"]
        )
        self.assertEqual(result["negotiationSignal"]["code"], "NEGOTIATE_REDUCTION")
        self.assertEqual(result["negotiationSignal"]["priority"], "HIGH")
        self.assertLess(result["changes"]["threeMonthPercent"], -8)
        self.assertEqual(result["sourceCorroboration"]["status"], "HIGH_AGREEMENT")

    def test_strong_increase_requires_cost_verification(self):
        values = [90, 91, 92, 93, 94, 96, 98, 100, 103, 106, 110, 114, 120]
        result = module.build_material_signal(
            material(), wb_series(values), comparable(), rules()["thresholds"]
        )
        self.assertEqual(result["negotiationSignal"]["code"], "VERIFY_STRONG_INCREASE")
        self.assertEqual(result["negotiationSignal"]["priority"], "HIGH")
        self.assertGreater(result["changes"]["threeMonthPercent"], 8)
        self.assertIn("材料占比", result["negotiationSignal"]["recommendedAction"])

    def test_range_bound_creates_monitor_signal(self):
        values = [100, 101, 99, 100, 101, 100, 99.5, 100, 100.5, 100, 100.4, 100.2, 100.1]
        result = module.build_material_signal(
            material(), wb_series(values), comparable(), rules()["thresholds"]
        )
        self.assertEqual(result["negotiationSignal"]["code"], "MONITOR_AND_HOLD")
        self.assertEqual(result["trend"]["code"], "RANGE_BOUND")

    def test_unit_mismatch_does_not_change_world_bank_signal(self):
        values = [120, 119, 118, 117, 116, 114, 112, 110, 108, 105, 102, 98, 94]
        comparison = {
            "comparisonAvailable": False,
            "comparisonReason": "dmtu與metric ton不可直接比較",
        }
        result = module.build_material_signal(
            material(), wb_series(values), comparison, rules()["thresholds"]
        )
        self.assertEqual(result["negotiationSignal"]["code"], "NEGOTIATE_REDUCTION")
        self.assertEqual(result["sourceCorroboration"]["status"], "UNIT_NOT_COMPARABLE")
        self.assertEqual(result["sourceCorroboration"]["confidence"], "MEDIUM")

    def test_payload_preserves_world_bank_primary_policy(self):
        materials = [material()]
        world_bank = {
            "source": "WORLD_BANK_PINK_SHEET",
            "isRealData": True,
            "dataset": {"latestPeriod": "2025-01", "workbookUpdatedOn": "2025-02-04", "download": {"sha256": "a"*64}},
            "series": {"copper": wb_series([100 + i for i in range(13)])},
        }
        comparison = {
            "isRealData": True,
            "primarySource": "WORLD_BANK_PINK_SHEET",
            "materials": {"copper": comparable()},
        }
        payload = module.build_signals_payload(
            world_bank, comparison, materials, rules(), "2026-08-04T00:00:00Z"
        )
        self.assertTrue(payload["isRealData"])
        self.assertEqual(payload["primarySource"], "WORLD_BANK_PINK_SHEET")
        self.assertFalse(payload["signalPolicy"]["automaticSupplierPriceDecision"])
        self.assertEqual(payload["summary"]["materialCount"], 1)
        self.assertEqual(payload["sourceSha256"], "a"*64)
        self.assertEqual(payload["materials"]["copper"]["changeBasePeriods"]["sixMonthPercent"], "2024-07")

    def test_rejects_unsorted_observations(self):
        series = wb_series([100 + i for i in range(13)])
        series["observations"][0], series["observations"][1] = (
            series["observations"][1],
            series["observations"][0],
        )
        with self.assertRaisesRegex(RuntimeError, "遞增排列"):
            module.build_material_signal(
                material(), series, comparable(), rules()["thresholds"]
            )


    def test_recent_calendar_gap_is_rejected(self):
        obs = observations([100.0] * 14)
        obs = [item for item in obs if item["period"] != "2024-08"]
        item = wb_series([100.0] * 13)
        item["observations"] = obs
        item["latestPeriod"] = obs[-1]["period"]
        with self.assertRaisesRegex(RuntimeError, "不連續"):
            module.build_material_signal(material(), item, comparable(), rules()["thresholds"])

    def test_latest_period_metadata_must_match_observation(self):
        item = wb_series([100.0] * 13)
        item["latestPeriod"] = "2024-12"
        with self.assertRaisesRegex(RuntimeError, "最後觀測值不一致"):
            module.build_material_signal(material(), item, comparable(), rules()["thresholds"])

    def test_future_sentinel_cannot_cross_dataset_origin(self):
        item = wb_series([100.0] * 14)
        world_bank = {
            "source": "WORLD_BANK_PINK_SHEET", "isRealData": True,
            "dataset": {"latestPeriod": "2025-01", "workbookUpdatedOn": "2025-02-04",
                        "download": {"sha256": "a" * 64}},
            "series": {"copper": item},
        }
        comparison = {"isRealData": True, "primarySource": "WORLD_BANK_PINK_SHEET",
                      "materials": {"copper": comparable()}}
        with self.assertRaisesRegex(RuntimeError, "最新月份不一致"):
            module.build_signals_payload(
                world_bank, comparison, [material()], rules(), "2025-02-04T00:00:00Z"
            )

    def test_conflicting_horizons_keep_original_rule_order(self):
        values = [100.0] * 13
        values[6], values[9], values[-1] = 120, 90, 100
        result = module.build_material_signal(
            material(), wb_series(values), comparable(), rules()["thresholds"]
        )
        self.assertGreater(result["changes"]["threeMonthPercent"], 0)
        self.assertLess(result["changes"]["sixMonthPercent"], 0)
        self.assertEqual(result["negotiationSignal"]["code"], "CHALLENGE_INCREASE")
        self.assertEqual(result["negotiationSignal"]["triggerWindow"], "6M+12M_AVERAGE")

    def test_percent_and_anchor_contract_remain_unscaled(self):
        values = [100.0] * 13
        values[6], values[9], values[-1] = 90, 95, 110
        result = module.build_material_signal(
            material(), wb_series(values), comparable(), rules()["thresholds"]
        )
        self.assertAlmostEqual(result["changes"]["threeMonthPercent"], (110 - 95) / 95 * 100, 5)
        self.assertAlmostEqual(result["changes"]["sixMonthPercent"], (110 - 90) / 90 * 100, 5)
        self.assertEqual(result["changeBasePeriods"]["threeMonthPercent"], "2024-10")
        self.assertEqual(result["changeBasePeriods"]["sixMonthPercent"], "2024-07")


if __name__ == "__main__":
    unittest.main()
