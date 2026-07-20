import unittest

from ciel_runtime_support.llm_presets import (
    PresetIdentityPolicy,
    normalize_preset_token,
)


class PresetIdentityPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = PresetIdentityPolicy(
            {
                "balanced": ("Balanced", "Balanced defaults"),
                "large-output": ("Large Output", "Large responses"),
                "million-context-1m": ("Million Context", "Large context"),
            },
            lambda preset_id: f"llm-{preset_id}",
        )

    def test_normalizes_punctuation_and_case(self):
        self.assertEqual("large-output", normalize_preset_token(" Large_Output! "))

    def test_resolves_builtin_aliases(self):
        self.assertEqual("large-output", self.policy.resolve("report"))
        self.assertEqual("million-context-1m", self.policy.resolve("1M"))

    def test_resolves_id_label_and_command_name(self):
        self.assertEqual("balanced", self.policy.resolve("balanced"))
        self.assertEqual("large-output", self.policy.resolve("Large Output"))
        self.assertEqual("large-output", self.policy.resolve("llm-large-output"))

    def test_rejects_blank_and_unknown_values(self):
        self.assertIsNone(self.policy.resolve(""))
        self.assertIsNone(self.policy.resolve("not-a-preset"))


if __name__ == "__main__":
    unittest.main()
