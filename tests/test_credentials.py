import unittest

from ciel_runtime_support.credentials import (
    CredentialChain,
    CredentialContext,
    InboundHeaderCredentialSource,
    mask_secret,
    looks_like_masked_secret,
    redact_sensitive_obj,
    redact_sensitive_text,
    resolve_anthropic_credentials,
    secret_fingerprint,
    transportable_api_key,
)
from ciel_runtime_support.header_forwarding import (
    project_end_to_end_request_headers,
)


class CredentialTests(unittest.TestCase):
    def test_api_key_precedes_inbound_oauth(self):
        result = resolve_anthropic_credentials("stored-key", {"authorization": "Bearer oauth"})
        self.assertEqual("api_key", result.source)
        self.assertEqual({"x-api-key": "stored-key"}, result.headers)

    def test_inbound_oauth_preserves_only_allowlisted_headers(self):
        result = resolve_anthropic_credentials(
            "",
            {"Authorization": "Bearer oauth", "anthropic-beta": "tools", "cookie": "secret"},
        )
        self.assertEqual("inbound", result.source)
        self.assertEqual("Bearer oauth", result.headers["authorization"])
        self.assertNotIn("cookie", result.headers)

    def test_anthropic_upstream_projection_is_case_insensitive_and_open(self):
        projected = project_end_to_end_request_headers(
            {
                "Anthropic-Version": "2023-06-01",
                "ANTHROPIC-FUTURE": "unchanged",
                "X-Claude-Code-Agent-Id": "agent-1",
                "X-New-Claude-Metadata": "future-safe-value",
                "Authorization": "Bearer secret",
                "Cookie": "secret",
                "Host": "127.0.0.1:9464",
                "Content-Length": "999",
                "Connection": "keep-alive",
            },
            replace_credentials=True,
        )

        self.assertEqual(
            {
                "Anthropic-Version": "2023-06-01",
                "ANTHROPIC-FUTURE": "unchanged",
                "X-Claude-Code-Agent-Id": "agent-1",
                "X-New-Claude-Metadata": "future-safe-value",
            },
            projected,
        )

    def test_inbound_source_requires_auth_header(self):
        source = InboundHeaderCredentialSource(("authorization", "anthropic-beta"))
        self.assertIsNone(source.resolve(CredentialContext("anthropic", inbound_headers={"anthropic-beta": "tools"})))

    def test_empty_chain_returns_none(self):
        self.assertIsNone(CredentialChain().resolve(CredentialContext("provider")))

    def test_secret_projection_masks_and_fingerprints_without_disclosure(self):
        secret = "sk-super-secret-value"
        self.assertEqual("sk-s...alue", mask_secret(secret))
        self.assertTrue(looks_like_masked_secret(mask_secret(secret)))
        self.assertFalse(transportable_api_key(mask_secret(secret)))
        self.assertTrue(transportable_api_key(secret))
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
