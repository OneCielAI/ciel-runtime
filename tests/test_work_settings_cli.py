import argparse
import json
import unittest
from unittest.mock import patch

from ciel_runtime_support.work_live_settings import BASIC
from ciel_runtime_support.work_parallel_menu import KEYS
from ciel_runtime_support.work_settings_cli import command
from ciel_runtime_support.work_settings_menu import panel_rows


class WorkSettingsCliTests(unittest.TestCase):
    def invoke(self, config, action='list', key=None, value=None):
        output = []
        saved = []
        args = argparse.Namespace(action=action, key=key, value=value, json=True)
        with patch('ciel_runtime_support.work_parallel_mcp.ledger') as ledger:
            ledger.return_value.read.return_value = {'policy': {
                'enabled': True, 'paused': False, 'max_active': 5, 'p1_reserved': 1,
                'launches_per_minute': 4, 'max_rework': 2}}
            result = command(lambda: config, lambda c: saved.append(dict(c)), output.append)(args)
        self.assertEqual(result, json.loads(output[0]))
        return result, saved

    def test_every_menu_and_supported_setting_is_listed(self):
        result, _ = self.invoke({})
        keys = {r['key'] for r in result['settings']}
        self.assertTrue(set(BASIC + KEYS).issubset(keys))
        with patch('ciel_runtime_support.work_parallel_mcp.ledger') as ledger:
            ledger.return_value.read.side_effect = OSError('unavailable')
            _, menu = panel_rows({})
        self.assertTrue(set(menu).difference({'back', '__info__'}).issubset(keys))
        self.assertIn('sse_injection_mode', keys)
        self.assertTrue(all(r['description'] for r in result['settings']))

    def test_set_and_read_mode(self):
        config = {}
        result, saved = self.invoke(config, 'set', 'work_mode', 'off')
        self.assertFalse(config['work_mode'])
        self.assertEqual(1, len(saved))
        self.assertFalse(result['settings'][0]['value'])

    def test_injection_mode_updates_one_canonical_setting(self):
        config = {}
        self.invoke(config, 'set', 'sse_injection_mode', 'btw_with_mcp')
        self.assertEqual({'enabled': True, 'type': 'btw_with_mcp'}, config['work_side_intake'])
        self.invoke(config, 'set', 'sse_injection_mode', 'session')
        self.assertFalse(config['work_side_intake']['enabled'])

    def test_new_side_flags_use_shared_menu_and_cli_store(self):
        config = {}
        for key, value in [('context_mode', 'full'), ('context_percent', '30'),
                           ('model', 'inherit'), ('priority_policy', 'adaptive'),
                           ('comparison_enabled', 'on'), ('comparison_model', 'fixture-comparison')]:
            self.invoke(config, 'set', 'work_side_intake_' + key, value)
        self.assertEqual('full', config['work_side_intake']['context_mode'])
        self.assertEqual(30, config['work_side_intake']['context_percent'])
        result, _ = self.invoke(config)
        rows = {r['key']:r for r in result['settings']}
        self.assertTrue(rows['work_side_intake_comparison_enabled']['value'])
        self.assertEqual('fixture-comparison', rows['work_side_intake_comparison_model']['value'])
        self.assertEqual('router_capture_required', rows['work_side_intake_context_mode']['status'])
        with self.assertRaises(ValueError):
            self.invoke(config, 'set', 'work_side_intake_context_percent', '0')
        with self.assertRaises(ValueError):
            self.invoke(config, 'set', 'work_side_intake_priority_policy', 'unrestricted')

    def test_invalid_setting_does_not_save(self):
        with self.assertRaises(ValueError):
            self.invoke({}, 'set', 'work_mode', 'invalid')
        with self.assertRaises(ValueError):
            self.invoke({}, 'get', 'unknown')
