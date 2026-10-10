import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ciel_runtime_support.codex_config_effort import (
    CodexLaunchModelSettings,
    codex_home_config_path,
    ensure_default_reasoning_effort,
    ensure_logged,
    with_default_reasoning_effort,
)

MEDIUM_LINE = 'model_reasoning_effort = "medium"'


class DefaultReasoningEffortTextTests(unittest.TestCase):
    def test_line_goes_first_and_keeps_the_rest_byte_for_byte(self):
        text = '# mine\nmodel = "gpt-6.1-sol"\n\n[tui]\nalternate_screen = "never"\n'
        self.assertEqual(f"{MEDIUM_LINE}\n{text}", with_default_reasoning_effort(text))

    def test_crlf_and_bom_are_kept(self):
        self.assertEqual(f"{MEDIUM_LINE}\r\n[tui]\r\n", with_default_reasoning_effort("[tui]\r\n"))
        self.assertEqual(f"﻿{MEDIUM_LINE}\n[tui]\n", with_default_reasoning_effort("﻿[tui]\n"))

    def test_existing_top_level_effort_is_left_alone(self):
        for text in ('model_reasoning_effort = "high"\n', '[tui]\nx = 1\n', 'model = "m"\nmodel_reasoning_effort="low" # mine\n'):
            expected = None if "model_reasoning_effort" in text else f"{MEDIUM_LINE}\n{text}"
            self.assertEqual(expected, with_default_reasoning_effort(text), text)

    def test_effort_only_in_a_profile_or_other_table_still_gets_the_top_level_default(self):
        for text in ('[profiles.deep]\nmodel_reasoning_effort = "high"\n', '[tui]\nmodel_reasoning_effort = "high"\n',
                     '# model_reasoning_effort = "high"\n'):
            self.assertEqual(f"{MEDIUM_LINE}\n{text}", with_default_reasoning_effort(text), text)

    def test_empty_file(self):
        self.assertEqual(f"{MEDIUM_LINE}\n", with_default_reasoning_effort(""))


class EnsureDefaultReasoningEffortTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_writes_once_then_leaves_the_file(self):
        path = self.home / "config.toml"
        path.write_bytes(b"[tui]\r\nalternate_screen = \"never\"\r\n")
        self.assertTrue(ensure_default_reasoning_effort(path))
        self.assertEqual(b'model_reasoning_effort = "medium"\r\n[tui]\r\nalternate_screen = "never"\r\n', path.read_bytes())
        before = path.stat().st_mtime_ns
        self.assertFalse(ensure_default_reasoning_effort(path))
        self.assertEqual(before, path.stat().st_mtime_ns)
        self.assertEqual(["config.toml"], sorted(p.name for p in self.home.iterdir()))

    def test_missing_file_and_home_are_created(self):
        path = self.home / "new-home" / "config.toml"
        self.assertTrue(ensure_default_reasoning_effort(path))
        self.assertEqual(f"{MEDIUM_LINE}\n", path.read_text(encoding="utf-8"))

    def test_failure_is_logged_and_does_not_raise(self):
        logs = []
        with mock.patch("ciel_runtime_support.codex_config_effort.os.replace", side_effect=PermissionError("locked")):
            self.assertFalse(ensure_logged(self.home / "config.toml", lambda level, message: logs.append((level, message))))
        self.assertEqual("WARN", logs[0][0])
        self.assertIn("codex_config_effort_default_failed", logs[0][1])
        self.assertEqual([], list(self.home.iterdir()))

    def test_written_default_is_logged(self):
        logs = []
        path = self.home / "config.toml"
        self.assertTrue(ensure_logged(path, lambda level, message: logs.append((level, message))))
        self.assertEqual([("INFO", f"codex_config_effort_default_written path={path} effort=medium")], logs)

    def test_codex_home_follows_codex_home_env(self):
        self.assertEqual(self.home / "config.toml", codex_home_config_path({"CODEX_HOME": str(self.home)}, Path("unused")))
        self.assertEqual(self.home / ".codex" / "config.toml", codex_home_config_path({}, self.home))

    def test_launch_model_settings_prepare_the_config_before_building_args(self):
        seen = []

        def model_catalog_args(codex, cfg, passthrough):
            seen.append((self.home / "config.toml").read_text(encoding="utf-8"))
            return ["-c", "x=1"]

        settings = CodexLaunchModelSettings(model_catalog_args, lambda: {"CODEX_HOME": str(self.home)}, Path("unused"), lambda *_a: None)
        self.assertEqual(["-c", "x=1"], settings("codex", {}, ["--yolo"]))
        self.assertEqual([f"{MEDIUM_LINE}\n"], seen)


if __name__ == "__main__":
    unittest.main()
