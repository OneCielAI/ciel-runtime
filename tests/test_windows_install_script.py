import unittest
from pathlib import Path


class WindowsInstallScriptTests(unittest.TestCase):
    def test_installer_registers_bin_directory_in_user_path(self):
        script = (Path(__file__).resolve().parents[1] / "install.ps1").read_text(encoding="utf-8")

        self.assertIn('[Environment]::GetEnvironmentVariable("Path", "User")', script)
        self.assertIn('[Environment]::SetEnvironmentVariable("Path", $nextUserPath, "User")', script)
        self.assertIn("$cleanPathEntries.Add($binDir.TrimEnd('\\'))", script)
        self.assertIn("HashSet[string]", script)


if __name__ == "__main__":
    unittest.main()
