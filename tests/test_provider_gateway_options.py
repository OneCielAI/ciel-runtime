import argparse
import ast
import copy
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import ciel_runtime
from ciel_runtime_support.provider_option_status import gateway_health_lines


class GatewayOptionTests(unittest.TestCase):
    def test_cli_round_trip_and_other_provider_unchanged(self):
        cfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG)
        before = copy.deepcopy(cfg["providers"]["vllm"])
        with (mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
              mock.patch.object(ciel_runtime, "save_config") as save,
              mock.patch.object(ciel_runtime, "clear_model_cache") as clear,
              mock.patch.object(ciel_runtime, "cap_context_settings_to_model_capacity") as cap,
              redirect_stdout(io.StringIO()) as out):
            for value in ("off", "auto"):
                ciel_runtime.cmd_provider_options(argparse.Namespace(values=["cielairouter", f"gateway_selection={value}"]))
                self.assertEqual(value, cfg["providers"]["cielairouter"]["gateway_selection"])
                self.assertIn(f"gateway_selection={value}", out.getvalue())
            self.assertEqual(2, save.call_count)
            clear.assert_not_called()
            cap.assert_not_called()
        self.assertEqual(before, cfg["providers"]["vllm"])

    def test_gateway_retry_cli_round_trip_does_not_mutate_model_options(self):
        cfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG)
        pcfg = cfg["providers"]["cielairouter"]
        pcfg.pop("gateway_retries", None)
        before = copy.deepcopy(pcfg)
        with (mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
              mock.patch.object(ciel_runtime, "save_config") as save,
              mock.patch.object(ciel_runtime, "clear_model_cache") as clear,
              mock.patch.object(ciel_runtime, "cap_context_settings_to_model_capacity") as cap_context,
              mock.patch.object(ciel_runtime, "cap_output_settings_to_context_ratio") as cap_output,
              mock.patch.object(ciel_runtime, "apply_recommended_timeout_for_model_context") as timeout):
            for token, expected in (("gateway_retries=0", 0), ("gateway_retries=10", 10),
                                    ("unset:gateway_retries", 3)):
                with self.subTest(token=token), redirect_stdout(io.StringIO()) as out:
                    ciel_runtime.cmd_provider_options(argparse.Namespace(values=["cielairouter", token]))
                    self.assertIn(f"gateway_retries={expected}", out.getvalue())
                    self.assertEqual(before, {key: value for key, value in pcfg.items() if key != "gateway_retries"})
            self.assertEqual(3, save.call_count)
            clear.assert_not_called()
            cap_context.assert_not_called()
            cap_output.assert_not_called()
            timeout.assert_not_called()
        self.assertNotIn("gateway_retries", pcfg)

    def test_gateway_retry_status_projects_effective_limit_without_mutation(self):
        for value, expected in ((None, 3), (0, 0), (10, 10), (99, 10),
                                (-1, 0), ("2", 2), (True, 3), (1.5, 3), ("invalid", 3)):
            with self.subTest(value=value):
                pcfg = {} if value is None else {"gateway_retries": value}
                before = copy.deepcopy(pcfg)
                self.assertIn(f"gateway_retries={expected}", ciel_runtime.provider_options_status("cielairouter", pcfg))
                self.assertIn(f"gateway_retries={expected}", ciel_runtime.llm_options_status("cielairouter", pcfg))
                self.assertEqual(before, pcfg)
        self.assertNotIn("gateway_retries=", ciel_runtime.provider_options_status("vllm", {"gateway_retries": 5}))

    def test_gateway_retry_validation_and_unset(self):
        pcfg = {}
        for value in ("0", "3", "10"):
            ciel_runtime.apply_provider_option("cielairouter", pcfg, f"gateway_retries={value}")
            self.assertEqual(int(value), pcfg["gateway_retries"])
        for value in ("-1", "11", "true", "false", "1.5", "3.0", "nan", "inf", "1e2", "", "none", "９"):
            with self.subTest(value=value), self.assertRaises(SystemExit):
                ciel_runtime.apply_provider_option("cielairouter", pcfg, f"gateway_retries={value}")
            self.assertEqual(10, pcfg["gateway_retries"])
        for provider in ("vllm", "ollama", "anthropic", "codex"):
            with self.subTest(provider=provider), self.assertRaises(SystemExit):
                ciel_runtime.apply_provider_option(provider, pcfg, "gateway_retries=3")
        with self.assertRaises(SystemExit):
            ciel_runtime.apply_provider_option("cielairouter", pcfg, "gateway_retries")
        ciel_runtime.apply_provider_option("cielairouter", pcfg, "unset:gateway_retries")
        self.assertNotIn("gateway_retries", pcfg)

    def test_retry_prompt_and_portable_menu_round_trip(self):
        cfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG)
        pcfg = cfg["providers"]["cielairouter"]
        pcfg.pop("gateway_retries", None)
        with (mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
              mock.patch.object(ciel_runtime, "save_config") as save,
              mock.patch.object(ciel_runtime, "clear_model_cache"),
              mock.patch.object(ciel_runtime, "cap_context_settings_to_model_capacity", return_value=[]),
              mock.patch.object(ciel_runtime, "cap_output_settings_to_context_ratio", return_value=[])):
            self.assertEqual("3", ciel_runtime.llm_option_prompt_default("cielairouter", pcfg, "gateway_retries"))
            for raw, expected in (("0", 0), ("10", 10), ("default", None), ("3", 3), ("unset", None)):
                ciel_runtime.set_llm_option_config("cielairouter", "gateway_retries", raw)
                self.assertEqual(expected, pcfg.get("gateway_retries"))
                self.assertEqual(str(3 if expected is None else expected),
                                 ciel_runtime.llm_option_prompt_default("cielairouter", pcfg, "gateway_retries"))
            self.assertEqual(5, save.call_count)

    def test_validation_and_unset(self):
        pcfg = {}
        for value in ("on", "false", "refresh", "", "automatic"):
            with self.assertRaises(SystemExit):
                ciel_runtime.apply_provider_option("cielairouter", pcfg, f"gateway_selection={value}")
        with self.assertRaises(SystemExit):
            ciel_runtime.apply_provider_option("vllm", pcfg, "gateway_selection=off")
        with self.assertRaises(SystemExit):
            ciel_runtime.apply_provider_option("cielairouter", pcfg, "gateway_selection")
        ciel_runtime.apply_provider_option("cielairouter", pcfg, "gateway_selection=off")
        ciel_runtime.apply_provider_option("cielairouter", pcfg, "unset:gateway_selection")
        self.assertNotIn("gateway_selection", pcfg)

    def test_portable_menu_config_path(self):
        cfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG)
        with (mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
              mock.patch.object(ciel_runtime, "save_config") as save,
              mock.patch.object(ciel_runtime, "clear_model_cache")):
            ciel_runtime.set_llm_option_config("cielairouter", "gateway_selection", "off")
            self.assertEqual("off", cfg["providers"]["cielairouter"]["gateway_selection"])
            save.assert_called_once()

    def test_cli_status_is_read_only(self):
        cfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG)
        with (mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
              mock.patch.object(ciel_runtime, "save_config") as save,
              mock.patch.object(ciel_runtime, "router_health", return_value={"cielairouter_gateway": {"routes": []}}),
              mock.patch.object(ciel_runtime, "router_health_config_matches_current", return_value=True),
              mock.patch("ciel_runtime_support.cielairouter_gateway_overlay._load_selector") as selector,
              redirect_stdout(io.StringIO()) as out):
            ciel_runtime.cmd_provider_options(argparse.Namespace(values=["cielairouter", "gateway-status"]))
            self.assertIn("No route observed", out.getvalue())
            save.assert_not_called()
            selector.assert_not_called()
            with self.assertRaises(SystemExit):
                ciel_runtime.cmd_provider_options(argparse.Namespace(values=["cielairouter", "gateway-status", "gateway_selection=off"]))
            save.assert_not_called()

    def test_status_rejects_foreign_router(self):
        with (mock.patch.object(ciel_runtime, "router_health", return_value={}),
              mock.patch.object(ciel_runtime, "router_health_config_matches_current", return_value=False)):
            self.assertIn("another configuration", ciel_runtime.gateway_status_lines()[0])

    def test_health_states_scope_and_url_sanitization(self):
        cfg = {"base_url": "https://central.example"}
        for state in ("gateway", "central", "failed"):
            health = {"cielairouter_gateway": {"routes": [
                {"central": "other.example", "endpoint": "https://other.example", "reason": "foreign"},
                {"central": "central.example", "state": state, "endpoint": "https://user:secret@gateway.example:443/path?key=secret", "reason": "ranked"},
            ], "selector_error": "private"}}
            text = " ".join(gateway_health_lines(health, cfg))
            self.assertIn(f"{state}:", text)
            self.assertIn("reason: ranked", text)
            self.assertIn("not per-session", text)
            for hidden in ("secret", "private", "foreign", "other.example"):
                self.assertNotIn(hidden, text)
        self.assertIn("not reachable", " ".join(gateway_health_lines(None, cfg)))
        self.assertIn("no CielAiRouter", " ".join(gateway_health_lines({}, cfg)))
        self.assertIn("may be stale", " ".join(gateway_health_lines(None, {**cfg, "gateway_selection": "off"})))

    def test_portable_panel_contains_controls_only_for_cielairouter(self):
        cfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG)
        with mock.patch.object(ciel_runtime, "load_config", return_value=cfg):
            for provider in ("cielairouter", "vllm"):
                _rows, keys = ciel_runtime.llm_option_panel_rows(provider, cfg["providers"][provider], "en")
                self.assertEqual(provider == "cielairouter", "gateway_selection" in keys)
                self.assertEqual(provider == "cielairouter", "gateway_status" in keys)
                self.assertEqual(provider == "cielairouter", "gateway_retries" in keys)

    def test_external_menu_choices_and_default_on(self):
        # Pure function extraction avoids the POSIX-only termios import on Windows.
        source = Path(ciel_runtime.__file__).with_name("ciel-runtime-menu.py").read_text(encoding="utf-8")
        names = {"has_provider_options", "build_provider_options_submenu"}
        tree = ast.parse(source)
        code = ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names], type_ignores=[])
        for config, selected in (({}, "auto"), ({"gateway_selection": "off"}, "off")):
            namespace = {"current_provider_cfg": lambda: ("cielairouter", config), "provider_option_description": lambda _: "description"}
            exec(compile(code, "ciel-runtime-menu.py", "exec"), namespace)
            self.assertTrue(namespace["has_provider_options"]("cielairouter"))
            choices = namespace["build_provider_options_submenu"]()["items"]
            self.assertTrue(any(item["value"] == f"gateway_selection={selected}" and item["current"] for item in choices))
            self.assertTrue(any(item["value"] == "__gateway_status__" for item in choices))
            self.assertFalse(any("refresh" in item["value"] for item in choices))


if __name__ == "__main__":
    unittest.main()
