import unittest

from ciel_runtime_support.architecture import ProviderConfig
from ciel_runtime_support.providers.opencode import OpenCodeProviderAdapter
from ciel_runtime_support.providers.opencode_go import OpenCodeGoProviderAdapter
from ciel_runtime_support.providers.opencode_catalog import OPENCODE_GO_MODEL_PROTOCOLS, OPENCODE_ZEN_MODEL_PROTOCOLS


class OpenCodeCatalogTests(unittest.TestCase):
    def test_all_documented_routes_and_fallback_choices(self):
        for adapter, catalog in [(OpenCodeProviderAdapter(), OPENCODE_ZEN_MODEL_PROTOCOLS),
                                 (OpenCodeGoProviderAdapter(), OPENCODE_GO_MODEL_PROTOCOLS)]:
            for model, protocol in catalog.items():
                with self.subTest(provider=adapter.name, model=model):
                    config = ProviderConfig(adapter.name, adapter.base_url, model)
                    self.assertEqual(protocol, adapter.select_protocol('anthropic_messages', config, model))
                    self.assertIn(model, adapter.configuration_defaults_value['custom_models'])
                    self.assertEqual(protocol, adapter.select_protocol('anthropic_messages', config, f'ciel-runtime-{adapter.name}-{model}'))

    def test_plan_difference_and_override(self):
        self.assertEqual('openai_chat', OPENCODE_ZEN_MODEL_PROTOCOLS['minimax-m3'])
        self.assertEqual('anthropic_messages', OPENCODE_GO_MODEL_PROTOCOLS['minimax-m3'])
        adapter = OpenCodeGoProviderAdapter()
        config = ProviderConfig(adapter.name, adapter.base_url, 'hy4-preview',
                                options={'model_endpoints': {'hy4-preview': 'responses'}})
        self.assertEqual('openai_responses', adapter.select_protocol('anthropic_messages', config))
