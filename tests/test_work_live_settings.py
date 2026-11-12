import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ciel_runtime_support.work_live_settings import settings
from ciel_runtime_support.work_parallel import WorkParallelStore


class LiveSettingsTests(unittest.TestCase):
    def test_discovery_file_can_be_set_and_cleared_without_writing_file(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = WorkParallelStore(Path(root) / 'ledger.json')
            config = {'language': 'ko', 'work_discovery': {'enabled': False}}
            path = Path(root) / 'not-created.txt'
            saved = []
            with patch('ciel_runtime_support.work_parallel_mcp.ledger', return_value=ledger):
                result = settings(config, {'key': 'work_discovery_file_path', 'value': str(path)},
                                  lambda c: saved.append(dict(c)))
                row = next(r for r in result['settings'] if r['key'] == 'work_discovery_file_path')
                self.assertEqual(str(path), row['value'])
                self.assertIn('탐색 지시 파일', row['label'])
                self.assertFalse(path.exists())
                self.assertFalse(config['work_discovery']['enabled'])
                settings(config, {'key': 'work_discovery_file_path', 'value': ''},
                         lambda c: saved.append(dict(c)))
                self.assertNotIn('file_path', config['work_discovery'])
                self.assertEqual(2, len(saved))

    def test_off_can_be_reenabled_and_parallel_and_side_apply_live(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = WorkParallelStore(Path(root) / 'ledger.json')
            config = {'work_mode': False, 'language': 'ko'}
            saved = []
            with patch('ciel_runtime_support.work_parallel_mcp.ledger', return_value=ledger):
                for key, value in [('work_mode', 'on'), ('work_auto_continue', 'off'),
                                   ('work_parallel_p2_limit', '4'), ('work_side_intake_enabled', 'on')]:
                    result = settings(config, {'key': key, 'value': value}, lambda c: saved.append(dict(c)))
                self.assertTrue(config['work_mode'])
                self.assertFalse(config['work_auto_continue'])
                self.assertTrue(config['work_side_intake']['enabled'])
                self.assertEqual(4, ledger.read()['policy']['priority_limits']['P2'])
                self.assertEqual(5, ledger.read()['policy']['priority_limits']['P1'])
                self.assertEqual(4, len(saved))
                self.assertIn('work_discovery_instruction', [r['key'] for r in result['settings']])

    def test_claude_agent_view_controls_write_claude_files_not_work_config(self):
        import json
        import os
        with tempfile.TemporaryDirectory() as root:
            ledger = WorkParallelStore(Path(root) / 'ledger.json')
            (Path(root) / '.claude.json').write_text('{"projects":{"x":{"keep":true}}}', encoding='utf8')
            config = {'language': 'ko'}
            saved = []
            with patch('ciel_runtime_support.work_parallel_mcp.ledger', return_value=ledger), \
                 patch.dict(os.environ), patch('pathlib.Path.home', return_value=Path(root)):
                os.environ.pop('CLAUDE_CONFIG_DIR', None)
                os.environ.pop('CLAUDE_CODE_CUSTOM_OAUTH_URL', None)
                before = {row['key']: row for row in settings(config, {}, None)['settings']}
                settings(config, {'key': 'claude_left_arrow_opens_agents', 'value': 'off'}, saved.append)
                result = settings(config, {'key': 'claude_background_agents_enabled', 'value': 'off'}, saved.append)
            self.assertEqual((True, True), (before['claude_left_arrow_opens_agents']['value'],
                                            before['claude_background_agents_enabled']['value']))
            stored = json.loads((Path(root) / '.claude.json').read_text(encoding='utf8'))
            self.assertEqual({'x': {'keep': True}}, stored['projects'])
            self.assertIs(False, stored['leftArrowOpensAgents'])
            user = json.loads((Path(root) / '.claude' / 'settings.json').read_text(encoding='utf8'))
            self.assertIs(True, user['disableAgentView'])
            self.assertEqual([], saved)
            rows = {row['key']: row for row in result['settings']}
            self.assertEqual((False, 'next_claude_session'), (rows['claude_left_arrow_opens_agents']['value'],
                                               rows['claude_left_arrow_opens_agents']['status']))
            self.assertEqual((False, 'next_claude_session'), (rows['claude_background_agents_enabled']['value'],
                                                              rows['claude_background_agents_enabled']['status']))
            self.assertIn('왼쪽 화살표', rows['claude_left_arrow_opens_agents']['label'])
            keys = [row['key'] for row in result['settings']]
            self.assertLess(keys.index('claude_background_agents_enabled'), 10)

    def test_projection_describes_every_editor_and_cached_model_choice(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = WorkParallelStore(Path(root) / 'ledger.json')
            registry = Path(root) / 'model-registry.json'
            registry.write_text('{"providers":{"cielairouter":{"models":["_One","anthropic/claude-opus-5-5"]}}}', encoding='utf8')
            config = {'current_provider':'cielairouter','work_plan_approval_ask':True,
                      'work_side_intake':{'enabled':True,'model':'_One'}}
            with patch('ciel_runtime_support.work_parallel_mcp.ledger', return_value=ledger), \
                 patch('ciel_runtime_support.runtime_paths.CONFIG_DIR', Path(root)):
                result = settings(config, {}, lambda _c: None)
            rows = {row['key']:row for row in result['settings']}
            self.assertTrue(all(row.get('kind') for row in rows.values()))
            self.assertEqual(('enum','ask',['auto','ask']),
                             (rows['work_plan_approval_ask']['kind'],rows['work_plan_approval_ask']['value'],
                              rows['work_plan_approval_ask']['choices']))
            self.assertEqual('boolean',rows['work_mode']['kind'])
            self.assertEqual(['number']*5,[rows[f'work_parallel_p{n}_limit']['kind'] for n in range(1,6)])
            self.assertEqual('text',rows['work_discovery_instruction']['kind'])
            self.assertEqual('json',rows['work_side_intake_reply_connectors']['kind'])
            # Side events are native forks of main: no side model/context row.
            self.assertFalse({'work_side_intake_model','work_side_intake_context_mode',
                              'work_side_intake_context_percent'} & set(rows))
            self.assertFalse([row for row in rows.values() if row.get('kind') == 'model'])
            self.assertIn('native fork inheriting the main model, context, tools and permissions',result['applies'])
            self.assertEqual('cielairouter',result['provider'])
