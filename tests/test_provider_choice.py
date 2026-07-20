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

    def test_standard_selection_applies_adapter_owned_updates_and_status(self):
        config = {"current_provider": "ollama", "providers": {"nvidia-hosted": {"base_url": "localhost"}}}
        saved = []
        cleared = []

        class Adapter:
            @staticmethod
            def selection_config_updates(_contract):
                return {"base_url": "https://integrate.api.nvidia.com/v1"}

            @staticmethod
            def selection_status_lines(_contract):
                return ("adapter status",)

            @staticmethod
            def selection_update_status_lines(_contract, updates):
                return (f"normalized: {updates['base_url']}",)

        controller = ProviderChoiceController(
            ProviderChoicePorts(
                load_config=lambda: config,
                save_config=saved.append,
                clear_model_cache=lambda: cleared.append(True),
                provider_has_api_key=lambda _provider, _config: False,
                configured_adapter=lambda _provider, _config: Adapter(),
                contract_config=lambda provider, provider_config: (provider, dict(provider_config)),
                provider_label=lambda _provider: "NVIDIA hosted",
            )
        )

        lines = controller.select("nvidia-hosted")

        self.assertEqual("nvidia-hosted", config["current_provider"])
        self.assertEqual("https://integrate.api.nvidia.com/v1", config["providers"]["nvidia-hosted"]["base_url"])
        self.assertEqual(1, len(saved))
        self.assertEqual([True], cleared)
        self.assertEqual("adapter status", lines[-2])
        self.assertIn("normalized:", lines[-1])

    @staticmethod
    def _controller(config, saved, cleared, *, has_key):
        return ProviderChoiceController(
            ProviderChoicePorts(
                load_config=lambda: config,
                save_config=saved.append,
                clear_model_cache=lambda: cleared.append(True),
                provider_has_api_key=lambda _provider, _config: has_key,
                configured_adapter=lambda _provider, _config: None,
                contract_config=lambda _provider, _config: None,
                provider_label=lambda provider: provider,
            )
        )


if __name__ == "__main__":
    unittest.main()
