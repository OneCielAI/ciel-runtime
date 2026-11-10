from __future__ import annotations

import http.client
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import ssl
import tempfile
import threading
import unittest

from ciel_runtime_support.web_tls_runtime import (
    PLAINTEXT_REFUSAL,
    WebTLSBinding,
    admit_connection,
    wrap_server,
)
from tls_test_certificates import cached_rsa_key, make_certificate


class _TransportHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = (b"tls" if isinstance(self.connection, ssl.SSLSocket) else b"plain")
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        return None


class _FakeSocket:
    def __init__(self, first: bytes, local: str) -> None:
        self.first, self.local, self.sent, self.timeout = first, local, b"", None

    def gettimeout(self):
        return self.timeout

    def settimeout(self, value) -> None:
        self.timeout = value

    def recv(self, size: int, _flags: int = 0) -> bytes:
        return self.first[:size]

    def getsockname(self):
        return (self.local, 9505)

    def sendall(self, data: bytes) -> None:
        self.sent += data


class WebTLSListenerTests(unittest.TestCase):
    """One router port: HTTPS for external peers, unchanged HTTP for this host."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        key = cached_rsa_key("web-tls-listener")
        self.cert = root / "certificate.pem"
        self.key = root / "private-key.pem"
        self.cert.write_bytes(make_certificate(key, ("router.example.test", "127.0.0.1")))
        self.key.write_bytes(key.private_pem())
        if os.name != "nt":
            os.chmod(self.key, 0o600)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_same_port_serves_verified_tls_and_local_plain_http(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _TransportHandler)
        binding = WebTLSBinding(mode="certificate", server_names=("router.example.test", "127.0.0.1"),
                                cert_file=self.cert, key_file=self.key, status={"mode": "certificate"})
        self.assertIs(server, wrap_server(server, binding))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(5)))
        port = server.server_address[1]

        client_context = ssl.create_default_context(cafile=str(self.cert))
        secure = http.client.HTTPSConnection("127.0.0.1", port, timeout=5, context=client_context)
        secure.connect()
        # The client verified the chain and the 127.0.0.1 SAN during connect.
        self.assertEqual(("router.example.test", "127.0.0.1"),
                         tuple(value for _kind, value in secure.sock.getpeercert()["subjectAltName"]))
        secure.request("GET", "/")
        response = secure.getresponse()
        self.assertEqual((200, b"tls"), (response.status, response.read()))
        secure.close()

        local = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        local.request("GET", "/")
        response = local.getresponse()
        self.assertEqual((200, b"plain"), (response.status, response.read()))
        local.close()

    def test_external_plaintext_is_refused_and_same_host_plaintext_is_kept(self) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        remote = _FakeSocket(b"G", "203.0.113.7")
        self.assertIsNone(admit_connection(remote, ("198.51.100.20", 51000), context))
        self.assertEqual(PLAINTEXT_REFUSAL, remote.sent)
        own_address = _FakeSocket(b"G", "203.0.113.7")
        self.assertIs(own_address, admit_connection(own_address, ("203.0.113.7", 51001), context))
        self.assertEqual(b"", own_address.sent)
        closed = _FakeSocket(b"", "203.0.113.7")
        self.assertIsNone(admit_connection(closed, ("198.51.100.20", 51002), context))

    def test_disabled_binding_leaves_the_server_untouched(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _TransportHandler)
        self.addCleanup(server.server_close)
        finish = server.finish_request
        self.assertIs(server, wrap_server(server, WebTLSBinding(mode="http")))
        self.assertEqual(finish, server.finish_request)
        self.assertFalse(hasattr(server, "ciel_tls"))


if __name__ == "__main__":
    unittest.main()
