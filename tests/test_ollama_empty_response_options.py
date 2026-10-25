"""Configuration-only coverage for the Ollama empty-response retry option."""
import argparse
import ast
import copy
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import ciel_runtime
from ciel_runtime_support.llm_presentation_data import LLM_OPTION_DESCRIPTIONS


KEY = "empty_response_retries"


class OllamaEmptyResponseOptionTests(unittest.TestCase):
    def test_mutation_stays_out_of_upstream_options_and_clamps(self):
        config = {"ollama_options": {"temperature": 0.7}, "ollama_explicit_options": ["temperature"]}
        before = copy.deepcopy(config)
        for raw, expected in (("0", 0), ("1", 1), ("2", 2), ("99", 2), ("0001", 1), ("9" * 5000, 2)):
            ciel_runtime.apply_ollama_option(config, f"{KEY}={raw}")
            self.assertEqual(expected, config[KEY])
            self.assertEqual(before, {k: v for k, v in config.items() if k != KEY})
        for token in (f"unset:{KEY}", f"{KEY}=default", f"{KEY}=unset", f"{KEY}=none", f"{KEY}=null"):
            config[KEY] = 0
            ciel_runtime.apply_ollama_option(config, token)
            self.assertNotIn(KEY, config)

    def test_bad_mutations_are_rejected_without_changes(self):
        config = {KEY: 2}
        for raw in ("", "true", "false", "off", "-1", "1.0", "1e1", "nan", "[]", "{}", '\"1\"', "９"):
            with self.subTest(raw=raw), self.assertRaises(SystemExit):
                ciel_runtime.apply_ollama_option(config, f"{KEY}={raw}")
            self.assertEqual({KEY: 2}, config)
        for provider in ("vllm", "anthropic", "cielairouter"):
            with self.subTest(provider=provider), self.assertRaises(SystemExit):
                ciel_runtime.apply_provider_option(provider, config, f"{KEY}=1")

    def test_effective_status_and_prompt_are_read_only(self):
        for provider in ("ollama", "ollama-cloud"):
            for raw, expected in ((None, 1), (0, 0), (2, 2), (99, 2), (True, 1), (False, 1), (2.5, 1), ([], 1), ({}, 1), ("invalid", 1)):
                with self.subTest(provider=provider, raw=raw):
                    config = {} if raw is None else {KEY: raw}
                    before = copy.deepcopy(config)
                    self.assertEqual(str(expected), ciel_runtime.llm_option_prompt_default(provider, config, KEY))
                    self.assertIn(f"{KEY}={expected}", ciel_runtime.provider_options_status(provider, config))
                    self.assertIn(f"{KEY}={expected}", ciel_runtime.llm_options_status(provider, config))
                    self.assertEqual(before, config)
        self.assertNotIn(f"{KEY}=", ciel_runtime.provider_options_status("vllm", {KEY: 2}))

    def test_portable_menu_row_and_mutation_roundtrip(self):
        config = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG)
        with (mock.patch.object(ciel_runtime, "load_config", return_value=config),
              mock.patch.object(ciel_runtime, "save_config") as save,
              mock.patch.object(ciel_runtime, "clear_model_cache") as clear,
              mock.patch.object(ciel_runtime, "cap_context_settings_to_model_capacity") as cap_context,
              mock.patch.object(ciel_runtime, "cap_output_settings_to_context_ratio") as cap_output):
            for provider in ("ollama", "ollama-cloud", "vllm"):
                pcfg = config["providers"][provider]
                rows, values = ciel_runtime.llm_option_panel_rows(provider, pcfg, "en")
                self.assertEqual(provider != "vllm", KEY in values)
                if KEY in values:
                    self.assertIn("default 1, 0 off, max 2", rows[values.index(KEY)])
            pcfg = config["providers"]["ollama-cloud"]
            before = copy.deepcopy(pcfg)
            for raw, expected in (("0", 0), ("7", 2), ("default", None), ("2", 2), ("unset", None)):
                messages = ciel_runtime.set_llm_option_config("ollama-cloud", KEY, raw)
                self.assertEqual(expected, pcfg.get(KEY))
                self.assertIn(f"{KEY}: {1 if expected is None else expected}", messages)
                self.assertEqual(before, {k: v for k, v in pcfg.items() if k != KEY})
            self.assertEqual(5, save.call_count)
            clear.assert_not_called()
            cap_context.assert_not_called()
            cap_output.assert_not_called()

    def test_cli_both_entrypoints_and_read_only_status(self):
        config = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG)
        with (mock.patch.object(ciel_runtime, "load_config", return_value=config),
              mock.patch.object(ciel_runtime, "save_config") as save,
              mock.patch.object(ciel_runtime, "clear_model_cache"),
              mock.patch.object(ciel_runtime, "cap_context_settings_to_model_capacity", return_value=[]),
              mock.patch.object(ciel_runtime, "cap_output_settings_to_context_ratio", return_value=[])):
            for command in (ciel_runtime.cmd_ollama_options, ciel_runtime.cmd_provider_options):
                for token, expected in ((f"{KEY}=0", 0), (f"{KEY}=99", 2), (f"unset:{KEY}", 1)):
                    with redirect_stdout(io.StringIO()) as output:
                        command(argparse.Namespace(values=["ollama-cloud", token]))
                    self.assertIn(f"{KEY}{':' if command == ciel_runtime.cmd_ollama_options else '='}{' ' if command == ciel_runtime.cmd_ollama_options else ''}{expected}", output.getvalue())
            save.reset_mock()
            with redirect_stdout(io.StringIO()) as output:
                ciel_runtime.cmd_ollama_options(argparse.Namespace(values=["ollama"]))
            self.assertIn("default 1, 0 off, capped at 2", output.getvalue())
            save.assert_not_called()

    def test_standalone_menu_has_edit_control_and_effective_summary(self):
        # Extract pure functions: the standalone menu imports POSIX-only termios.
        source = Path(ciel_runtime.__file__).with_name("ciel-runtime-menu.py").read_text(encoding="utf-8")
        names = {"ollama_ctx_text", "ollama_options_summary", "ollama_option_description", "build_ollama_options_submenu"}
        tree = ast.parse(source)
        code = ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names], type_ignores=[])
        descriptions = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                            and any(isinstance(t, ast.Name) and t.id == "OLLAMA_OPTION_DESCRIPTIONS" for t in n.targets))
        namespace = {"current_provider_cfg": lambda: ("ollama-cloud", {}),
                     "current_language": lambda: "en", "OLLAMA_OPTION_DESCRIPTIONS": ast.literal_eval(descriptions)}
        exec(compile(code, "ciel-runtime-menu.py", "exec"), namespace)
        items = namespace["build_ollama_options_submenu"]()["items"]
        row = next(item for item in items if item["value"] == "__edit_empty_response_retries__")
        self.assertIn("1 extra; 0 off, max 2", row["label"])
        self.assertIn("Default 1", row["description"])
        self.assertIn(f"{KEY}=1", namespace["ollama_options_summary"]({}))
        for lang in ("en", "ko", "ja", "zh"):
            self.assertTrue(LLM_OPTION_DESCRIPTIONS[KEY][lang])


if __name__ == "__main__":
    unittest.main()
