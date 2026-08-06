import copy
import unittest

import ciel_runtime


class AlibabaProviderTests(unittest.TestCase):
    def config(self, **overrides):
        config = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["alims-intl"])
        config.update(overrides)
        return config

    def token_config(self, **overrides):
        config = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["alitoken"])
        config.update(overrides)
        return config

    def test_token_plan_qwen38_preview_defaults_match_documented_model_limits(self):
        config = self.token_config()

        self.assertEqual("qwen3.8-max-preview", config["current_model"])
        self.assertEqual(1_048_576, config["context_window"])
        self.assertEqual(1_048_576, config["max_model_len"])
        self.assertEqual(131_072, config["max_output_tokens"])
        self.assertEqual(900_000, config["auto_compact_window"])
        self.assertEqual("xhigh", config["effort_level"])
        self.assertTrue(config["explicit_cache"])
        self.assertEqual(4, config["explicit_cache_markers"])
        self.assertEqual("alims-intl", ciel_runtime.PROVIDER_ALIASES["dashscope-intl"])
        self.assertEqual("alitoken", ciel_runtime.PROVIDER_ALIASES["alibaba-token-plan"])
        self.assertEqual("ap-southeast-1", config["region"])

    def test_token_plan_uses_responses_for_codex_and_anthropic_for_claude(self):
        config = self.token_config()

        self.assertEqual(
            "openai_responses",
            ciel_runtime.select_provider_protocol(
                "alitoken", config, "openai_responses", "qwen3.8-max-preview"
            ),
        )
        self.assertEqual(
            "anthropic_messages",
            ciel_runtime.select_provider_protocol(
                "alitoken", config, "anthropic_messages", "qwen3.8-max-preview"
            ),
        )
        self.assertEqual(
            "https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic",
            ciel_runtime.native_anthropic_base_url("alitoken", config),
        )

    def test_responses_preserves_and_normalizes_qwen_builtin_tools(self):
        config = self.token_config()
        body = {
            "model": "qwen3.8-max-preview",
            "input": [{"role": "user", "content": "research this"}],
            "enable_thinking": True,
            "reasoning": {"effort": "ultra"},
            "tools": [
                {"type": "web_search_preview"},
                {"type": "t2i_search"},
                {"type": "i2i_search"},
                {"type": "function", "name": "local_tool", "parameters": {}},
            ],
        }

        normalized = ciel_runtime.apply_provider_adapter_request_policy(
            "alitoken", config, body
        )

        self.assertEqual("xhigh", normalized["reasoning"]["effort"])
        self.assertNotIn("enable_thinking", normalized)
        self.assertEqual(
            ["web_search", "web_search_image", "image_search", "function"],
            [tool["type"] for tool in normalized["tools"]],
        )
        self.assertTrue(normalized["parallel_tool_calls"])
        self.assertEqual("web_search_preview", body["tools"][0]["type"])

    def test_claude_web_tools_become_qwen_search_without_losing_local_tools(self):
        config = self.token_config()
        body = {
            "model": "qwen3.8-max-preview",
            "messages": [{"role": "system", "content": "stable instructions"}],
            "tools": [
                {"type": "function", "function": {"name": "WebSearch"}},
                {"type": "function", "function": {"name": "WebFetch"}},
                {"type": "function", "function": {"name": "Read"}},
            ],
            "tool_choice": {"type": "function", "function": {"name": "WebSearch"}},
        }

        normalized = ciel_runtime.apply_provider_adapter_request_policy(
            "alitoken", config, body
        )

        self.assertTrue(normalized["enable_search"])
        self.assertEqual("agent_max", normalized["search_options"]["search_strategy"])
        self.assertEqual("Read", normalized["tools"][0]["function"]["name"])
        self.assertTrue(normalized["parallel_tool_calls"])
        self.assertEqual("auto", normalized["tool_choice"])
        content = normalized["messages"][0]["content"]
        self.assertEqual({"type": "ephemeral"}, content[0]["cache_control"])
        self.assertEqual("stable instructions", body["messages"][0]["content"])

    def test_claude_projection_applies_alibaba_policy_after_wire_conversion(self):
        config = self.token_config()
        request = ciel_runtime.openai_compatible_chat_request(
            "alitoken",
            "qwen3.8-max-preview",
            {
                "model": "qwen3.8-max",
                "system": "stable instructions",
                "messages": [{"role": "user", "content": "search"}],
                "tools": [
                    {
                        "name": "WebSearch",
                        "description": "Search the web",
                        "input_schema": {"type": "object", "properties": {}},
                    },
                    {
                        "name": "Read",
                        "description": "Read a file",
                        "input_schema": {"type": "object", "properties": {}},
                    },
                ],
                "max_tokens": 4096,
            },
            config,
            False,
        )

        self.assertTrue(request["enable_search"])
        self.assertEqual("agent", request["search_options"]["search_strategy"])
        self.assertEqual("Read", request["tools"][0]["function"]["name"])
        self.assertEqual(
            {"type": "ephemeral"},
            request["messages"][0]["content"][0]["cache_control"],
        )

    def test_responses_models_expose_claude_server_web_tools(self):
        config = self.token_config()

        blocked = ciel_runtime.resolve_blocked_tools("alitoken", config)
        self.assertNotIn("WebSearch", blocked)
        self.assertNotIn("WebFetch", blocked)
        self.assertFalse(
            ciel_runtime.should_disallow_claude_server_side_web_tools(
                "alitoken", config, False
            )
        )

        qwen37_plus = self.config(current_model="qwen3.7-plus")
        self.assertNotIn("WebSearch", ciel_runtime.resolve_blocked_tools("alims-intl", qwen37_plus))
        self.assertFalse(
            ciel_runtime.should_disallow_claude_server_side_web_tools(
                "alims-intl", qwen37_plus, False
            )
        )

        responses_only = self.config(current_model="qwen3.7-max")
        self.assertIn(
            "WebSearch", ciel_runtime.resolve_blocked_tools("alims-intl", responses_only)
        )

        third_party = self.config(current_model="deepseek-v4-pro")
        self.assertIn("WebSearch", ciel_runtime.resolve_blocked_tools("alims-intl", third_party))
        self.assertTrue(
            ciel_runtime.should_disallow_claude_server_side_web_tools(
                "alims-intl", third_party, False
            )
        )

    def test_model_studio_uses_chat_for_models_without_responses_support(self):
        config = self.config(current_model="deepseek-v4-pro")

        self.assertEqual(
            "openai_chat",
            ciel_runtime.select_provider_protocol(
                "alims-intl", config, "openai_responses", "deepseek-v4-pro"
            ),
        )

    def test_routed_alias_still_selects_responses_for_supported_qwen(self):
        config = self.config(current_model="qwen3.7-max")

        self.assertEqual(
            "openai_responses",
            ciel_runtime.select_provider_protocol(
                "alims-intl",
                config,
                "openai_responses",
                "ciel-runtime-alims-intl-qwen3.7-max",
            ),
        )

    def test_all_alibaba_catalogs_include_current_provider_models(self):
        expected_coding = {
            "qwen3.7-plus",
            "qwen3.6-plus",
            "MiniMax-M2.5",
            "qwen3-max-2026-01-23",
            "qwen3-coder-plus",
            "glm-4.7",
        }
        for provider in ("alicode", "alicode-intl"):
            config = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"][provider])
            models = set(ciel_runtime.cached_or_configured_model_ids(provider, config))
            self.assertTrue(expected_coding.issubset(models), provider)

        cfg = {
            "migrations": {},
            "providers": {"alims-intl": self.config(custom_models=["legacy-custom"])},
        }
        ciel_runtime.apply_config_migrations(cfg)
        config = cfg["providers"]["alims-intl"]
        models = set(ciel_runtime.cached_or_configured_model_ids("alims-intl", config))
        self.assertTrue(
            {
                "qwen3.7-max",
                "qwen3.7-plus",
                "qwen3.6-flash",
                "deepseek-v4-pro",
                "kimi-k2.7-code",
                "legacy-custom",
            }.issubset(models)
        )
        token = self.token_config()
        token_models = set(
            ciel_runtime.cached_or_configured_model_ids("alitoken", token)
        )
        self.assertTrue(
            {
                "qwen3.8-max-preview",
                "qwen3.7-max",
                "deepseek-v4-pro",
                "kimi-k2.7-code",
                "glm-5.2",
            }.issubset(token_models)
        )

    def test_migration_merges_new_models_without_removing_custom_models(self):
        cfg = {
            "migrations": {},
            "providers": {
                "alicode": {"custom_models": ["private-coding-model"]},
                "alicode-intl": {"custom_models": ["qwen3.5-plus"]},
                "alims-intl": {"custom_models": ["legacy-custom"]},
                "alitoken": {"custom_models": ["private-token-model"]},
            },
        }

        ciel_runtime.apply_config_migrations(cfg)

        self.assertIn("private-coding-model", cfg["providers"]["alicode"]["custom_models"])
        self.assertIn("qwen3.7-plus", cfg["providers"]["alicode"]["custom_models"])
        self.assertIn("qwen3-coder-plus", cfg["providers"]["alicode-intl"]["custom_models"])
        self.assertIn("legacy-custom", cfg["providers"]["alims-intl"]["custom_models"])
        self.assertIn("qwen3.7-max", cfg["providers"]["alims-intl"]["custom_models"])
        self.assertIn("private-token-model", cfg["providers"]["alitoken"]["custom_models"])
        self.assertIn("qwen3.8-max-preview", cfg["providers"]["alitoken"]["custom_models"])
        self.assertEqual("ap-southeast-1", cfg["providers"]["alitoken"]["region"])
        self.assertTrue(cfg["migrations"]["alibaba_provider_catalogs_20260806"])
        self.assertTrue(cfg["migrations"]["alibaba_token_plan_singapore_20260806"])
        self.assertTrue(cfg["migrations"]["alibaba_native_anthropic_routes_20260806"])
        for provider in ("alicode", "alicode-intl", "alims-intl", "alitoken"):
            self.assertTrue(cfg["providers"][provider]["native_compat"])

    def test_explicit_cache_uses_four_rolling_markers_within_recent_history(self):
        config = self.config()
        original_messages = [
            {"role": "system", "content": "stable system"},
            *[
                {"role": "user" if index % 2 == 0 else "assistant", "content": f"turn {index}"}
                for index in range(30)
            ],
        ]

        normalized = ciel_runtime.apply_provider_adapter_request_policy(
            "alims-intl",
            config,
            {"model": "qwen3.8-max", "messages": original_messages},
        )

        marked = [
            index
            for index, message in enumerate(normalized["messages"])
            if isinstance(message.get("content"), list)
            and any(
                isinstance(block, dict) and block.get("cache_control") == {"type": "ephemeral"}
                for block in message["content"]
            )
        ]
        self.assertEqual([0, 14, 22, 30], marked)
        self.assertEqual("stable system", original_messages[0]["content"])

    def test_explicit_cache_marker_limit_is_configurable_and_bounded(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"turn {index}",
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            }
            for index in range(12)
        ]

        normalized = ciel_runtime.apply_provider_adapter_request_policy(
            "alims-intl",
            self.config(explicit_cache_markers=2),
            {"model": "qwen3.8-max", "messages": messages},
        )

        marked = [
            message
            for message in normalized["messages"]
            if any(
                isinstance(block, dict) and "cache_control" in block
                for block in message["content"]
            )
        ]
        self.assertEqual(2, len(marked))


if __name__ == "__main__":
    unittest.main()
