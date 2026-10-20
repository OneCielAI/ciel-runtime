import socket
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from ciel_runtime_support import provider_network


class ProviderNetworkTests(unittest.TestCase):
    def test_user_agent_preserves_explicit_header_case_insensitively(self):
        headers = provider_network.with_upstream_user_agent({"User-Agent": "custom"})
        self.assertEqual({"User-Agent": "custom"}, headers)

    def test_ip_family_aliases_and_provider_default(self):
        self.assertEqual("ipv4-preferred", provider_network.normalize_ip_family("prefer-v4"))
        self.assertEqual("ipv6-preferred", provider_network.default_provider_ip_family("opencode"))
        self.assertEqual("ipv4-preferred", provider_network.default_provider_ip_family("codex"))
        self.assertEqual("ipv4-preferred", provider_network.default_provider_ip_family("kimi"))
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


class GatewayRedirectTests(unittest.TestCase):
    def test_generation_redirects_never_deliver_credentials_to_target(self):
        received = []

        class Target(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                received.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.end_headers()

            do_POST = do_GET

        target = ThreadingHTTPServer(("127.0.0.1", 0), Target)
        target_thread = threading.Thread(target=target.serve_forever, daemon=True)
        target_thread.start()

        class Redirect(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self.send_response(int(self.path.strip("/")))
                self.send_header("Location", f"http://127.0.0.1:{target.server_port}/target")
                self.send_header("Content-Length", "0")
                self.end_headers()

        source = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        source_thread = threading.Thread(target=source.serve_forever, daemon=True)
        source_thread.start()
        try:
            for status in (301, 302, 303, 307, 308):
                with self.subTest(status=status):
                    request = urllib.request.Request(
                        f"http://127.0.0.1:{source.server_port}/{status}", data=b"{}",
                        headers={"Authorization": "Bearer synthetic-test-key", "x-api-key": "synthetic-test-key"},
                    )
                    with self.assertRaises(urllib.error.HTTPError) as raised:
                        provider_network.provider_urlopen(
                            request, 2, "cielairouter", {"ciel_gateway_route": "test"}, lambda *_args: None,
                        )
                    self.assertEqual(status, raised.exception.code)
                    raised.exception.close()
            self.assertEqual([], received)
        finally:
            for server in (source, target):
                server.shutdown()
                server.server_close()
            source_thread.join(2)
            target_thread.join(2)

    def test_other_providers_keep_existing_transport(self):
        request = urllib.request.Request("https://example.test")
        with mock.patch.object(provider_network.urllib.request, "urlopen") as open_request:
            provider_network.provider_urlopen(request, 2, "vllm", {}, lambda *_args: None)
            open_request.assert_called_once_with(request, timeout=2)


if __name__ == "__main__":
    unittest.main()
