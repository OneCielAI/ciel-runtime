import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ciel_runtime_support.work_parallel import WorkParallelStore
from ciel_runtime_support.work_settings_menu import panel_rows, apply


class SeparateSettingsTests(unittest.TestCase):
    def test_boolean_and_lane_choices_never_require_free_text(self):
        from ciel_runtime_support.work_settings_menu import choices
        self.assertEqual(['on', 'off', 'back'], choices({'language': 'ko'}, 'work_mode')[1])
        self.assertEqual(['session', 'btw_with_mcp', 'back'], choices({}, 'work_side_intake_type')[1])
        self.assertNotIn('__edit__', choices({}, 'work_parallel_max_active')[1])


    def test_first_screen_panels_are_independent_and_preserve_selected_type(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = WorkParallelStore(Path(root) / 'parallel.json')
            config = {'language': 'ko', 'work_mode': False}
            with patch('ciel_runtime_support.work_parallel_mcp.ledger', return_value=ledger):
                work_labels, work_keys = panel_rows(config)
                side_labels, side_keys = panel_rows(config, side=True)
                self.assertIn('work_mode', work_keys)
                self.assertNotIn('work_mode', side_keys)
                self.assertNotIn('work_side_intake_enabled', work_keys)
                self.assertIn('work_side_intake_type', side_keys)
                self.assertIn('뒤로', side_labels)
                apply(config, 'work_side_intake_type', 'session', lambda _: None)
                apply(config, 'work_side_intake_enabled', 'on', lambda _: None)
                self.assertFalse(config['work_mode'])
                self.assertEqual('session', config['work_side_intake']['type'])
                self.assertTrue(config['work_side_intake']['enabled'])
                self.assertTrue(work_labels)
