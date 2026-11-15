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

    def test_union_alpha_left_both_live_catalogs(self):
        # Neither /inference/v1/models nor /inference/go/v1/models lists it (2026-11-15).
        for adapter in (OpenCodeProviderAdapter(), OpenCodeGoProviderAdapter()):
            with self.subTest(provider=adapter.name):
                self.assertNotIn('union-alpha', adapter.configuration_defaults_value['custom_models'])
                self.assertNotIn('union-alpha', adapter.documented_model_protocols())

    def test_snapshot_matches_the_live_catalogs_of_2026_11_15(self):
        self.assertEqual(86, len(OPENCODE_ZEN_MODEL_PROTOCOLS))
        self.assertEqual(30, len(OPENCODE_GO_MODEL_PROTOCOLS))
        for model in ('qwen3.6-plus', 'mimo-v2.5', 'mimo-v2.5-pro', 'glm-5.1', 'grok-4.6', 'kimi-k2.6'):
            self.assertNotIn(model, OPENCODE_GO_MODEL_PROTOCOLS)
        self.assertEqual('openai_chat', OPENCODE_GO_MODEL_PROTOCOLS['kimi-k3'])
        self.assertEqual('anthropic_messages', OPENCODE_GO_MODEL_PROTOCOLS['claude-haiku-5-5'])
        self.assertEqual('google_generative', OPENCODE_GO_MODEL_PROTOCOLS['gemini-4-argon'])
        self.assertEqual('openai_responses', OPENCODE_ZEN_MODEL_PROTOCOLS['gpt-6.1-sol'])
        self.assertEqual('openai_chat', OPENCODE_ZEN_MODEL_PROTOCOLS['qwen3.8-max'])
        self.assertEqual('anthropic_messages', OPENCODE_GO_MODEL_PROTOCOLS['qwen3.8-max'])

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
