import socket
import unittest
from unittest import mock

from ciel_runtime_support import provider_network


class ProviderNetworkTests(unittest.TestCase):
    def test_user_agent_preserves_explicit_header_case_insensitively(self):
        headers = provider_network.with_upstream_user_agent({"User-Agent": "custom"})
        self.assertEqual({"User-Agent": "custom"}, headers)

    def test_ip_family_aliases_and_provider_default(self):
        self.assertEqual("ipv4-preferred", provider_network.normalize_ip_family("prefer-v4"))
        self.assertEqual("ipv6-preferred", provider_network.default_provider_ip_family("opencode"))
        self.assertEqual("auto", provider_network.default_provider_ip_family("anthropic"))

    def test_strict_ip_family_filters_dns_results(self):
        ipv4 = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        ipv6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 443, 0, 0))
        original = mock.Mock(return_value=[ipv6, ipv4])

        with mock.patch.object(provider_network.socket, "getaddrinfo", original):
            with provider_network.socket_ip_family_policy("ipv4"):
                self.assertEqual([ipv4], provider_network.socket.getaddrinfo("example.test", 443))

    def test_preferred_ip_family_orders_without_dropping_results(self):
        ipv4 = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        ipv6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 443, 0, 0))
        original = mock.Mock(return_value=[ipv6, ipv4])

        with mock.patch.object(provider_network.socket, "getaddrinfo", original):
            with provider_network.socket_ip_family_policy("ipv4-preferred"):
                self.assertEqual([ipv4, ipv6], provider_network.socket.getaddrinfo("example.test", 443))

    def test_invalid_ip_family_is_rejected(self):
        with self.assertRaises(SystemExit):
            provider_network.normalize_ip_family("ipx")


if __name__ == "__main__":
    unittest.main()
