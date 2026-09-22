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

    def test_openai_chat_uses_each_catalog_provider_contract_path(self):
        adapter = PROVIDER_ADAPTERS.create("byteplus")
        config = ProviderConfig(
            name="byteplus",
            base_url=adapter.default_base_url(),
            api_keys=("secret",),
            model="seed-2-0-code-preview-260328",
        )

        self.assertEqual(
            "/chat/completions",
            adapter.resolve_endpoint("openai_chat", config),
        )

    def test_alibaba_coding_plan_uses_native_anthropic_endpoint_for_claude(self):
        adapter = PROVIDER_ADAPTERS.create("alicode-intl")
        config = ProviderConfig(
            name="alicode-intl",
            base_url=adapter.default_base_url(),
            api_keys=("secret",),
            model="qwen3.7-plus",
            options={"native_compat": True},
        )

        self.assertEqual(
            "anthropic_messages",
            adapter.select_protocol("anthropic_messages", config),
        )
        self.assertEqual(
            "https://coding-intl.dashscope.aliyuncs.com/apps/anthropic",
            adapter.anthropic_base_url(config),
        )

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


class XiaomiMimoCatalogTests(unittest.TestCase):
    """MiMo-V2.6 (2026-09-21) is part of both Xiaomi provider lineups.

    Ids verified against two clients that talk to api.xiaomimimo.com/v1
    (sdcb/xiaomimimo-for-copilot and LiteLLM's xiaomi_mimo provider), plus
    OpenRouter's xiaomi/mimo-v2.6-* catalog.
    """

    V26 = ("mimo-v2.6-pro", "mimo-v2.6-pro-ultraspeed", "mimo-v2.6-flash")

    def test_both_xiaomi_specs_carry_the_v26_lineup(self):
        specs = {spec.name: spec for spec in COMPATIBLE_PROVIDER_SPECS}

        for name in ("xiaomi-mimo", "xiaomi-tokenplan"):
            with self.subTest(provider=name):
                spec = specs[name]
                self.assertEqual(name, spec.name)
                for model in self.V26:
                    self.assertIn(model, spec.models)
                # The adapter's picker fallback offers the same list.
                adapter = PROVIDER_ADAPTERS.create(name)
                config = ProviderConfig(
                    name=name, base_url=adapter.default_base_url(), api_keys=("k",), model=""
                )
                fallback = adapter.model_catalog_policy(config).fallback_models
                for model in self.V26:
                    self.assertIn(model, fallback)

    def test_the_new_flagship_leads_the_default_model(self):
        specs = {spec.name: spec for spec in COMPATIBLE_PROVIDER_SPECS}

        self.assertEqual("mimo-v2.6-pro", specs["xiaomi-mimo"].models[0])
        self.assertEqual("mimo-v2.6-pro", specs["xiaomi-tokenplan"].models[0])

    def test_existing_configs_gain_the_models_without_losing_their_choice(self):
        import ciel_runtime

        cfg = {
            "providers": {
                "xiaomi-mimo": {
                    "current_model": "mimo-v2.5-pro",
                    "custom_models": ["mimo-v2.5-pro", "mimo-v2.5"],
                },
                "xiaomi-tokenplan": {
                    "current_model": "mimo-v2.5-pro-claude",
                    "custom_models": ["mimo-v2.5-pro-claude"],
                },
            },
            "migrations": {},
        }

        ciel_runtime.apply_config_migrations(cfg)

        mimo = cfg["providers"]["xiaomi-mimo"]
        self.assertEqual("mimo-v2.5-pro", mimo["current_model"])
        for model in self.V26:
            self.assertIn(model, mimo["custom_models"])
        self.assertEqual(1, mimo["custom_models"].count("mimo-v2.5-pro"))
        tokenplan = cfg["providers"]["xiaomi-tokenplan"]
        self.assertEqual("mimo-v2.5-pro-claude", tokenplan["current_model"])
        for model in self.V26:
            self.assertIn(model, tokenplan["custom_models"])
        self.assertTrue(cfg["migrations"]["xiaomi_mimo_v26_catalog_20260921"])
