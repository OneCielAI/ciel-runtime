import unittest

from ciel_runtime_support.model_context_hints import (
    ModelContextHintPolicy,
    ModelContextHintPorts,
)


def positive_int(value):
    try:
        fixed = int(value)
    except (TypeError, ValueError):
        return None
    return fixed if fixed > 0 else None


class ModelContextHintPolicyTests(unittest.TestCase):
    def policy(self, *, catalog=None, preset=None):
        return ModelContextHintPolicy(
            (("glm-5", 200000), ("glm-4.7", 131072)),
            ModelContextHintPorts(
                strip_context_suffix=lambda value: value.removesuffix("[1m]"),
                catalog_context=lambda _model: (catalog, "family", "catalog"),
                model_preset=lambda _model: preset or {},
                positive_int=positive_int,
            ),
        )

    def test_qwen36_plus_identity_ignores_separators(self):
        self.assertTrue(ModelContextHintPolicy.is_qwen36_plus("Qwen-3.6-Plus"))
        self.assertFalse(ModelContextHintPolicy.is_qwen36_plus("qwen3.6-27b"))

    def test_kimi_k3_identity_accepts_runtime_prefix_and_context_suffix(self):
        policy = self.policy()

        self.assertTrue(policy.is_kimi_k3("ciel-runtime-kimi-k3[1m]"))
        self.assertEqual(1048576, policy.resolve("kimi-code/k3"))

    def test_zai_hint_uses_longest_configured_prefix_order(self):
        policy = self.policy()

        self.assertEqual(200000, policy.zai_hint("glm-5-air"))
        self.assertEqual(131072, policy.zai_hint("glm-4.7-flash"))

    def test_catalog_hint_precedes_generic_model_markers(self):
        policy = self.policy(catalog=98304)

        self.assertEqual(98304, policy.resolve("private-model"))

    def test_known_model_families_have_stable_context_hints(self):
        policy = self.policy()

        self.assertEqual(1048576, policy.resolve("deepseek-v4-pro"))
        self.assertEqual(262144, policy.resolve("kimi-k2.7"))
        self.assertEqual(131072, policy.resolve("deepseek-r1"))

    def test_model_preset_is_the_final_fallback(self):
        policy = self.policy(preset={"num_ctx_max": "65536"})

        self.assertEqual(65536, policy.resolve("unclassified-model"))
        self.assertIsNone(policy.resolve(""))


if __name__ == "__main__":
    unittest.main()
