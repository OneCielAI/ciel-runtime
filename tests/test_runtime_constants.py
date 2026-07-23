import unittest

from ciel_runtime_support import runtime_constants


class RuntimeConstantsTest(unittest.TestCase):
    def test_provider_aliases_cover_native_and_hosted_entrypoints(self):
        self.assertEqual("anthropic", runtime_constants.PROVIDER_ALIASES["claude-native"])
        self.assertEqual("codex", runtime_constants.PROVIDER_ALIASES["openai-codex"])
        self.assertEqual("nvidia-hosted", runtime_constants.PROVIDER_ALIASES["nvidia"])

    def test_routed_prompt_preserves_tool_execution_contract(self):
        prompt = runtime_constants.ROUTED_COMPAT_PROMPT
        self.assertIn("Do not stop after announcing", prompt)
        self.assertIn("TaskList: no input", prompt)
        self.assertIn("Do not call WaitForMcpServers", prompt)

    def test_model_defaults_are_internally_consistent(self):
        self.assertEqual(
            runtime_constants.ANTHROPIC_PUBLIC_MODEL_FALLBACK_IDS,
            runtime_constants.ANTHROPIC_PUBLIC_MODEL_DEFAULT_IDS,
        )
        self.assertIn("kimi-for-coding", runtime_constants.KIMI_MODEL_FALLBACK_IDS)
        self.assertIn("kimi-for-coding-highspeed", runtime_constants.KIMI_MODEL_FALLBACK_IDS)


if __name__ == "__main__":
    unittest.main()
