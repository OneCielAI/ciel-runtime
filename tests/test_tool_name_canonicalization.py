import unittest

import ciel_runtime


class ToolNameCanonicalizationTests(unittest.TestCase):
    def test_matches_mcp_server_hyphen_underscore_drift_when_unique(self):
        available = {
            "mcp__ai-net-http__list_assignments",
            "mcp__other-server__list_assignments",
        }

        self.assertEqual(
            "mcp__ai-net-http__list_assignments",
            ciel_runtime._match_available_tool_name("mcp__ai-net_http__list_assignments", available),
        )
        self.assertEqual(
            "mcp__ai-net-http__list_assignments",
            ciel_runtime._match_available_tool_name("mcp__ai_net_http__list_assignments", available),
        )

    def test_does_not_match_ambiguous_mcp_server_normalization(self):
        available = {
            "mcp__ab__get_messages",
            "mcp__a-b__get_messages",
        }

        self.assertIsNone(
            ciel_runtime._match_available_tool_name("mcp__a_b__get_messages", available)
        )

    def test_does_not_normalize_mcp_tool_segment(self):
        available = {"mcp__ai-net-http__get_messages"}

        self.assertIsNone(
            ciel_runtime._match_available_tool_name("mcp__ai-net_http__get-messages", available)
        )

    def test_matches_non_mcp_tool_separator_drift_when_unique(self):
        available = {"WebSearch", "WebFetch"}

        self.assertEqual(
            "WebSearch",
            ciel_runtime._match_available_tool_name("web_search", available),
        )
        self.assertEqual(
            "WebFetch",
            ciel_runtime._match_available_tool_name("web-fetch", available),
        )

    def test_ollama_nonstream_drops_tool_call_missing_required_input(self):
        source_body = {
            "model": "ciel-runtime-ollama-qwen",
            "tools": [
                {
                    "name": "WebSearch",
                    "input_schema": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                }
            ],
            "messages": [{"role": "user", "content": "search"}],
        }
        data = {
            "message": {
                "role": "assistant",
                "tool_calls": [
                    {"function": {"name": "web_search", "arguments": "{}"}}
                ],
            }
        }

        out = ciel_runtime.ollama_chat_to_anthropic(data, "qwen", source_body)

        self.assertEqual("end_turn", out["stop_reason"])
        self.assertFalse(
            any(block.get("type") == "tool_use" for block in out["content"])
        )

    def test_ollama_nonstream_keeps_tool_call_with_required_input(self):
        source_body = {
            "model": "ciel-runtime-ollama-qwen",
            "tools": [
                {
                    "name": "WebSearch",
                    "input_schema": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                }
            ],
            "messages": [{"role": "user", "content": "search"}],
        }
        data = {
            "message": {
                "role": "assistant",
                "tool_calls": [
                    {
                        "function": {
                            "name": "web_search",
                            "arguments": '{"query":"2026 technology trends"}',
                        }
                    }
                ],
            }
        }

        out = ciel_runtime.ollama_chat_to_anthropic(data, "qwen", source_body)

        self.assertEqual("tool_use", out["stop_reason"])
        self.assertEqual("WebSearch", out["content"][0]["name"])
        self.assertEqual({"query": "2026 technology trends"}, out["content"][0]["input"])

    def test_ollama_nonstream_emits_available_mcp_tool_name(self):
        source_body = {
            "model": "ciel-runtime-ollama-gemma4-12b",
            "tools": [
                {
                    "name": "mcp__ai-net-http__list_assignments",
                    "input_schema": {"type": "object", "properties": {}},
                }
            ],
            "messages": [{"role": "user", "content": "check assignments"}],
        }
        data = {
            "message": {
                "role": "assistant",
                "tool_calls": [
                    {
                        "function": {
                            "name": "mcp__ai-net_http__list_assignments",
                            "arguments": "{}",
                        }
                    }
                ],
            }
        }

        out = ciel_runtime.ollama_chat_to_anthropic(data, "gemma4:12b", source_body)

        self.assertEqual("tool_use", out["stop_reason"])
        self.assertEqual("mcp__ai-net-http__list_assignments", out["content"][0]["name"])


if __name__ == "__main__":
    unittest.main()
