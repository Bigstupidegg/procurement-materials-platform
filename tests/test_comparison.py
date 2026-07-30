import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_comparison.py"
SPEC = importlib.util.spec_from_file_location("build_comparison", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def series(values, display_unit="公噸(MT)", source_unit="($/mt)"):
    observations = [{"period": period, "value": value} for period, value in values]
    return {
        "latestPeriod": observations[-1]["period"],
        "displayUnit": display_unit,
        "sourceUnit": source_unit,
        "units": "U.S. Dollars per Metric Ton",
        "fredSeriesId": "TEST",
        "observations": observations,
    }


class ComparisonTests(unittest.TestCase):
    def test_direct_comparison(self):
        material = {"id": "zinc", "nameZh": "鋅", "nameEn": "Zinc"}
        wb = series([("2026-05", 100), ("2026-06", 110)])
        fred = series([("2026-05", 102), ("2026-06", 111)])
        result = MODULE.build_material_comparison(material, wb, fred)
        self.assertTrue(result["comparisonAvailable"])
        self.assertAlmostEqual(result["latestDifferencePercentVsWorldBank"], 0.909091)

    def test_iron_ore_not_directly_compared(self):
        material = {"id": "iron_ore", "nameZh": "鐵礦砂", "nameEn": "Iron Ore"}
        wb = series([("2026-06", 100)], "乾公噸單位(dmtu)", "($/dmtu)")
        fred = series([("2026-06", 103)], "乾公噸單位(dmtu)")
        result = MODULE.build_material_comparison(material, wb, fred)
        self.assertFalse(result["comparisonAvailable"])
        self.assertEqual(result["observations"], [])


if __name__ == "__main__":
    unittest.main()
