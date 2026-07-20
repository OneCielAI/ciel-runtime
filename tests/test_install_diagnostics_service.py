import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.install_diagnostics import (
    InstallDiagnosticsPorts,
    InstallDiagnosticsService,
    InstallDiagnosticsSettings,
)


class InstallDiagnosticsServiceTests(unittest.TestCase):
    def service(self, root, *, rows=None, writes=None):
        writes = writes if writes is not None else []
        return InstallDiagnosticsService(
            settings=InstallDiagnosticsSettings(
                home=root,
                environ={"PATH": str(root / "bin")},
                windows=True,
            ),
            ports=InstallDiagnosticsPorts(
                extra_dirs=lambda: [root / "bin", root / "extra"],
                package_root=lambda path: path.parent,
                current_root=lambda: root / "current",
                parse_version=lambda value: tuple(
                    int(part) for part in value.split(".")
                ),
                diagnostics=lambda: list(rows or []),
                stdin_isatty=lambda: True,
                stdout_isatty=lambda: True,
                write_error=writes.append,
            ),
        )

    def test_candidate_discovery_deduplicates_directories_and_launchers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            launcher = root / "bin" / "ciel-runtime"
            launcher.parent.mkdir()
            launcher.write_text("launcher", encoding="utf-8")
            service = self.service(root)
            self.assertEqual(1, service.candidate_dirs().count(root / "bin"))
            self.assertEqual([launcher], service.candidates())

    def test_warning_reports_shadowed_newer_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            writes = []
            rows = [
                {"launcher": "first", "package_root": "root-a", "version": "1.0.0"},
                {"launcher": "second", "package_root": "root-b", "version": "2.0.0"},
            ]
            self.service(root, rows=rows, writes=writes).warn_if_multiple()
            text = "\n".join(writes)
            self.assertIn("multiple ciel-runtime npm installs", text)
            self.assertIn("second (2.0.0)", text)


if __name__ == "__main__":
    unittest.main()
