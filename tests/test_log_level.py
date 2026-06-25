import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import ciel_runtime


class LogLevelTests(unittest.TestCase):
    def tearDown(self):
        ciel_runtime.reset_log_level_cache()

    def test_set_log_level_writes_file_and_updates_effective_level(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "log-level"
            with mock.patch.object(ciel_runtime, "LOG_LEVEL_PATH", path), mock.patch.object(ciel_runtime, "CONFIG_DIR", Path(td)):
                lines = ciel_runtime.set_log_level_config("debug")
                self.assertEqual(["Log level set to DEBUG."], lines)
                self.assertEqual("DEBUG", path.read_text(encoding="utf-8").strip())
                self.assertEqual(ciel_runtime.LOG_LEVELS["DEBUG"], ciel_runtime.current_log_level())

    def test_reset_log_level_removes_file_and_uses_default(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "log-level"
            path.write_text("TRACE\n", encoding="utf-8")
            with (
                mock.patch.object(ciel_runtime, "LOG_LEVEL_PATH", path),
                mock.patch.object(ciel_runtime, "CONFIG_DIR", Path(td)),
                mock.patch.dict(ciel_runtime.os.environ, {"CIEL_RUNTIME_LOG_LEVEL": ""}, clear=False),
            ):
                ciel_runtime.reset_log_level_cache()
                lines = ciel_runtime.set_log_level_config("default")
                self.assertFalse(path.exists())
                self.assertIn("Log level reset to ERROR (default).", lines)
                self.assertEqual(ciel_runtime.LOG_LEVELS["ERROR"], ciel_runtime.current_log_level())

    def test_headless_flag_sets_log_level_without_launch_when_configure_only(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "log-level"
            with (
                mock.patch.object(ciel_runtime, "LOG_LEVEL_PATH", path),
                mock.patch.object(ciel_runtime, "CONFIG_DIR", Path(td)),
                mock.patch.object(ciel_runtime, "CONFIG_PATH", Path(td) / "config.json"),
                mock.patch.object(ciel_runtime, "launch_claude") as launch,
            ):
                with redirect_stdout(io.StringIO()):
                    rc = ciel_runtime.run_cli(["--ca-log-level", "INFO", "--ca-no-launch"])
            self.assertEqual(0, rc)
            self.assertEqual("INFO", path.read_text(encoding="utf-8").strip())
            launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
