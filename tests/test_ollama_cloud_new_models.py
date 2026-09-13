"""Cloud discovery metadata must work without inheriting older model contracts."""
import unittest

from ciel_runtime_support.ollama_thinking import OllamaThinkingPolicy, ollama_cloud_model_config_updates
from ciel_runtime_support.providers.ollama import OllamaCloudProviderAdapter


class CloudNewModelsTests(unittest.TestCase):
    def test_new_model_available_in_offline_defaults(self):
        self.assertIn('deepseek-v4.1-flash', OllamaCloudProviderAdapter().configuration_defaults_value['custom_models'])

    def test_discovered_multimodal_metadata_and_safe_thinking(self):
        model = 'deepseek-v4.1-flash'
        capabilities = ['completion', 'thinking', 'tools', 'vision']
        updates = ollama_cloud_model_config_updates(
            model, architecture='deepseek_v41', capabilities=capabilities,
            context_window=1048576)
        self.assertEqual(updates['codex_model_catalog']['input_modalities'], ['text', 'image'])
        self.assertEqual(updates['ollama_think_levels'], [])
        options = {**updates, 'ollama_model_architecture': 'deepseek_v41',
                   'ollama_model_metadata_model': model, 'ollama_model_capabilities': capabilities}
        self.assertIs(OllamaThinkingPolicy().value(options, model, {}), True)
        self.assertIs(OllamaThinkingPolicy().value(options, model, {'thinking': {'type': 'disabled'}}), False)
