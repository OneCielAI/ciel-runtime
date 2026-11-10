from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import time
import unittest

from ciel_runtime_support.acme_runtime import (
    ACMEAccount,
    ACMEConfig,
    ACMEConfigurationError,
    ACMECertificateManager,
    ACMEDirectory,
    ACMEOperationError,
    HTTP01ChallengeHandler,
    HTTP01TokenStore,
    IssuedCertificate,
    renewal_due,
    validate_public_identifiers,
)
from ciel_runtime_support.tls_runtime import TLSBundleStore, inspect_certificate_pem
from tls_test_certificates import cached_rsa_key, make_certificate


NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
TOKEN = "vMLOztYaEBlSGRFl7pO8fA"
AUTHORIZATION = f"{TOKEN}.thumbprint_value"


class FakeAdapter:
    def __init__(self, issued: list[IssuedCertificate]) -> None:
        self.issued = list(issued)
        self.directory_calls = 0
        self.register_calls = 0
        self.issue_calls = 0
        self.started = threading.Event()
        self.release = threading.Event()
        self.block = False
        self.failure: Exception | None = None
        self.secret = "adapter-super-secret"

    def get_directory(self, directory_url):
        self.directory_calls += 1
        return {
            "newNonce": "https://acme.test/new-nonce",
            "newAccount": "https://acme.test/new-account",
            "newOrder": "https://acme.test/new-order",
            "renewalInfo": "https://acme.test/renewal-info",
            "meta": {"profiles": {"classic": "Classic", "shortlived": "Short"}},
        }

    def register_account(self, directory, contacts):
        self.register_calls += 1
        return ACMEAccount("https://acme.test/account/1", "-----BEGIN PRIVATE KEY-----\naccount-secret\n-----END PRIVATE KEY-----\n", contacts)

    def issue_certificate(self, request, account, challenges):
        self.issue_calls += 1
        challenges.provision(TOKEN, AUTHORIZATION)
        self.started.set()
        if self.block:
            self.release.wait(5)
        if self.failure is not None:
            raise RuntimeError(f"{self.failure}: {self.secret}")
        if not self.issued:
            raise RuntimeError(f"no fake certificate: {self.secret}")
        return self.issued.pop(0)


def resolver(_host: str) -> tuple[str, ...]:
    return ("93.184.216.34",)


class ACMERuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.key1 = cached_rsa_key("acme-runtime-1")
        cls.key2 = cached_rsa_key("acme-runtime-2")

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def issued(self, key, *, days: float = 30) -> IssuedCertificate:
        return IssuedCertificate(
            make_certificate(
                key,
                ("api.example.test",),
                not_before=NOW - timedelta(days=1),
                not_after=NOW + timedelta(days=days),
            ),
            key.private_pem(),
        )

    def manager(self, adapter: FakeAdapter) -> ACMECertificateManager:
        return ACMECertificateManager(
            adapter,
            ACMEConfig(
                "https://acme.test/directory",
                ("api.example.test",),
                profile="classic",
                contacts=("mailto:ops@example.test",),
            ),
            self.root / "acme",
            TLSBundleStore(self.root / "tls"),
            resolver=resolver,
            clock=lambda: NOW,
        )

    def test_public_address_and_config_validation(self) -> None:
        for address in (
            "127.0.0.1",
            "10.0.0.1",
            "192.168.1.2",
            "169.254.1.2",
            "224.0.0.1",
            "::1",
            "fe80::1",
            "ff02::1",
            "2002:0a00:0001::1",
        ):
            with self.subTest(address=address), self.assertRaisesRegex(ACMEConfigurationError, "public IP"):
                validate_public_identifiers((address,))
        with self.assertRaisesRegex(ACMEConfigurationError, "public IP"):
            validate_public_identifiers(("internal.example",), lambda _host: ("10.0.0.1",))
        with self.assertRaisesRegex(ACMEConfigurationError, "wildcard"):
            ACMEConfig("https://acme.test/directory", ("*.example.test",)).validated()
        with self.assertRaisesRegex(ACMEConfigurationError, "HTTPS"):
            ACMEConfig("http://acme.test/directory", ("203.0.113.20",)).validated()
        self.assertEqual({"93.184.216.34": ("93.184.216.34",)}, validate_public_identifiers(("93.184.216.34",)))

    def test_directory_capabilities_require_endpoints_and_requested_profile(self) -> None:
        directory = ACMEDirectory.from_mapping(
            {
                "newNonce": "https://acme.test/nonce",
                "newAccount": "https://acme.test/account",
                "newOrder": "https://acme.test/order",
                "meta": {"profiles": {"classic": "Classic"}},
            },
            "classic",
        )
        self.assertEqual(("classic",), directory.profiles)
        with self.assertRaisesRegex(ACMEConfigurationError, "not advertised"):
            ACMEDirectory.from_mapping(
                {
                    "newNonce": "https://acme.test/nonce",
                    "newAccount": "https://acme.test/account",
                    "newOrder": "https://acme.test/order",
                    "meta": {"profiles": {"classic": "Classic"}},
                },
                "shortlived",
            )
        with self.assertRaisesRegex(ACMEConfigurationError, "newOrder"):
            ACMEDirectory.from_mapping(
                {"newNonce": "https://acme.test/nonce", "newAccount": "https://acme.test/account"}
            )

    def test_http01_store_and_handler_only_serve_explicit_tokens(self) -> None:
        tokens = HTTP01TokenStore()
        handler = HTTP01ChallengeHandler(tokens)
        self.assertIsNone(handler.handle("GET", "/other"))
        self.assertEqual(404, handler.handle("GET", f"/.well-known/acme-challenge/{TOKEN}").status)
        with self.assertRaises(ACMEConfigurationError):
            tokens.provision(TOKEN, "other-token.thumbprint")
        tokens.provision(TOKEN, AUTHORIZATION)
        response = handler.handle("GET", f"/.well-known/acme-challenge/{TOKEN}?ignored=1")
        self.assertEqual(200, response.status)
        self.assertEqual(AUTHORIZATION.encode("ascii"), response.body)
        self.assertEqual("application/octet-stream", response.headers["Content-Type"])
        self.assertEqual(405, handler.handle("POST", f"/.well-known/acme-challenge/{TOKEN}").status)
        self.assertEqual(404, handler.handle("GET", "/.well-known/acme-challenge/../secret").status)
        tokens.remove(TOKEN)
        self.assertEqual(404, handler.handle("GET", f"/.well-known/acme-challenge/{TOKEN}").status)

    def test_initial_issuance_persists_account_and_activates_certificate(self) -> None:
        adapter = FakeAdapter([self.issued(self.key1)])
        manager = self.manager(adapter)
        active = manager.ensure_certificate()
        self.assertEqual(1, adapter.directory_calls)
        self.assertEqual(1, adapter.register_calls)
        self.assertEqual(1, adapter.issue_calls)
        self.assertEqual("issued", manager.status()["last_action"])
        self.assertEqual("api.example.test", active.metadata.subject_alt_names[0])
        account_raw = (self.root / "acme" / "account.json").read_text(encoding="utf-8")
        self.assertNotIn("account-secret", account_raw)
        self.assertEqual(0, stat.S_IMODE((self.root / "acme" / "account.json").stat().st_mode) & 0o077 if os.name != "nt" else 0)
        self.assertIsNone(manager.challenges.resolve(TOKEN))

    def test_fresh_certificate_is_reused_and_short_lived_threshold_is_half_life(self) -> None:
        metadata = inspect_certificate_pem(
            make_certificate(
                self.key1,
                ("api.example.test",),
                not_before=NOW - timedelta(days=1),
                not_after=NOW + timedelta(days=5),
            )
        )
        self.assertFalse(renewal_due(metadata, NOW))
        self.assertTrue(renewal_due(metadata, NOW + timedelta(days=2, hours=1)))

        adapter = FakeAdapter([self.issued(self.key1, days=30)])
        manager = self.manager(adapter)
        first = manager.ensure_certificate()
        again = manager.ensure_certificate()
        self.assertEqual(first.generation, again.generation)
        self.assertEqual(1, adapter.issue_calls)
        self.assertEqual("unchanged", manager.status()["last_action"])

    def test_renewal_activates_new_generation(self) -> None:
        adapter = FakeAdapter([self.issued(self.key1, days=30), self.issued(self.key2, days=30)])
        manager = self.manager(adapter)
        first = manager.ensure_certificate()
        second = manager.ensure_certificate(force=True)
        self.assertNotEqual(first.generation, second.generation)
        self.assertNotEqual(first.metadata.sha256_fingerprint, second.metadata.sha256_fingerprint)
        self.assertEqual(1, adapter.register_calls)
        self.assertEqual("renewed", manager.status()["last_action"])

    def test_failure_retains_prior_certificate_and_redacts_adapter_secrets(self) -> None:
        adapter = FakeAdapter([self.issued(self.key1)])
        manager = self.manager(adapter)
        first = manager.ensure_certificate()
        adapter.failure = RuntimeError("upstream failed at C:\\secret\\account.json")
        with self.assertRaisesRegex(ACMEOperationError, "issuance failed") as caught:
            manager.ensure_certificate(force=True)
        self.assertNotIn(adapter.secret, str(caught.exception))
        self.assertNotIn("account.json", str(caught.exception))
        current = manager.tls_store.current(now=NOW)
        self.assertEqual(first.generation, current.generation)
        status_text = json.dumps(manager.status())
        self.assertNotIn(adapter.secret, status_text)
        self.assertNotIn(str(self.root), status_text)
        self.assertIsNone(manager.challenges.resolve(TOKEN))

    def test_single_flight_shares_one_issuance_across_waiters(self) -> None:
        adapter = FakeAdapter([self.issued(self.key1)])
        adapter.block = True
        manager = self.manager(adapter)
        results = []
        errors = []

        def run():
            try:
                results.append(manager.ensure_certificate())
            except Exception as error:
                errors.append(error)

        first = threading.Thread(target=run)
        second = threading.Thread(target=run)
        first.start()
        self.assertTrue(adapter.started.wait(2))
        second.start()
        time.sleep(0.05)
        self.assertEqual(1, adapter.issue_calls)
        adapter.release.set()
        first.join(3)
        second.join(3)
        self.assertFalse(errors)
        self.assertEqual(2, len(results))
        self.assertEqual(results[0].generation, results[1].generation)
        self.assertEqual(1, adapter.issue_calls)


if __name__ == "__main__":
    unittest.main()
