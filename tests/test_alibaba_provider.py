import copy
import unittest
from unittest import mock

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

    def test_token_plan_qwen38_defaults_match_live_catalog_and_model_limits(self):
        config = self.token_config()

        self.assertEqual("qwen3.8-max", config["current_model"])
        self.assertEqual(1_048_576, config["context_window"])
        self.assertEqual(1_048_576, config["max_model_len"])
        self.assertEqual(131_072, config["max_output_tokens"])
        self.assertEqual(900_000, config["auto_compact_window"])
        self.assertEqual("xhigh", config["effort_level"])
        self.assertTrue(config["explicit_cache"])
        self.assertEqual(4, config["explicit_cache_markers"])
        self.assertEqual("alims-intl", ciel_runtime.PROVIDER_ALIASES["dashscope-intl"])
        self.assertEqual("alitoken", ciel_runtime.PROVIDER_ALIASES["alibaba-token-plan"])
        self.assertEqual(
            "alitoken-individual",
            ciel_runtime.PROVIDER_ALIASES["alibaba-token-individual"],
        )
        self.assertEqual("ap-southeast-1", config["region"])

    def test_token_plan_uses_responses_for_codex_and_anthropic_for_claude(self):
        config = self.token_config()

        self.assertEqual(
            "openai_responses",
            ciel_runtime.select_provider_protocol(
                "alitoken", config, "openai_responses", "qwen3.8-max"
            ),
        )
        self.assertEqual(
            "anthropic_messages",
            ciel_runtime.select_provider_protocol(
                "alitoken", config, "anthropic_messages", "qwen3.8-max"
            ),
        )
        self.assertEqual(
            "https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic",
            ciel_runtime.native_anthropic_base_url("alitoken", config),
        )

    def test_individual_token_plan_has_separate_endpoint_and_same_harness_catalog(self):
        config = copy.deepcopy(
            ciel_runtime.DEFAULT_CONFIG["providers"]["alitoken-individual"]
        )

        self.assertEqual(
            "https://coding.dashscope.aliyuncs.com/v1", config["base_url"]
        )
        self.assertEqual("qwen3.8-max", config["current_model"])
        self.assertEqual(
            "https://coding.dashscope.aliyuncs.com/apps/anthropic",
            ciel_runtime.native_anthropic_base_url("alitoken-individual", config),
        )
        self.assertEqual(
            "openai_responses",
            ciel_runtime.select_provider_protocol(
                "alitoken-individual",
                config,
                "openai_responses",
                "qwen3.8-max",
            ),
        )

    def test_responses_preserves_and_normalizes_qwen_builtin_tools(self):
        config = self.token_config()
        body = {
            "model": "qwen3.8-max",
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
        self.assertNotIn("parallel_tool_calls", normalized)
        self.assertEqual("web_search_preview", body["tools"][0]["type"])

    def test_claude_web_tools_become_qwen_search_without_losing_local_tools(self):
        config = self.token_config()
        body = {
            "model": "qwen3.8-max",
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
        self.assertEqual("max", normalized["search_options"]["search_strategy"])
        self.assertEqual("Read", normalized["tools"][0]["function"]["name"])
        self.assertNotIn("parallel_tool_calls", normalized)
        self.assertEqual("auto", normalized["tool_choice"])
        content = normalized["messages"][0]["content"]
        self.assertEqual({"type": "ephemeral"}, content[0]["cache_control"])
        self.assertEqual("stable instructions", body["messages"][0]["content"])

    def test_thinking_request_removes_forced_tool_choice_but_keeps_tools(self):
        config = self.token_config()
        body = {
            "model": "qwen3.8-max",
            "thinking": {"type": "enabled", "budget_tokens": 4096},
            "tools": [{"name": "compat_echo", "input_schema": {"type": "object"}}],
            "tool_choice": {"type": "tool", "name": "compat_echo"},
        }

        normalized = ciel_runtime.normalize_tool_choice_for_provider(
            "alitoken", config, body
        )

        self.assertNotIn("tool_choice", normalized)
        self.assertEqual(body["tools"], normalized["tools"])

    def test_non_thinking_request_preserves_forced_tool_choice(self):
        config = self.token_config()
        config["effort_level"] = "none"
        body = {
            "model": "qwen3.8-max",
            "tools": [{"name": "compat_echo", "input_schema": {"type": "object"}}],
            "tool_choice": {"type": "tool", "name": "compat_echo"},
        }

        normalized = ciel_runtime.normalize_tool_choice_for_provider(
            "alitoken", config, body
        )

        self.assertEqual(body, normalized)

    def test_explicitly_disabled_thinking_overrides_provider_effort(self):
        config = self.token_config()
        body = {
            "model": "qwen3.8-max",
            "thinking": {"type": "disabled"},
            "tools": [{"name": "compat_echo", "input_schema": {"type": "object"}}],
            "tool_choice": {"type": "tool", "name": "compat_echo"},
        }

        normalized = ciel_runtime.normalize_tool_choice_for_provider(
            "alitoken", config, body
        )

        self.assertEqual(body, normalized)

    def test_provider_default_thinking_removes_forced_tool_choice(self):
        config = self.token_config()
        body = ciel_runtime.compatibility_tool_request("qwen3.8-max")
        normalized = ciel_runtime.normalize_tool_choice_for_provider(
            "alitoken", config, body
        )

        self.assertNotIn("tool_choice", normalized)
        self.assertTrue(normalized["tools"])

    def test_claude_projection_applies_alibaba_policy_after_wire_conversion(self):
        config = self.token_config()
        request = ciel_runtime.openai_compatible_chat_request(
            "alitoken",
            "qwen3.8-max",
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
        self.assertEqual("max", request["search_options"]["search_strategy"])
        self.assertEqual("Read", request["tools"][0]["function"]["name"])
        self.assertEqual("xhigh", request["reasoning_effort"])
        self.assertEqual(4096, request["max_completion_tokens"])
        self.assertNotIn("max_tokens", request)
        self.assertEqual(
            {"type": "ephemeral"},
            request["messages"][0]["content"][0]["cache_control"],
        )

    def test_qwen38_reasoning_effort_matches_documented_values(self):
        config = self.token_config()
        expected = {
            "max": "xhigh",
            "high": "xhigh",
            "xhigh": "xhigh",
            "medium": "medium",
            "minimal": "low",
            "low": "low",
            "none": "none",
        }

        for supplied, normalized_effort in expected.items():
            with self.subTest(supplied=supplied):
                body = {
                    "model": "qwen3.8-max",
                    "input": [{"role": "user", "content": "test"}],
                    "reasoning": {"effort": supplied},
                    "thinking_budget": 4096,
                }
                normalized = ciel_runtime.apply_provider_adapter_request_policy(
                    "alitoken", config, body
                )
                self.assertEqual(
                    normalized_effort, normalized["reasoning"]["effort"]
                )
                self.assertNotIn("thinking_budget", normalized)

    def test_qwen38_disable_thinking_maps_to_none_reasoning(self):
        config = self.token_config()
        body = {
            "model": "qwen3.8-max",
            "input": [{"role": "user", "content": "test"}],
            "enable_thinking": False,
        }

        normalized = ciel_runtime.apply_provider_adapter_request_policy(
            "alitoken", config, body
        )

        self.assertEqual({"effort": "none"}, normalized["reasoning"])
        self.assertNotIn("enable_thinking", normalized)

    def test_qwen38_chat_preserves_explicit_provider_parameters(self):
        config = self.token_config()
        body = {
            "model": "qwen3.8-max",
            "messages": [{"role": "user", "content": "test"}],
            "enable_thinking": True,
            "thinking_budget": 8192,
            "preserve_thinking": False,
            "temperature": 0.4,
            "top_p": 0.8,
            "top_k": 20,
            "parallel_tool_calls": False,
            "tool_stream": True,
            "enable_search": False,
        }

        normalized = ciel_runtime.apply_provider_adapter_request_policy(
            "alitoken", config, body
        )

        for key in (
            "enable_thinking",
            "thinking_budget",
            "preserve_thinking",
            "temperature",
            "top_p",
            "top_k",
            "parallel_tool_calls",
            "tool_stream",
            "enable_search",
        ):
            self.assertEqual(body[key], normalized[key])
        self.assertNotIn("reasoning_effort", normalized)

    def test_qwen38_responses_uses_provider_defaults_when_unspecified(self):
        normalized = ciel_runtime.apply_provider_adapter_request_policy(
            "alitoken",
            self.token_config(),
            {
                "model": "qwen3.8-max",
                "input": [{"role": "user", "content": "test"}],
            },
        )

        self.assertNotIn("reasoning", normalized)
        self.assertNotIn("parallel_tool_calls", normalized)

    def test_qwen38_profile_exposes_official_codex_catalog_metadata(self):
        config = self.token_config()

        ciel_runtime.apply_provider_model_profile("alitoken", config)
        catalog = config["codex_model_catalog"]

        self.assertEqual(983_616, catalog["context_window"])
        self.assertEqual(95, catalog["effective_context_window_percent"])
        self.assertFalse(catalog["supports_parallel_tool_calls"])
        self.assertEqual(
            ["low", "medium", "xhigh"],
            [item["effort"] for item in catalog["supported_reasoning_levels"]],
        )

    def test_qwen38_preserves_reasoning_content_for_multi_turn_cache(self):
        config = self.token_config()

        self.assertTrue(
            ciel_runtime.openai_chat_reasoning_passback_enabled(
                "alitoken", "qwen3.8-max", config
            )
        )
        messages = ciel_runtime.anthropic_messages_to_openai(
            {
                "messages": [
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "thinking",
                                "thinking": "retained chain of thought",
                                "signature": "provider-signature",
                            },
                            {"type": "text", "text": "visible answer"},
                        ],
                    }
                ]
            },
            reasoning_passback=True,
        )
        assistant = next(
            message for message in messages if message.get("role") == "assistant"
        )
        self.assertEqual("retained chain of thought", assistant["reasoning_content"])
        self.assertEqual("visible answer", assistant["content"])

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
                "qwen3.8-max",
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
                "alitoken-individual": {"custom_models": ["private-individual-model"]},
            },
        }

        ciel_runtime.apply_config_migrations(cfg)

        self.assertIn("private-coding-model", cfg["providers"]["alicode"]["custom_models"])
        self.assertIn("qwen3.7-plus", cfg["providers"]["alicode"]["custom_models"])
        self.assertIn("qwen3-coder-plus", cfg["providers"]["alicode-intl"]["custom_models"])
        self.assertIn("legacy-custom", cfg["providers"]["alims-intl"]["custom_models"])
        self.assertIn("qwen3.7-max", cfg["providers"]["alims-intl"]["custom_models"])
        self.assertIn("private-token-model", cfg["providers"]["alitoken"]["custom_models"])
        self.assertIn("qwen3.8-max", cfg["providers"]["alitoken"]["custom_models"])
        self.assertEqual("ap-southeast-1", cfg["providers"]["alitoken"]["region"])
        self.assertTrue(cfg["migrations"]["alibaba_provider_catalogs_20260806"])
        self.assertTrue(cfg["migrations"]["alibaba_token_plan_singapore_20260806"])
        self.assertTrue(cfg["migrations"]["alibaba_native_anthropic_routes_20260806"])
        self.assertTrue(cfg["migrations"]["alibaba_token_plan_individual_20260806"])
        for provider in (
            "alicode", "alicode-intl", "alims-intl", "alitoken",
            "alitoken-individual",
        ):
            self.assertTrue(cfg["providers"][provider]["native_compat"])

    def test_preview_model_id_is_preserved_for_models_endpoint_authority(self):
        cfg = {
            "migrations": {},
            "providers": {
                "alitoken": self.token_config(
                    current_model="qwen3.8-max-preview",
                    opus_model="qwen3.8-max-preview",
                    custom_models=["qwen3.8-max-preview", "private-model"],
                )
            },
        }

        ciel_runtime.apply_config_migrations(cfg)
        provider = cfg["providers"]["alitoken"]

        self.assertEqual("qwen3.8-max-preview", provider["current_model"])
        self.assertEqual("qwen3.8-max-preview", provider["opus_model"])
        self.assertEqual("qwen3.8-max-preview", provider["custom_models"][0])
        self.assertIn("private-model", provider["custom_models"])
        self.assertEqual(
            "qwen3.8-max-preview",
            ciel_runtime.upstream_api_model_id(
                "alitoken", "qwen3.8-max-preview"
            ),
        )

    def test_alibaba_models_endpoint_catalog_is_authoritative(self):
        config = self.token_config()
        adapter = ciel_runtime.PROVIDER_ADAPTERS.create("alitoken")

        policy = adapter.model_catalog_policy(
            ciel_runtime.provider_contract_config("alitoken", config)
        )

        self.assertTrue(policy.authoritative_upstream_catalog)

    def test_alibaba_models_endpoint_preserves_exact_remote_model_ids(self):
        config = self.token_config(
            custom_models=["qwen3.8-max"],
            current_model="qwen3.8-max",
        )
        with (
            mock.patch.object(ciel_runtime, "read_model_list_cache", return_value=None),
            mock.patch.object(ciel_runtime, "write_model_list_cache"),
            mock.patch.object(ciel_runtime, "write_model_registry"),
            mock.patch.object(
                ciel_runtime,
                "http_json",
                return_value={"data": [{"id": "qwen3.8-max-preview"}]},
            ),
        ):
            models = ciel_runtime.upstream_model_ids(
                "alitoken", config, force_refresh=True
            )

        self.assertEqual(["qwen3.8-max-preview"], models)

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
