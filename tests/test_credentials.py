import unittest

from ciel_runtime_support.credentials import (
    CredentialChain,
    CredentialContext,
    InboundHeaderCredentialSource,
    mask_secret,
    redact_sensitive_obj,
    redact_sensitive_text,
    resolve_anthropic_credentials,
    secret_fingerprint,
)


class CredentialTests(unittest.TestCase):
    def test_api_key_precedes_inbound_oauth(self):
        result = resolve_anthropic_credentials("stored-key", {"authorization": "Bearer oauth"})
        self.assertEqual("api_key", result.source)
        self.assertEqual({"x-api-key": "stored-key"}, result.headers)

    def test_inbound_oauth_preserves_only_allowlisted_headers(self):
        result = resolve_anthropic_credentials(
            "",
            {"authorization": "Bearer oauth", "anthropic-beta": "tools", "cookie": "secret"},
        )
        self.assertEqual("inbound", result.source)
        self.assertEqual("Bearer oauth", result.headers["authorization"])
        self.assertNotIn("cookie", result.headers)

    def test_inbound_source_requires_auth_header(self):
        source = InboundHeaderCredentialSource(("authorization", "anthropic-beta"))
        self.assertIsNone(source.resolve(CredentialContext("anthropic", inbound_headers={"anthropic-beta": "tools"})))

    def test_empty_chain_returns_none(self):
        self.assertIsNone(CredentialChain().resolve(CredentialContext("provider")))

    def test_secret_projection_masks_and_fingerprints_without_disclosure(self):
        secret = "sk-super-secret-value"
        self.assertEqual("sk-s...alue", mask_secret(secret))
        fingerprint = secret_fingerprint(secret)
        self.assertEqual(12, len(fingerprint))
        self.assertNotIn(secret, fingerprint)

    def test_sensitive_redaction_handles_text_and_nested_objects(self):
        text = redact_sensitive_text("Authorization: Bearer sk-super-secret-value")
        self.assertNotIn("sk-super-secret-value", text)
        projected = redact_sensitive_obj(
            {"api_key": "sk-super-secret-value", "nested": ["AINET_API_KEY=secret-value"]}
        )
        self.assertNotIn("sk-super-secret-value", str(projected))
        self.assertNotIn("secret-value", str(projected))


if __name__ == "__main__":
    unittest.main()
