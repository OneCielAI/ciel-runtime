import unittest
from unittest import mock

from ciel_runtime_support.configuration_cli import (
    ConfigurationCliConfigPorts,
    ConfigurationCliController,
    ConfigurationCliDisplayPorts,
    ConfigurationCliIO,
    ConfigurationCliModelPorts,
    ConfigurationCliProviderPorts,
)


class ConfigurationCliControllerTests(unittest.TestCase):
    def controller(self, *, builtin_advisor=False):
        self.config = {
            "current_provider": "provider",
            "providers": {
                "provider": {
                    "current_model": "model-a",
                    "advisor_model": "",
                }
            },
            "language": "en",
        }
        self.output: list[str] = []
        self.save = mock.Mock()
        self.set_model = mock.Mock(return_value=["model updated"])
        self.set_advisor = mock.Mock(return_value=["advisor updated"])
        self.set_choice = mock.Mock(return_value=["provider updated"])
        return ConfigurationCliController(
            config=ConfigurationCliConfigPorts(
                load=lambda: self.config,
                save=self.save,
                current_provider=lambda config: (
                    config["current_provider"],
                    config["providers"][config["current_provider"]],
                ),
            ),
            provider=ConfigurationCliProviderPorts(
                normalize_choice=lambda value: (
                    value if value == "routed" else None
                ),
                normalize_provider=lambda value: value.strip().lower(),
                panel_rows=lambda _config: (["* Provider"], ["provider"]),
                menu_label=lambda provider, _config: provider.title(),
                set_choice=self.set_choice,
                set_provider=mock.Mock(return_value=["provider updated"]),
                set_base_url=mock.Mock(return_value=["base updated"]),
            ),
            model=ConfigurationCliModelPorts(
                cached_ids=lambda _p, _c: ["model-a", "model-b"],
                alias_for=lambda _p, model: f"alias-{model}",
                read_cache=lambda _p, _c: ["model-a"],
                set_model=self.set_model,
                upstream_ids=lambda _p, _c: ["model-a", "model-b"],
                set_advisor=self.set_advisor,
                advisor_uses_builtin=lambda _p, _c: builtin_advisor,
            ),
            display=ConfigurationCliDisplayPorts(
                log_level_names={20: "INFO", 30: "WARN"},
                log_level_status=lambda: "INFO",
                log_level_name=lambda: "INFO",
                set_log_level=mock.Mock(return_value=["log updated"]),
                languages={"en": "English", "ko": "한국어"},
                web_tools_config_path="web-tools.json",  # type: ignore[arg-type]
            ),
            io=ConfigurationCliIO(output=self.output.append),
        )

    def test_provider_command_lists_and_updates_provider(self):
        controller = self.controller()

        controller.provider_command(None)
        controller.provider_command("routed")

        self.assertTrue(
            any("Available providers" in line for line in self.output)
        )
        self.set_choice.assert_called_once_with("routed")

    def test_model_command_normalizes_add_prefix(self):
        controller = self.controller()

        controller.model_command(["add", "model-b"])

        self.set_model.assert_called_once_with("model-b")
        self.assertIn("model updated", self.output)

    def test_advisor_command_uses_explicit_provider_capability(self):
        controller = self.controller(builtin_advisor=True)

        controller.advisor_model_command(None)

        self.assertTrue(any("built-in /advisor" in line for line in self.output))
        self.set_advisor.assert_not_called()

    def test_language_alias_is_persisted(self):
        controller = self.controller()

        controller.language_command("한국어")

        self.assertEqual("ko", self.config["language"])
        self.save.assert_called_once_with(self.config)

    def test_web_search_and_fetch_mutations_are_persisted(self):
        controller = self.controller()

        controller.web_search_command("off")
        controller.web_fetch_command("ignore-robots-on")

        web = self.config["web_search"]
        self.assertFalse(web["auto_for_non_native"])
        self.assertTrue(web["fetch_ignore_robots_txt"])
        self.assertEqual(2, self.save.call_count)

    def test_log_and_model_list_commands_render_values(self):
        controller = self.controller()

        controller.log_level_command(None)
        controller.models_command(None)

        self.assertTrue(any("log_level: INFO" in line for line in self.output))
        self.assertTrue(any("alias-model-a" in line for line in self.output))


if __name__ == "__main__":
    unittest.main()
