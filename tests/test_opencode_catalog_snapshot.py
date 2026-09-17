import unittest
from copy import deepcopy

import ciel_runtime
from ciel_runtime_support.architecture import ProviderConfig
from ciel_runtime_support.providers.opencode import OpenCodeProviderAdapter
from ciel_runtime_support.providers.opencode_go import OpenCodeGoProviderAdapter
from ciel_runtime_support.providers.opencode_catalog import OPENCODE_GO_MODEL_PROTOCOLS, OPENCODE_ZEN_MODEL_PROTOCOLS


class OpenCodeCatalogTests(unittest.TestCase):
    def test_runtime_applies_union_alpha_profile_from_selected_model(self):
        for provider in ('opencode', 'opencode-go'):
            with self.subTest(provider=provider):
                config = deepcopy(ciel_runtime.DEFAULT_CONFIG['providers'][provider])
                config['current_model'] = 'union-alpha'
                notices = ciel_runtime.apply_provider_model_profile(provider, config)
                self.assertEqual(262144, config['context_window'])
                self.assertEqual(262144, config['max_model_len'])
                self.assertEqual(131072, config['max_output_tokens'])
                self.assertTrue(config['supports_vision'])
                self.assertEqual(1, len(notices))
                self.assertEqual([], ciel_runtime.apply_provider_model_profile(provider, config))

    def test_union_alpha_model_card_applies_to_both_plans(self):
        for adapter in (OpenCodeProviderAdapter(), OpenCodeGoProviderAdapter()):
            with self.subTest(provider=adapter.name):
                config = ProviderConfig(adapter.name, adapter.base_url, 'union-alpha')
                updates, notice = adapter.model_configuration_profile(config)
                self.assertEqual(262144, updates['context_window'])
                self.assertEqual(262144, updates['max_model_len'])
                self.assertEqual(131072, updates['max_output_tokens'])
                self.assertIs(updates['supports_vision'], True)
                self.assertNotIn('effort_level', updates)
                self.assertIn('Union Alpha', notice)
                other = ProviderConfig(adapter.name, adapter.base_url, 'minimax-m3')
                self.assertEqual(({}, None), adapter.model_configuration_profile(other))

    def test_union_alpha_is_available_and_uses_messages_in_both_plans(self):
        for adapter in (OpenCodeProviderAdapter(), OpenCodeGoProviderAdapter()):
            with self.subTest(provider=adapter.name):
                config = ProviderConfig(adapter.name, adapter.base_url, 'union-alpha')
                self.assertIn('union-alpha', adapter.configuration_defaults_value['custom_models'])
                self.assertEqual('anthropic_messages', adapter.documented_model_protocols()['union-alpha'])
                for operation in ('anthropic_messages', 'openai_chat', 'openai_responses'):
                    self.assertEqual('anthropic_messages', adapter.select_protocol(operation, config))
                self.assertTrue(adapter.router_native_anthropic_enabled(config))
                alias = f'ciel-runtime-{adapter.name}-union-alpha'
                self.assertEqual('anthropic_messages', adapter.select_protocol('openai_responses', config, alias))

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
