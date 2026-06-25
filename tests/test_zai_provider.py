import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import ciel_runtime


class ZaiProviderTests(unittest.TestCase):
    def zai_cfg(self, **overrides):
        pcfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["zai"])
        pcfg.update(overrides)
        return {
            "current_provider": "zai",
            "providers": {
                "zai": pcfg,
            },
        }

    def test_provider_is_registered(self):
        self.assertEqual("zai", ciel_runtime.PROVIDER_ALIASES["z.ai"])
        self.assertEqual("zai", ciel_runtime.PROVIDER_ALIASES["zhipu"])
        self.assertEqual("zai", ciel_runtime.PROVIDER_ALIASES["glm"])
        self.assertEqual("Z.AI GLM", ciel_runtime.PROVIDER_LABELS["zai"])
        self.assertEqual(ciel_runtime.ZAI_ANTHROPIC_BASE_URL, ciel_runtime.default_base_url("zai"))

    def test_default_config_matches_zai_claude_code_docs(self):
        pcfg = ciel_runtime.DEFAULT_CONFIG["providers"]["zai"]
        self.assertEqual("https://api.z.ai/api/anthropic", pcfg["base_url"])
        self.assertEqual("glm-5.2[1m]", pcfg["current_model"])
        self.assertEqual("glm-5.2[1m]", pcfg["opus_model"])
        self.assertEqual("glm-5.2[1m]", pcfg["sonnet_model"])
        self.assertEqual("glm-4.7", pcfg["haiku_model"])
        self.assertEqual(1000000, pcfg["context_window"])
        self.assertEqual(1000000, pcfg["auto_compact_window"])
        self.assertEqual(3000000, pcfg["request_timeout_ms"])
        self.assertTrue(pcfg["native_compat"])
        self.assertTrue(pcfg["preserve_anthropic_thinking"])
        self.assertIn("thinking", pcfg["claude_code_supported_capabilities"])

    def test_model_suffix_is_preserved_for_zai_one_million_context(self):
        self.assertEqual("glm-5.2[1m]", ciel_runtime.normalize_model_id("zai", "glm-5.2[1m]"))
        pcfg = self.zai_cfg(current_model="glm-5.2[1m]")["providers"]["zai"]

        self.assertEqual(1000000, ciel_runtime.provider_model_context_capacity("zai", pcfg))

    def test_zai_turbo_suffix_does_not_claim_one_million_context(self):
        pcfg = self.zai_cfg(
            current_model="glm-5-turbo[1m]",
            context_window=524288,
            auto_compact_window=1000000,
            max_output_tokens=32768,
            context_reserve_tokens=16384,
        )["providers"]["zai"]

        self.assertEqual(200000, ciel_runtime.provider_model_context_capacity("zai", pcfg))
        self.assertEqual(200000, ciel_runtime.context_limit_for_status("zai", pcfg))
        self.assertEqual(200000, ciel_runtime.claude_code_auto_compact_window("zai", pcfg))
        self.assertEqual("long-context", ciel_runtime.model_option_family("zai", pcfg))
        self.assertEqual("long-context-128k", ciel_runtime.recommended_preset_id("zai", pcfg))

        messages = ciel_runtime.cap_context_settings_to_model_capacity("zai", pcfg)
        messages.extend(ciel_runtime.cap_output_settings_to_context_ratio("zai", pcfg))

        self.assertEqual(200000, pcfg["context_window"])
        self.assertEqual(6144, pcfg["max_output_tokens"])
        self.assertTrue(any("Context window capped" in line for line in messages))

    def test_provider_headers_include_zai_api_key(self):
        pcfg = self.zai_cfg(api_key="sk-zai-test")["providers"]["zai"]

        headers = ciel_runtime.provider_headers("zai", pcfg)

        self.assertEqual("Bearer sk-zai-test", headers["authorization"])
        self.assertEqual("sk-zai-test", headers["x-api-key"])
        self.assertEqual("2023-06-01", headers["anthropic-version"])
        self.assertEqual("claude-cli", headers["user-agent"])

    def test_model_list_fetches_zai_models_and_keeps_documented_fallbacks(self):
        pcfg = self.zai_cfg(api_key="sk-zai-test", custom_models=[])["providers"]["zai"]
        response = {
            "data": [
                {
                    "id": "glm-5.2[1m]",
                    "context_length": 1000000,
                }
            ]
        }

        with (
            mock.patch.object(ciel_runtime, "read_model_list_cache", return_value=None),
            mock.patch.object(ciel_runtime, "http_json", return_value=response) as http_json,
            mock.patch.object(ciel_runtime, "write_model_list_cache") as write_cache,
        ):
            models = ciel_runtime.upstream_model_ids("zai", pcfg)

        self.assertIn("glm-5.2[1m]", models)
        self.assertIn("glm-4.7", models)
        self.assertTrue(http_json.call_args.args[0].endswith("/anthropic/v1/models"))
        write_cache.assert_called_once()
        metadata = write_cache.call_args.args[3]
        self.assertEqual(1000000, metadata["model_info"]["glm-5.2[1m]"]["max_model_len"])

    def test_model_list_falls_back_to_documented_zai_models_without_network(self):
        pcfg = self.zai_cfg(api_key="", custom_models=[])["providers"]["zai"]

        with (
            mock.patch.object(ciel_runtime, "read_model_list_cache", return_value=None),
            mock.patch.object(ciel_runtime, "http_json", side_effect=RuntimeError("network down")),
            mock.patch.object(ciel_runtime, "write_model_list_cache") as write_cache,
        ):
            models = ciel_runtime.upstream_model_ids("zai", pcfg)

        self.assertIn("glm-5.2[1m]", models)
        self.assertIn("glm-5-turbo", models)
        self.assertIn("glm-4.7-flash", models)
        self.assertIn("glm-4.7-flashx", models)
        self.assertIn("glm-4.6", models)
        self.assertIn("glm-4.5-flash", models)
        self.assertIn("glm-4-32b-0414-128k", models)
        write_cache.assert_called_once()

    def test_zai_documented_model_context_hints_cover_current_text_models(self):
        cases = {
            "glm-5.2": 1000000,
            "glm-5.2[1m]": 1000000,
            "glm-5.1": 200000,
            "glm-5": 200000,
            "glm-5-turbo": 200000,
            "glm-4.7": 200000,
            "glm-4.7-flashx": 200000,
            "glm-4.7-flash": 200000,
            "glm-4.6": 200000,
            "glm-4.5": 128000,
            "glm-4.5-x": 128000,
            "glm-4.5-airx": 128000,
            "glm-4.5-air": 128000,
            "glm-4.5-flash": 128000,
            "glm-4-32b-0414-128k": 128000,
        }
        for model, expected in cases.items():
            with self.subTest(model=model):
                self.assertEqual(expected, ciel_runtime.model_context_hint_from_model_id(model))

    def test_env_vars_route_zai_through_ciel_runtime_router_with_glm_defaults(self):
        cfg = self.zai_cfg(api_key="sk-zai-test")

        env = ciel_runtime.env_vars(cfg)

        self.assertEqual("zai", env["CIEL_RUNTIME_PROVIDER"])
        self.assertEqual(ciel_runtime.ROUTER_BASE, env["ANTHROPIC_BASE_URL"])
        self.assertEqual("sk-zai-test", env["ANTHROPIC_AUTH_TOKEN"])
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertEqual("1000000", env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"])
        self.assertEqual("8192", env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"])
        self.assertEqual("ciel-runtime-zai-glm-4.7", env["ANTHROPIC_DEFAULT_HAIKU_MODEL"])
        self.assertEqual("ciel-runtime-zai-glm-5.2-1m[1m]", env["ANTHROPIC_DEFAULT_OPUS_MODEL"])
        self.assertEqual("ciel-runtime-zai-glm-5.2-1m[1m]", env["ANTHROPIC_DEFAULT_SONNET_MODEL"])
        self.assertIn("thinking", env["ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES"])

    def test_resolve_requested_model_strips_zai_context_suffix_for_api(self):
        cfg = self.zai_cfg(api_key="sk-zai-test")
        pcfg = cfg["providers"]["zai"]

        self.assertEqual("glm-5.2[1m]", ciel_runtime.current_upstream_model_id("zai", pcfg))
        self.assertEqual(
            "glm-5.2",
            ciel_runtime.resolve_requested_model("zai", pcfg, "ciel-runtime-zai-glm-5.2-1m[1m]"),
        )
        self.assertEqual("glm-5.2", ciel_runtime.resolve_requested_model("zai", pcfg, "glm-5.2[1m]"))
        self.assertEqual("glm-5-turbo", ciel_runtime.resolve_requested_model("zai", pcfg, "glm-5-turbo[1m]"))

    def test_compatibility_test_uses_zai_api_model_without_context_suffix(self):
        cfg = self.zai_cfg(api_key="sk-zai-test")
        response = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "glm-5.2",
            "content": [{"type": "text", "text": "OK"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        args = type("Args", (), {"mode": "quick", "timeout": 10})()

        with (
            mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
            mock.patch.object(ciel_runtime, "save_config"),
            mock.patch.object(ciel_runtime, "post_json", return_value=response) as post_json,
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                ciel_runtime._cmd_test(args)

        request_body = post_json.call_args.args[1]
        self.assertEqual("glm-5.2", request_body["model"])
        self.assertIn("Model: glm-5.2[1m]", stdout.getvalue())
        self.assertIn("API model: glm-5.2", stdout.getvalue())

    def test_zai_managed_mcp_config_contains_official_servers(self):
        pcfg = self.zai_cfg(api_key="sk-zai-test")["providers"]["zai"]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "zai-mcp.json"
            with (
                mock.patch.object(ciel_runtime, "CONFIG_DIR", root),
                mock.patch.object(ciel_runtime, "ZAI_MCP_CONFIG", path),
                mock.patch.object(ciel_runtime, "find_executable", side_effect=lambda name: f"/bin/{name}"),
            ):
                written = ciel_runtime.write_zai_mcp_config("zai", pcfg)

            self.assertEqual(path, written)
            data = json.loads(path.read_text(encoding="utf-8"))
            servers = data["mcpServers"]
            self.assertEqual("/bin/npx", servers["zai-mcp-server"]["command"])
            self.assertEqual(["-y", "@z_ai/mcp-server@latest"], servers["zai-mcp-server"]["args"])
            self.assertEqual("sk-zai-test", servers["zai-mcp-server"]["env"]["Z_AI_API_KEY"])
            self.assertEqual("ZAI", servers["zai-mcp-server"]["env"]["Z_AI_MODE"])
            self.assertEqual("https://api.z.ai/api/mcp/web_search_prime/mcp", servers["web-search-prime"]["url"])
            self.assertEqual("https://api.z.ai/api/mcp/web_reader/mcp", servers["web-reader"]["url"])
            self.assertEqual("https://api.z.ai/api/mcp/zread/mcp", servers["zread"]["url"])
            self.assertEqual("Bearer sk-zai-test", servers["web-search-prime"]["headers"]["Authorization"])

    def test_zai_managed_mcp_config_is_removed_for_other_providers(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "zai-mcp.json"
            path.write_text("{}", encoding="utf-8")
            with mock.patch.object(ciel_runtime, "ZAI_MCP_CONFIG", path):
                ciel_runtime.reset_zai_mcp_config_if_inactive("deepseek")
            self.assertFalse(path.exists())

    def test_zai_uses_managed_mcp_instead_of_generic_web_search_by_default(self):
        cfg = self.zai_cfg(api_key="sk-zai-test")
        self.assertFalse(ciel_runtime.should_attach_web_search("zai", cfg, None))
        self.assertTrue(ciel_runtime.should_attach_web_search("zai", cfg, True))

    def test_launch_requires_zai_api_key(self):
        with mock.patch.object(ciel_runtime, "base_url_status_line", return_value="Base URL: Z.AI configured"):
            errors = ciel_runtime.launch_readiness_errors(self.zai_cfg(api_key=""))
        self.assertTrue(any("Z.AI GLM requires" in err for err in errors))
        self.assertTrue(ciel_runtime.launch_blockers_require_api_key(errors))


if __name__ == "__main__":
    unittest.main()
