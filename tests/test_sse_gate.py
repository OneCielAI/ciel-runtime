import dataclasses
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from ciel_runtime_support.sse_gate import SSE_GATE_MARKER, arguments, command, enabled
from ciel_runtime_support.statusline_script import STATUSLINE_SCRIPT
import test_runtime_compaction_binding as fixtures
import test_router_shortcuts as shortcuts


class SseGateTests(unittest.TestCase):
    def test_command_marker_is_latest_user_only(self):
        self.assertIsNone(arguments({"messages": [{"role": "user", "content": SSE_GATE_MARKER}, {"role": "user", "content": "hello"}]}))
        self.assertIsNone(arguments({"messages": [{"role": "assistant", "content": SSE_GATE_MARKER}]}))
        self.assertEqual("off", arguments({"messages": [{"role": "user", "content": SSE_GATE_MARKER + "\nArguments: off"}]}))
        self.assertIsNone(arguments({"messages": [{"role": "user", "content": "quoted " + SSE_GATE_MARKER}]}))

    def test_default_status_invalid_and_mutation(self):
        config = {}
        saves = []
        self.assertTrue(enabled(config))
        def load():
            return config
        def save(value):
            saves.append(dict(value))
        self.assertIn("SSE gate on", command("status", load, save, lambda: {})[0])
        self.assertEqual([], saves)
        self.assertIn("Usage", command("typo", load, save, lambda: {})[0])
        command("off", load, save, lambda: {"state": "bypassed"})
        self.assertFalse(enabled(config))
        command("on", load, save, lambda: {"state": "open"})
        self.assertTrue(enabled(config))
        self.assertEqual(2, len(saves))

    def test_command_distinguishes_policy_from_actual_admission(self):
        for mode in (True, False):
            for state, phrase in [("held", "HELD by this gate"), ("open", "not held by this gate"),
                                  ("bypassed", "compact hold bypassed"), ("unknown", "UNKNOWN")]:
                config = {"sse_gate_enabled": mode}
                lines = command("status", lambda: config, lambda _: self.fail("status mutated config"),
                                lambda: {"state": state, "reason": "identity_pending" if state == "held" else ""})
                self.assertIn(phrase, " ".join(lines))
                self.assertIn("not whether SSE reception is enabled", " ".join(lines))
                if state == "held":
                    self.assertIn("identity_pending", lines[0])

    def test_shortcut_intercepts_without_api_or_upstream(self):
        fixture = shortcuts.RouterShortcutControllerTests()
        controller = fixture.controller()
        config = controller.response.load_config()
        saves = []
        controller = dataclasses.replace(controller, response=dataclasses.replace(controller.response,
            save_config=lambda value: saves.append(dict(value)), gate_status=lambda: {"state": "open"}))
        body = {"messages": [{"role": "user", "content": SSE_GATE_MARKER + "\nArguments: off"}]}
        self.assertTrue(controller.handle_live_api_keys(object(), body))
        self.assertFalse(config["sse_gate_enabled"])
        self.assertEqual(1, len(saves))
        self.assertIn("SSE gate off", fixture.writes[-1][2])
        self.assertEqual([], fixture.restarts)

    def test_menu_roundtrip_and_command_asset(self):
        import copy
        import ciel_runtime
        from ciel_runtime_support.llm_presentation_data import LLM_OPTION_DESCRIPTIONS, LLM_OPTION_TOGGLE_KEYS
        config = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG)
        before = copy.deepcopy(config["providers"])
        with patch.object(ciel_runtime, "load_config", return_value=config), patch.object(ciel_runtime, "save_config") as save:
            for raw, expected in [("off", False), ("on", True), ("false", False), ("true", True)]:
                ciel_runtime.set_llm_option_config("cielairouter", "sse_gate_enabled", raw)
                self.assertEqual(expected, config["sse_gate_enabled"])
                self.assertEqual(expected, ciel_runtime.llm_option_current_bool("cielairouter", config["providers"]["cielairouter"], "sse_gate_enabled"))
                rows, values = ciel_runtime.llm_option_panel_rows("cielairouter", config["providers"]["cielairouter"], "en")
                self.assertIn("SSE compact gate", rows[values.index("sse_gate_enabled")])
            self.assertEqual(4, save.call_count)
            ciel_runtime.set_llm_option_config("cielairouter", "sse_gate_enabled", "typo")
            self.assertEqual(4, save.call_count)
        self.assertEqual(before, config["providers"])
        self.assertIn("sse_gate_enabled", LLM_OPTION_TOGGLE_KEYS)
        self.assertIn("/sse-gate", LLM_OPTION_DESCRIPTIONS["sse_gate_enabled"]["en"])
        assets = ciel_runtime._ciel_runtime_command_assets()
        self.assertIn("sse-gate.md", assets)
        self.assertIn(SSE_GATE_MARKER, assets["sse-gate.md"].content)

    def test_statusline_mode_effective_state_and_staleness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "statusline.py"
            script.write_text(STATUSLINE_SCRIPT, encoding="utf-8")
            cfg = {"current_provider": "vllm", "providers": {"vllm": {"current_model": "fixture"}}}
            env = {k: v for k, v in os.environ.items() if not k.startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI"))}
            env.update(CIEL_RUNTIME_CONFIG_DIR=tmp, CIEL_RUNTIME_STATE_DIR=tmp,
                       CIEL_RUNTIME_STATUSLINE_ANSI="0", PYTHONIOENCODING="cp1252")
            def run():
                (root / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
                return subprocess.run([sys.executable, str(script)], env=env, text=True, capture_output=True,
                    input=json.dumps({"model": {"display_name": "ciel-runtime-vllm-fixture"}}), check=True).stdout
            self.assertIn("SSE gate on: unknown", run())
            for mode, state, reason in [(True, "held", "compaction_shrink_pending"), (False, "bypassed", "compaction_shrink_pending"), (False, "held", "identity_pending"), (True, "open", "")]:
                cfg["sse_gate_enabled"] = mode
                (root / "sse-gate-status.json").write_text(json.dumps({"enabled": mode, "state": state, "reason": reason, "updated_at": time.time()}), encoding="utf-8")
                self.assertIn("SSE gate " + ("on" if mode else "off") + ": " + state, run())
            (root / "sse-gate-status.json").write_text(json.dumps({"enabled": True, "state": "open", "updated_at": 1}), encoding="utf-8")
            self.assertIn("SSE gate on: unknown", run())


class SseGateBindingTests(unittest.TestCase):
    setUp = fixtures.BindingHarness.setUp
    make = fixtures.BindingHarness.make
    write = fixtures.BindingHarness.write
    usage = fixtures.BindingHarness.usage

    def test_off_only_bypasses_compact_not_safety_holds(self):
        config = {"sse_gate_enabled": False}
        self.binding.gate_config = lambda: config
        for reason in ["compaction_checkpoint_pending", "compaction_shrink_pending", "compaction_retry_wait", "context_recovery"]:
            with patch.object(self.binding, "_raw_admission_reason", return_value=reason):
                self.assertFalse(self.binding.external_admission_paused())
                self.assertEqual("bypassed", self.binding.gate_status()["state"])
                self.assertEqual("", self.binding.admission_reason())
                config["sse_gate_enabled"] = True
                self.assertTrue(self.binding.external_admission_paused())
                self.assertEqual(reason, self.binding.admission_reason())
                config["sse_gate_enabled"] = False
        for reason in ["identity_pending", "native_handoff_pending", "state_unavailable", "compaction_state_unavailable", "compaction_unknown_safety_hold"]:
            with patch.object(self.binding, "_raw_admission_reason", return_value=reason):
                self.assertTrue(self.binding.external_admission_paused())
                self.assertEqual(reason, self.binding.admission_reason())
        with patch.object(self.binding, "_raw_admission_reason", return_value=""):
            self.assertEqual("open", self.binding.gate_status()["state"])

    def test_real_cycle_off_does_not_forge_shrink_or_clear_request(self):
        self.binding.bind("claude", session_id="exact", cwd=self.root)
        self.binding.observe_request("other", {}, {"tokens": 100}, "claude")
        self.binding.observe_failure("other", {}, {"tokens": 15000}, "prompt is too long: 21000 tokens > 20000 maximum", "claude")
        self.assertTrue(self.binding.external_admission_paused())
        config = {"sse_gate_enabled": False}
        self.binding.gate_config = lambda: config
        before = self.binding.control.ports.load()
        request = self.requests.read()
        self.assertFalse(self.binding.external_admission_paused())
        after = self.binding.control.ports.load()
        self.assertEqual(before["phase"], after["phase"])
        self.assertEqual(before.get("checkpoint_id"), after.get("checkpoint_id"))
        self.assertEqual(request, self.requests.read())
        self.assertNotEqual("verified", after["phase"])
        config["sse_gate_enabled"] = True
        self.assertTrue(self.binding.external_admission_paused())

    def test_config_read_failure_holds_gate(self):
        self.binding.gate_config = lambda: (_ for _ in ()).throw(OSError("owned fixture"))
        self.assertTrue(self.binding.external_admission_paused())
        self.assertEqual("state_unavailable", self.binding.admission_reason())

    def test_status_write_failure_does_not_open_gate(self):
        with patch.object(self.binding, "_raw_admission_reason", return_value="identity_pending"), patch.object(type(self.binding.gate_snapshot), "save", side_effect=OSError):
            self.assertTrue(self.binding.external_admission_paused())
