import unittest

from ciel_runtime_support import claude_stream_recovery_settings as option
from ciel_runtime_support.config_repository import build_default_config, deep_merge
from ciel_runtime_support.prelaunch import MAIN_MENU_ACTIONS


class ClaudeStreamRecoverySettingsTests(unittest.TestCase):
    def test_default_is_enabled_with_one_attempt(self):
        config = build_default_config({})
        self.assertEqual(
            {"enabled": True, "max_attempts": 1},
            config[option.CONFIG_KEY],
        )
        self.assertTrue(option.enabled(config))
        self.assertEqual(1, option.max_attempts(config))

    def test_deep_merge_preserves_disabled_override_and_default_siblings(self):
        defaults = build_default_config({})
        merged = deep_merge(defaults, {option.CONFIG_KEY: {"enabled": False}})
        self.assertFalse(option.enabled(merged))
        self.assertEqual(1, option.max_attempts(merged))
        self.assertEqual("Claude · recovery off", option.summary(merged))

    def test_attempt_count_is_bounded_and_toggle_is_one_or_two(self):
        for supplied, expected in ((-10, 1), (0, 1), (2, 2), (3, 2), (99, 2), (True, 1), ("2", 1)):
            with self.subTest(supplied=supplied):
                self.assertEqual(expected, option.max_attempts({option.CONFIG_KEY: {"max_attempts": supplied}}))
        config = {option.CONFIG_KEY: {"enabled": True, "max_attempts": 1}}
        option.toggle_attempts(config)
        self.assertEqual(2, option.max_attempts(config))
        option.toggle_attempts(config)
        self.assertEqual(1, option.max_attempts(config))

    def test_panel_has_text_only_safety_row_and_visible_recovery_text(self):
        rows, values = option.panel_rows({})
        self.assertEqual(len(rows), len(values))
        self.assertIn("Claude stream recovery", rows[0])
        self.assertIn("text-only", rows[2])
        self.assertIn("never one with a tool call", rows[2])
        self.assertEqual("  Recent: no mid-response server error seen", rows[3])
        self.assertEqual([option.VALUE_INFO, option.VALUE_INFO], values[2:])
        # Choosing an information row changes nothing, not even the delete prompt sharing the panel.
        config = {}
        self.assertTrue(option.handles(values[2]))
        self.assertEqual([], option.apply(config, values[2]))
        self.assertEqual({}, config)

    def test_recent_row_reports_the_last_decision(self):
        recent = [
            {"at": 0, "error": "e1", "status": "admitted", "reason": "attempt=1/1"},
            {"at": 0, "error": "e2", "status": "skipped", "reason": "tool_use_seen"},
        ]
        row = option.panel_rows({}, recent)[0][3]
        self.assertIn("last skipped (tool_use_seen)", row)
        self.assertIn("1 continued of 2", row)

    def test_recovery_settings_are_reachable_from_existing_visible_settings_menu(self):
        rows, values = option.with_panel_rows(
            ["Auto-allow Claude's critical-path removal prompt  [off]", "Back"],
            ["toggle", "back"],
            {},
        )
        self.assertIn("Claude stream recovery", " ".join(rows))
        self.assertIn(option.VALUE_TOGGLE, values)
        self.assertIn(option.VALUE_ATTEMPTS, values)
        self.assertEqual(len(rows), len(values))
        self.assertEqual("quit", MAIN_MENU_ACTIONS[-1])

    def test_main_menu_opens_recovery_rows_and_saves_both_toggles(self):
        from unittest import mock

        import ciel_runtime

        cfg = build_default_config({})
        renders = []
        # Main menu starts on "dangerous-rm"; Enter opens it, Down x1 reaches the
        # recovery toggle, Down x1 more the attempts toggle, then quit.
        keys = iter(["enter", "down", "enter", "down", "enter", "q", "q"])

        def render(main_idx, panel, panel_idx, panel_rows, *_args):
            renders.append((panel, panel_idx, list(panel_rows)))
            return False

        with (
            mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
            mock.patch.object(ciel_runtime, "get_current_provider", return_value=("anthropic", {})),
            mock.patch.object(ciel_runtime, "settings_ready_except_api_key", return_value=True),
            mock.patch.object(
                ciel_runtime, "prelaunch_action_index", return_value=MAIN_MENU_ACTIONS.index("dangerous-rm")
            ),
            mock.patch.object(ciel_runtime, "preflight_lines", return_value=[]),
            mock.patch.object(ciel_runtime, "save_config") as save,
            mock.patch.object(ciel_runtime, "render_prelaunch_screen", side_effect=render),
            mock.patch.object(ciel_runtime, "read_menu_key", side_effect=lambda *_args: next(keys)),
            mock.patch.object(ciel_runtime, "enable_ansi"),
        ):
            result = ciel_runtime.portable_prelaunch_menu([])

        self.assertEqual(ciel_runtime.PRELAUNCH_CANCEL, result)
        opened = [rows for panel, _idx, rows in renders if panel == "dangerous-rm"]
        self.assertTrue(opened)
        self.assertIn("Claude stream recovery  [on]", opened[0])
        self.assertTrue(any(row.startswith("  Continues only a text-only reply") for row in opened[0]))
        self.assertEqual("Back", opened[0][-1])
        self.assertEqual({"enabled": False, "max_attempts": 2}, cfg[option.CONFIG_KEY])
        self.assertFalse(cfg.get("claude_dangerous_rm_auto_allow", False))
        self.assertEqual(2, save.call_count)


if __name__ == "__main__":
    unittest.main()
