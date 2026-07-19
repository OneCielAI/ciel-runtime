import unittest

from ciel_runtime_support.credentials import (
    CredentialChain,
    CredentialContext,
    InboundHeaderCredentialSource,
    resolve_anthropic_credentials,
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


if __name__ == "__main__":
    unittest.main()
