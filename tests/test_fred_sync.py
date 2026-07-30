import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "sync_fred.py"
SPEC = importlib.util.spec_from_file_location("sync_fred", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class FredSyncTests(unittest.TestCase):
    def test_validate_api_key(self):
        self.assertEqual(MODULE.validate_api_key("a" * 32), "a" * 32)
        with self.assertRaises(RuntimeError):
            MODULE.validate_api_key("ABC")

    def test_parse_metadata_monthly(self):
        payload = {"seriess": [{
            "id": "PZINCUSDM",
            "title": "Global price of Zinc",
            "frequency": "Monthly",
            "frequency_short": "M",
            "units": "U.S. Dollars per Metric Ton",
            "units_short": "USD/mt",
            "seasonal_adjustment": "Not Seasonally Adjusted",
            "last_updated": "2026-07-13 10:40:00-05",
        }]}
        result = MODULE.parse_series_metadata("PZINCUSDM", payload)
        self.assertEqual(result["frequencyShort"], "M")
        self.assertIn("Zinc", result["title"])

    def test_parse_observations_skips_missing_and_rejects_duplicates(self):
        observations = []
        for year in range(2020, 2025):
            for month in range(1, 13):
                observations.append({"date": f"{year}-{month:02d}-01", "value": "10.5"})
        observations.append({"date": "2025-01-01", "value": "."})
        result = MODULE.parse_observations("TEST", {"observations": observations})
        self.assertEqual(len(result), 60)
        self.assertEqual(result[-1]["period"], "2024-12")
        with self.assertRaises(RuntimeError):
            MODULE.parse_observations("TEST", {"observations": observations[:-1] + [observations[0]]})

    def test_build_status_keeps_world_bank_primary(self):
        fred = {"dataset": {
            "latestCommonPeriod": "2026-06",
            "latestAvailablePeriod": "2026-06",
            "latestPeriods": {"zinc": "2026-06"},
            "seriesCount": 1,
        }}
        previous = {
            "worldBank": {"status": "SUCCESS", "latestPeriod": "2026-06"},
            "isStale": False,
            "warnings": [],
        }
        result = MODULE.build_status("2026-07-30T00:00:00Z", fred, previous)
        self.assertEqual(result["dataMode"], "WORLD_BANK_PRIMARY")
        self.assertEqual(result["fred"]["role"], "INDEPENDENT_COMPARISON_ONLY")


if __name__ == "__main__":
    unittest.main()
