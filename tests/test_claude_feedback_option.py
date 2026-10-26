"""Global Claude feedback toggle: menu persistence and launch defaults."""
import copy
import unittest
from unittest import mock

import ciel_runtime
from ciel_runtime_support.config_repository import build_default_config, deep_merge
from ciel_runtime_support.llm_presentation_data import LLM_OPTION_DESCRIPTIONS, LLM_OPTION_TOGGLE_KEYS


class ClaudeFeedbackOptionTests(unittest.TestCase):
    def test_defaults_and_old_config_merge_disable_drafts(self):
        defaults = build_default_config({})
        self.assertIs(False, defaults["claude_feedback_drafts"])
        self.assertIs(False, deep_merge(defaults, {"language": "ko"})["claude_feedback_drafts"])
        self.assertIs(True, deep_merge(defaults, {"claude_feedback_drafts": True})["claude_feedback_drafts"])

    def test_menu_global_roundtrip_does_not_touch_provider_model_options(self):
        config = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG)
        config.pop("claude_feedback_drafts", None)
        before = copy.deepcopy(config["providers"])
        with (mock.patch.object(ciel_runtime, "load_config", return_value=config),
              mock.patch.object(ciel_runtime, "save_config") as save,
              mock.patch.object(ciel_runtime, "clear_model_cache") as clear,
              mock.patch.object(ciel_runtime, "cap_context_settings_to_model_capacity") as cap,
              mock.patch.object(ciel_runtime, "cap_output_settings_to_context_ratio") as output_cap):
            for provider, raw, expected in (("cielairouter", "true", True),
                                            ("anthropic", "off", False), ("ollama", "on", True),
                                            ("cielairouter", "default", False), ("vllm", "yes", True),
                                            ("anthropic", "unset", False)):
                with self.subTest(provider=provider, raw=raw):
                    messages = ciel_runtime.set_llm_option_config(provider, "claude_feedback_drafts", raw)
                    self.assertEqual(expected, config.get("claude_feedback_drafts", False))
                    pcfg = config["providers"][provider]
                    self.assertEqual(expected, ciel_runtime.llm_option_current_bool(provider, pcfg, "claude_feedback_drafts"))
                    self.assertEqual("true" if expected else "false",
                                     ciel_runtime.llm_option_prompt_default(provider, pcfg, "claude_feedback_drafts"))
                    rows, values = ciel_runtime.llm_option_panel_rows(provider, pcfg, "en")
                    self.assertIn("Claude feedback drafts", rows[values.index("claude_feedback_drafts")])
                    self.assertIn("[on]" if expected else "[off (default)]",
                                  rows[values.index("claude_feedback_drafts")])
                    self.assertIn("every Claude launch", " ".join(messages))
            self.assertEqual(6, save.call_count)
            clear.assert_not_called()
            cap.assert_not_called()
            output_cap.assert_not_called()
        self.assertEqual(before, config["providers"])

    def test_bad_values_do_not_change_or_save_config(self):
        config = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG)
        config["claude_feedback_drafts"] = True
        with (mock.patch.object(ciel_runtime, "load_config", return_value=config),
              mock.patch.object(ciel_runtime, "save_config") as save):
            for value in ("quiet", "notify", "unexpected", "2"):
                with self.subTest(value=value):
                    result = ciel_runtime.set_llm_option_config("anthropic", "claude_feedback_drafts", value)
                    self.assertIn("enter on or off", result[0])
                    self.assertIs(True, config["claude_feedback_drafts"])
            save.assert_not_called()

    def test_invalid_stored_value_is_off_even_with_provider_override(self):
        config = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG)
        config["claude_feedback_drafts"] = "true"
        pcfg = config["providers"]["cielairouter"]
        pcfg["claude_feedback_drafts"] = True
        with mock.patch.object(ciel_runtime, "load_config", return_value=config):
            self.assertFalse(ciel_runtime.llm_option_current_bool("cielairouter", pcfg, "claude_feedback_drafts"))
            self.assertEqual("0", ciel_runtime.env_vars(config)["CLAUDE_CODE_SEND_FEEDBACK"])

    def test_menu_description_and_enter_toggle_are_registered(self):
        self.assertIn("claude_feedback_drafts", LLM_OPTION_TOGGLE_KEYS)
        for language in ("en", "ko", "ja", "zh"):
            self.assertIn("/feedback", LLM_OPTION_DESCRIPTIONS["claude_feedback_drafts"][language])


if __name__ == "__main__":
    unittest.main()
