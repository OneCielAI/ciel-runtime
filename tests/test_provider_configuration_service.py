import unittest
from types import SimpleNamespace

from ciel_runtime_support.provider_configuration_service import (
    ProviderEndpointPolicy,
    ProviderEndpointPorts,
    ProviderEndpointService,
    ProviderStatusProjectionPorts,
    ProviderStatusService,
    RuntimeStatusPorts,
)


class ProviderConfigurationServiceTest(unittest.TestCase):
    def test_endpoint_change_resets_model_and_applies_detection(self):
        config = {"providers": {"vllm": {"base_url": "http://old", "current_model": "old", "custom_models": ["old"]}}}
        saved = []
        cleared = []
        service = ProviderEndpointService(
            ProviderEndpointPolicy(frozenset({"vllm"}).__contains__),
            ProviderEndpointPorts(
                load_config=lambda: config,
                save_config=lambda value: saved.append(value),
                clear_model_cache=lambda: cleared.append(True),
                normalize_base_url=lambda provider, pcfg, url: url.rstrip("/"),
                detect_native_compat=lambda provider, pcfg: (True, "probe"),
                ensure_current_model=lambda provider, pcfg, force_refresh: ("model-a", ["selected model-a"]),
            ),
        )

        lines = service.set_base_url("vllm", "http://new/")

        provider = config["providers"]["vllm"]
        self.assertEqual("http://new", provider["base_url"])
        self.assertTrue(provider["native_compat"])
        self.assertEqual("", provider["current_model"])
        self.assertEqual([], provider["custom_models"])
        self.assertEqual(1, len(saved))
        self.assertEqual(1, len(cleared))
        self.assertIn("selected model-a", lines)

    def test_status_projection_uses_adapter_configuration_policy(self):
        config = {"language": "en", "providers": {"codex": {"base_url": "url", "current_model": "gpt"}}}
        policy = SimpleNamespace(uses_ollama_status=False, status_fields=(), runtime_owns_model=True)
        service = ProviderStatusService(
            ProviderStatusProjectionPorts(
                get_current_provider=lambda cfg: ("codex", cfg["providers"]["codex"]),
                mode_label=lambda provider, pcfg: "codex-native",
                direct_native_anthropic=lambda provider, pcfg: False,
                configured_adapter=lambda provider, pcfg: SimpleNamespace(configuration_policy=lambda contract: policy),
                contract_config=lambda provider, pcfg: object(),
                ollama_num_ctx_status=lambda pcfg: "",
                ollama_options_status=lambda pcfg: "",
                ollama_think_status=lambda model, pcfg: "",
                current_upstream_model=lambda provider, pcfg: "upstream",
                current_alias=lambda cfg: "alias",
            ),
            RuntimeStatusPorts(
                load_config=lambda: config,
                log_level_status=lambda: "INFO",
                channel_status_text=lambda cfg: "off",
                channel_delivery_mode=lambda cfg: "turn",
                router_up=lambda: False,
                router_base="http://router",
                config_path="config.json",
            ),
        )

        lines = service.lines()

        self.assertIn("mode: codex-native", lines)
        self.assertIn("claude_model: disabled for native runtime provider", lines)
        self.assertIn("router: down http://router", lines)


if __name__ == "__main__":
    unittest.main()
