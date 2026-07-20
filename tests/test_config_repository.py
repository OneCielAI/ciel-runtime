import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.config_repository import (
    ConfigRepositoryProvider,
    build_default_config,
    deep_merge,
    normalize_loaded_config,
)


class ConfigRepositoryTest(unittest.TestCase):
    def test_default_config_embeds_provider_registry(self):
        providers = {"example": {"base_url": "http://example"}}
        config = build_default_config(providers)
        self.assertIs(providers, config["providers"])
        self.assertEqual("nvidia-hosted", config["current_provider"])
        self.assertEqual("llm", config["claude_code"]["channel_delivery"])

    def test_deep_merge_preserves_nested_defaults(self):
        merged = deep_merge(
            {"provider": {"model": "default", "options": ["a"]}},
            {"provider": {"model": "custom"}},
        )
        self.assertEqual("custom", merged["provider"]["model"])
        self.assertEqual(["a"], merged["provider"]["options"])

    def test_normalization_migrates_legacy_key_and_model_ids(self):
        config = {
            "providers": {
                "ollama": {"api_key": "legacy-key"},
                "ollama-cloud": {"api_key": "", "current_model": " MODEL ", "custom_models": [" A ", ""]},
            }
        }
        normalize_loaded_config(config, lambda provider, model: model.strip().lower())
        cloud = config["providers"]["ollama-cloud"]
        self.assertEqual("legacy-key", cloud["api_key"])
        self.assertEqual("model", cloud["current_model"])
        self.assertEqual(["a"], cloud["custom_models"])

    def test_provider_reuses_same_path_and_rebuilds_for_new_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = ConfigRepositoryProvider()
            callbacks = {
                "defaults": {},
                "merge": deep_merge,
                "migrate": lambda config: None,
                "normalize": lambda config: None,
            }
            first = provider.get(path=Path(tmp) / "one.json", **callbacks)
            same = provider.get(path=Path(tmp) / "one.json", **callbacks)
            second = provider.get(path=Path(tmp) / "two.json", **callbacks)
        self.assertIs(first, same)
        self.assertIsNot(first, second)


if __name__ == "__main__":
    unittest.main()
