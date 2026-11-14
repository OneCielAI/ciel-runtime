"""The prelaunch menu's Work settings write is pinned to what the person saw.

Drives the real portable_prelaunch_menu loop (keys and rendering mocked, the
configuration a temp JSON file) the same way tests/test_claude_stream_recovery_settings.py
does. While the choice panel is open, another writer changes a different
setting; the person's choice must then be refused with "settings changed
elsewhere; reloaded", nothing saved, and the refused value is the default of
the next try (the cursor starts on it; a typed value is the prompt default).
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ciel_runtime_support.prelaunch import MAIN_MENU_ACTIONS
from ciel_runtime_support.work_parallel import WorkParallelStore

RELOADED = "settings changed elsewhere; reloaded"


class PrelaunchWorkSettingsPinTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "config.json"
        self.path.write_text(json.dumps({"work_side_intake": {"max_active": 2}}), encoding="utf-8")
        ledger = mock.patch("ciel_runtime_support.work_parallel_mcp.ledger",
                            return_value=WorkParallelStore(self.root / "work-parallel.json"))
        ledger.start()
        self.addCleanup(ledger.stop)
        from ciel_runtime_support import runtime_paths
        lock = mock.patch.object(runtime_paths, "CONFIG_PATH", self.path)
        lock.start()
        self.addCleanup(lock.stop)

    def load(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, config):
        self.path.write_text(json.dumps(config), encoding="utf-8")

    def elsewhere(self):
        current = self.load()
        current.setdefault("work_side_intake", {})["max_active"] = 5
        self.save(current)

    def run_menu(self, keys, prompts=()):
        import ciel_runtime
        from ciel_runtime_support.work_settings_menu import panel_rows
        renders, prompted = [], []
        sequence = iter(keys)

        def read_key(*_args):
            step = next(sequence)
            if callable(step):
                step()
                return next(sequence)
            return step

        def render(_main_idx, panel, panel_idx, panel_rows_, _checks, messages, *_args):
            renders.append((panel, list(messages), panel_idx))
            return False

        answers = iter(prompts)

        def prompt(label, default="", **_kwargs):
            prompted.append((label, default))
            return next(answers)

        with (
            mock.patch.object(ciel_runtime, "load_config", side_effect=self.load),
            mock.patch.object(ciel_runtime, "save_config", side_effect=self.save),
            mock.patch.object(ciel_runtime, "get_current_provider", return_value=("anthropic", {})),
            mock.patch.object(ciel_runtime, "settings_ready_except_api_key", return_value=True),
            mock.patch.object(ciel_runtime, "prelaunch_action_index",
                              return_value=MAIN_MENU_ACTIONS.index("work-settings")),
            mock.patch.object(ciel_runtime, "preflight_lines", return_value=[]),
            mock.patch.object(ciel_runtime, "render_prelaunch_screen", side_effect=render),
            mock.patch.object(ciel_runtime, "read_menu_key", side_effect=read_key),
            mock.patch.object(ciel_runtime, "prompt_menu_value", side_effect=prompt),
            mock.patch.object(ciel_runtime, "enable_ansi"),
        ):
            ciel_runtime.portable_prelaunch_menu([])
        return renders, prompted, panel_rows(self.load())[1]

    def downs(self, key):
        from ciel_runtime_support.work_settings_menu import panel_rows
        return ["down"] * panel_rows(self.load())[1].index(key)

    def test_a_change_made_elsewhere_refuses_the_menu_write(self):
        # Open Work settings, open work_mode, move to "off", another writer
        # changes max_active, then Enter.
        keys = ["enter", *self.downs("work_mode"), "enter", "down", self.elsewhere, "enter", "q", "q"]
        renders, _, _ = self.run_menu(keys)
        config = self.load()
        self.assertNotIn("work_mode", config)          # the person's stale choice is not saved
        self.assertEqual(5, config["work_side_intake"]["max_active"])  # the other change stands
        self.assertIn([RELOADED], [messages for panel, messages, _ in renders if panel == "work-settings"])

    def test_a_refused_choice_is_the_default_of_the_next_try(self):
        from ciel_runtime_support.work_settings_menu import choices
        off = choices(self.load(), "work_mode")[1].index("off")
        # Refused as above, then open work_mode again and press Enter at once.
        keys = ["enter", *self.downs("work_mode"), "enter", "down", self.elsewhere, "enter",
                "enter", "enter", "q", "q"]
        renders, _, _ = self.run_menu(keys)
        choice_cursors = [index for panel, _, index in renders if panel == "work-setting-choice"]
        self.assertEqual(off, choice_cursors[-1])       # the reopened choice starts on "off"
        self.assertIs(False, self.load()["work_mode"])
        self.assertEqual(5, self.load()["work_side_intake"]["max_active"])

    def test_without_a_concurrent_change_the_choice_is_saved(self):
        keys = ["enter", *self.downs("work_mode"), "enter", "down", "enter", "q", "q"]
        self.run_menu(keys)
        self.assertIs(False, self.load()["work_mode"])

    def test_a_refused_typed_value_is_offered_again_and_then_saved(self):
        key = "work_side_intake_aging_seconds"
        # The numeric choice list ends with Edit, Back; Edit is the last but one.
        from ciel_runtime_support.work_settings_menu import choices
        to_edit = ["down"] * choices(self.load(), key)[1].index("__edit__")
        # On the retry the cursor already starts on Edit, so Enter goes
        # straight to the prompt, which offers the refused "17".
        keys = ["enter", *self.downs(key), "enter", *to_edit, self.elsewhere, "enter",
                "enter", "enter", "q", "q"]
        renders, prompted, _ = self.run_menu(keys, prompts=["17", "17"])
        self.assertEqual([(key, ""), (key, "17")], prompted)
        self.assertEqual(17, self.load()["work_side_intake"]["aging_seconds"])
        self.assertEqual(5, self.load()["work_side_intake"]["max_active"])
        self.assertIn([RELOADED], [messages for panel, messages, _ in renders if panel == "work-settings"])


if __name__ == "__main__":
    unittest.main()
