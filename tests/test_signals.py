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
            "dataset": {"latestPeriod": "2025-01"},
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


if __name__ == "__main__":
    unittest.main()
