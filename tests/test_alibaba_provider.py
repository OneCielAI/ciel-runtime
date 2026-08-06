import copy
import unittest

import ciel_runtime


class AlibabaProviderTests(unittest.TestCase):
    def config(self, **overrides):
        config = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["alims-intl"])
        config.update(overrides)
        return config

    def test_qwen38_max_defaults_match_documented_model_limits(self):
        config = self.config()

        self.assertEqual("qwen3.8-max", config["current_model"])
        self.assertEqual(1_048_576, config["context_window"])
        self.assertEqual(1_048_576, config["max_model_len"])
        self.assertEqual(131_072, config["max_output_tokens"])
        self.assertEqual(900_000, config["auto_compact_window"])
        self.assertEqual("xhigh", config["effort_level"])
        self.assertTrue(config["explicit_cache"])
        self.assertEqual("alims-intl", ciel_runtime.PROVIDER_ALIASES["dashscope-intl"])

    def test_codex_uses_native_responses_and_claude_uses_chat(self):
        config = self.config()

        self.assertEqual(
            "openai_responses",
            ciel_runtime.select_provider_protocol(
                "alims-intl", config, "openai_responses", "qwen3.8-max"
            ),
        )
        self.assertEqual(
            "openai_chat",
            ciel_runtime.select_provider_protocol(
                "alims-intl", config, "anthropic_messages", "qwen3.8-max"
            ),
        )

    def test_responses_preserves_and_normalizes_qwen_builtin_tools(self):
        config = self.config()
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
            "alims-intl", config, body
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
        config = self.config()
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
            "alims-intl", config, body
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
        config = self.config()
        request = ciel_runtime.openai_compatible_chat_request(
            "alims-intl",
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
        self.assertEqual("agent", request["search_options"]["search_strategy"])
        self.assertEqual("Read", request["tools"][0]["function"]["name"])
        self.assertEqual(
            {"type": "ephemeral"},
            request["messages"][0]["content"][0]["cache_control"],
        )

    def test_only_qwen38_exposes_claude_server_web_tools(self):
        config = self.config()

        blocked = ciel_runtime.resolve_blocked_tools("alims-intl", config)
        self.assertNotIn("WebSearch", blocked)
        self.assertNotIn("WebFetch", blocked)
        self.assertFalse(
            ciel_runtime.should_disallow_claude_server_side_web_tools(
                "alims-intl", config, False
            )
        )

        legacy = self.config(current_model="qwen3.7-max")
        self.assertIn("WebSearch", ciel_runtime.resolve_blocked_tools("alims-intl", legacy))
        self.assertTrue(
            ciel_runtime.should_disallow_claude_server_side_web_tools(
                "alims-intl", legacy, False
            )
        )


if __name__ == "__main__":
    unittest.main()
