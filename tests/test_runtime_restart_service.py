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
                RuntimeRestartSettings(["runtime", "cli", "status"], "python", environ),
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

    def test_forced_upgrade_environment_does_not_mutate_source(self):
        source = {"PATH": "bin"}
        result = forced_upgrade_environment(source)
        self.assertNotIn("CI", source)
        self.assertEqual("1", result["CI"])
        self.assertEqual("true", result["NPM_CONFIG_YES"])


if __name__ == "__main__":
    unittest.main()
