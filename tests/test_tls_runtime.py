from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from ciel_runtime_support.tls_runtime import (
    TLSBundleStore,
    TLSCertificateError,
    TLSConfig,
    TLSConfigurationError,
    TLSMode,
    inspect_certificate,
    load_server_ssl_context,
)
from tls_test_certificates import cached_rsa_key, make_certificate


NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


class TLSRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.key = cached_rsa_key("tls-runtime")
        cls.other_key = cached_rsa_key("tls-runtime-other")

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_bundle(
        self,
        *,
        names: tuple[str, ...] = ("api.example.test",),
        not_before: datetime = NOW - timedelta(days=1),
        not_after: datetime = NOW + timedelta(days=29),
        key=None,
    ) -> tuple[Path, Path]:
        signing_key = key or self.key
        cert_path = self.root / "certificate.pem"
        key_path = self.root / "private-key.pem"
        cert_path.write_bytes(make_certificate(signing_key, names, not_before=not_before, not_after=not_after))
        key_path.write_bytes(signing_key.private_pem())
        if os.name != "nt":
            os.chmod(key_path, 0o600)
        return cert_path, key_path

    def test_validated_modes_and_redacted_config(self) -> None:
        disabled = TLSConfig().validated()
        self.assertIs(disabled.mode, TLSMode.DISABLED)
        with self.assertRaises(TLSConfigurationError):
            TLSConfig(mode="invalid").validated()
        with self.assertRaises(TLSConfigurationError):
            TLSConfig(mode="manual", cert_file=self.root / "c.pem").validated()
        with self.assertRaises(TLSConfigurationError):
            TLSConfig(mode="acme", key_file=self.root / "secret.pem").validated()
        cert_path, key_path = self.write_bundle()
        status = TLSConfig(
            mode="manual",
            cert_file=cert_path,
            key_file=key_path,
            server_names=("API.EXAMPLE.TEST.",),
        ).redacted_status()
        self.assertEqual({"mode": "manual", "configured": True, "server_names": ["api.example.test"]}, status)
        self.assertNotIn(str(cert_path), repr(status))
        self.assertNotIn(str(key_path), repr(status))

    def test_loads_context_and_reports_metadata(self) -> None:
        cert_path, key_path = self.write_bundle(names=("api.example.test", "203.0.113.7"))
        context, metadata = load_server_ssl_context(
            cert_path,
            key_path,
            ("api.example.test", "203.0.113.7"),
            now=NOW,
        )
        self.assertEqual("PROTOCOL_TLS_SERVER", context.protocol.name)
        self.assertEqual(64, len(metadata.sha256_fingerprint))
        self.assertEqual(("api.example.test", "203.0.113.7"), metadata.subject_alt_names)
        self.assertEqual(NOW + timedelta(days=29), metadata.not_after)

    def test_missing_bad_mismatch_expired_future_and_san_mismatch_are_rejected(self) -> None:
        missing = self.root / "missing.pem"
        cert_path, key_path = self.write_bundle()
        with self.assertRaisesRegex(TLSConfigurationError, "missing or unreadable"):
            load_server_ssl_context(missing, key_path, now=NOW)
        cert_path.write_text("not a certificate", encoding="utf-8")
        with self.assertRaisesRegex(TLSCertificateError, "PEM certificate"):
            load_server_ssl_context(cert_path, key_path, now=NOW)

        cert_path, key_path = self.write_bundle()
        key_path.write_bytes(self.other_key.private_pem())
        with self.assertRaisesRegex(TLSCertificateError, "matching TLS bundle"):
            load_server_ssl_context(cert_path, key_path, now=NOW)

        cert_path, key_path = self.write_bundle(not_after=NOW)
        with self.assertRaisesRegex(TLSCertificateError, "expired"):
            load_server_ssl_context(cert_path, key_path, now=NOW)
        cert_path, key_path = self.write_bundle(not_before=NOW + timedelta(seconds=1))
        with self.assertRaisesRegex(TLSCertificateError, "not yet valid"):
            load_server_ssl_context(cert_path, key_path, now=NOW)
        cert_path, key_path = self.write_bundle(names=("wrong.example.test",))
        with self.assertRaisesRegex(TLSCertificateError, "alternative names"):
            load_server_ssl_context(cert_path, key_path, ("api.example.test",), now=NOW)

    def test_insecure_key_paths_are_rejected(self) -> None:
        cert_path, key_path = self.write_bundle()
        if os.name != "nt":
            os.chmod(key_path, 0o644)
            with self.assertRaisesRegex(TLSConfigurationError, "permissions"):
                load_server_ssl_context(cert_path, key_path, now=NOW)
        link = self.root / "linked-key.pem"
        try:
            link.symlink_to(key_path)
        except OSError:
            self.skipTest("symbolic links are unavailable")
        with self.assertRaisesRegex(TLSConfigurationError, "non-symlink"):
            load_server_ssl_context(cert_path, link, now=NOW)

    def test_atomic_activation_switches_only_after_validation(self) -> None:
        store = TLSBundleStore(self.root / "store")
        first_cert = make_certificate(
            self.key,
            ("api.example.test",),
            not_before=NOW - timedelta(days=1),
            not_after=NOW + timedelta(days=29),
        )
        first = store.activate(first_cert, self.key.private_pem(), ("api.example.test",), now=NOW)
        initial_manifest = (store.active_manifest).read_bytes()
        self.assertEqual(first.generation, store.current(now=NOW).generation)
        self.assertFalse("store" in repr(store.redacted_status(now=NOW)))

        with self.assertRaises(TLSCertificateError):
            store.activate(first_cert, self.other_key.private_pem(), ("api.example.test",), now=NOW)
        self.assertEqual(initial_manifest, store.active_manifest.read_bytes())
        self.assertEqual(first.generation, store.current(now=NOW).generation)

        second_cert = make_certificate(
            self.other_key,
            ("api.example.test",),
            not_before=NOW - timedelta(hours=1),
            not_after=NOW + timedelta(days=7),
        )
        second = store.activate(second_cert, self.other_key.private_pem(), ("api.example.test",), now=NOW)
        self.assertNotEqual(first.generation, second.generation)
        self.assertEqual(second.generation, json.loads(store.active_manifest.read_text())["generation"])
        self.assertEqual(second.metadata.sha256_fingerprint, inspect_certificate(second.cert_file).sha256_fingerprint)
        self.assertEqual(0, stat.S_IMODE(second.key_file.stat().st_mode) & 0o077 if os.name != "nt" else 0)


if __name__ == "__main__":
    unittest.main()
