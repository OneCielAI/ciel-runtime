import unittest
from pathlib import Path


class WindowsInstallScriptTests(unittest.TestCase):
    def test_installer_registers_bin_directory_in_user_path(self):
        script = (Path(__file__).resolve().parents[1] / "install.ps1").read_text(encoding="utf-8")

        self.assertIn('[Environment]::GetEnvironmentVariable("Path", "User")', script)
        self.assertIn('[Environment]::SetEnvironmentVariable("Path", $nextUserPath, "User")', script)
        self.assertIn("$cleanPathEntries.Add($binDir.TrimEnd('\\'))", script)
        self.assertIn("HashSet[string]", script)

    def test_installer_pins_the_installed_runtime_for_future_launches(self):
        script = (Path(__file__).resolve().parents[1] / "install.ps1").read_text(encoding="utf-8")

        self.assertIn(
            '[Environment]::SetEnvironmentVariable("CIEL_RUNTIME_HOME", $shareDir, "User")',
            script,
        )

    def test_installer_rejects_runtime_home_below_windows_temp(self):
        script = (Path(__file__).resolve().parents[1] / "install.ps1").read_text(encoding="utf-8")

        self.assertIn("$temporaryRuntimeHome", script)
        self.assertIn("$ephemeralRuntimeHome = $snapshotHome -or $temporaryRuntimeHome", script)
        self.assertIn("Ignoring ephemeral CIEL_RUNTIME_HOME", script)

    def test_windows_launchers_prefer_registered_pin_over_stale_terminal_snapshot(self):
        root = Path(__file__).resolve().parents[1]
        for name in (
            "ciel-runtime.ps1",
            "ciel-runtimectl.ps1",
            "ciel-runtime-stop.ps1",
        ):
            with self.subTest(name=name):
                script = (root / name).read_text(encoding="utf-8")
                selection = script[script.index("$runtimeHome =") :]
                self.assertLess(
                    selection.index("CIEL_RUNTIME_HOME_OVERRIDE"),
                    selection.index("elseif ($registeredHome"),
                )
                self.assertLess(
                    selection.index("elseif ($registeredHome"),
                    selection.index("elseif ($env:CIEL_RUNTIME_HOME)"),
                )

        for name in ("ciel-runtime.cmd", "ciel-runtimectl.cmd", "ciel-runtime-stop.cmd"):
            with self.subTest(name=name):
                script = (root / name).read_text(encoding="utf-8")
                self.assertIn("reg query HKCU\\Environment /v CIEL_RUNTIME_HOME", script)
                self.assertIn(
                    'if defined CIEL_RUNTIME_REGISTERED_HOME if not exist '
                    '"%CIEL_RUNTIME_REGISTERED_HOME%\\ciel_runtime.py"',
                    script,
                )
                self.assertLess(
                    script.index("CIEL_RUNTIME_REGISTERED_HOME"),
                    script.index("else if defined CIEL_RUNTIME_HOME ("),
                )


if __name__ == "__main__":
    unittest.main()
