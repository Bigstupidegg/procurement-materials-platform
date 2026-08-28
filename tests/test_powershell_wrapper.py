from __future__ import annotations

import base64
from pathlib import Path
import os
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "run_c3_1_live_dry_run.ps1"
SCHEDULED_WRAPPER = ROOT / "scripts" / "run_c3_2_windows_readonly.ps1"
PILOT_WRAPPER = ROOT / "scripts" / "run_c3_2_windows_pilot_dry_run.ps1"


class PowerShellWrapperEncodingTests(unittest.TestCase):
    def test_wrapper_is_utf8_with_bom_for_windows_powershell_51(self):
        payload = WRAPPER.read_bytes()
        self.assertTrue(
            payload.startswith(b"\xef\xbb\xbf"),
            "Windows PowerShell 5.1 requires a BOM to decode this UTF-8 script safely",
        )
        payload.decode("utf-8-sig")

    def test_wrapper_still_force_disables_google_sheet_write(self):
        text = WRAPPER.read_text(encoding="utf-8-sig")
        self.assertIn('$env:ALLOW_GOOGLE_SHEET_WRITE = "0"', text)
        self.assertNotIn('$env:ALLOW_GOOGLE_SHEET_WRITE = "1"', text)

    def test_scheduled_wrapper_is_ascii_and_forces_read_only_environment(self):
        payload = SCHEDULED_WRAPPER.read_bytes()
        payload.decode("ascii")
        text = payload.decode("ascii")
        self.assertIn('$env:ALLOW_GOOGLE_SHEET_WRITE = "0"', text)
        self.assertIn("Remove-Item Env:GOOGLE_SHEET_ID", text)
        self.assertIn("Remove-Item Env:GOOGLE_SERVICE_ACCOUNT_FILE", text)
        self.assertNotIn("service_account.json", text)
        self.assertNotIn("GOOGLE_SHEET_ID =", text)

    def test_pilot_wrapper_is_ascii_and_forces_write_disabled(self):
        payload = PILOT_WRAPPER.read_bytes()
        text = payload.decode("ascii")
        self.assertIn('$env:ALLOW_GOOGLE_SHEET_WRITE = "0"', text)
        self.assertIn("Remove-Item Env:CONTROLLED_WRITE_APPROVAL", text)
        self.assertNotIn('$env:ALLOW_GOOGLE_SHEET_WRITE = "1"', text)

    def test_windows_powershell_parser_accepts_wrappers(self):
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if powershell is None or os.name != "nt":
            self.skipTest("Windows PowerShell parser is available only on Windows")

        parser_script = r"""
$tokens = $null
$errors = $null
[System.Management.Automation.Language.Parser]::ParseFile(
    $env:C3_PS1_PARSE_PATH,
    [ref]$tokens,
    [ref]$errors
) | Out-Null
if ($errors.Count -gt 0) {
    $errors | ForEach-Object { [Console]::Error.WriteLine($_.ToString()) }
    exit 1
}
"""
        encoded = base64.b64encode(parser_script.encode("utf-16-le")).decode("ascii")
        environment = os.environ.copy()
        for wrapper in (WRAPPER, SCHEDULED_WRAPPER, PILOT_WRAPPER):
            environment["C3_PS1_PARSE_PATH"] = str(wrapper)
            completed = subprocess.run(
                [powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
                timeout=30,
                check=False,
            )
            self.assertEqual(
                completed.returncode,
                0,
                f"Windows PowerShell parser rejected {wrapper.name}:\n{completed.stderr}",
            )


if __name__ == "__main__":
    unittest.main()
