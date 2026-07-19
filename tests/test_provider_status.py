import unittest
from unittest import mock

import ciel_runtime


class ProviderStatusTests(unittest.TestCase):
    def _status_lines(self, provider, provider_config):
        cfg = {
            "current_provider": provider,
            "language": "en",
            "providers": {provider: provider_config},
        }
        with (
            mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
            mock.patch.object(ciel_runtime, "provider_mode_label", return_value="router"),
            mock.patch.object(ciel_runtime, "direct_native_anthropic_enabled", return_value=False),
            mock.patch.object(ciel_runtime, "current_alias", return_value="alias-model"),
            mock.patch.object(ciel_runtime, "log_level_status", return_value="INFO"),
            mock.patch.object(ciel_runtime, "channel_status_text", return_value="off"),
            mock.patch.object(ciel_runtime, "channel_delivery_mode", return_value="turn"),
            mock.patch.object(ciel_runtime, "router_up", return_value=False),
        ):
            return ciel_runtime.status_lines()

    def test_openai_compatible_status_fields_are_adapter_owned(self):
        lines = self._status_lines(
            "vllm",
            {
                "base_url": "http://vllm",
                "current_model": "model-a",
                "context_window": 131072,
                "context_reserve_tokens": 8192,
                "max_output_tokens": 4096,
                "request_timeout_ms": 300000,
            },
        )

        self.assertIn("context_window: 131072", lines)
        self.assertIn("context_reserve_tokens: 8192", lines)
        self.assertIn("stream_idle_timeout_ms: auto", lines)

    def test_native_runtime_provider_owns_model_status(self):
        lines = self._status_lines(
            "codex",
            {"base_url": "https://api.openai.com", "current_model": "gpt-5"},
        )

        self.assertIn("claude_model: disabled for native runtime provider", lines)
        self.assertFalse(any(line.startswith("context_window:") for line in lines))


if __name__ == "__main__":
    unittest.main()
