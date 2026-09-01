from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ScheduledShadowRunnerTests(unittest.TestCase):
    def test_runner_uses_shadow_only_components(self):
        text = (ROOT / "scripts" / "c3_2_scheduled_shadow_runner.py").read_text(encoding="utf-8")
        self.assertIn("enforce_shadow_write_safety()", text)
        self.assertIn("append_shadow_observation_plan", text)
        self.assertNotIn("Market_Raw", text)
        self.assertNotIn("ALLOW_GOOGLE_SHEET_WRITE\"] = \"1\"", text)

    def test_windows_launcher_forces_all_formal_write_gates_off(self):
        payload = (ROOT / "scripts" / "run_c3_2_scheduled_shadow.ps1").read_bytes()
        text = payload.decode("ascii")
        self.assertIn('$env:ALLOW_GOOGLE_SHEET_WRITE = "0"', text)
        self.assertIn('$env:ALLOW_PENDING_RAW_WRITE = "0"', text)
        self.assertIn("Remove-Item Env:CONTROLLED_WRITE_APPROVAL", text)
        self.assertIn('if ($DryRun) { $args += "--dry-run" }', text)
        self.assertNotIn('ALLOW_GOOGLE_SHEET_WRITE = "1"', text)


if __name__ == "__main__":
    unittest.main()
