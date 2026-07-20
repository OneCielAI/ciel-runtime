from types import SimpleNamespace
import unittest

from ciel_runtime_support.prelaunch_panel_projection import (
    ConfigurationPanelPorts,
    ConfigurationPanelProjection,
    MainMenuProjection,
    MainMenuProjectionPorts,
    ProviderPanelConstants,
    ProviderPanelPorts,
    ProviderPanelProjection,
)


class PrelaunchPanelProjectionTests(unittest.TestCase):
    def test_main_menu_projects_placeholders_and_runtime_compatibility(self):
        projection = MainMenuProjection(
            MainMenuProjectionPorts(
                languages={"en": "English"},
                ui_text=lambda key, _lang: key.replace("_", " ").title(),
                compact_text=lambda value, _limit: str(value),
                provider_label=lambda _p, _c: "Provider Label",
                stored_api_key_mask=lambda _p, _c: "configured",
                llm_options_status=lambda _p, _c: "balanced",
                log_level_status=lambda: "INFO",
                supports_runtime=lambda runtime, _provider: runtime == "codex",
                provider_family=lambda _p, _label: "OpenAI",
                provider_ui_policy=lambda _p, _c: SimpleNamespace(
                    model_placeholder="runtime-owned",
                    advisor_placeholder="not available",
                ),
            )
        )

        rows = projection.rows({}, "provider", {}, "en")

        self.assertIn("[runtime-owned]", rows[4])
        self.assertIn("[not available]", rows[5])
        self.assertIn("disabled: OpenAI provider selected", rows[9])
        self.assertNotIn("disabled:", rows[10])

    def test_provider_panel_projects_native_and_routed_choices(self):
        projection = ProviderPanelProjection(
            ProviderPanelConstants(
                labels={"anthropic": "Anthropic", "custom": "Custom"},
                anthropic_native_choice="anthropic-native",
                anthropic_routed_choice="anthropic-routed",
                agy_native_choice="agy-native",
                agy_routed_choice="agy-routed",
                codex_native_choice="codex-native",
                codex_routed_choice="codex-routed",
            ),
            ProviderPanelPorts(
                anthropic_routed=lambda _p, _c: True,
                agy_routed=lambda _p, _c: False,
                codex_routed=lambda _p, _c: False,
                has_api_key=lambda _p, _c: False,
                compact_text=lambda value, _limit: str(value),
            ),
        )

        rows, values = projection.rows(
            {
                "current_provider": "anthropic",
                "providers": {
                    "anthropic": {"base_url": "https://anthropic"},
                    "custom": {"base_url": "https://custom"},
                },
            }
        )

        self.assertEqual(
            {"anthropic-native", "anthropic-routed", "custom"},
            set(values),
        )
        routed_row = rows[values.index("anthropic-routed")]
        self.assertTrue(routed_row.startswith("*"))

    def configuration_projection(self, *, platform_name="nt"):
        return ConfigurationPanelProjection(
            ConfigurationPanelPorts(
                languages={"en": "English", "ko": "한국어"},
                log_level_names={20: "INFO", 30: "WARN"},
                log_level_name=lambda: "INFO",
                log_level_status=lambda: "INFO",
                ui_text=lambda key, _lang: key.title(),
                compact_text=lambda value, _limit: str(value),
                default_base_url=lambda provider: f"https://{provider}",
                api_key_count=lambda _p, _c: 1,
                platform_name=platform_name,
            )
        )

    def test_configuration_panels_project_language_and_log_levels(self):
        projection = self.configuration_projection()

        language_rows, language_values = projection.language_rows(
            {"language": "ko"}
        )
        log_rows, log_values = projection.log_level_rows({"language": "en"})

        self.assertEqual(["en", "ko"], language_values)
        self.assertTrue(language_rows[1].startswith("*"))
        self.assertEqual(["INFO", "WARN", "DEFAULT", "back"], log_values)
        self.assertTrue(log_rows[0].startswith("*"))

    def test_api_key_and_base_url_panels_preserve_platform_policy(self):
        projection = self.configuration_projection(platform_name="posix")

        api_rows, api_values = projection.api_key_rows("provider", {})
        base_rows, base_values = projection.base_url_rows("provider", {})

        self.assertIn("desktop clipboard", api_rows[4])
        self.assertIn("clear", api_values)
        self.assertIn("https://provider", base_rows[0])
        self.assertEqual(["edit", "default", "back"], base_values)


if __name__ == "__main__":
    unittest.main()
