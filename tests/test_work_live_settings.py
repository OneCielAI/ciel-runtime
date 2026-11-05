import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ciel_runtime_support.work_live_settings import settings
from ciel_runtime_support.work_parallel import WorkParallelStore


class LiveSettingsTests(unittest.TestCase):
    def test_off_can_be_reenabled_and_parallel_and_side_apply_live(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = WorkParallelStore(Path(root) / 'ledger.json')
            config = {'work_mode': False, 'language': 'ko'}
            saved = []
            with patch('ciel_runtime_support.work_parallel_mcp.ledger', return_value=ledger):
                for key, value in [('work_mode', 'on'), ('work_auto_continue', 'off'),
                                   ('work_parallel_max_active', '4'), ('work_side_intake_enabled', 'on')]:
                    result = settings(config, {'key': key, 'value': value}, lambda c: saved.append(dict(c)))
                self.assertTrue(config['work_mode'])
                self.assertFalse(config['work_auto_continue'])
                self.assertTrue(config['work_side_intake']['enabled'])
                self.assertEqual(4, ledger.read()['policy']['max_active'])
                self.assertEqual(4, len(saved))
                self.assertIn('work_discovery_instruction', [r['key'] for r in result['settings']])
