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

    def test_dynamic_context_uses_model_limit_for_budget_but_omits_wire_override(self):
        config = {
            "current_model": "qwen:latest",
            "model_context_model": "qwen",
            "model_context_max": 262_144,
            "num_ctx": "auto",
            "num_ctx_max": 131_072,
            "llm_preset": "balanced",
        }
        policy = self.policy()
        self.assertIsNone(policy.num_ctx_for_payload(config, {}))
        self.assertEqual(131_072, policy.context_limit_for_budget(config))
        self.assertIn("model max 262,144", policy.num_ctx_status(config))

    def test_dynamic_context_without_model_card_omits_num_ctx(self):
        # Operator 2026-07-29: when the model card provides no context
        # information, the num_ctx parameter must be omitted entirely so the
        # server default applies — never substituted with a payload-size
        # estimate clamped into num_ctx_min/max.
        config = {"num_ctx": "auto", "num_ctx_min": 8192, "num_ctx_max": 65536}
        self.assertIsNone(
            self.policy(estimated_tokens=7000).num_ctx_for_payload(config, {})
        )
        self.assertEqual(
            "auto (server default — no model-card context)",
            self.policy().num_ctx_status(config),
        )
        # An explicit environment override still wins (operator escape hatch).
        self.assertEqual(
            32768,
            self.policy({"CIEL_RUNTIME_OLLAMA_NUM_CTX": "32768"}).num_ctx_for_payload(
                config, {}
            ),
        )

    def test_num_predict_model_card_gate(self):
        policy = self.policy()
        # Adapter-default num_predict with no model-card context → omitted.
        self.assertIsNone(policy.num_predict_for_payload({}, 4096))
        # Stored legacy/default values are omitted without explicit provenance.
        self.assertIsNone(
            policy.num_predict_for_payload(
                {"ollama_options": {"num_predict": 8192}}, 4096
            )
        )
        # Explicit user configuration passes through even without a card.
        self.assertEqual(
            4096,
            policy.num_predict_for_payload(
                {
                    "ollama_options": {"num_predict": 8192},
                    "output_tokens_explicit": True,
                },
                4096,
            ),
        )
        self.assertEqual(
            4096,
            policy.num_predict_for_payload(
                {"max_output_tokens": 8192, "output_tokens_explicit": True},
                4096,
            ),
        )
        # A model-card context is for budgeting and does not become an override.
        card = {
            "current_model": "qwen:latest",
            "model_context_model": "qwen",
            "model_context_max": 262_144,
        }
        self.assertIsNone(policy.num_predict_for_payload(card, 4096))

        provider_default = {
            "max_output_tokens": 8192,
            "ollama_options": {"num_predict": 8192},
        }
        self.assertIsNone(
            policy.num_predict_for_payload(provider_default, 8192)
        )
        provider_default["output_tokens_explicit"] = True
        self.assertEqual(
            8192, policy.num_predict_for_payload(provider_default, 8192)
        )

    def test_context_error_recovery_omits_unproven_output_option(self):
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
        self.assertNotIn("num_predict", recovered["ollama_options"])
        self.assertEqual(0.5, recovered["ollama_options"]["temperature"])

        explicit = policy.context_retry_config(
            {
                "max_output_tokens": 8192,
                "output_tokens_explicit": True,
                "ollama_options": {"num_predict": 4096},
                "ollama_explicit_options": ["num_predict"],
            },
            32768,
        )
        self.assertEqual(2048, explicit["ollama_options"]["num_predict"])
        self.assertIn("num_predict", explicit["ollama_transient_options"])

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
