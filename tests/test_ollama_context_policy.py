import unittest

from ciel_runtime_support.config_value_codec import positive_int
from ciel_runtime_support.providers.ollama_context import OllamaRequestContextPolicy


class OllamaRequestContextPolicyTests(unittest.TestCase):
    def policy(self, environment=None, estimated_tokens=1000):
        return OllamaRequestContextPolicy(
            environ=environment or {},
            positive_int=positive_int,
            estimate_tokens=lambda _payload, _cache=None: estimated_tokens,
            model_matches=lambda left, right: left.removesuffix(":latest")
            == str(right).removesuffix(":latest"),
            preset_names=frozenset({"balanced"}),
            default_request_timeout_ms=300_000,
        )

    def test_dynamic_context_uses_model_limit_and_preset_cap(self):
        config = {
            "current_model": "qwen:latest",
            "model_context_model": "qwen",
            "model_context_max": 262_144,
            "num_ctx": "auto",
            "num_ctx_max": 131_072,
            "llm_preset": "balanced",
        }
        policy = self.policy()
        self.assertEqual(131_072, policy.num_ctx_for_payload(config, {}))
        self.assertEqual(131_072, policy.context_limit_for_budget(config))
        self.assertIn("model max 262,144", policy.num_ctx_status(config))

    def test_dynamic_context_estimate_uses_bucket_and_environment_override(self):
        config = {"num_ctx": "auto", "num_ctx_min": 8192, "num_ctx_max": 65536}
        self.assertEqual(
            16384, self.policy(estimated_tokens=7000).num_ctx_for_payload(config, {})
        )
        self.assertEqual(
            32768,
            self.policy({"CIEL_RUNTIME_OLLAMA_NUM_CTX": "32768"}).num_ctx_for_payload(
                config, {}
            ),
        )

    def test_context_error_recovery_caps_output_and_options(self):
        policy = self.policy()
        self.assertEqual(
            32768,
            policy.context_error_limit("available context size (32768 tokens)"),
        )
        recovered = policy.context_retry_config(
            {
                "num_ctx_min": 65536,
                "max_output_tokens": 8192,
                "ollama_options": {"num_predict": 4096, "temperature": 0.5},
            },
            32768,
        )
        self.assertEqual(32768, recovered["num_ctx"])
        self.assertEqual(2048, recovered["max_output_tokens"])
        self.assertEqual(2048, recovered["ollama_options"]["num_predict"])

    def test_options_and_timeout_projection(self):
        policy = self.policy()
        self.assertEqual(
            {"temperature": 0.7},
            policy.extra_options(
                {"ollama_options": {"temperature": 0.7, "drop": None}}
            ),
        )
        self.assertEqual(300.0, policy.request_timeout_seconds({}))
        self.assertEqual(
            120.0, policy.request_timeout_seconds({"request_timeout_ms": -1})
        )


if __name__ == "__main__":
    unittest.main()
