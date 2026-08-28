from __future__ import annotations

import base64
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
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

    def _run_pilot_wrapper(self, *, pilot_exit: int, log_directory: Path):
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if powershell is None or os.name != "nt":
            self.skipTest("Windows PowerShell is available only on Windows")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scripts = root / "scripts"
            scripts.mkdir()
            wrapper = scripts / PILOT_WRAPPER.name
            wrapper.write_bytes(PILOT_WRAPPER.read_bytes())
            marker = root / "pilot-ran.marker"
            (scripts / "c3_2_pilot_dry_run.py").write_text(
                "from pathlib import Path\nimport os\nimport sys\n"
                f"Path({str(marker)!r}).write_text('ran', encoding='ascii')\n"
                "print('safe stdout diagnostic')\n"
                "print('safe stderr diagnostic', file=sys.stderr)\n"
                "print('write_disabled=' + os.environ.get('ALLOW_GOOGLE_SHEET_WRITE', ''))\n"
                "print('approval_present=' + str(bool(os.environ.get('CONTROLLED_WRITE_APPROVAL'))))\n"
                f"raise SystemExit({pilot_exit})\n",
                encoding="ascii",
            )
            (root / "service_account.json").write_text("{}", encoding="ascii")
            launcher = root / "launcher"
            launcher.mkdir()
            (launcher / "py.cmd").write_text(f"@echo off\r\necho {sys.executable}\r\n", encoding="ascii")
            environment = os.environ.copy()
            environment["PATH"] = str(launcher) + os.pathsep + environment.get("PATH", "")
            environment["ALLOW_GOOGLE_SHEET_WRITE"] = "1"
            environment["CONTROLLED_WRITE_APPROVAL"] = "test-approval"
            completed = subprocess.run(
                [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(wrapper), "-SheetId", "test-sheet-id", "-LogDirectory", str(log_directory)],
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
                timeout=30,
                check=False,
            )
            return completed, marker.exists()

    def test_pilot_wrapper_preserves_python_exit_and_logs_both_streams(self):
        with tempfile.TemporaryDirectory() as temporary:
            log_directory = Path(temporary) / "logs"
            success, ran_success = self._run_pilot_wrapper(pilot_exit=0, log_directory=log_directory)
            self.assertTrue(ran_success)
            self.assertEqual(success.returncode, 0)
            log_text = next(log_directory.glob("*.log")).read_text(encoding="utf-8")
            self.assertIn("safe stdout diagnostic", log_text)
            self.assertIn("safe stderr diagnostic", log_text)
            self.assertIn("write_disabled=0", log_text)
            self.assertIn("approval_present=False", log_text)

        with tempfile.TemporaryDirectory() as temporary:
            log_directory = Path(temporary) / "logs"
            failure, ran_failure = self._run_pilot_wrapper(pilot_exit=7, log_directory=log_directory)
            self.assertTrue(ran_failure)
            self.assertEqual(failure.returncode, 7)
            self.assertIn("safe stderr diagnostic", next(log_directory.glob("*.log")).read_text(encoding="utf-8"))

    def test_pilot_wrapper_log_failure_is_nonzero_after_python_runs(self):
        with tempfile.TemporaryDirectory() as temporary:
            blocked_log_path = Path(temporary) / "not-a-directory"
            blocked_log_path.write_text("occupied", encoding="ascii")
            success, ran_success = self._run_pilot_wrapper(pilot_exit=0, log_directory=blocked_log_path)
            self.assertTrue(ran_success)
            self.assertNotEqual(success.returncode, 0)
            self.assertIn("Pilot log write failed", success.stdout)

        with tempfile.TemporaryDirectory() as temporary:
            blocked_log_path = Path(temporary) / "not-a-directory"
            blocked_log_path.write_text("occupied", encoding="ascii")
            failure, ran_failure = self._run_pilot_wrapper(pilot_exit=7, log_directory=blocked_log_path)
            self.assertTrue(ran_failure)
            self.assertNotEqual(failure.returncode, 0)

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
