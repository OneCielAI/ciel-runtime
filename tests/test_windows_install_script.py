import json
import shutil
import subprocess
import tempfile
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

    def test_npm_repair_targets_only_missing_runtime_below_temp(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is unavailable")
        root = Path(__file__).resolve().parents[1]
        module = root / "npm-bin" / "repair-windows-runtime-pin.js"
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            stale = temp / "tmp_bundle" / "share" / "ciel-runtime"
            stable = temp.parent / "stable" / "ciel-runtime"
            script = """
const repair = require(process.argv[1]);
const stale = process.argv[2];
const stable = process.argv[3];
const temp = process.argv[4];
process.stdout.write(JSON.stringify({
  staleMissing: repair.shouldRepairRuntimeHome(stale, temp, () => false),
  stalePresent: repair.shouldRepairRuntimeHome(stale, temp, () => true),
  stableMissing: repair.shouldRepairRuntimeHome(stable, temp, () => false),
}));
"""
            result = subprocess.run(
                [node, "-e", script, str(module), str(stale), str(stable), str(temp)],
                capture_output=True,
                text=True,
                check=True,
            )
            observed = json.loads(result.stdout)

        self.assertEqual(
            {"staleMissing": True, "stalePresent": False, "stableMissing": False},
            observed,
        )

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
                self.assertLess(
                    script.index("CIEL_RUNTIME_REGISTERED_HOME"),
                    script.index("else if defined CIEL_RUNTIME_HOME ("),
                )


if __name__ == "__main__":
    unittest.main()
