import unittest
from unittest import mock

import ciel_runtime


class PrelaunchDefaultActionTests(unittest.TestCase):
    def test_all_runtime_defaults_focus_the_combined_launch_menu(self):
        for action in ("launch", "launch-codex", "launch-codex-app-server", "launch-agy", "launch-kimi"):
            self.assertEqual(
                ciel_runtime.MAIN_MENU_ACTIONS.index("launch-menu"),
                ciel_runtime.prelaunch_action_index(action),
            )

    def test_kimi_code_is_default_when_no_shared_runtime_is_remembered(self):
        with mock.patch.object(ciel_runtime, "load_config", return_value={}):
            self.assertEqual("launch-kimi", ciel_runtime.default_prelaunch_action("kimi"))

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

    def test_remembered_kimi_runtime_is_restored_for_kimi_provider(self):
        with mock.patch.object(
            ciel_runtime,
            "load_config",
            return_value={"last_launch_action": "launch-kimi"},
        ):
            self.assertEqual("launch-kimi", ciel_runtime.default_prelaunch_action("kimi"))

    def test_incompatible_remembered_runtime_falls_back_to_provider_default(self):
        with mock.patch.object(
            ciel_runtime,
            "load_config",
            return_value={"last_launch_action": "launch-codex"},
        ):
            self.assertEqual("launch", ciel_runtime.default_prelaunch_action("anthropic"))

    def test_combined_launch_panel_opens_on_remembered_runtime_row(self):
        renders = []
        keys = iter(["enter", "q", "q"])
        actions = [
            "launch",
            "launch-codex",
            "launch-agy",
            "launch-kimi",
            "launch-codex-app-server",
            "back",
        ]

        def render(main_idx, panel, panel_idx, *_args):
            renders.append((main_idx, panel, panel_idx))
            return False

        with (
            mock.patch.object(
                ciel_runtime,
                "load_config",
                return_value={"last_launch_action": "launch-codex-app-server"},
            ),
            mock.patch.object(
                ciel_runtime, "get_current_provider", return_value=("codex", {})
            ),
            mock.patch.object(
                ciel_runtime, "default_prelaunch_action", return_value=actions[4]
            ),
            mock.patch.object(ciel_runtime, "settings_ready_except_api_key", return_value=True),
            mock.patch.object(ciel_runtime, "preflight_lines", return_value=[]),
            mock.patch.object(
                ciel_runtime,
                "launch_panel_rows",
                return_value=([str(action) for action in actions], actions),
            ),
            mock.patch.object(ciel_runtime, "render_prelaunch_screen", side_effect=render),
            mock.patch.object(ciel_runtime, "read_menu_key", side_effect=lambda *_args: next(keys)),
            mock.patch.object(ciel_runtime, "enable_ansi"),
        ):
            result = ciel_runtime.portable_prelaunch_menu([])

        self.assertEqual(ciel_runtime.PRELAUNCH_CANCEL, result)
        self.assertEqual(
            (
                ciel_runtime.MAIN_MENU_ACTIONS.index("launch-menu"),
                "launch-menu",
                4,
            ),
            renders[1],
        )


if __name__ == "__main__":
    unittest.main()
