import unittest
from types import SimpleNamespace

from ciel_runtime_support.config_value_codec import positive_int
from ciel_runtime_support.provider_option_status import (
    ProviderContextStatusPorts,
    ProviderContextStatusProjection,
    format_context_tokens,
    format_parameter_count,
)


class ProviderContextStatusProjectionTests(unittest.TestCase):
    def projection(self, strategy):
        return ProviderContextStatusProjection(
            ProviderContextStatusPorts(
                capacity=lambda _provider, _config: 262144,
                context_policy=lambda _provider, _config: SimpleNamespace(
                    settings_strategy=strategy
                ),
                ollama_num_ctx_status=lambda _config: "num_ctx 128K",
                positive_int=positive_int,
            )
        )

    def test_context_and_parameter_formatters_use_compact_units(self):
        self.assertEqual("1M", format_context_tokens(1048576))
        self.assertEqual("128K", format_context_tokens(131072))
        self.assertEqual("1.5B", format_parameter_count(1_500_000_000, positive_int))
        self.assertEqual("", format_parameter_count(None, positive_int))

    def test_standard_status_includes_window_and_reserve(self):
        self.assertEqual(
            "model max 256K; window 128K; reserve 8K",
            self.projection("standard").status(
                "vllm",
                {"context_window": 131072, "context_reserve_tokens": 8192},
            ),
        )

    def test_ollama_and_managed_strategies_use_owned_presentations(self):
        self.assertEqual(
            "model max 256K; num_ctx 128K",
            self.projection("ollama").status("ollama", {}),
        )
        self.assertEqual(
            "managed by Claude Code",
            self.projection("managed").status("anthropic", {}),
        )


if __name__ == "__main__":
    unittest.main()
