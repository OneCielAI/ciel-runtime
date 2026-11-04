import unittest
from ciel_runtime_support.work_discovery import discovery_hint, DEFAULT_INSTRUCTION


class WorkDiscoveryTests(unittest.TestCase):
    def test_test_default_enabled_unless_explicitly_disabled(self):
        self.assertTrue(discovery_hint({}, {})["enabled"])
        self.assertFalse(discovery_hint({"work_discovery": {"enabled": False}}, {})["enabled"])

    def test_test_instruction_overrides_custom_value(self):
        hint = discovery_hint({"work_discovery": {"enabled": True, "instruction": "custom"}}, {})
        self.assertTrue(hint["enabled"])
        self.assertTrue(hint["instruction"].startswith(DEFAULT_INSTRUCTION))
        self.assertIn("픽업은 실행 승인이 아니며", hint["instruction"])

    def test_override_off_uses_injected_text(self):
        hint = discovery_hint({"work_discovery": {"enabled": True, "instruction": "custom", "test_default_override": False}}, {})
        self.assertTrue(hint["instruction"].startswith("custom"))

    def test_focus_orders_work_and_pause_is_not_bypassed(self):
        cfg = {"work_discovery": {"enabled": True}}
        self.assertTrue(discovery_hint(cfg, {"focus": {"node_ids": ["n1"]}})["enabled"])
        cfg["work_parallel"] = {"paused": True}
        self.assertFalse(discovery_hint(cfg, {})["enabled"])

    def test_external_receiver_uses_exact_workspace_and_env(self):
        import os
        from unittest.mock import patch
        from ciel_runtime_support.runtime_paths import ROUTER_WORKSPACE_ID
        cfg = {"external_event_receivers": {ROUTER_WORKSPACE_ID: {"walkie": {
            "enabled": True, "transport": "sse", "discovery_enabled": True,
            "discovery_instruction": "${WORK_DISCOVERY_FIXTURE}", "test_default_override": False}}}}
        with patch.dict(os.environ, {"WORK_DISCOVERY_FIXTURE": "external instruction"}):
            hint = discovery_hint(cfg, {})
            self.assertTrue(hint["instruction"].startswith("external instruction"))
            self.assertEqual(hint["source"], "walkie")
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(discovery_hint(cfg, {})["enabled"])

    def test_invalid_instruction_is_closed(self):
        cfg = {"work_discovery": {"enabled": True, "instruction": "", "test_default_override": False}}
        self.assertFalse(discovery_hint(cfg, {})["enabled"])

    def test_workspace_file_path_body_and_reload_on_change(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "stop.txt"
            target.write_text("file body one", encoding="utf-8")
            cfg = {"work_discovery": {"enabled": True, "file_path": str(target),
                                       "instruction": "stale inline", "test_default_override": True}}
            hint = discovery_hint(cfg, {})
            self.assertTrue(hint["instruction"].startswith("file body one"))
            self.assertEqual("file", hint["source"])
            target.write_text("file body two", encoding="utf-8")
            hint = discovery_hint(cfg, {})
            self.assertTrue(hint["instruction"].startswith("file body two"))
            # Deleting the file returns to the interim Walkie default body,
            # still without creating anything on disk.
            target.unlink()
            hint = discovery_hint(cfg, {})
            self.assertTrue(hint["instruction"].startswith("작업큐에 여유가 생기면 와키에서"))
            self.assertFalse(target.exists())

    def test_missing_file_uses_walkie_default_until_written(self):
        import tempfile
        from pathlib import Path
        from ciel_runtime_support.work_discovery_file import DEFAULT_FILE_INSTRUCTION
        self.assertIn("와키에서", DEFAULT_FILE_INSTRUCTION)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "not-yet.txt"
            cfg = {"work_discovery": {"enabled": True, "file_path": str(target)}}
            hint = discovery_hint(cfg, {})
            self.assertTrue(hint["instruction"].startswith(DEFAULT_FILE_INSTRUCTION))
            self.assertFalse(target.exists())
            target.write_text("written body", encoding="utf-8")
            hint = discovery_hint(cfg, {})
            self.assertTrue(hint["instruction"].startswith("written body"))


if __name__ == "__main__":
    unittest.main()
