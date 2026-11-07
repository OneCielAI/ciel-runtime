import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ciel_runtime_support.work_parallel import WorkParallelStore
from ciel_runtime_support.work_settings_menu import DESCRIPTIONS, panel_rows, apply, apply_feedback, choices, description


class UnifiedSettingsTests(unittest.TestCase):
    def test_boolean_and_lane_choices_never_require_free_text(self):
        self.assertEqual(['on', 'off', 'back'], choices({'language': 'ko'}, 'work_mode')[1])
        self.assertEqual(['ask', 'auto', 'back'], choices({}, 'work_plan_approval_ask')[1])
        self.assertEqual(['session', 'btw_with_mcp', 'back'], choices({}, 'sse_injection_mode')[1])
        self.assertNotIn('__edit__', choices({}, 'work_parallel_max_active')[1])

    def test_every_description_and_feedback_is_localized(self):
        for key, texts in DESCRIPTIONS.items():
            with self.subTest(key=key):
                self.assertEqual(4, len(texts))
                self.assertTrue(all(texts))
                self.assertEqual(4, len(set(texts)))
        for language, marker in (('en', 'Selected value'), ('ko', '선택 값'),
                                 ('ja', '選択値'), ('zh', '所选值')):
            config = {'language': language}
            with self.subTest(language=language):
                self.assertIn(marker, description(config, 'work_mode', 'on'))
                if language != 'en':
                    self.assertNotEqual(description({'language': 'en'}, 'work_mode'),
                                        description(config, 'work_mode'))
                self.assertNotIn('work_mode', apply_feedback(config, 'work_mode', 'on'))

    def test_plan_approval_and_side_model_wording_is_exact(self):
        english = description({}, 'work_plan_approval_ask')
        self.assertIn('manual user approval', english)
        self.assertIn('approves plans automatically', english)
        self.assertIn('does not mean “no approval”', english)
        self.assertIn('original SSE', description({}, 'work_side_intake_model'))
        self.assertIn('main session', description({}, 'work_side_intake_model'))

    def test_one_panel_one_side_option_and_explanations(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = WorkParallelStore(Path(root) / 'parallel.json')
            config = {'language': 'ko', 'work_mode': False}
            with patch('ciel_runtime_support.work_parallel_mcp.ledger', return_value=ledger):
                labels, keys = panel_rows(config)
                self.assertIn('work_mode', keys)
                self.assertIn('sse_injection_mode', keys)
                self.assertNotIn('work_side_intake_enabled', keys)
                self.assertNotIn('work_side_intake_type', keys)
                self.assertIn('뒤로', labels)
                for key in keys[:-1]:
                    self.assertTrue(description(config, key))
                apply(config, 'sse_injection_mode', 'btw_with_mcp', lambda _: None)
                self.assertFalse(config['work_mode'])
                self.assertTrue(config['work_side_intake']['enabled'])
                self.assertEqual('btw_with_mcp', config['work_side_intake']['type'])
                apply(config, 'sse_injection_mode', 'session', lambda _: None)
                self.assertFalse(config['work_side_intake']['enabled'])
                self.assertEqual('session', config['work_side_intake']['type'])
                with self.assertRaises(ValueError):
                    apply(config, 'sse_injection_mode', 'invalid', lambda _: None)
