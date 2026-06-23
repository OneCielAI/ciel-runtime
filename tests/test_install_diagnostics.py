import io
import json
import unittest
from pathlib import Path
from unittest import mock

import claude_any


class InstallDiagnosticsTests(unittest.TestCase):
    def test_windows_default_paths_use_appdata_locations(self):
        env = {
            "APPDATA": r"C:\Users\alice\AppData\Roaming",
            "LOCALAPPDATA": r"C:\Users\alice\AppData\Local",
            "PATH": r"C:\Windows\System32",
        }

        with mock.patch.object(claude_any.os, "name", "nt"), mock.patch.dict(claude_any.os.environ, env, clear=False):
            appdata = claude_any.platform_path(env["APPDATA"])
            local_appdata = claude_any.platform_path(env["LOCALAPPDATA"])
            self.assertEqual(
                appdata / "claude-any",
                claude_any.platform_config_dir("claude-any"),
            )
            self.assertEqual(
                local_appdata / "claude-any" / "bin",
                claude_any.claude_any_user_bin_dir(),
            )
            extra_dirs = claude_any.executable_extra_dirs()
            self.assertIn(appdata / "npm", extra_dirs)
            self.assertIn(local_appdata / "Programs" / "nodejs", extra_dirs)
            prefixed_path = claude_any.path_with_claude_any_user_dirs(dict(env))
            self.assertTrue(prefixed_path.startswith(str(local_appdata / "claude-any" / "bin")))

    def test_package_root_from_installed_path(self):
        root = Path("/usr/local/lib/node_modules/@oneciel-ai/claude-any")
        launcher = root / "npm-bin" / "claude-any.js"

        self.assertEqual(root.resolve(strict=False), claude_any.package_root_from_installed_path(launcher))

    def test_npm_prefix_from_posix_package_roots(self):
        self.assertEqual(
            Path("/usr/local"),
            claude_any.npm_prefix_from_package_root(Path("/usr/local/lib/node_modules/@oneciel-ai/claude-any")),
        )
        self.assertEqual(
            Path("/home/user/.local"),
            claude_any.npm_prefix_from_package_root(Path("/home/user/.local/lib/node_modules/@oneciel-ai/claude-any")),
        )
        self.assertEqual(
            Path("/home/user/.npm-global"),
            claude_any.npm_prefix_from_package_root(
                Path("/home/user/.npm-global/lib/node_modules/@oneciel-ai/claude-any")
            ),
        )

    def test_npm_global_install_command_can_pin_prefix(self):
        self.assertEqual(
            ["npm", "install", "-g", "--prefix", str(Path("/home/user/.local")), "@oneciel-ai/claude-any@latest"],
            claude_any.npm_global_install_command(
                "npm", "@oneciel-ai/claude-any@latest", Path("/home/user/.local")
            ),
        )

    def test_warns_when_newer_install_is_shadowed(self):
        rows = [
            {
                "launcher": "/usr/local/bin/claude-any",
                "resolved": "/usr/local/lib/node_modules/@oneciel-ai/claude-any/npm-bin/claude-any.js",
                "package_root": "/usr/local/lib/node_modules/@oneciel-ai/claude-any",
                "version": "0.1.104-nightly.20260531-070027.bb412de",
            },
            {
                "launcher": "/home/user/.local/bin/claude-any",
                "resolved": "/home/user/.local/lib/node_modules/@oneciel-ai/claude-any/npm-bin/claude-any.js",
                "package_root": "/home/user/.local/lib/node_modules/@oneciel-ai/claude-any",
                "version": "0.1.104-nightly.20260601-012855.916d3dc",
            },
        ]
        stderr = io.StringIO()

        with mock.patch.object(claude_any, "claude_any_install_diagnostics", return_value=rows), mock.patch.object(
            claude_any, "current_npm_package_root", return_value=Path(rows[0]["package_root"])
        ), mock.patch.object(claude_any.sys.stdin, "isatty", return_value=True), mock.patch.object(
            claude_any.sys.stdout, "isatty", return_value=True
        ), mock.patch.object(
            claude_any.sys, "stderr", stderr
        ):
            claude_any.warn_if_multiple_claude_any_installs()

        text = stderr.getvalue()
        self.assertIn("multiple claude-any npm installs", text)
        self.assertIn("/usr/local/bin/claude-any", text)
        self.assertIn("/home/user/.local/bin/claude-any", text)
        self.assertIn("0.1.104-nightly.20260601-012855.916d3dc", text)

    def test_npm_postinstall_best_effort_stops_managed_services(self):
        root = Path(__file__).resolve().parents[1]
        package = json.loads((root / "package.json").read_text(encoding="utf-8"))

        self.assertEqual("node npm-bin/postinstall.js", package["scripts"]["postinstall"])
        postinstall = (root / "npm-bin" / "postinstall.js").read_text(encoding="utf-8")
        self.assertIn('"cli", "stop"', postinstall)
        self.assertIn("CLAUDE_ANY_SKIP_POSTINSTALL_STOP", postinstall)


if __name__ == "__main__":
    unittest.main()
