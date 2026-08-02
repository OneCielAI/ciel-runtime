import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.runtime_restart import (
    RuntimeRestartPorts,
    RuntimeRestartService,
    RuntimeRestartSettings,
    forced_upgrade_environment,
)


class RuntimeRestartServiceTests(unittest.TestCase):
    def test_user_args_remove_only_the_internal_cli_dispatch_token(self):
        service = RuntimeRestartService(
            RuntimeRestartSettings(["runtime", "cli", "--version"], "python", {}),
            RuntimeRestartPorts(lambda: None, lambda _npm: None, lambda _name: None, lambda *_: None, lambda *_a, **_k: 0),
        )
        self.assertEqual(["--version"], service.user_args())

    def test_restart_prefers_the_updated_package_script(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "ciel_runtime.py"
            script.write_text("", encoding="utf-8")
            environ = {}
            service = RuntimeRestartService(
                RuntimeRestartSettings(
                    ["runtime", "cli", "status"],
                    "python",
                    environ,
                    platform_name="posix",
                ),
                RuntimeRestartPorts(
                    current_package_root=lambda: root,
                    global_package_root=lambda _npm: None,
                    find_executable=lambda _name: None,
                    execv=lambda executable, argv: calls.append((executable, argv)),
                    call=lambda *_args, **_kwargs: 0,
                ),
            )
            service.restart("npm")
            self.assertEqual(("python", ["python", str(script), "cli", "status"]), calls[0])
            self.assertEqual("1", environ["CIEL_RUNTIME_SKIP_SELF_UPDATE"])

    def test_windows_restart_uses_a_fresh_python_process(self):
        calls = []
        exec_calls = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "ciel_runtime.py"
            script.write_text("", encoding="utf-8")
            environ = {"PATH": "bin"}
            service = RuntimeRestartService(
                RuntimeRestartSettings(
                    ["runtime", "cli", "codex"],
                    "python",
                    environ,
                    platform_name="nt",
                ),
                RuntimeRestartPorts(
                    current_package_root=lambda: root,
                    global_package_root=lambda _npm: None,
                    find_executable=lambda _name: None,
                    execv=lambda executable, argv: exec_calls.append((executable, argv)),
                    call=lambda argv, **kwargs: calls.append((argv, kwargs)) or 17,
                ),
            )

            with self.assertRaisesRegex(SystemExit, "17"):
                service.restart("npm")

            self.assertEqual([], exec_calls)
            self.assertEqual(
                ["python", str(script), "cli", "codex"],
                calls[0][0],
            )
            self.assertEqual("1", calls[0][1]["env"]["CIEL_RUNTIME_SKIP_SELF_UPDATE"])
            self.assertEqual("1", environ["CIEL_RUNTIME_SKIP_SELF_UPDATE"])

    def test_forced_upgrade_environment_does_not_mutate_source(self):
        source = {"PATH": "bin"}
        result = forced_upgrade_environment(source)
        self.assertNotIn("CI", source)
        self.assertEqual("1", result["CI"])
        self.assertEqual("true", result["NPM_CONFIG_YES"])


if __name__ == "__main__":
    unittest.main()
