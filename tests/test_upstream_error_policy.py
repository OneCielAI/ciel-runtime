import unittest

from ciel_runtime_support.upstream_error_policy import configured_gateway_retries


class UpstreamErrorPolicyTests(unittest.TestCase):
    def test_generation_retries_are_disabled_by_default(self):
        self.assertEqual(0, configured_gateway_retries({}))
        self.assertEqual(0, configured_gateway_retries({"gateway_retries": "invalid"}))

    def test_generation_retries_remain_explicit_opt_in(self):
        self.assertEqual(2, configured_gateway_retries({"gateway_retries": 2}))
        self.assertEqual(0, configured_gateway_retries({"gateway_retries": -1}))


if __name__ == "__main__":
    unittest.main()
