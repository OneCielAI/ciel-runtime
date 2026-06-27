import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ciel_runtime


class LiveLlmOptionsTests(unittest.TestCase):
    def _with_config(self, cfg):
        return (
            mock.patch.object(ciel_runtime, "load_config", lambda: cfg),
            mock.patch.object(ciel_runtime, "save_config"),
            mock.patch.object(ciel_runtime, "clear_model_cache"),
        )

    def test_apply_runtime_preset_captures_and_restore_original_options(self):
        cfg = {
            "language": "en",
            "current_provider": "opencode",
            "providers": {
                "opencode": {
                    "current_model": "deepseek-v4-flash-free",
                    "context_window": 32768,
                    "context_reserve_tokens": 2048,
                    "max_output_tokens": 4096,
                    "request_timeout_ms": 300000,
                }
            },
        }
        patches = self._with_config(cfg)
        with patches[0], patches[1], patches[2]:
            lines, changed = ciel_runtime.handle_live_llm_options_action("long-context-128k")

        pcfg = cfg["providers"]["opencode"]
        self.assertTrue(changed)
        self.assertIn(ciel_runtime.RUNTIME_LLM_ORIGINAL_KEY, pcfg)
        self.assertEqual(131072, pcfg["context_window"])
        self.assertEqual(8192, pcfg["max_output_tokens"])
        self.assertTrue(any("Captured current live LLM options" in line for line in lines))

        patches = self._with_config(cfg)
        with patches[0], patches[1], patches[2]:
            lines, changed = ciel_runtime.handle_live_llm_options_action("restore")

        pcfg = cfg["providers"]["opencode"]
        self.assertTrue(changed)
        self.assertNotIn(ciel_runtime.RUNTIME_LLM_ORIGINAL_KEY, pcfg)
        self.assertEqual(32768, pcfg["context_window"])
        self.assertEqual(2048, pcfg["context_reserve_tokens"])
        self.assertEqual(4096, pcfg["max_output_tokens"])
        self.assertTrue(any("Restored live LLM options" in line for line in lines))

    def test_runtime_status_lists_restore_availability(self):
        cfg = {
            "language": "en",
            "current_provider": "ollama-cloud",
            "providers": {
                "ollama-cloud": {
                    "current_model": "deepseek-v4-flash",
                    "num_ctx": "auto",
                    "num_ctx_max": 1048576,
                    "ollama_options": {"num_predict": 8192},
                    ciel_runtime.RUNTIME_LLM_ORIGINAL_KEY: {"values": {}},
                }
            },
        }
        patches = self._with_config(cfg)
        with patches[0], patches[1], patches[2]:
            lines, changed = ciel_runtime.handle_live_llm_options_action("list")

        self.assertFalse(changed)
        self.assertTrue(any("Slider:" in line and "[1M]" in line for line in lines))
        self.assertTrue(any("Use `/llm left` or `/llm right`" in line for line in lines))
        self.assertFalse(any("/llm-long-context-" in line for line in lines))
        self.assertTrue(any("Restore available: yes" in line for line in lines))

    def test_anthropic_routed_live_preset_does_not_force_output_tokens(self):
        cfg = {
            "language": "en",
            "current_provider": "anthropic",
            "providers": {
                "anthropic": {
                    "current_model": "claude-opus-4-8",
                    "route_through_router": True,
                    "request_timeout_ms": 300000,
                }
            },
        }
        patches = self._with_config(cfg)
        with patches[0], patches[1], patches[2]:
            _lines, changed = ciel_runtime.handle_live_llm_options_action("large-output")

        self.assertTrue(changed)
        self.assertNotIn("max_output_tokens", cfg["providers"]["anthropic"])

    def test_slash_command_install_adds_keyboard_selectable_llm_presets(self):
        with tempfile.TemporaryDirectory() as td:
            commands_dir = Path(td)
            (commands_dir / "llm-balanced.md").write_text(
                "CIEL_RUNTIME_LIVE_LLM_OPTIONS\nValue: balanced\n",
                encoding="utf-8",
            )
            (commands_dir / "llm-long-context-300k.md").write_text(
                "CIEL_RUNTIME_LIVE_LLM_OPTIONS\nValue: long-context-300k\n",
                encoding="utf-8",
            )
            with mock.patch.object(ciel_runtime, "CLAUDE_COMMANDS_DIR", commands_dir):
                ciel_runtime.install_ciel_runtime_slash_commands(include_advisor=False)

            self.assertTrue((commands_dir / "llm.md").exists())
            self.assertTrue((commands_dir / "llm-options.md").exists())
            self.assertTrue((commands_dir / "llm-restore.md").exists())
            self.assertFalse((commands_dir / "llm-balanced.md").exists())
            self.assertFalse((commands_dir / "llm-long-context-128k.md").exists())
            self.assertFalse((commands_dir / "llm-long-context-256k.md").exists())
            self.assertFalse((commands_dir / "llm-long-context-300k.md").exists())
            self.assertFalse((commands_dir / "llm-long-context-512k.md").exists())
            llm_command = (commands_dir / "llm.md").read_text(encoding="utf-8")
            self.assertIn("CIEL_RUNTIME_LIVE_LLM_OPTIONS", llm_command)
            self.assertIn("Value: $0", llm_command)
            self.assertIn("Arguments: $ARGUMENTS", llm_command)

    def test_slash_command_install_replaces_legacy_live_llm_command(self):
        with tempfile.TemporaryDirectory() as td:
            commands_dir = Path(td)
            legacy_marker = ciel_runtime.LEGACY_LIVE_LLM_OPTIONS_MARKER
            (commands_dir / "llm.md").write_text(
                f"---\ndescription: old runtime command\n---\n\n{legacy_marker}\n\nValue: $ARGUMENTS\n",
                encoding="utf-8",
            )

            with mock.patch.object(ciel_runtime, "CLAUDE_COMMANDS_DIR", commands_dir):
                ciel_runtime.install_ciel_runtime_slash_commands(include_advisor=False)

            llm_command = (commands_dir / "llm.md").read_text(encoding="utf-8")
            self.assertIn("CIEL_RUNTIME_LIVE_LLM_OPTIONS", llm_command)
            self.assertNotIn(legacy_marker, llm_command)

    def test_live_llm_slash_value_uses_arg0(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "CIEL_RUNTIME_LIVE_LLM_OPTIONS\n\nValue: right\nArguments: $ARGUMENTS",
                        }
                    ],
                }
            ]
        }

        self.assertEqual("right", ciel_runtime.live_llm_options_value_from_body(body))

    def test_live_llm_slash_value_falls_back_to_arguments(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "CIEL_RUNTIME_LIVE_LLM_OPTIONS\n\nValue: $0\nArguments: left",
                        }
                    ],
                }
            ]
        }

        self.assertEqual("left", ciel_runtime.live_llm_options_value_from_body(body))

    def test_live_llm_slash_value_accepts_legacy_marker(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"{ciel_runtime.LEGACY_LIVE_LLM_OPTIONS_MARKER}\n\nValue: $0\nArguments: 300K",
                        }
                    ],
                }
            ]
        }

        self.assertEqual("300K", ciel_runtime.live_llm_options_value_from_body(body))

    def test_live_llm_slash_value_ignores_unexpanded_placeholders(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "CIEL_RUNTIME_LIVE_LLM_OPTIONS\n\nValue: $0\nArguments: $ARGUMENTS",
                        }
                    ],
                }
            ]
        }

        self.assertEqual("status", ciel_runtime.live_llm_options_value_from_body(body))

    def test_live_llm_slider_right_moves_to_next_preset(self):
        cfg = {
            "language": "en",
            "current_provider": "opencode",
            "providers": {
                "opencode": {
                    "current_model": "deepseek-v4-flash-free",
                    "llm_preset": "long-context-256k",
                    "context_window": 262144,
                    "context_reserve_tokens": 8192,
                    "max_output_tokens": 8192,
                    "request_timeout_ms": 300000,
                }
            },
        }
        patches = self._with_config(cfg)
        with patches[0], patches[1], patches[2]:
            lines, changed = ciel_runtime.handle_live_llm_options_action("right")

        pcfg = cfg["providers"]["opencode"]
        self.assertTrue(changed)
        self.assertEqual("long-context-300k", pcfg["llm_preset"])
        self.assertEqual(307200, pcfg["context_window"])
        self.assertTrue(any("Slider:" in line and "[300K]" in line for line in lines))

    def test_native_mode_removes_owned_llm_slash_commands(self):
        with tempfile.TemporaryDirectory() as td:
            commands_dir = Path(td)
            with mock.patch.object(ciel_runtime, "CLAUDE_COMMANDS_DIR", commands_dir):
                ciel_runtime.install_ciel_runtime_slash_commands(include_advisor=False)
                ciel_runtime.disable_ciel_runtime_slash_commands_for_native()

            self.assertFalse((commands_dir / "llm.md").exists())
            self.assertFalse((commands_dir / "llm-options.md").exists())
            self.assertFalse((commands_dir / "llm-balanced.md").exists())


if __name__ == "__main__":
    unittest.main()
