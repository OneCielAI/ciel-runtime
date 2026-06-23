import copy
import unittest

import claude_any


class ProviderWireNormalizationTests(unittest.TestCase):
    def test_same_model_id_uses_provider_wire_profile(self):
        body = {"model": "deepseek-v4-flash"}

        opencode_cfg = copy.deepcopy(claude_any.DEFAULT_CONFIG["providers"]["opencode"])
        opencode_cfg["current_model"] = "deepseek-v4-flash"
        self.assertEqual(
            "openai-chat",
            claude_any.provider_wire_profile("opencode", opencode_cfg, body)["upstream_format"],
        )

        ollama_cfg = copy.deepcopy(claude_any.DEFAULT_CONFIG["providers"]["ollama-cloud"])
        ollama_cfg["current_model"] = "deepseek-v4-flash"
        self.assertEqual(
            "ollama-chat",
            claude_any.provider_wire_profile("ollama-cloud", ollama_cfg, body)["upstream_format"],
        )

        deepseek_cfg = copy.deepcopy(claude_any.DEFAULT_CONFIG["providers"]["deepseek"])
        deepseek_cfg["current_model"] = "deepseek-v4-flash"
        self.assertEqual(
            "anthropic-messages",
            claude_any.provider_wire_profile("deepseek", deepseek_cfg, body)["upstream_format"],
        )

    def test_non_anthropic_missing_tool_result_downgrades_tool_use_to_text(self):
        pcfg = copy.deepcopy(claude_any.DEFAULT_CONFIG["providers"]["opencode"])
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

        out = claude_any.normalize_anthropic_tool_turns_for_provider("opencode", pcfg, body)

        content = out["messages"][0]["content"]
        self.assertEqual("text", content[1]["type"])
        self.assertIn("matching tool_result is not present", content[1]["text"])
        self.assertIn("tool=Bash", content[1]["text"])

    def test_matching_tool_result_is_preserved_for_non_anthropic_provider(self):
        pcfg = copy.deepcopy(claude_any.DEFAULT_CONFIG["providers"]["opencode"])
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

        out = claude_any.normalize_anthropic_tool_turns_for_provider("opencode", pcfg, body)

        self.assertIs(out, body)
        self.assertEqual("tool_use", out["messages"][0]["content"][0]["type"])
        self.assertEqual("tool_result", out["messages"][1]["content"][0]["type"])

    def test_orphan_tool_result_downgrades_to_text_for_non_anthropic_provider(self):
        pcfg = copy.deepcopy(claude_any.DEFAULT_CONFIG["providers"]["opencode"])
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "toolu_missing", "content": "late result"}],
                }
            ]
        }

        out = claude_any.normalize_anthropic_tool_turns_for_provider("opencode", pcfg, body)

        block = out["messages"][0]["content"][0]
        self.assertEqual("text", block["type"])
        self.assertIn("matching assistant tool_use is not present", block["text"])
        self.assertIn("late result", block["text"])

    def test_anthropic_provider_preserves_historical_tool_blocks(self):
        pcfg = copy.deepcopy(claude_any.DEFAULT_CONFIG["providers"]["anthropic"])
        body = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {}}],
                }
            ]
        }

        out = claude_any.normalize_anthropic_tool_turns_for_provider("anthropic", pcfg, body)

        self.assertIs(out, body)
        self.assertEqual("tool_use", out["messages"][0]["content"][0]["type"])


if __name__ == "__main__":
    unittest.main()
