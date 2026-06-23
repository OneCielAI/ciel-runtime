import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import claude_any


class ZaiProviderTests(unittest.TestCase):
    def zai_cfg(self, **overrides):
        pcfg = copy.deepcopy(claude_any.DEFAULT_CONFIG["providers"]["zai"])
        pcfg.update(overrides)
        return {
            "current_provider": "zai",
            "providers": {
                "zai": pcfg,
            },
        }

    def test_provider_is_registered(self):
        self.assertEqual("zai", claude_any.PROVIDER_ALIASES["z.ai"])
        self.assertEqual("zai", claude_any.PROVIDER_ALIASES["zhipu"])
        self.assertEqual("zai", claude_any.PROVIDER_ALIASES["glm"])
        self.assertEqual("Z.AI GLM", claude_any.PROVIDER_LABELS["zai"])
        self.assertEqual(claude_any.ZAI_ANTHROPIC_BASE_URL, claude_any.default_base_url("zai"))

    def test_default_config_matches_zai_claude_code_docs(self):
        pcfg = claude_any.DEFAULT_CONFIG["providers"]["zai"]
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
        self.assertEqual("glm-5.2[1m]", claude_any.normalize_model_id("zai", "glm-5.2[1m]"))
        pcfg = self.zai_cfg(current_model="glm-5.2[1m]")["providers"]["zai"]

        self.assertEqual(1000000, claude_any.provider_model_context_capacity("zai", pcfg))

    def test_zai_turbo_suffix_does_not_claim_one_million_context(self):
        pcfg = self.zai_cfg(
            current_model="glm-5-turbo[1m]",
            context_window=524288,
            auto_compact_window=1000000,
            max_output_tokens=32768,
            context_reserve_tokens=16384,
        )["providers"]["zai"]

        self.assertEqual(200000, claude_any.provider_model_context_capacity("zai", pcfg))
        self.assertEqual(200000, claude_any.context_limit_for_status("zai", pcfg))
        self.assertEqual(200000, claude_any.claude_code_auto_compact_window("zai", pcfg))
        self.assertEqual("long-context", claude_any.model_option_family("zai", pcfg))
        self.assertEqual("long-context-128k", claude_any.recommended_preset_id("zai", pcfg))

        messages = claude_any.cap_context_settings_to_model_capacity("zai", pcfg)
        messages.extend(claude_any.cap_output_settings_to_context_ratio("zai", pcfg))

        self.assertEqual(200000, pcfg["context_window"])
        self.assertEqual(6144, pcfg["max_output_tokens"])
        self.assertTrue(any("Context window capped" in line for line in messages))

    def test_provider_headers_include_zai_api_key(self):
        pcfg = self.zai_cfg(api_key="sk-zai-test")["providers"]["zai"]

        headers = claude_any.provider_headers("zai", pcfg)

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
            mock.patch.object(claude_any, "read_model_list_cache", return_value=None),
            mock.patch.object(claude_any, "http_json", return_value=response) as http_json,
            mock.patch.object(claude_any, "write_model_list_cache") as write_cache,
        ):
            models = claude_any.upstream_model_ids("zai", pcfg)

        self.assertIn("glm-5.2[1m]", models)
        self.assertIn("glm-4.7", models)
        self.assertTrue(http_json.call_args.args[0].endswith("/anthropic/v1/models"))
        write_cache.assert_called_once()
        metadata = write_cache.call_args.args[3]
        self.assertEqual(1000000, metadata["model_info"]["glm-5.2[1m]"]["max_model_len"])

    def test_model_list_falls_back_to_documented_zai_models_without_network(self):
        pcfg = self.zai_cfg(api_key="", custom_models=[])["providers"]["zai"]

        with (
            mock.patch.object(claude_any, "read_model_list_cache", return_value=None),
            mock.patch.object(claude_any, "http_json", side_effect=RuntimeError("network down")),
            mock.patch.object(claude_any, "write_model_list_cache") as write_cache,
        ):
            models = claude_any.upstream_model_ids("zai", pcfg)

        self.assertIn("glm-5.2[1m]", models)
        self.assertIn("glm-5-turbo", models)
        write_cache.assert_called_once()

    def test_env_vars_route_zai_through_claude_any_router_with_glm_defaults(self):
        cfg = self.zai_cfg(api_key="sk-zai-test")

        env = claude_any.env_vars(cfg)

        self.assertEqual("zai", env["CLAUDE_ANY_PROVIDER"])
        self.assertEqual(claude_any.ROUTER_BASE, env["ANTHROPIC_BASE_URL"])
        self.assertEqual("sk-zai-test", env["ANTHROPIC_AUTH_TOKEN"])
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertEqual("1000000", env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"])
        self.assertEqual("8192", env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"])
        self.assertEqual("claude-any-zai-glm-4.7", env["ANTHROPIC_DEFAULT_HAIKU_MODEL"])
        self.assertEqual("claude-any-zai-glm-5.2-1m[1m]", env["ANTHROPIC_DEFAULT_OPUS_MODEL"])
        self.assertEqual("claude-any-zai-glm-5.2-1m[1m]", env["ANTHROPIC_DEFAULT_SONNET_MODEL"])
        self.assertIn("thinking", env["ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES"])

    def test_resolve_requested_model_strips_zai_context_suffix_for_api(self):
        cfg = self.zai_cfg(api_key="sk-zai-test")
        pcfg = cfg["providers"]["zai"]

        self.assertEqual("glm-5.2[1m]", claude_any.current_upstream_model_id("zai", pcfg))
        self.assertEqual(
            "glm-5.2",
            claude_any.resolve_requested_model("zai", pcfg, "claude-any-zai-glm-5.2-1m[1m]"),
        )
        self.assertEqual("glm-5.2", claude_any.resolve_requested_model("zai", pcfg, "glm-5.2[1m]"))
        self.assertEqual("glm-5-turbo", claude_any.resolve_requested_model("zai", pcfg, "glm-5-turbo[1m]"))

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
            mock.patch.object(claude_any, "load_config", return_value=cfg),
            mock.patch.object(claude_any, "save_config"),
            mock.patch.object(claude_any, "post_json", return_value=response) as post_json,
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                claude_any._cmd_test(args)

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
                mock.patch.object(claude_any, "CONFIG_DIR", root),
                mock.patch.object(claude_any, "ZAI_MCP_CONFIG", path),
                mock.patch.object(claude_any, "find_executable", side_effect=lambda name: f"/bin/{name}"),
            ):
                written = claude_any.write_zai_mcp_config("zai", pcfg)

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
            with mock.patch.object(claude_any, "ZAI_MCP_CONFIG", path):
                claude_any.reset_zai_mcp_config_if_inactive("deepseek")
            self.assertFalse(path.exists())

    def test_zai_uses_managed_mcp_instead_of_generic_web_search_by_default(self):
        cfg = self.zai_cfg(api_key="sk-zai-test")
        self.assertFalse(claude_any.should_attach_web_search("zai", cfg, None))
        self.assertTrue(claude_any.should_attach_web_search("zai", cfg, True))

    def test_launch_requires_zai_api_key(self):
        with mock.patch.object(claude_any, "base_url_status_line", return_value="Base URL: Z.AI configured"):
            errors = claude_any.launch_readiness_errors(self.zai_cfg(api_key=""))
        self.assertTrue(any("Z.AI GLM requires" in err for err in errors))
        self.assertTrue(claude_any.launch_blockers_require_api_key(errors))


if __name__ == "__main__":
    unittest.main()
