import unittest

from ciel_runtime_support.architecture import ProviderConfig
from ciel_runtime_support.provider_adapters import (
    PROVIDER_ADAPTERS,
    PROVIDER_DESCRIPTORS,
)
from ciel_runtime_support.providers.catalog import (
    COMPATIBLE_PROVIDER_SPECS,
    CatalogOpenAIProviderAdapter,
    CompatibleProviderSpec,
)
from ciel_runtime_support.providers.anthropic_catalog import (
    ANTHROPIC_COMPATIBLE_PROVIDER_SPECS,
    CatalogAnthropicProviderAdapter,
)
from ciel_runtime_support.providers.cloud import (
    AzureOpenAIProviderAdapter,
    CodeBuddyCnProviderAdapter,
)


class CatalogProviderAdapterTests(unittest.TestCase):
    def test_all_compatible_specs_are_registered(self):
        expected = {spec.name for spec in COMPATIBLE_PROVIDER_SPECS}
        self.assertTrue(expected.issubset(PROVIDER_ADAPTERS.names()))
        self.assertGreaterEqual(len(PROVIDER_ADAPTERS.names()), 40)

    def test_descriptor_aliases_resolve_to_canonical_provider(self):
        aliases = PROVIDER_DESCRIPTORS.aliases()
        self.assertEqual("blackbox", aliases["bb"])
        self.assertEqual("xai", aliases["grok"])
        self.assertEqual("vercel-ai-gateway", aliases["vercel"])

    def test_adapter_projects_auth_protocol_and_bundled_models(self):
        adapter = PROVIDER_ADAPTERS.create("groq")
        self.assertIsInstance(adapter, CatalogOpenAIProviderAdapter)
        config = ProviderConfig(
            name="groq",
            base_url=adapter.default_base_url(),
            api_keys=("secret",),
            model="llama-3.3-70b-versatile",
        )
        self.assertEqual(
            {"Authorization": "Bearer secret"},
            adapter.build_headers(config, "secret"),
        )
        self.assertEqual(
            "openai_chat",
            adapter.select_protocol("anthropic_messages", config),
        )
        self.assertIn(
            "llama-3.3-70b-versatile",
            adapter.model_catalog_policy(config).fallback_models,
        )

    def test_custom_base_url_is_preserved_by_descriptor_factory(self):
        adapter = PROVIDER_ADAPTERS.create(
            "vercel-ai-gateway",
            base_url="https://gateway.example/v1",
        )
        self.assertEqual("https://gateway.example/v1", adapter.default_base_url())

    def test_compatible_spec_remains_a_small_value_object(self):
        self.assertLessEqual(len(CompatibleProviderSpec.__dataclass_fields__), 10)

    def test_no_auth_catalog_provider_does_not_emit_placeholder_credentials(self):
        adapter = PROVIDER_ADAPTERS.create("mimo-free")
        config = ProviderConfig(
            name="mimo-free",
            base_url=adapter.default_base_url(),
            model="mimo-v2-flash",
        )
        self.assertFalse(adapter.capabilities(config).requires_api_key)
        self.assertEqual({}, adapter.build_headers(config, None))

    def test_anthropic_compatible_specs_use_native_messages_contract(self):
        expected = {spec.name for spec in ANTHROPIC_COMPATIBLE_PROVIDER_SPECS}
        self.assertTrue(expected.issubset(PROVIDER_ADAPTERS.names()))
        adapter = PROVIDER_ADAPTERS.create("minimax")
        self.assertIsInstance(adapter, CatalogAnthropicProviderAdapter)
        config = ProviderConfig(
            name="minimax",
            base_url=adapter.default_base_url(),
            api_keys=("secret",),
            model="MiniMax-M2.5",
        )
        self.assertEqual(
            "anthropic_messages",
            adapter.select_protocol("anthropic_messages", config),
        )
        self.assertEqual("/v1/messages", adapter.resolve_endpoint("chat", config))

    def test_azure_uses_raw_api_key_and_configurable_api_version(self):
        adapter = PROVIDER_ADAPTERS.create(
            "azure",
            base_url="https://demo.openai.azure.com/openai/deployments/coder",
        )
        self.assertIsInstance(adapter, AzureOpenAIProviderAdapter)
        config = ProviderConfig(
            name="azure",
            base_url=adapter.default_base_url(),
            api_keys=("secret",),
            model="gpt-5",
            options={"api_version": "2025-04-01-preview"},
        )
        self.assertEqual({"api-key": "secret"}, adapter.build_headers(config, "secret"))
        self.assertEqual(
            "/chat/completions?api-version=2025-04-01-preview",
            adapter.resolve_endpoint("openai_chat", config),
        )

    def test_codebuddy_adds_required_product_headers(self):
        adapter = PROVIDER_ADAPTERS.create("codebuddy-cn")
        self.assertIsInstance(adapter, CodeBuddyCnProviderAdapter)
        config = ProviderConfig(
            name="codebuddy-cn",
            base_url=adapter.default_base_url(),
            api_keys=("token",),
            model="glm-5.2",
        )
        headers = adapter.build_headers(config, "token")
        self.assertEqual("Bearer token", headers["authorization"])
        self.assertEqual("CLI", headers["X-IDE-Type"])
        self.assertEqual("1", headers["x-codebuddy-request"])


if __name__ == "__main__":
    unittest.main()
