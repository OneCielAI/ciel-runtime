import unittest

from ciel_runtime_support.provider_launch_endpoint import (
    ProviderLaunchEndpointGroups,
    ProviderLaunchEndpointPolicy,
    ProviderLaunchEndpointQueries,
)


class ProviderLaunchEndpointPolicyTests(unittest.TestCase):
    def setUp(self):
        self.detected = []
        self.policy = ProviderLaunchEndpointPolicy(
            groups=ProviderLaunchEndpointGroups(
                native_runtimes=frozenset({"anthropic"}),
                auto_detect=frozenset({"local"}),
                claude_anthropic=frozenset({"messages"}),
                codex_openai=frozenset({"chat"}),
                model_specific=frozenset({"model-specific"}),
            ),
            query=ProviderLaunchEndpointQueries(
                detect_native_compat=lambda provider, _config: (
                    self.detected.append(provider) or False,
                    "detected",
                ),
                endpoint_kind=lambda _provider, model, _config: model,
            ),
        )

    def test_native_runtime_provider_has_no_preference(self):
        self.assertEqual(
            (None, ""),
            self.policy.preferred_native_compat(
                "claude", "anthropic", {}
            ),
        )

    def test_claude_delegates_auto_detection(self):
        self.assertEqual(
            (False, "detected"),
            self.policy.preferred_native_compat("CLAUDE", "local", {}),
        )
        self.assertEqual(["local"], self.detected)

    def test_claude_uses_model_specific_endpoint(self):
        desired, reason = self.policy.preferred_native_compat(
            "claude",
            "model-specific",
            {"current_model": "anthropic-messages"},
        )
        self.assertTrue(desired)
        self.assertIn("model's Anthropic Messages", reason)

    def test_codex_prefers_declared_openai_group(self):
        desired, reason = self.policy.preferred_native_compat(
            "codex-app-server", "chat", {}
        )
        self.assertFalse(desired)
        self.assertIn("OpenAI Chat compatible", reason)

    def test_unrelated_runtime_has_no_preference(self):
        self.assertEqual(
            (None, ""),
            self.policy.preferred_native_compat("agy", "messages", {}),
        )


if __name__ == "__main__":
    unittest.main()
