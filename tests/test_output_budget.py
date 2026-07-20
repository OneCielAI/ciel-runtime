import unittest

from ciel_runtime_support.config_value_codec import positive_int
from ciel_runtime_support.output_budget import OpenAIContextBudgetPolicy, OutputBudgetPolicy


class OutputBudgetPolicyTests(unittest.TestCase):
    def policy(self, input_tokens=1000):
        return OutputBudgetPolicy(
            positive_int=positive_int,
            estimate_tokens=lambda _payload, _cache=None: input_tokens,
            provider_options=lambda config: dict(config.get("options") or {}),
        )

    def test_configured_tokens_honors_provider_option_and_request_cap(self):
        policy = self.policy()
        config = {"max_output_tokens": 8192, "options": {"num_predict": 4096}}
        self.assertEqual(
            2048,
            policy.configured_tokens(config, {"max_tokens": 2048}, "num_predict"),
        )

    def test_context_cap_reserves_space_and_handles_exhaustion(self):
        config = {"context_reserve_tokens": 1024}
        self.assertEqual(
            3072,
            self.policy(input_tokens=4096).cap_tokens_for_context(
                config, {}, {}, 8192, 4096
            ),
        )
        self.assertEqual(
            256,
            self.policy(input_tokens=8000).cap_tokens_for_context(
                config, {}, {}, 8192, 4096
            ),
        )

    def test_default_reserve_scales_with_context(self):
        policy = self.policy()
        self.assertEqual(1024, policy.reserve_tokens({}, None))
        self.assertEqual(16384, policy.reserve_tokens({}, 524288))


class OpenAIContextBudgetPolicyTests(unittest.TestCase):
    def policy(self, runtime=0):
        return OpenAIContextBudgetPolicy(
            positive_int=positive_int,
            runtime_capacity=lambda _provider, _config: runtime,
            nvidia_default=lambda _model: 131072,
        )

    def test_runtime_capacity_overrides_stale_vllm_window(self):
        self.assertEqual(
            262144,
            self.policy(runtime=262144).context_limit(
                "vllm", {"context_window": 200000}
            ),
        )

    def test_explicit_preset_caps_runtime_capacity(self):
        self.assertEqual(
            131072,
            self.policy(runtime=262144).context_limit(
                "vllm", {"context_window": 131072, "llm_preset": "long-128k"}
            ),
        )

    def test_provider_defaults_are_transport_specific(self):
        self.assertEqual(131072, self.policy().context_limit("nvidia-hosted", {}))
        self.assertEqual(65536, self.policy().context_limit("openrouter", {}))


if __name__ == "__main__":
    unittest.main()
