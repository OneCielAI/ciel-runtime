import unittest
from argparse import Namespace
from unittest.mock import Mock

from ciel_runtime_support.provider_option_cli import (
    OllamaOptionCommands,
    ProviderOptionCliConfig,
    ProviderOptionCliController,
    ProviderOptionCommands,
)


class ProviderOptionCliControllerTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "current_provider": "vllm",
            "providers": {
                "ollama": {
                    "base_url": "http://localhost:11434",
                    "current_model": "qwen3",
                },
                "ollama-cloud": {"rate_limit_status": True},
                "vllm": {},
                "opencode": {},
            },
        }
        self.save = Mock()
        self.clear = Mock()
        self.output = Mock()
        self.apply_ollama = Mock()
        self.apply_provider = Mock()
        self.ollama_timeout = Mock(return_value=["ollama timeout adjusted"])
        self.provider_timeout = Mock(return_value=["provider timeout adjusted"])
        self.controller = ProviderOptionCliController(
            ProviderOptionCliConfig(
                load=lambda: self.config,
                save=self.save,
                normalize_provider=lambda value: value,
                clear_model_cache=self.clear,
                output=self.output,
            ),
            OllamaOptionCommands(
                apply=self.apply_ollama,
                apply_timeout=self.ollama_timeout,
                context_status=lambda _config: "auto (32768)",
                rate_usage=lambda _provider, _config: (4, 0),
                options_status=lambda _config: "temperature=0.7",
            ),
            ProviderOptionCommands(
                apply=self.apply_provider,
                cap_context=lambda _provider, _config: ["context capped"],
                cap_output=lambda _provider, _config: ["output capped"],
                apply_timeout=self.provider_timeout,
                status=lambda provider, _config: f"status:{provider}",
            ),
            supported_providers=("vllm", "opencode"),
            ollama_providers=("ollama", "ollama-cloud"),
            provider_notes={"opencode": ("  opencode note",)},
            unsupported_message="unsupported provider",
        )

    def output_lines(self):
        return [call.args[0] for call in self.output.call_args_list]

    def test_native_updates_and_projects_ollama_configuration(self):
        self.controller.native(Namespace(value="off"))

        self.assertFalse(self.config["providers"]["ollama"]["native_compat"])
        self.save.assert_called_once_with(self.config)
        self.clear.assert_not_called()
        self.assertIn("ollama_native_compat: off", self.output_lines())
        self.assertIn("model: qwen3", self.output_lines())

    def test_ollama_options_consumes_provider_and_applies_context_timeout(self):
        self.controller.ollama_options(
            Namespace(values=["ollama-cloud", "num_ctx=auto"])
        )

        provider_config = self.config["providers"]["ollama-cloud"]
        self.apply_ollama.assert_called_once_with(provider_config, "num_ctx=auto")
        self.ollama_timeout.assert_called_once_with("ollama-cloud", provider_config)
        self.save.assert_called_once_with(self.config)
        self.clear.assert_called_once_with()
        self.assertIn("Ollama options updated for ollama-cloud.", self.output_lines())
        self.assertIn("rpm_used: 4/min (unmanaged)", self.output_lines())

    def test_explicit_ollama_timeout_suppresses_recommendation(self):
        self.controller.ollama_options(
            Namespace(values=["num_ctx=65536", "timeout=300000"])
        )

        self.ollama_timeout.assert_not_called()
        self.assertEqual(2, self.apply_ollama.call_count)

    def test_provider_options_runs_mutations_caps_and_provider_notes(self):
        self.controller.provider_options(
            Namespace(values=["opencode", "context_window=65536"])
        )

        provider_config = self.config["providers"]["opencode"]
        self.apply_provider.assert_called_once_with(
            "opencode", provider_config, "context_window=65536"
        )
        self.provider_timeout.assert_called_once_with("opencode", provider_config)
        self.assertIn("context capped", self.output_lines())
        self.assertIn("output capped", self.output_lines())
        self.assertIn("provider timeout adjusted", self.output_lines())
        self.assertIn("  opencode note", self.output_lines())
        self.assertIn("provider_options: status:opencode", self.output_lines())

    def test_unsupported_current_provider_is_rejected(self):
        self.config["current_provider"] = "unknown"

        with self.assertRaisesRegex(SystemExit, "unsupported provider"):
            self.controller.provider_options(Namespace(values=[]))


if __name__ == "__main__":
    unittest.main()
