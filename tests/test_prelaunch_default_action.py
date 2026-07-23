import unittest
from unittest import mock

import ciel_runtime


class PrelaunchDefaultActionTests(unittest.TestCase):
    def test_remembered_claude_is_the_shared_default_for_dual_runtime_provider(self):
        with mock.patch.object(
            ciel_runtime,
            "load_config",
            return_value={"last_launch_action": "launch"},
        ):
            self.assertEqual("launch", ciel_runtime.default_prelaunch_action("kimi"))

    def test_remembered_app_server_is_the_shared_default_for_codex_provider(self):
        with mock.patch.object(
            ciel_runtime,
            "load_config",
            return_value={"last_launch_action": "launch-codex-app-server"},
        ):
            self.assertEqual(
                "launch-codex-app-server",
                ciel_runtime.default_prelaunch_action("kimi"),
            )

    def test_incompatible_remembered_runtime_falls_back_to_provider_default(self):
        with mock.patch.object(
            ciel_runtime,
            "load_config",
            return_value={"last_launch_action": "launch-codex"},
        ):
            self.assertEqual("launch", ciel_runtime.default_prelaunch_action("anthropic"))


if __name__ == "__main__":
    unittest.main()
