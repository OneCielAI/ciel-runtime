import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class InstallScriptPathTests(unittest.TestCase):
    def test_powershell_installer_reads_assets_from_its_own_directory(self):
        script = (ROOT / "install.ps1").read_text(encoding="utf-8")
        self.assertIn("$MyInvocation.MyCommand.Path", script)
        self.assertIn('(Join-Path $sourceDir "ciel_runtime.py")', script)
        self.assertIn('(Join-Path $sourceDir "ciel_runtime_support")', script)
        pwsh = shutil.which("pwsh")
        if not pwsh:
            self.skipTest("PowerShell is unavailable")
        with tempfile.TemporaryDirectory() as prefix, tempfile.TemporaryDirectory() as unrelated_cwd:
            read_user_path = [
                pwsh,
                "-NoProfile",
                "-Command",
                "[Environment]::GetEnvironmentVariable('Path', 'User')",
            ]
            path_before = subprocess.run(
                read_user_path, capture_output=True, text=True, check=True
            ).stdout
            read_runtime_home = [
                pwsh,
                "-NoProfile",
                "-Command",
                '[Environment]::GetEnvironmentVariable("CIEL_RUNTIME_HOME", "User")',
            ]
            runtime_home_before = subprocess.run(
                read_runtime_home, capture_output=True, text=True, check=True
            ).stdout.rstrip("\r\n")
            env = {
                **os.environ,
                "PREFIX": prefix,
                "CIEL_RUNTIME_INSTALL_HOME": str(
                    Path(prefix) / "share" / "ciel-runtime"
                ),
                "CIEL_RUNTIME_SKIP_PATH_REGISTRATION": "1",
            }
            try:
                subprocess.run(
                    [pwsh, "-NoProfile", "-File", str(ROOT / "install.ps1")],
                    cwd=unrelated_cwd,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=True,
                )
            finally:
                restore_env = {
                    **os.environ,
                    "CIEL_RUNTIME_TEST_SAVED_HOME": runtime_home_before,
                    "CIEL_RUNTIME_TEST_HOME_WAS_SET": "1" if runtime_home_before else "0",
                }
                restore = (
                    "$value = if ($env:CIEL_RUNTIME_TEST_HOME_WAS_SET -eq '1') { "
                    "$env:CIEL_RUNTIME_TEST_SAVED_HOME } else { $null }; "
                    '[Environment]::SetEnvironmentVariable("CIEL_RUNTIME_HOME", $value, "User")'
                )
                subprocess.run(
                    [pwsh, "-NoProfile", "-Command", restore],
                    env=restore_env,
                    capture_output=True,
                    text=True,
                    check=True,
                )
            path_after = subprocess.run(
                read_user_path, capture_output=True, text=True, check=True
            ).stdout
            installed = Path(prefix) / "share" / "ciel-runtime" / "ciel_runtime.py"
            self.assertEqual((ROOT / "ciel_runtime.py").read_bytes(), installed.read_bytes())
            self.assertEqual(path_before, path_after)
            self.assertEqual(
                runtime_home_before,
                subprocess.run(
                    read_runtime_home,
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.rstrip("\r\n"),
            )

    def test_posix_installer_reads_assets_from_its_own_directory(self):
        script = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)', script)
        self.assertIn('"$SOURCE_DIR/ciel_runtime.py"', script)
        self.assertIn('"$SOURCE_DIR/ciel_runtime_support/."', script)
        shell = shutil.which("sh")
        if not shell:
            self.skipTest("POSIX sh is unavailable")
        with tempfile.TemporaryDirectory() as prefix, tempfile.TemporaryDirectory() as unrelated_cwd:
            env = {**os.environ, "PREFIX": prefix}
            subprocess.run(
                [shell, str(ROOT / "install.sh")],
                cwd=unrelated_cwd,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            installed = Path(prefix) / "share" / "ciel-runtime" / "ciel_runtime.py"
            self.assertEqual((ROOT / "ciel_runtime.py").read_bytes(), installed.read_bytes())

    def test_installers_ignore_stale_version_snapshot_runtime_home(self):
        powershell = (ROOT / "install.ps1").read_text(encoding="utf-8")
        posix = (ROOT / "install.sh").read_text(encoding="utf-8")

        self.assertIn("CIEL_RUNTIME_INSTALL_HOME", powershell)
        self.assertIn("^ciel-runtime-[0-9a-f]{7,40}$", powershell)
        self.assertIn("CIEL_RUNTIME_INSTALL_HOME", posix)
        self.assertIn("ciel-runtime-[0-9a-f]", posix)

    def test_powershell_installer_does_not_pollute_path_during_isolated_install(self):
        script = (ROOT / "install.ps1").read_text(encoding="utf-8")

        self.assertIn("CIEL_RUNTIME_SKIP_PATH_REGISTRATION", script)
        self.assertIn("Send-CielRuntimeEnvironmentChanged", script)
        self.assertIn("SendMessageTimeout", script)
        self.assertIn("$isDeadTemporaryBin", script)


if __name__ == "__main__":
    unittest.main()
