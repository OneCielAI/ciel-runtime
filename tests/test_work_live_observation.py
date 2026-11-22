import json
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from ciel_runtime_support.work_live_observation import fork_errors, observe, probe


class WorkLiveObservationTests(unittest.TestCase):
    def test_present_board_and_disabled_workflow_are_separate(self):
        sid = 'claude:example'
        data = {
            'board': {'ok': True, 'session_id': sid, 'version': 8, 'nodes': [{'id': 'n1'}], 'root_ids': ['n1'], 'continuation': {'actionable': 1}},
            'workflow': {'ok': True, 'session_id': sid, 'settings': {'enabled': False}, 'goals': [], 'counts': {}},
        }
        result = observe(sid, data.__getitem__)
        self.assertEqual('observed', result['board']['state'])
        self.assertEqual(['n1'], result['board']['node_ids'])
        self.assertEqual('disabled', result['workflow']['state'])
        self.assertEqual('unobserved', result['mcp'])
        self.assertEqual('unobserved', result['terminal'])

    def test_unavailable_or_wrong_scope_is_not_an_empty_board(self):
        result = observe('claude:target', lambda name: {'ok': True, 'session_id': 'claude:other', 'nodes': []})
        self.assertEqual('different_session', result['board']['state'])
        self.assertNotIn('node_ids', result['board'])
        def fail(_):
            raise TimeoutError()
        self.assertEqual('unavailable', observe('claude:target', fail)['board']['state'])

    def test_mcp_timeout_stays_separate_from_successful_rest(self):
        sid = 'claude:target'
        def open_url(req, *, timeout):
            if req.get_method() == 'POST':
                self.assertEqual('work_status', json.loads(req.data)['params']['name'])
                raise TimeoutError()
            return BytesIO(json.dumps({'ok': True, 'session_id': sid, 'version': 2,
                'nodes': [{'id': 'n1'}], 'root_ids': ['n1'], 'settings': {'enabled': True}, 'counts': {}}).encode())
        result = probe(9385, sid, open_url=open_url)
        self.assertEqual('observed', result['board']['state'])
        self.assertEqual('probe_failed', result['mcp']['state'])
        self.assertEqual('TimeoutError', result['mcp']['error'])
        self.assertEqual('unobserved', result['terminal'])

    def test_fork_errors_do_not_leak_result_text_or_include_unowned_files(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'target' / 'subagents'
            directory.mkdir(parents=True)
            for agent, is_fork in [('f1', True), ('other', False)]:
                (directory / f'agent-{agent}.meta.json').write_text(json.dumps({'isFork': is_fork}))
                (directory / f'agent-{agent}.jsonl').write_text(json.dumps({
                    'timestamp': '2026-11-22T01:02:03Z', 'message': {'content': [
                        {'type': 'tool_result', 'is_error': True, 'content': 'private'}]}}) + '\n')
            result = fork_errors(tmp, 'claude:target', '2026-11-22T01:00:00Z')
            self.assertEqual(1, result['error_records'])
            self.assertEqual('f1', result['recent_errors'][0]['agent'])
            self.assertNotIn('private', str(result))

    def test_zero_rows_are_not_evidence_of_an_empty_durable_board(self):
        result = observe('claude:target', lambda _: {'ok': True, 'session_id': 'claude:target', 'version': 0, 'nodes': [], 'root_ids': []})
        self.assertEqual('empty_view', result['board']['state'])


if __name__ == '__main__':
    unittest.main()
