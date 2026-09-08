from __future__ import annotations

from pathlib import Path
from datetime import date
import unittest

from scripts.c3_2_observation_canonicalization import RawObservation, YAHOO_UNCONFIRMED
from scripts.c3_2_scheduled_shadow_runner import _eligible_yahoo_confirmation_dates, _open_shadow_source_dates

ROOT = Path(__file__).resolve().parents[1]


class ScheduledShadowRunnerTests(unittest.TestCase):
    def test_prior_yahoo_observation_is_eligible_for_next_day_reevaluation(self):
        observations = [RawObservation(
            "id", "BRENT_FUT", "YFINANCE_BZ=F", "2026-09-03", 70.0, "USD", "USD/bbl", "FUTURE",
            "2026-09-03T16:30:00+08:00", YAHOO_UNCONFIRMED,
        )]
        self.assertEqual(_eligible_yahoo_confirmation_dates(observations, evaluated_on=date(2026, 9, 4)), ["2026-09-03"])

    def test_same_day_yahoo_observation_is_not_reevaluated(self):
        observations = [RawObservation(
            "id", "BRENT_FUT", "YFINANCE_BZ=F", "2026-09-03", 70.0, "USD", "USD/bbl", "FUTURE",
            "2026-09-03T16:30:00+08:00", YAHOO_UNCONFIRMED,
        )]
        self.assertEqual(_eligible_yahoo_confirmation_dates(observations, evaluated_on=date(2026, 9, 3)), [])

    def test_open_shadow_dates_are_reconsidered_for_later_delayed_sources(self):
        observations = [
            RawObservation(
                "shadow-smm", "CU_SMM_CATHODE", "SMM", "2026-09-03", 70.0, "CNY", "CNY/MT", "SPOT",
                "2026-09-03T16:30:00+08:00", "DAILY_SNAPSHOT",
            ),
            RawObservation(
                "legacy-row", "CU_LME_CASH", "LME", "2026-09-03", 70.0, "USD", "USD/MT", "SPOT",
                "", "LEGACY_UNVERIFIED",
            ),
        ]
        self.assertEqual(_open_shadow_source_dates(observations), ["2026-09-03"])

    def test_recovery_source_date_sunday_is_not_a_canonical_target(self):
        observations = [RawObservation(
            "shadow-sunday", "BRENT_FUT", "YFINANCE_BZ=F", "2026-09-06", 70.0,
            "USD", "USD/bbl", "FUTURE", "2026-09-07T08:05:04+08:00", YAHOO_UNCONFIRMED,
        )]
        self.assertEqual(_open_shadow_source_dates(observations), [])
        self.assertEqual(_eligible_yahoo_confirmation_dates(observations, evaluated_on=date(2026, 9, 7)), [])

    def test_runner_uses_shadow_only_components(self):
        text = (ROOT / "scripts" / "c3_2_scheduled_shadow_runner.py").read_text(encoding="utf-8")
        self.assertIn("enforce_shadow_write_safety()", text)
        self.assertIn("NORMAL_SKIP_NON_BUSINESS_DAY", text)
        self.assertIn("no_smm_backfill=TRUE", text)
        self.assertIn("TASK_SCHEDULER_START_WHEN_AVAILABLE", text)
        self.assertIn("DATE_CONTEXT", text)
        self.assertIn("canonical_target_basis", text)
        self.assertIn("skipped_nonbusiness_source", text)
        self.assertIn("append_shadow_observation_plan", text)
        self.assertNotIn("Market_Raw", text)
        self.assertNotIn("ALLOW_GOOGLE_SHEET_WRITE\"] = \"1\"", text)

    def test_windows_launcher_forces_all_formal_write_gates_off(self):
        payload = (ROOT / "scripts" / "run_c3_2_scheduled_shadow.ps1").read_bytes()
        text = payload.decode("ascii")
        self.assertIn('$env:ALLOW_GOOGLE_SHEET_WRITE = "0"', text)
        self.assertIn('$env:ALLOW_PENDING_RAW_WRITE = "0"', text)
        self.assertIn("Remove-Item Env:CONTROLLED_WRITE_APPROVAL", text)
        self.assertIn('if ($DryRun) { $PythonArgs += "--dry-run" }', text)
        self.assertIn("Get-Command py -ErrorAction Stop", text)
        self.assertIn('$ErrorActionPreference = "Continue"', text)
        self.assertIn("$NativeOutput = & py -3 @PythonArgs *>&1", text)
        self.assertIn("$ChildExitCode = $LASTEXITCODE", text)
        self.assertIn("Tee-Object -FilePath $LogPath", text)
        self.assertNotIn('ALLOW_GOOGLE_SHEET_WRITE = "1"', text)


if __name__ == "__main__":
    unittest.main()
