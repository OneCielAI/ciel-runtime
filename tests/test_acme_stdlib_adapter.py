from __future__ import annotations

import base64
import json
import unittest
from unittest import mock

from ciel_runtime_support.acme_runtime import (
    ACMEAccount,
    ACMEDirectory,
    HTTP01ChallengeHandler,
    HTTP01ChallengeServer,
    HTTP01TokenStore,
    StdlibACMEAdapter,
    _generate_rsa,
    _parse_rsa_private,
)


class Response:
    def __init__(self, body=b"{}", status=200, headers=None):
        self.body, self.status = body, status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=-1):
        return self.body


class StdlibACMEAdapterTests(unittest.TestCase):
    def test_generated_account_key_round_trips_and_signs(self):
        key = _generate_rsa(1024)
        loaded = _parse_rsa_private(key.private_pem())
        message = b"protected.payload"
        signature = loaded.sign(message)
        self.assertEqual(loaded.size, len(signature))
        encoded = int.from_bytes(signature, "big")
        decoded = pow(encoded, loaded.e, loaded.n).to_bytes(loaded.size, "big")
        self.assertTrue(decoded.endswith(__import__("hashlib").sha256(message).digest()))
        self.assertEqual(key.thumbprint(), loaded.thumbprint())

    def test_http01_server_serves_only_the_provisioned_token(self):
        import urllib.error
        import urllib.request
        tokens = HTTP01TokenStore()
        tokens.provision("vMLOztYaEBlSGRFl7pO8fA", "vMLOztYaEBlSGRFl7pO8fA.thumbprint")
        with HTTP01ChallengeServer(HTTP01ChallengeHandler(tokens), "127.0.0.1", 0) as listener:
            port = listener.server.server_port
            body = urllib.request.urlopen(
                f"http://127.0.0.1:{port}/.well-known/acme-challenge/vMLOztYaEBlSGRFl7pO8fA").read()
            self.assertEqual(b"vMLOztYaEBlSGRFl7pO8fA.thumbprint", body)
            with self.assertRaises(urllib.error.HTTPError) as missing:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/other")
            self.assertEqual(404, missing.exception.code)

    def test_directory_and_account_registration_use_acme_v2_jws(self):
        directory_body = json.dumps({
            "newNonce": "https://acme.test/nonce",
            "newAccount": "https://acme.test/account",
            "newOrder": "https://acme.test/order",
            "meta": {"profiles": {"shortlived": "Short-lived"}},
        }).encode()
        calls = []
        responses = [
            Response(directory_body),
            Response(b"", headers={"Replay-Nonce": "nonce-1"}),
            Response(b"{}", headers={"Replay-Nonce": "nonce-2", "Location": "https://acme.test/acct/1"}),
        ]

        def urlopen(request, timeout):
            calls.append((request, timeout))
            return responses.pop(0)

        adapter = StdlibACMEAdapter(timeout=3)
        with mock.patch("ciel_runtime_support.acme_runtime.urllib.request.urlopen", side_effect=urlopen):
            raw = adapter.get_directory("https://acme.test/directory")
            directory = ACMEDirectory.from_mapping(raw, "shortlived")
            account = adapter.register_account(directory, ("mailto:ops@example.test",))

        self.assertIsInstance(account, ACMEAccount)
        self.assertEqual("https://acme.test/acct/1", account.account_url)
        body = json.loads(calls[-1][0].data)
        protected = json.loads(base64.urlsafe_b64decode(body["protected"] + "=="))
        payload = json.loads(base64.urlsafe_b64decode(body["payload"] + "=="))
        self.assertEqual("RS256", protected["alg"])
        self.assertIn("jwk", protected)
        self.assertTrue(payload["termsOfServiceAgreed"])
        self.assertEqual(["mailto:ops@example.test"], payload["contact"])
        self.assertTrue(body["signature"])


if __name__ == "__main__":
    unittest.main()
