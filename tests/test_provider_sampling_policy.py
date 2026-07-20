import unittest

from ciel_runtime_support.provider_sampling_policy import ProviderSamplingPolicy


class ProviderSamplingPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = ProviderSamplingPolicy()

    def test_normalizes_supported_aliases(self):
        self.assertEqual("temperature", self.policy.option_key("TEMP"))
        self.assertEqual("top_p", self.policy.option_key("top-p"))
        self.assertEqual("top_k", self.policy.option_key("topk"))
        self.assertIsNone(self.policy.option_key("frequency_penalty"))

    def test_validates_sampling_ranges(self):
        self.assertEqual(0.0, self.policy.validate("temperature", 0))
        self.assertEqual(2.0, self.policy.validate("temperature", "2"))
        self.assertEqual(0.5, self.policy.validate("top_p", "0.5"))
        self.assertEqual(4, self.policy.validate("top_k", "4"))

    def test_rejects_invalid_sampling_values(self):
        invalid = (
            ("temperature", -0.1),
            ("temperature", 2.1),
            ("top_p", 0),
            ("top_p", 1.1),
            ("top_k", 0),
            ("unknown", 1),
        )
        for key, value in invalid:
            with self.subTest(key=key, value=value), self.assertRaises(SystemExit):
                self.policy.validate(key, value)


if __name__ == "__main__":
    unittest.main()
