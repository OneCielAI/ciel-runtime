import copy
import unittest

import ciel_runtime


class ProviderWireNormalizationTests(unittest.TestCase):
    def test_parallel_responses_tool_turn_survives_provider_normalization(self):
        """Codex emits parallel calls/results as consecutive Responses items."""

        raw = {
            "model": "ciel-runtime-ollama-cloud-deepseek-v4-pro-0813",
            "input": [
                {
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": "inspect MCP state"}],
                },
                {
                    "type": "function_call",
                    "call_id": "call_resources",
                    "name": "list_mcp_resources",
                    "arguments": "{}",
                },
                {
                    "type": "function_call",
                    "call_id": "call_templates",
                    "name": "list_mcp_resource_templates",
                    "arguments": "{}",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_resources",
                    "output": "resources",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_templates",
                    "output": "templates",
                },
            ],
        }
        converted = ciel_runtime.openai_responses_to_anthropic_messages(raw, "fallback")
        pcfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["ollama-cloud"])

        normalized = ciel_runtime.normalize_anthropic_tool_turns_for_provider(
            "ollama-cloud", pcfg, converted
        )

        self.assertEqual(["assistant", "user"], [item["role"] for item in normalized["messages"]])
        self.assertEqual(
            ["call_resources", "call_templates"],
            [block["id"] for block in normalized["messages"][0]["content"] if block["type"] == "tool_use"],
        )
        self.assertEqual(
            ["call_resources", "call_templates"],
            [block["tool_use_id"] for block in normalized["messages"][1]["content"]],
        )

    def test_same_model_id_uses_provider_wire_profile(self):
        body = {"model": "deepseek-v4-flash"}

        opencode_cfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["opencode"])
        opencode_cfg["current_model"] = "deepseek-v4-flash"
        self.assertEqual(
            "openai-chat",
            ciel_runtime.provider_wire_profile("opencode", opencode_cfg, body)["upstream_format"],
        )

        ollama_cfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["ollama-cloud"])
        ollama_cfg["current_model"] = "deepseek-v4-flash"
        self.assertEqual(
            "ollama-chat",
            ciel_runtime.provider_wire_profile("ollama-cloud", ollama_cfg, body)["upstream_format"],
        )

        deepseek_cfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["deepseek"])
        deepseek_cfg["current_model"] = "deepseek-v4-flash"
        self.assertEqual(
            "anthropic-messages",
            ciel_runtime.provider_wire_profile("deepseek", deepseek_cfg, body)["upstream_format"],
        )

    def test_non_anthropic_missing_tool_result_discards_only_orphan_tool_use(self):
        pcfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["opencode"])
        body = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "I will inspect it."},
                        {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "pwd"}},
                    ],
                },
                {"role": "user", "content": "continue"},
            ]
        }

        out = ciel_runtime.normalize_anthropic_tool_turns_for_provider("opencode", pcfg, body)

        content = out["messages"][0]["content"]
        self.assertEqual([{"type": "text", "text": "I will inspect it."}], content)
        self.assertNotIn("ciel-runtime", str(out))

    def test_matching_tool_result_is_preserved_for_non_anthropic_provider(self):
        pcfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["opencode"])
        body = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {}}],
                },
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"}],
                },
            ]
        }

        out = ciel_runtime.normalize_anthropic_tool_turns_for_provider("opencode", pcfg, body)

        self.assertIs(out, body)
        self.assertEqual("tool_use", out["messages"][0]["content"][0]["type"])
        self.assertEqual("tool_result", out["messages"][1]["content"][0]["type"])

    def test_orphan_tool_result_is_discarded_for_non_anthropic_provider(self):
        pcfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["opencode"])
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "toolu_missing", "content": "late result"}],
                }
            ]
        }

        out = ciel_runtime.normalize_anthropic_tool_turns_for_provider("opencode", pcfg, body)

        self.assertEqual([], out["messages"])
        self.assertNotIn("late result", str(out))
        self.assertNotIn("ciel-runtime", str(out))

    def test_anthropic_provider_preserves_historical_tool_blocks(self):
        pcfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["anthropic"])
        body = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {}}],
                }
            ]
        }

        out = ciel_runtime.normalize_anthropic_tool_turns_for_provider("anthropic", pcfg, body)

        self.assertIs(out, body)
        self.assertEqual("tool_use", out["messages"][0]["content"][0]["type"])

    def test_anthropic_wire_discards_empty_name_tool_history_and_paired_results(self):
        pcfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["anthropic"])
        body = {
            "model": "claude-opus-4-6",
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "continuing"},
                        {
                            "type": "tool_use",
                            "id": "toolu_bad_1",
                            "name": "",
                            "input": {"raw_arguments": "}"},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_bad_1",
                            "is_error": True,
                            "content": "No such tool available",
                        }
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_bad_2",
                            "name": "   ",
                            "input": {"raw_arguments": ""},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_bad_2",
                            "is_error": True,
                            "content": "No such tool available",
                        }
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_valid",
                            "name": "Bash",
                            "input": {"command": "pwd"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_valid",
                            "content": "workspace",
                        }
                    ],
                },
            ],
        }

        out = ciel_runtime.normalize_request_for_provider_wire("anthropic", pcfg, body)

        serialized = str(out["messages"])
        self.assertNotIn("toolu_bad_1", serialized)
        self.assertNotIn("toolu_bad_2", serialized)
        self.assertNotIn("No such tool available", serialized)
        self.assertIn("continuing", serialized)
        self.assertIn("toolu_valid", serialized)
        self.assertIn("workspace", serialized)


if __name__ == "__main__":
    unittest.main()
