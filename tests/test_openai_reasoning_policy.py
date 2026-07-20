import unittest

from ciel_runtime_support.architecture import ProviderConfig
from ciel_runtime_support.protocols.openai_reasoning import (
    OpenAiReasoningPolicy,
    anthropic_tool_choice_to_openai,
    openai_reasoning_to_anthropic_thinking_block,
)
from ciel_runtime_support.providers.opencode import OpenCodeProviderAdapter
from ciel_runtime_support.providers.openrouter import OpenRouterProviderAdapter


class OpenAiReasoningPolicyTests(unittest.TestCase):
    def setUp(self):
        self.config = ProviderConfig(
            name="opencode",
            base_url="https://opencode.ai/zen",
            model="deepseek-v4-flash-free",
            options={"model_endpoints": {}},
        )

    def test_anthropic_tool_choice_projects_to_openai_shape(self):
        self.assertEqual(
            {"type": "function", "function": {"name": "Read"}},
            anthropic_tool_choice_to_openai({"type": "tool", "name": "Read"}),
        )
        self.assertEqual("required", anthropic_tool_choice_to_openai({"type": "any"}))
        self.assertEqual("auto", anthropic_tool_choice_to_openai({"type": "auto"}))

    def test_reasoning_projection_is_deterministic_and_empty_safe(self):
        first = openai_reasoning_to_anthropic_thinking_block("private chain")
        second = openai_reasoning_to_anthropic_thinking_block("private chain")
        self.assertEqual(first, second)
        self.assertEqual("thinking", first["type"])
        self.assertEqual("private chain", first["thinking"])
        self.assertIsNone(openai_reasoning_to_anthropic_thinking_block(""))

    def test_opencode_adapter_owns_deepseek_passback_strategy(self):
        adapter = OpenCodeProviderAdapter()
        self.assertTrue(
            adapter.openai_reasoning_passback_enabled(
                self.config, "ciel-runtime-opencode-deepseek-v4-flash-free"
            )
        )
        self.assertFalse(adapter.openai_reasoning_passback_enabled(self.config, "glm-5.1"))
        self.assertFalse(
            OpenRouterProviderAdapter().openai_reasoning_passback_enabled(
                self.config, "deepseek-v4"
            )
        )

    def test_policy_delegates_without_provider_name_branching(self):
        adapter = OpenCodeProviderAdapter()
        policy = OpenAiReasoningPolicy(
            adapter_for=lambda _provider, _raw: adapter,
            config_for=lambda _provider, _raw: self.config,
        )
        body = {"model": "deepseek-v4-flash-free", "tool_choice": "required"}
        self.assertTrue(policy.passback_enabled_for_body("custom-name", {}, body))
        self.assertTrue(
            policy.should_omit_tool_choice(
                "custom-name", "deepseek-v4-flash-free", body, {}
            )
        )
        self.assertFalse(
            policy.should_omit_tool_choice(
                "custom-name", "deepseek-v4-flash-free", {"tool_choice": None}, {}
            )
        )


if __name__ == "__main__":
    unittest.main()
