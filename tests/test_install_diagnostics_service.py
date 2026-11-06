import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from ciel_runtime_support.install_diagnostics import (
    InstallDiagnosticsPorts,
    InstallDiagnosticsService,
    InstallDiagnosticsSettings,
)


class InstallDiagnosticsServiceTests(unittest.TestCase):
    def service(
        self, root, *, rows=None, writes=None, package_root=None, path=None, environ=None,
        registered_home=None,
    ):
        writes = writes if writes is not None else []
        package_root = package_root or (lambda path: path.parent)
        environment = {"PATH": str(path or root / "bin")}
        environment.update(environ or {})
        return InstallDiagnosticsService(
            settings=InstallDiagnosticsSettings(
                home=root,
                environ=environment,
                windows=True,
            ),
            ports=InstallDiagnosticsPorts(
                extra_dirs=lambda: [root / "bin", root / "extra"],
                package_root=package_root,
                windows_user_runtime_home=registered_home or (lambda _expand: ""),
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
            launcher = root / "bin" / "ciel-runtime.cmd"
            launcher.parent.mkdir()
            launcher.write_text("launcher", encoding="utf-8")
            service = self.service(root)
            self.assertEqual(1, service.candidate_dirs().count(root / "bin"))
            self.assertEqual([launcher], service.candidates())

    def test_known_npm_wrappers_read_metadata_without_launching_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            npm_root = root / "node_modules" / "@one-ciel-ai" / "ciel-runtime"
            npm_root.mkdir(parents=True)
            (npm_root / "package.json").write_text(
                json.dumps({"version": "2.3.4"}), encoding="utf-8"
            )
            bin_dir = root / "bin"
            bin_dir.mkdir()
            for name in ("ciel-runtime", "ciel-runtime.cmd"):
                (bin_dir / name).write_text("wrapper", encoding="utf-8")
            service = self.service(
                root, package_root=lambda path: npm_root if path.parent == bin_dir else None
            )

            started = time.monotonic()
            with mock.patch(
                "ciel_runtime_support.install_diagnostics.subprocess.run",
                side_effect=AssertionError("known installs must not recursively launch"),
            ):
                rows = service.diagnostics()
            elapsed = time.monotonic() - started

            self.assertEqual(1, len(rows))
            self.assertEqual("2.3.4", rows[0]["version"])
            self.assertEqual(str(npm_root.resolve(strict=False)), rows[0]["package_root"])
            self.assertLess(elapsed, 0.2)

    def test_local_install_wrapper_reads_runtime_constants_without_launching(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            local = home / ".local"
            launcher = local / "bin" / "ciel-runtime.cmd"
            launcher.parent.mkdir(parents=True)
            launcher.write_text("wrapper", encoding="utf-8")
            root = local / "share" / "ciel-runtime"
            constants = root / "ciel_runtime_support" / "runtime_constants.py"
            constants.parent.mkdir(parents=True)
            (root / "ciel_runtime.py").write_text("", encoding="utf-8")
            constants.write_text('VERSION = "5.6.7"\n', encoding="utf-8")
            service = self.service(
                home,
                package_root=lambda _path: None,
                path=launcher.parent,
                environ={"CIEL_RUNTIME_HOME": str(root)},
            )

            with mock.patch(
                "ciel_runtime_support.install_diagnostics.subprocess.run",
                side_effect=AssertionError("known installs must not recursively launch"),
            ):
                rows = service.diagnostics()

            self.assertEqual("5.6.7", rows[0]["version"])
            self.assertEqual(str(root.resolve(strict=False)), rows[0]["package_root"])

    def test_windows_local_wrapper_home_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            launcher_dir = home / ".local" / "bin"
            launcher_dir.mkdir(parents=True)
            roots = {}
            for name, version in (
                ("override", "1.0.0"), ("registered", "2.0.0"),
                ("environment", "3.0.0"), ("default", "4.0.0"),
            ):
                root = home / name
                constants = root / "ciel_runtime_support" / "runtime_constants.py"
                constants.parent.mkdir(parents=True)
                (root / "ciel_runtime.py").write_text("", encoding="utf-8")
                constants.write_text(f'VERSION = "{version}"\n', encoding="utf-8")
                roots[name] = root
            default = home / ".local" / "share" / "ciel-runtime"
            default.parent.mkdir(parents=True)
            roots["default"].rename(default)
            roots["default"] = default

            cases = (
                ({"CIEL_RUNTIME_HOME_OVERRIDE": str(roots["override"]),
                  "CIEL_RUNTIME_HOME": str(roots["environment"])}, str(roots["registered"]), "1.0.0"),
                ({"CIEL_RUNTIME_HOME": str(roots["environment"])}, str(roots["registered"]), "2.0.0"),
                ({"CIEL_RUNTIME_HOME": str(roots["environment"])}, "", "3.0.0"),
                ({}, "", "4.0.0"),
            )
            launcher = launcher_dir / "ciel-runtime.cmd"
            launcher.write_text("wrapper", encoding="utf-8")
            for environ, registered, expected in cases:
                with self.subTest(expected=expected):
                    service = self.service(
                        home, package_root=lambda _path: None, path=launcher.parent,
                        environ=environ, registered_home=lambda _expand, value=registered: value,
                    )
                    self.assertEqual(expected, service.launcher_version(launcher))

    def test_powershell_registry_lookup_requests_expandable_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            launcher = home / ".local" / "bin" / "ciel-runtime.ps1"
            launcher.parent.mkdir(parents=True)
            launcher.write_text("wrapper", encoding="utf-8")
            registered = home / "registered"
            constants = registered / "ciel_runtime_support" / "runtime_constants.py"
            constants.parent.mkdir(parents=True)
            (registered / "ciel_runtime.py").write_text("", encoding="utf-8")
            constants.write_text('VERSION = "2.0.0"\n', encoding="utf-8")
            expand_requests = []
            service = self.service(
                home, package_root=lambda _path: None, path=launcher.parent,
                registered_home=lambda expand: (
                    expand_requests.append(expand), str(registered)
                )[1],
            )
            self.assertEqual("2.0.0", service.launcher_version(launcher))
            self.assertEqual([True], expand_requests)

    def test_windows_registry_home_requires_runtime_entrypoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            launcher = home / ".local" / "bin" / "ciel-runtime.cmd"
            launcher.parent.mkdir(parents=True)
            launcher.write_text("wrapper", encoding="utf-8")
            registered = home / "registered"
            registered.mkdir()
            environment = home / "environment"
            constants = environment / "ciel_runtime_support" / "runtime_constants.py"
            constants.parent.mkdir(parents=True)
            (environment / "ciel_runtime.py").write_text("", encoding="utf-8")
            constants.write_text('VERSION = "3.0.0"\n', encoding="utf-8")
            service = self.service(
                home, package_root=lambda _path: None, path=launcher.parent,
                environ={"CIEL_RUNTIME_HOME": str(environment)},
                registered_home=lambda _expand: str(registered),
            )
            self.assertEqual("3.0.0", service.launcher_version(launcher))

    def test_selected_missing_override_does_not_fall_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            launcher = home / ".local" / "bin" / "ciel-runtime.cmd"
            launcher.parent.mkdir(parents=True)
            launcher.write_text("wrapper", encoding="utf-8")
            default = home / ".local" / "share" / "ciel-runtime"
            constants = default / "ciel_runtime_support" / "runtime_constants.py"
            constants.parent.mkdir(parents=True)
            (default / "ciel_runtime.py").write_text("", encoding="utf-8")
            constants.write_text('VERSION = "4.0.0"\n', encoding="utf-8")
            service = self.service(
                home, package_root=lambda _path: None, path=launcher.parent,
                environ={"CIEL_RUNTIME_HOME_OVERRIDE": str(home / "missing")},
            )
            completed = mock.Mock(returncode=1, stdout="missing runtime")
            with mock.patch(
                "ciel_runtime_support.install_diagnostics.subprocess.run", return_value=completed
            ) as run:
                self.assertEqual("", service.launcher_version(launcher, timeout=0.25))
            self.assertEqual(1, run.call_count)

    def test_posix_wrapper_uses_only_runtime_home_or_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            launcher = home / ".local" / "bin" / "ciel-runtime"
            launcher.parent.mkdir(parents=True)
            launcher.write_text("wrapper", encoding="utf-8")
            selected = home / "selected"
            constants = selected / "ciel_runtime_support" / "runtime_constants.py"
            constants.parent.mkdir(parents=True)
            (selected / "ciel_runtime.py").write_text("", encoding="utf-8")
            constants.write_text('VERSION = "5.0.0"\n', encoding="utf-8")
            service = InstallDiagnosticsService(
                InstallDiagnosticsSettings(home, {
                    "PATH": str(launcher.parent), "CIEL_RUNTIME_HOME": str(selected),
                    "CIEL_RUNTIME_HOME_OVERRIDE": str(home / "ignored"),
                }, False),
                InstallDiagnosticsPorts(lambda: [], lambda _path: None, lambda _expand: "",
                                        lambda: None, lambda _value: (), lambda: [],
                                        lambda: True, lambda: True, lambda _line: None),
            )
            self.assertEqual("5.0.0", service.launcher_version(launcher))

    def test_unknown_external_launcher_uses_bounded_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            launcher = root / "bin" / "ciel-runtime.cmd"
            launcher.parent.mkdir()
            launcher.write_text("external", encoding="utf-8")
            service = self.service(root, package_root=lambda _path: None)
            completed = mock.Mock(returncode=0, stdout="ciel-runtime 9.8.7\n")

            with mock.patch(
                "ciel_runtime_support.install_diagnostics.subprocess.run",
                return_value=completed,
            ) as run:
                self.assertEqual("9.8.7", service.launcher_version(launcher, timeout=0.25))

            self.assertEqual(0.25, run.call_args.kwargs["timeout"])
            child_env = run.call_args.kwargs["env"]
            self.assertNotIn("CIEL_RUNTIME_ROUTER_PORT", child_env)
            self.assertEqual("1", child_env["CIEL_RUNTIME_SKIP_INSTALL_DIAGNOSTIC"])
            self.assertEqual("1", child_env["CIEL_RUNTIME_SKIP_SELF_UPDATE"])
            self.assertEqual("off", child_env["CIEL_RUNTIME_SELF_UPDATE_CHECK"])

    def test_same_version_distinct_roots_do_not_warn(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            writes = []
            rows = [
                {"launcher": "first", "package_root": "root-a", "version": "2.0.0"},
                {"launcher": "second", "package_root": "root-b", "version": "2.0.0"},
            ]
            self.service(root, rows=rows, writes=writes).warn_if_multiple()
            self.assertEqual([], writes)

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
