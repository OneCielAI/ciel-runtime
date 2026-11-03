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

    def test_focus_and_pause_are_not_bypassed(self):
        cfg = {"work_discovery": {"enabled": True}}
        self.assertFalse(discovery_hint(cfg, {"focus": {"node_ids": ["n1"]}})["enabled"])
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


if __name__ == "__main__":
    unittest.main()
