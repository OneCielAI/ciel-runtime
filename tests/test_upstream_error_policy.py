import unittest
import urllib.error

from ciel_runtime_support.upstream_error_policy import (
    configured_gateway_retries,
    retryable_exception,
)


class UpstreamErrorPolicyTests(unittest.TestCase):
    def test_network_route_and_dns_failures_are_retryable(self):
        self.assertTrue(retryable_exception(OSError(113, "No route to host")))
        self.assertTrue(
            retryable_exception(OSError(-3, "Temporary failure in name resolution"))
        )

    def test_windows_remote_reset_is_retryable(self):
        error = urllib.error.URLError(
            OSError(
                10054,
                "An existing connection was forcibly closed by the remote host",
            )
        )

        self.assertTrue(retryable_exception(error))

    def test_generation_retries_are_disabled_by_default(self):
        self.assertEqual(0, configured_gateway_retries({}))
        self.assertEqual(0, configured_gateway_retries({"gateway_retries": "invalid"}))

    def test_generation_retries_remain_explicit_opt_in(self):
        self.assertEqual(2, configured_gateway_retries({"gateway_retries": 2}))
        self.assertEqual(0, configured_gateway_retries({"gateway_retries": -1}))


if __name__ == "__main__":
    unittest.main()
