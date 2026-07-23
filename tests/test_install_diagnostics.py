import io
import json
import unittest
from pathlib import Path
from unittest import mock

import ciel_runtime


class InstallDiagnosticsTests(unittest.TestCase):
    def test_windows_default_paths_use_appdata_locations(self):
        env = {
            "APPDATA": r"C:\Users\alice\AppData\Roaming",
            "LOCALAPPDATA": r"C:\Users\alice\AppData\Local",
            "PATH": r"C:\Windows\System32",
        }

        host_path_type = type(Path())
        with (
            mock.patch.object(ciel_runtime.os, "name", "nt"),
            mock.patch.object(ciel_runtime, "Path", host_path_type),
            mock.patch.dict(ciel_runtime.os.environ, env, clear=False),
        ):
            appdata = ciel_runtime.platform_path(env["APPDATA"])
            local_appdata = ciel_runtime.platform_path(env["LOCALAPPDATA"])
            self.assertEqual(
                appdata / "ciel-runtime",
                ciel_runtime.platform_config_dir("ciel-runtime"),
            )
            self.assertEqual(
                local_appdata / "ciel-runtime" / "bin",
                ciel_runtime.ciel_runtime_user_bin_dir(),
            )
            extra_dirs = ciel_runtime.executable_extra_dirs()
            self.assertIn(appdata / "npm", extra_dirs)
            self.assertIn(local_appdata / "Programs" / "nodejs", extra_dirs)
            prefixed_path = ciel_runtime.path_with_ciel_runtime_user_dirs(dict(env))
            self.assertTrue(prefixed_path.startswith(str(local_appdata / "ciel-runtime" / "bin")))

    def test_package_root_from_installed_path(self):
        root = Path("/usr/local/lib/node_modules/@oneciel-ai/ciel-runtime")
        launcher = root / "npm-bin" / "ciel-runtime.js"

        self.assertEqual(root.resolve(strict=False), ciel_runtime.package_root_from_installed_path(launcher))

    def test_npm_prefix_from_posix_package_roots(self):
        self.assertEqual(
            Path("/usr/local"),
            ciel_runtime.npm_prefix_from_package_root(Path("/usr/local/lib/node_modules/@oneciel-ai/ciel-runtime")),
        )
        self.assertEqual(
            Path("/home/user/.local"),
            ciel_runtime.npm_prefix_from_package_root(Path("/home/user/.local/lib/node_modules/@oneciel-ai/ciel-runtime")),
        )
        self.assertEqual(
            Path("/home/user/.npm-global"),
            ciel_runtime.npm_prefix_from_package_root(
                Path("/home/user/.npm-global/lib/node_modules/@oneciel-ai/ciel-runtime")
            ),
        )

    def test_npm_global_install_command_can_pin_prefix(self):
        self.assertEqual(
            ["npm", "install", "-g", "--prefix", str(Path("/home/user/.local")), "@oneciel-ai/ciel-runtime@latest"],
            ciel_runtime.npm_global_install_command(
                "npm", "@oneciel-ai/ciel-runtime@latest", Path("/home/user/.local")
            ),
        )

    def test_warns_when_newer_install_is_shadowed(self):
        rows = [
            {
                "launcher": "/usr/local/bin/ciel-runtime",
                "resolved": "/usr/local/lib/node_modules/@oneciel-ai/ciel-runtime/npm-bin/ciel-runtime.js",
                "package_root": "/usr/local/lib/node_modules/@oneciel-ai/ciel-runtime",
                "version": "0.1.104-nightly.20260531-070027.bb412de",
            },
            {
                "launcher": "/home/user/.local/bin/ciel-runtime",
                "resolved": "/home/user/.local/lib/node_modules/@oneciel-ai/ciel-runtime/npm-bin/ciel-runtime.js",
                "package_root": "/home/user/.local/lib/node_modules/@oneciel-ai/ciel-runtime",
                "version": "0.1.104-nightly.20260601-012855.916d3dc",
            },
        ]
        stderr = io.StringIO()

        with mock.patch.object(ciel_runtime, "ciel_runtime_install_diagnostics", return_value=rows), mock.patch.object(
            ciel_runtime, "current_npm_package_root", return_value=Path(rows[0]["package_root"])
        ), mock.patch.object(ciel_runtime.sys.stdin, "isatty", return_value=True), mock.patch.object(
            ciel_runtime.sys.stdout, "isatty", return_value=True
        ), mock.patch.object(
            ciel_runtime.sys, "stderr", stderr
        ):
            ciel_runtime.warn_if_multiple_ciel_runtime_installs()

        text = stderr.getvalue()
        self.assertIn("multiple ciel-runtime npm installs", text)
        self.assertIn("/usr/local/bin/ciel-runtime", text)
        self.assertIn("/home/user/.local/bin/ciel-runtime", text)
        self.assertIn("0.1.104-nightly.20260601-012855.916d3dc", text)

    def test_npm_package_has_no_install_scripts(self):
        root = Path(__file__).resolve().parents[1]
        package = json.loads((root / "package.json").read_text(encoding="utf-8"))

        scripts = package.get("scripts", {})
        self.assertNotIn("preinstall", scripts)
        self.assertNotIn("install", scripts)
        self.assertNotIn("postinstall", scripts)
        self.assertFalse((root / "npm-bin" / "postinstall.js").exists())

    def test_npm_launcher_rejects_python_older_than_310(self):
        root = Path(__file__).resolve().parents[1]
        launcher = (root / "npm-bin" / "run-ciel-runtime.js").read_text(encoding="utf-8")

        self.assertIn("minimumPython = { major: 3, minor: 10 }", launcher)
        self.assertIn("sys.version_info.major", launcher)
        self.assertIn("but Ciel Runtime requires Python", launcher)


if __name__ == "__main__":
    unittest.main()
