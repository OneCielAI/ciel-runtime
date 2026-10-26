"""Global Work mode menu options: defaults, persistence, panel rows and help."""
import copy
import unittest
from unittest import mock

import ciel_runtime
from ciel_runtime_support.config_repository import build_default_config, deep_merge
from ciel_runtime_support.llm_presentation_data import LLM_OPTION_DESCRIPTIONS, LLM_OPTION_TOGGLE_KEYS
from ciel_runtime_support.provider_option_panel import OptionPanelRuntime
from ciel_runtime_support.work_mode_launch import (WORK_MODE_KEY, WORK_PLAN_APPROVAL_ASK_KEY, work_mode_enabled,
                                                   work_plan_approval_ask)


def _row(provider, pcfg, key):
    rows, values = ciel_runtime.llm_option_panel_rows(provider, pcfg, "en")
    return rows[values.index(key)]


class WorkModeOptionTests(unittest.TestCase):
    def test_defaults_and_old_config_merge(self):
        defaults = build_default_config({})
        self.assertIs(True, defaults[WORK_MODE_KEY])
        self.assertIs(False, defaults[WORK_PLAN_APPROVAL_ASK_KEY])
        merged = deep_merge(defaults, {"language": "ko"})
        self.assertTrue(work_mode_enabled(merged))
        self.assertFalse(work_plan_approval_ask(merged))
        self.assertIs(False, deep_merge(defaults, {WORK_MODE_KEY: False})[WORK_MODE_KEY])
        self.assertIs(True, deep_merge(defaults, {WORK_PLAN_APPROVAL_ASK_KEY: True})[WORK_PLAN_APPROVAL_ASK_KEY])

    def test_runtime_fields_are_optional_for_older_callers(self):
        runtime = OptionPanelRuntime(*([lambda *args: None] * 7))
        self.assertIs(True, runtime.work_mode())
        self.assertIs(False, runtime.work_plan_approval_ask())

    def test_default_rows_without_stored_keys(self):
        config = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG)
        config.pop(WORK_MODE_KEY, None)
        config.pop(WORK_PLAN_APPROVAL_ASK_KEY, None)
        pcfg = config["providers"]["cielairouter"]
        with mock.patch.object(ciel_runtime, "load_config", return_value=config):
            self.assertIn("[on (default)]", _row("cielairouter", pcfg, WORK_MODE_KEY))
            self.assertIn("Work mode", _row("cielairouter", pcfg, WORK_MODE_KEY))
            self.assertIn("[auto (default)]", _row("cielairouter", pcfg, WORK_PLAN_APPROVAL_ASK_KEY))
            self.assertIn("Work plan approval", _row("cielairouter", pcfg, WORK_PLAN_APPROVAL_ASK_KEY))
            self.assertTrue(ciel_runtime.llm_option_current_bool("cielairouter", pcfg, WORK_MODE_KEY))
            self.assertFalse(ciel_runtime.llm_option_current_bool("cielairouter", pcfg, WORK_PLAN_APPROVAL_ASK_KEY))

    def test_enter_toggle_roundtrip_is_global(self):
        config = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG)
        before = copy.deepcopy(config["providers"])
        with (mock.patch.object(ciel_runtime, "load_config", return_value=config),
              mock.patch.object(ciel_runtime, "save_config") as save,
              mock.patch.object(ciel_runtime, "clear_model_cache") as clear):
            for provider, key, labels in (("cielairouter", WORK_MODE_KEY, ("[on (default)]", "[off]")),
                                          ("anthropic", WORK_PLAN_APPROVAL_ASK_KEY, ("[auto (default)]", "[ask user]"))):
                pcfg = config["providers"][provider]
                for expected_label in (labels[1], labels[0]):
                    with self.subTest(provider=provider, key=key, label=expected_label):
                        current = ciel_runtime.llm_option_current_bool(provider, pcfg, key)
                        messages = ciel_runtime.set_llm_option_config(provider, key, "false" if current else "true")
                        self.assertIs(not current, config[key])
                        self.assertIn(expected_label, _row(provider, pcfg, key))
                        self.assertEqual("true" if config[key] else "false",
                                         ciel_runtime.llm_option_prompt_default(provider, pcfg, key))
                        text = " ".join(messages)
                        self.assertIn("Native sessions are unchanged", text)
                        self.assertIn("does not enforce", text)
            self.assertEqual(4, save.call_count)
            clear.assert_not_called()
        self.assertEqual(before, config["providers"])

    def test_aliases_default_and_bad_values(self):
        config = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG)
        with (mock.patch.object(ciel_runtime, "load_config", return_value=config),
              mock.patch.object(ciel_runtime, "save_config") as save):
            ciel_runtime.set_llm_option_config("ollama", WORK_PLAN_APPROVAL_ASK_KEY, "ask")
            self.assertIs(True, config[WORK_PLAN_APPROVAL_ASK_KEY])
            ciel_runtime.set_llm_option_config("ollama", WORK_PLAN_APPROVAL_ASK_KEY, "auto")
            self.assertIs(False, config[WORK_PLAN_APPROVAL_ASK_KEY])
            ciel_runtime.set_llm_option_config("ollama", WORK_MODE_KEY, "off")
            ciel_runtime.set_llm_option_config("ollama", WORK_MODE_KEY, "default")
            self.assertNotIn(WORK_MODE_KEY, config)
            self.assertTrue(work_mode_enabled(config))
            self.assertEqual(4, save.call_count)
            self.assertIn("enter on or off", ciel_runtime.set_llm_option_config("ollama", WORK_MODE_KEY, "maybe")[0])
            self.assertIn("enter ask or auto",
                          ciel_runtime.set_llm_option_config("ollama", WORK_PLAN_APPROVAL_ASK_KEY, "later")[0])
            self.assertEqual(4, save.call_count)

    def test_enter_toggle_and_help_are_registered_in_all_languages(self):
        for key in (WORK_MODE_KEY, WORK_PLAN_APPROVAL_ASK_KEY):
            self.assertIn(key, LLM_OPTION_TOGGLE_KEYS)
            for language in ("en", "ko", "ja", "zh"):
                with self.subTest(key=key, language=language):
                    text = LLM_OPTION_DESCRIPTIONS[key][language]
                    self.assertTrue(text)
                    self.assertEqual(text, ciel_runtime.llm_option_description("cielairouter", key, language))
            self.assertIn("Native sessions are unchanged", LLM_OPTION_DESCRIPTIONS[key]["en"])
            self.assertIn("does not enforce", LLM_OPTION_DESCRIPTIONS[key]["en"])


if __name__ == "__main__":
    unittest.main()
