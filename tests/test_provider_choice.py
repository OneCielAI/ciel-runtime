import unittest

from ciel_runtime_support.provider_choice import (
    AGY_ROUTED_PROVIDER_CHOICE,
    ANTHROPIC_ROUTED_PROVIDER_CHOICE,
    CODEX_NATIVE_PROVIDER_CHOICE,
    ProviderChoiceController,
    ProviderChoicePorts,
    normalize_provider_choice,
)


class ProviderChoiceTests(unittest.TestCase):
    def test_aliases_normalize_to_canonical_runtime_choices(self):
        self.assertEqual(ANTHROPIC_ROUTED_PROVIDER_CHOICE, normalize_provider_choice("claude-router"))
        self.assertEqual(AGY_ROUTED_PROVIDER_CHOICE, normalize_provider_choice("antigravity-routed"))
        self.assertEqual(CODEX_NATIVE_PROVIDER_CHOICE, normalize_provider_choice("native-codex"))
        self.assertIsNone(normalize_provider_choice("ollama"))

    def test_runtime_strategy_updates_provider_and_route_mode(self):
        config = {
            "current_provider": "ollama",
            "providers": {"anthropic": {"route_through_router": False}},
        }
        saved = []
        cleared = []
        controller = self._controller(config, saved, cleared, has_key=False)
        lines = controller.select("claude-router")
        self.assertEqual("anthropic", config["current_provider"])
        self.assertTrue(config["providers"]["anthropic"]["route_through_router"])
        self.assertIn("OAuth/API auth headers", lines[-1])
        self.assertEqual([config], saved)
        self.assertEqual([True], cleared)

    def test_standard_provider_delegates_without_loading_runtime_config(self):
        selected = []
        controller = ProviderChoiceController(
            ProviderChoicePorts(
                load_config=lambda: self.fail("standard provider should not load runtime config"),
                save_config=lambda _config: None,
                clear_model_cache=lambda: None,
                provider_has_api_key=lambda _provider, _config: False,
                select_standard_provider=lambda provider: selected.append(provider) or [provider],
            )
        )
        self.assertEqual(["ollama"], controller.select("ollama"))
        self.assertEqual(["ollama"], selected)

    @staticmethod
    def _controller(config, saved, cleared, *, has_key):
        return ProviderChoiceController(
            ProviderChoicePorts(
                load_config=lambda: config,
                save_config=saved.append,
                clear_model_cache=lambda: cleared.append(True),
                provider_has_api_key=lambda _provider, _config: has_key,
                select_standard_provider=lambda provider: [provider],
            )
        )


if __name__ == "__main__":
    unittest.main()
