import unittest
from unittest.mock import Mock

from ciel_runtime_support.architecture import ProviderContextPolicy
from ciel_runtime_support.context_setup import ContextSetupPorts, ContextSetupService


def positive_int(value):
    try:
        fixed = int(value)
    except (TypeError, ValueError):
        return None
    return fixed if fixed > 0 else None


class ContextSetupServiceTests(unittest.TestCase):
    def service(self, strategy="standard", capacity=262144):
        self.cap_context = Mock(return_value=["context capped"])
        self.cap_output = Mock(return_value=["output capped"])
        self.apply_timeout = Mock(return_value=["timeout applied"])
        return ContextSetupService(
            ContextSetupPorts(
                context_capacity=lambda _provider, _config: capacity,
                context_policy=lambda _provider, _config: ProviderContextPolicy(
                    settings_strategy=strategy
                ),
                positive_int=positive_int,
                format_context=lambda value: f"{value // 1024}K" if value else "unknown",
                ui_text=lambda key, _language: {"back": "Back", "context_setup": "Context setup"}[key],
                pad_cells=lambda value, width: value.ljust(width),
                cap_context=self.cap_context,
                cap_output=self.cap_output,
                apply_timeout=self.apply_timeout,
                context_status=lambda _provider, config: str(
                    config.get("context_window") or config.get("num_ctx_max")
                ),
            )
        )

    def test_mode_values_are_clamped_to_model_capacity(self):
        values = ContextSetupService.mode_values(65536)

        self.assertEqual(32768, values["context-compact"][0])
        self.assertEqual(65536, values["context-balanced"][0])
        self.assertEqual(65536, values["context-project"][0])
        self.assertEqual(65536, values["context-full"][0])

    def test_managed_panel_has_no_mutable_context_modes(self):
        rows, values = self.service(strategy="managed").panel_rows(
            "anthropic", {}, "en"
        )

        self.assertIn("Claude Code manages Anthropic context automatically.", rows)
        self.assertEqual(["__info__", "__info__", "back"], values)

    def test_panel_deduplicates_modes_with_the_same_window(self):
        rows, values = self.service(capacity=65536).panel_rows(
            "vllm", {"context_window": 65536}, "en"
        )

        self.assertEqual(2, len([value for value in values if value.startswith("context-")]))
        self.assertTrue(any(row.startswith("*") for row in rows))

    def test_standard_apply_mutates_owned_fields_then_runs_guards(self):
        config = {}
        messages = self.service().apply(
            "vllm", config, "context-project", "en"
        )

        self.assertEqual(262144, config["context_window"])
        self.assertEqual(8192, config["context_reserve_tokens"])
        self.assertEqual(8192, config["max_output_tokens"])
        self.assertEqual(
            ["context capped", "output capped", "timeout applied"],
            messages[2:],
        )

    def test_ollama_apply_mutates_only_ollama_context_fields(self):
        config = {}
        self.service(strategy="ollama").apply(
            "ollama", config, "context-balanced", "ko"
        )

        self.assertEqual("auto", config["num_ctx"])
        self.assertEqual(131072, config["num_ctx_max"])
        self.assertEqual(65536, config["num_ctx_min"])
        self.assertEqual(4096, config["ollama_options"]["num_predict"])
        self.assertNotIn("context_window", config)


if __name__ == "__main__":
    unittest.main()
