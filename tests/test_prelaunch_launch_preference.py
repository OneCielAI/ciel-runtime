import unittest

from ciel_runtime_support.prelaunch_launch_preference import (
    preferred_launch_action,
    preferred_provider_launch_action,
    remember_launch_action,
)


class PrelaunchLaunchPreferenceTests(unittest.TestCase):
    def test_restores_last_compatible_launch_action(self):
        config = {"last_launch_action": "launch-codex-app-server"}

        selected = preferred_launch_action(
            config,
            "zai",
            fallback=lambda _provider: "launch-codex",
            supports_claude=lambda _provider: True,
            supports_codex=lambda _provider: True,
        )

        self.assertEqual("launch-codex-app-server", selected)

    def test_restores_claude_instead_of_defaulting_to_codex(self):
        config = {"last_launch_action": "launch"}

        selected = preferred_launch_action(
            config,
            "zai",
            fallback=lambda _provider: "launch-codex",
            supports_claude=lambda _provider: True,
            supports_codex=lambda _provider: True,
        )

        self.assertEqual("launch", selected)

    def test_incompatible_remembered_action_uses_provider_fallback(self):
        config = {"last_launch_action": "launch-codex-app-server"}

        selected = preferred_launch_action(
            config,
            "anthropic",
            fallback=lambda _provider: "launch",
            supports_claude=lambda _provider: True,
            supports_codex=lambda _provider: False,
        )

        self.assertEqual("launch", selected)

    def test_records_only_supported_menu_launch_choices(self):
        config = {}

        self.assertTrue(remember_launch_action(config, "launch-codex"))
        self.assertEqual("launch-codex", config["last_launch_action"])
        self.assertFalse(remember_launch_action(config, "launch-codex"))
        self.assertFalse(remember_launch_action(config, "launch-agy"))
        self.assertEqual("launch-codex", config["last_launch_action"])

    def test_provider_fallback_prefers_agy_for_agy_provider(self):
        selected = preferred_provider_launch_action(
            {},
            "agy",
            supports_agy=lambda provider: provider == "agy",
            supports_claude=lambda _provider: False,
            supports_codex=lambda _provider: False,
        )

        self.assertEqual("launch-agy", selected)


if __name__ == "__main__":
    unittest.main()
