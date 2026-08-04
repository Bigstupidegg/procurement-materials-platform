import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "sync_fred.py"
SPEC = importlib.util.spec_from_file_location("sync_fred", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class FredSyncTests(unittest.TestCase):
    def test_validate_series_id(self):
        self.assertEqual(MODULE.validate_series_id("PZINCUSDM"), "PZINCUSDM")
        with self.assertRaises(RuntimeError):
            MODULE.validate_series_id("bad id")

    def test_parse_csv_observations(self):
        lines = ["observation_date,PZINCUSDM"]
        for year in range(2020, 2025):
            for month in range(1, 13):
                value = "." if (year == 2020 and month == 1) else "10.5"
                lines.append(f"{year}-{month:02d}-01,{value}")
        lines.append("2025-01-01,11.25")
        result = MODULE.parse_csv_observations("PZINCUSDM", "\n".join(lines))
        self.assertEqual(len(result), 60)
        self.assertEqual(result[-1]["period"], "2025-01")

    def test_parse_csv_accepts_date_header(self):
        lines = ["DATE,PZINCUSDM"]
        for year in range(2020, 2025):
            for month in range(1, 13):
                lines.append(f"{year}-{month:02d}-01,10")
        result = MODULE.parse_csv_observations("PZINCUSDM", "\n".join(lines))
        self.assertEqual(result[0]["period"], "2020-01")

    def test_parse_csv_rejects_duplicate_month(self):
        lines = ["observation_date,PZINCUSDM"]
        for year in range(2020, 2025):
            for month in range(1, 13):
                lines.append(f"{year}-{month:02d}-01,10")
        lines.append("2024-12-15,11")
        with self.assertRaises(RuntimeError):
            MODULE.parse_csv_observations("PZINCUSDM", "\n".join(lines))

    def test_build_status_keeps_world_bank_primary(self):
        fred = {"dataset": {
            "latestCommonPeriod": "2026-06",
            "latestAvailablePeriod": "2026-06",
            "latestPeriods": {"zinc": "2026-06"},
            "seriesCount": 1,
            "downloadMethod": "PUBLIC_GRAPH_CSV_EXPORT",
        }}
        previous = {
            "worldBank": {"status": "SUCCESS", "latestPeriod": "2026-06"},
            "isStale": False,
            "warnings": [],
        }
        result = MODULE.build_status("2026-07-30T00:00:00Z", fred, previous)
        self.assertEqual(result["dataMode"], "WORLD_BANK_PRIMARY")
        self.assertEqual(result["fred"]["role"], "INDEPENDENT_COMPARISON_ONLY")
        self.assertFalse(result["fred"]["apiKeyRequired"])


if __name__ == "__main__":
    unittest.main()
