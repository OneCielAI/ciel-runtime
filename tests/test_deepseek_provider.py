import copy
import unittest
from contextlib import ExitStack
from unittest import mock

import ciel_runtime


class DeepSeekProviderTests(unittest.TestCase):
    def deepseek_cfg(self, **overrides):
        pcfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["deepseek"])
        pcfg.update(overrides)
        return {
            "current_provider": "deepseek",
            "providers": {
                "deepseek": pcfg,
            },
        }

    def test_provider_is_registered(self):
        self.assertEqual("deepseek", ciel_runtime.PROVIDER_ALIASES["deepseek"])
        self.assertEqual("deepseek", ciel_runtime.PROVIDER_ALIASES["deepseek.com"])
        self.assertEqual("DeepSeek.com", ciel_runtime.PROVIDER_LABELS["deepseek"])
        self.assertEqual("https://api.deepseek.com/anthropic", ciel_runtime.default_base_url("deepseek"))

    def test_default_config_matches_deepseek_claude_code_docs(self):
        pcfg = ciel_runtime.DEFAULT_CONFIG["providers"]["deepseek"]
        self.assertEqual("https://api.deepseek.com/anthropic", pcfg["base_url"])
        self.assertEqual("deepseek-v4-pro[1m]", pcfg["current_model"])
        self.assertEqual("deepseek-v4-flash", pcfg["haiku_model"])
        self.assertEqual("deepseek-v4-flash", pcfg["subagent_model"])
        self.assertEqual("max", pcfg["effort_level"])
        self.assertTrue(pcfg["native_compat"])

    def test_env_vars_route_deepseek_through_ciel_runtime_router(self):
        cfg = self.deepseek_cfg(api_key="sk-deepseek-test")
        pcfg = cfg["providers"]["deepseek"]
        env = ciel_runtime.env_vars(cfg)
        self.assertEqual("deepseek", env["CIEL_RUNTIME_PROVIDER"])
        self.assertEqual(ciel_runtime.ROUTER_BASE, env["ANTHROPIC_BASE_URL"])
        self.assertEqual("sk-deepseek-test", env["ANTHROPIC_AUTH_TOKEN"])
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        expected_model = ciel_runtime.claude_code_context_model_alias("deepseek", pcfg, ciel_runtime.current_alias(cfg))
        self.assertEqual(expected_model, env["ANTHROPIC_MODEL"])
        self.assertEqual(expected_model, env["ANTHROPIC_DEFAULT_OPUS_MODEL"])
        self.assertEqual(expected_model, env["ANTHROPIC_DEFAULT_SONNET_MODEL"])
        self.assertEqual(expected_model, env["ANTHROPIC_DEFAULT_HAIKU_MODEL"])
        self.assertEqual(expected_model, env["CLAUDE_CODE_SUBAGENT_MODEL"])
        self.assertEqual("8192", env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"])

    def test_long_context_deepseek_alias_marks_claude_code_as_one_million_context(self):
        cfg = self.deepseek_cfg(
            api_key="sk-deepseek-test",
            current_model="deepseek-v4-flash",
            context_window=524288,
        )

        env = ciel_runtime.env_vars(cfg)

        self.assertEqual("ciel-runtime-deepseek-deepseek-v4-flash[1m]", env["ANTHROPIC_MODEL"])
        self.assertEqual("524288", env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"])

    def test_launch_removes_inherited_anthropic_api_key_for_deepseek(self):
        cfg = self.deepseek_cfg(api_key="sk-deepseek-test")
        with ExitStack() as stack:
            stack.enter_context(mock.patch.dict(
                "os.environ",
                {"PATH": "/usr/local/bin", "ANTHROPIC_API_KEY": "sk-ant-old"},
                clear=True,
            ))
            stack.enter_context(mock.patch.object(ciel_runtime, "run_prelaunch_menu", return_value=0))
            stack.enter_context(mock.patch.object(ciel_runtime, "load_config", return_value=cfg))
            stack.enter_context(mock.patch.object(ciel_runtime, "launch_readiness_errors", return_value=[]))
            stack.enter_context(mock.patch.object(ciel_runtime, "start_router_if_needed"))
            stack.enter_context(mock.patch.object(ciel_runtime, "cleanup_managed_services_for_provider"))
            stack.enter_context(mock.patch.object(ciel_runtime, "find_executable", return_value="/usr/local/bin/claude"))
            stack.enter_context(mock.patch.object(ciel_runtime, "run_claude_update_check"))
            stack.enter_context(mock.patch.object(ciel_runtime, "claude_supports_permission_mode_arg", return_value=True))
            stack.enter_context(mock.patch.object(ciel_runtime, "install_ciel_runtime_slash_commands"))
            stack.enter_context(mock.patch.object(ciel_runtime, "install_tool_guard_hooks"))
            stack.enter_context(mock.patch.object(ciel_runtime, "install_ciel_runtime_statusline"))
            stack.enter_context(mock.patch.object(ciel_runtime, "should_attach_web_search", return_value=False))
            stack.enter_context(mock.patch.object(ciel_runtime, "should_append_compat_prompt", return_value=False))
            stack.enter_context(mock.patch.object(ciel_runtime, "external_mcp_channel_server_names_from_configs", return_value=[]))
            stack.enter_context(mock.patch.object(ciel_runtime, "prepare_channel_llm_delivery_for_launch"))
            stack.enter_context(mock.patch.object(ciel_runtime, "ensure_channel_probe_cache_for_launch", return_value=False))
            stack.enter_context(mock.patch.object(ciel_runtime, "cached_channel_capable_server_names", return_value=["ciel-runtime-router"]))
            stack.enter_context(mock.patch.object(ciel_runtime, "cached_channel_source_paths_for_specs", return_value=[]))
            stack.enter_context(mock.patch.object(ciel_runtime, "read_channel_probe_cache", return_value={"probed_at": 1700000000}))
            stack.enter_context(mock.patch.object(ciel_runtime, "write_channel_mcp_config", return_value="channel-mcp.json"))
            stack.enter_context(mock.patch.object(ciel_runtime, "write_mcp_proxy_config", return_value=None))
            stack.enter_context(mock.patch.object(ciel_runtime, "auto_start_sse_channels_from_mcp_configs", return_value=[]))
            proxy = stack.enter_context(mock.patch.object(ciel_runtime, "subprocess_call_with_channel_wake_proxy", return_value=0))
            call = stack.enter_context(mock.patch.object(ciel_runtime.subprocess, "call", return_value=0))
            rc = ciel_runtime.launch_claude([], update_check=False, self_update_check=False)

        self.assertEqual(0, rc)
        proxy.assert_called_once()
        launch_cmd = proxy.call_args.args[0]
        self.assertIn("--dangerously-skip-permissions", launch_cmd)
        mode_idx = launch_cmd.index("--permission-mode")
        self.assertEqual("bypassPermissions", launch_cmd[mode_idx + 1])
        disallowed_idx = launch_cmd.index("--disallowedTools")
        self.assertEqual("WebSearch,WebFetch", launch_cmd[disallowed_idx + 1])
        self.assertFalse(proxy.call_args.kwargs.get("inject_web_chat_only", False))
        call.assert_not_called()
        launch_env = proxy.call_args.args[1]
        self.assertEqual(ciel_runtime.ROUTER_BASE, launch_env["ANTHROPIC_BASE_URL"])
        self.assertEqual("sk-deepseek-test", launch_env["ANTHROPIC_AUTH_TOKEN"])
        self.assertNotIn("ANTHROPIC_API_KEY", launch_env)

    def test_deepseek_base_status_does_not_probe_model_list(self):
        cfg = self.deepseek_cfg(api_key="sk-deepseek-test")
        pcfg = cfg["providers"]["deepseek"]
        with mock.patch("urllib.request.urlopen") as urlopen:
            status = ciel_runtime.base_url_status_line("deepseek", pcfg)
        urlopen.assert_not_called()
        self.assertIn("DeepSeek Anthropic API configured", status)

    def test_launch_requires_deepseek_api_key(self):
        errors = ciel_runtime.launch_readiness_errors(self.deepseek_cfg(api_key=""))
        self.assertTrue(any("DeepSeek.com requires" in err for err in errors))
        self.assertTrue(ciel_runtime.launch_blockers_require_api_key(errors))

    def test_base_url_blocker_does_not_open_api_key_setup(self):
        errors = ["Launch blocked: Base URL unreachable."]
        self.assertFalse(ciel_runtime.launch_blockers_require_api_key(errors))

    def test_model_list_uses_documented_deepseek_models_without_network(self):
        pcfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["deepseek"])
        with (
            mock.patch.object(ciel_runtime, "read_model_list_cache", return_value=None),
            mock.patch.object(ciel_runtime, "write_model_list_cache") as write_cache,
            mock.patch.object(ciel_runtime, "http_json") as http_json,
        ):
            models = ciel_runtime.upstream_model_ids("deepseek", pcfg)
        http_json.assert_not_called()
        self.assertIn("deepseek-v4-pro[1m]", models)
        self.assertIn("deepseek-v4-flash", models)
        write_cache.assert_called_once()

    def test_provider_headers_include_deepseek_api_key(self):
        headers = ciel_runtime.provider_headers("deepseek", self.deepseek_cfg(api_key="sk-deepseek-test")["providers"]["deepseek"])
        self.assertEqual("Bearer sk-deepseek-test", headers["authorization"])
        self.assertEqual("sk-deepseek-test", headers["x-api-key"])
        self.assertEqual("2023-06-01", headers["anthropic-version"])
        self.assertEqual("claude-cli", headers["user-agent"])

    def test_deepseek_v4_removes_forced_tool_choice(self):
        pcfg = self.deepseek_cfg(current_model="deepseek-v4-pro[1m]")["providers"]["deepseek"]
        body = ciel_runtime.compatibility_tool_request("ciel-runtime-deepseek-deepseek-v4-pro[1m]")

        out = ciel_runtime.normalize_tool_choice_for_provider("deepseek", pcfg, body)

        self.assertIn("tool_choice", body)
        self.assertNotIn("tool_choice", out)
        self.assertIn("tools", out)

    def test_deepseek_non_v4_keeps_forced_tool_choice(self):
        pcfg = self.deepseek_cfg(current_model="deepseek-chat")["providers"]["deepseek"]
        body = ciel_runtime.compatibility_tool_request("deepseek-chat")

        out = ciel_runtime.normalize_tool_choice_for_provider("deepseek", pcfg, body)

        self.assertIs(out, body)
        self.assertIn("tool_choice", out)

    def test_deepseek_tool_choice_override_is_respected(self):
        pcfg = self.deepseek_cfg(current_model="deepseek-v4-pro[1m]", supports_tool_choice=True)["providers"]["deepseek"]
        body = ciel_runtime.compatibility_tool_request("deepseek-v4-pro[1m]")

        out = ciel_runtime.normalize_tool_choice_for_provider("deepseek", pcfg, body)

        self.assertIs(out, body)
        self.assertIn("tool_choice", out)


if __name__ == "__main__":
    unittest.main()
