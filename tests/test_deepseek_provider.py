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
        self.assertEqual(
            ["effort", "max_effort", "thinking", "interleaved_thinking"],
            pcfg["claude_code_supported_capabilities"],
        )

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
        self.assertEqual(
            "effort,max_effort,thinking,interleaved_thinking",
            env["ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES"],
        )

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
            stack.enter_context(mock.patch.object(ciel_runtime, "prepare_channel_llm_delivery_for_launch"))
            stack.enter_context(mock.patch.object(ciel_runtime, "write_channel_mcp_config", return_value="channel-mcp.json"))
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

    def test_thinking_request_omits_unsupported_sampling_and_normalizes_effort(self):
        pcfg = self.deepseek_cfg()["providers"]["deepseek"]
        body = {
            "model": "deepseek-v4-pro",
            "thinking": {"type": "enabled"},
            "output_config": {"effort": "xhigh"},
            "temperature": 0.8,
            "top_p": 0.9,
            "top_k": 20,
            "messages": [{"role": "user", "content": "work"}],
        }

        normalized = ciel_runtime.apply_provider_adapter_request_policy(
            "deepseek", pcfg, body
        )

        self.assertNotIn("temperature", normalized)
        self.assertNotIn("top_p", normalized)
        self.assertNotIn("top_k", normalized)
        self.assertEqual("max", normalized["output_config"]["effort"])

    def test_non_thinking_request_keeps_supported_sampling(self):
        pcfg = self.deepseek_cfg()["providers"]["deepseek"]
        body = {
            "model": "deepseek-v4-flash",
            "thinking": {"type": "disabled"},
            "temperature": 0.8,
            "top_p": 0.9,
            "messages": [{"role": "user", "content": "work"}],
        }

        normalized = ciel_runtime.apply_provider_adapter_request_policy(
            "deepseek", pcfg, body
        )

        self.assertEqual(0.8, normalized["temperature"])
        self.assertEqual(0.9, normalized["top_p"])

    def test_codex_reasoning_effort_maps_to_deepseek_anthropic_contract(self):
        pcfg = self.deepseek_cfg()["providers"]["deepseek"]
        body = ciel_runtime.openai_responses_to_anthropic_messages(
            {
                "model": "deepseek-v4-pro",
                "input": "work",
                "reasoning": {"effort": "xhigh"},
            },
            "deepseek-v4-pro",
        )

        normalized = ciel_runtime.apply_provider_adapter_request_policy(
            "deepseek", pcfg, body
        )

        self.assertEqual({"type": "enabled"}, normalized["thinking"])
        self.assertEqual({"effort": "max"}, normalized["output_config"])

    def test_codex_none_effort_disables_deepseek_thinking(self):
        pcfg = self.deepseek_cfg()["providers"]["deepseek"]
        body = ciel_runtime.openai_responses_to_anthropic_messages(
            {"input": "work", "reasoning": {"effort": "none"}},
            "deepseek-v4-flash",
        )

        normalized = ciel_runtime.apply_provider_adapter_request_policy(
            "deepseek", pcfg, body
        )

        self.assertEqual({"type": "disabled"}, normalized["thinking"])
        self.assertNotIn("output_config", normalized)

    def test_openai_tool_history_preserves_reasoning_content_for_v4(self):
        pcfg = self.deepseek_cfg(native_compat=False)["providers"]["deepseek"]
        body = {
            "model": "deepseek-v4-pro",
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "tool rationale"},
                        {"type": "tool_use", "id": "call_1", "name": "Read", "input": {"path": "a.py"}},
                    ],
                },
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "ok"}],
                },
            ],
        }

        request = ciel_runtime.openai_compatible_chat_request(
            "deepseek", "deepseek-v4-pro", body, pcfg
        )

        assistant = next(item for item in request["messages"] if item["role"] == "assistant")
        self.assertEqual("tool rationale", assistant["reasoning_content"])
        self.assertEqual("max", request["reasoning_effort"])
        self.assertNotIn("temperature", request)
        self.assertNotIn("top_p", request)

    def test_openai_response_projects_deepseek_kv_cache_usage(self):
        response = ciel_runtime.openai_chat_to_anthropic(
            {
                "choices": [{"message": {"content": "done"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 4,
                    "prompt_cache_hit_tokens": 100,
                    "prompt_cache_miss_tokens": 20,
                },
            },
            "deepseek-v4-pro",
        )

        self.assertEqual(20, response["usage"]["input_tokens"])
        self.assertEqual(100, response["usage"]["cache_read_input_tokens"])
        self.assertEqual(4, response["usage"]["output_tokens"])

    def test_openai_response_projects_cache_creation_without_a_cache_hit(self):
        response = ciel_runtime.openai_chat_to_anthropic(
            {
                "choices": [
                    {"finish_reason": "stop", "message": {"content": "ok"}}
                ],
                "usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 4,
                    "prompt_tokens_details": {"cache_write_tokens": 100},
                },
            },
            "deepseek-chat",
            source_body={"messages": [{"role": "user", "content": "hello"}]},
        )

        self.assertEqual(20, response["usage"]["input_tokens"])
        self.assertEqual(100, response["usage"]["cache_creation_input_tokens"])


if __name__ == "__main__":
    unittest.main()
