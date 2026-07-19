import unittest

from ciel_runtime_support.architecture import ProviderOptionPresentationPolicy
from ciel_runtime_support.provider_option_panel import (
    OptionPanelPolicy,
    OptionPanelProvider,
    OptionPanelRuntime,
    OptionPanelServices,
    OptionPanelText,
    build_option_panel_rows,
)


class ProviderOptionPanelTests(unittest.TestCase):
    def services(self):
        return OptionPanelServices(
            text=OptionPanelText(
                compact_text=lambda value, _limit: str(value),
                ui_text=lambda key, _language: key,
                context_status=lambda _provider, _config: "context",
                applied_preset=lambda _provider, _config: "balanced",
                preset_text=lambda preset, _language: (preset, ""),
                timeout_status=lambda _config, _language: "auto",
            ),
            runtime=OptionPanelRuntime(
                router_debug_external=lambda: False,
                message_preview_chars=lambda: 0,
                direct_native=lambda _provider, _config: True,
                capability_string=lambda *_args: "",
                current_model=lambda _provider, config: str(config.get("current_model") or "model"),
                workflows_enabled=lambda _provider, _config: False,
                ultracode_enabled=lambda _provider, _config: False,
            ),
            provider=OptionPanelProvider(
                ollama_options=lambda config: config.get("ollama_options", {}),
                ollama_context_status=lambda _config: "auto",
                ollama_think_status=lambda _model, _config: "off",
                query_status=lambda _provider, _config: "empty",
                tool_choice_status=lambda _provider, _config: "auto",
                rate_limit_status=lambda _provider, _config: "off",
                rate_limit_rpm=lambda _provider, _config: "0 (off)",
                ip_family=lambda _provider, _config: "auto",
                parse_bool=lambda value, default=False: default if value is None else bool(value),
            ),
        )

    def test_route_policy_projects_runtime_controls_without_provider_dispatch(self):
        rows, values = build_option_panel_rows(
            "runtime",
            {"route_through_router": True},
            OptionPanelPolicy(
                presentation=ProviderOptionPresentationPolicy(show_route=True),
                context_strategy="managed",
                shows_workflows=False,
                timeout_default="runtime default",
            ),
            self.services(),
            language="en",
        )
        self.assertIn("route_through_router", values)
        self.assertTrue(any("Route through router" in row and "on" in row for row in rows))
        self.assertTrue(any("runtime default" in row for row in rows))

    def test_ollama_strategy_projects_ollama_controls(self):
        _rows, values = build_option_panel_rows(
            "local",
            {"ollama_options": {"num_predict": 4096}},
            OptionPanelPolicy(
                presentation=ProviderOptionPresentationPolicy(),
                context_strategy="ollama",
                shows_workflows=False,
                timeout_default="default",
            ),
            self.services(),
            language="en",
        )
        self.assertIn("num_ctx", values)
        self.assertIn("num_predict", values)
        self.assertIn("keep_alive", values)


if __name__ == "__main__":
    unittest.main()
