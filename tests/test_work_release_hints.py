import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.work_application import WorkApplication
from ciel_runtime_support.work_gate_check import digest, event_text, gates
from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices
from ciel_runtime_support.work_mcp_tools import dispatch_work_tool
from ciel_runtime_support.work_plan_store import session_work_plan_store


class WorkReleaseHintTests(unittest.TestCase):
    def test_pending_hold_survives_reload_and_reaches_actor(self):
        doc = tree.empty_document()
        root = tree.add_node(doc, None, 'Goal', refs=['thread-7'])
        node = tree.add_node(doc, root, 'Change')
        tree.update_node(doc, node, status='in_progress')
        tree.update_node(doc, node, status='pending', release_hint={
            'condition': 'CI is green', 'lookup': 'Read job 13', 'prompt': 'Verify the exact SHA'})
        doc = tree.normalize_document(doc)
        rows = gates(doc)
        self.assertEqual([node], [r['id'] for r in rows])
        self.assertIn('Read job 13', event_text(rows, 10))
        self.assertEqual(['thread-7'], rows[0]['refs'])
        pin = digest(rows)
        tree.update_node(doc, node, release_hint={'condition': 'Job B', 'lookup': 'Read job 14'})
        self.assertNotEqual(pin, digest(gates(doc)))
        tree.update_node(doc, node, status='in_progress')
        self.assertFalse(gates(doc))

    def test_mcp_and_application_store_update_and_clear_release_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'state'
            identity = 'claude:rel1'
            store = session_work_plan_store(state, identity)
            app = WorkApplication(store, approval_ask=False)
            res = app.command(
                'plan_set',
                {
                    'goal': 'Ship patch',
                    'refs': ['thread-42'],
                    'children': [{'title': 'Wait CI', 'waiting_on': 'CI run 99', 'release_hint': {
                        'condition': 'CI run 99 succeeds',
                        'lookup': 'gh run view 99 --json conclusion',
                        'prompt': 'Verify conclusion == success before releasing wait',
                    }}],
                    'expected_version': 0,
                },
                'op-plan-set-1',
            )
            self.assertGreater(res['version'], 0, res)
            doc = store.read_snapshot()
            leaf = [n for n in doc['nodes'] if n['parent_id'] is not None][0]
            self.assertEqual('CI run 99 succeeds', leaf['release_hint']['condition'])
            # Clear wait via status/waiting_on change pops release_hint automatically
            cleared = app.command(
                'node_update',
                {'id': leaf['id'], 'waiting_on': '', 'expected_version': doc['version']},
                'op-node-clear-1',
            )
            self.assertGreater(cleared['version'], doc['version'])
            doc2 = store.read_snapshot()
            leaf2 = [n for n in doc2['nodes'] if n['id'] == leaf['id']][0]
            self.assertNotIn('release_hint', leaf2)
            # Re-add on blocked node via dispatch_work_tool
            captured = []

            def respond(req_id, text, is_error=False):
                captured.append((req_id, text, is_error))
                return {'id': req_id, 'text': text, 'isError': is_error}

            upd = dispatch_work_tool(
                1, 'work_node_update',
                {
                    'session_id': identity,
                    'expected_version': doc2['version'],
                    'id': leaf['id'],
                    'status': 'blocked',
                    'waiting_on': 'Review approval',
                    'release_hint': {
                        'condition': 'Reviewer posts LGTM',
                        'lookup': 'Check thread-42 replies after last turn',
                    },
                },
                load_config=lambda: {'work_mode': True},
                state_dir=state,
                respond=respond,
            )
            self.assertFalse(upd['isError'], upd)
            doc3 = store.read_snapshot()
            rows = gates(doc3)
            self.assertEqual([leaf['id']], [r['id'] for r in rows])
            self.assertEqual('Reviewer posts LGTM', rows[0]['release_hint']['condition'])
            self.assertEqual(['thread-42'], rows[0]['refs'])

    def test_rest_http_controller_accepts_and_validates_release_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'state'
            responses = []

            def write_json(_handler, payload, status=200):
                responses.append((status, payload))

            ctl = WorkHttpController(WorkHttpServices(
                state, lambda: {'work_mode': True}, lambda: 'claude:rest1',
                lambda identity: {'session_id': identity}, write_json, 'inst-1'))
            store = session_work_plan_store(state, 'claude:rest1')
            doc, root = store.transaction(lambda d: tree.add_node(d, None, 'Goal'))
            # Add child with release_hint via REST
            ctl.post(object(), '/ca/work/nodes/add', {
                'session_id': 'claude:rest1',
                'expected_version': doc['version'],
                'parent_id': root,
                'title': 'Blocked step',
                'waiting_on': 'deploy token',
                'release_hint': {'condition': 'Token issued', 'lookup': 'Check vault key X'},
            })
            status, payload = responses[-1]
            self.assertEqual(200, status, payload)
            child = [n for n in payload['nodes'] if n['parent_id'] == root][0]
            self.assertEqual('Token issued', child['release_hint']['condition'])
            # Update release_hint via REST
            ctl.post(object(), '/ca/work/nodes/update', {
                'session_id': 'claude:rest1',
                'expected_version': payload['version'],
                'id': child['id'],
                'release_hint': {'condition': 'Token rotated', 'lookup': 'Check vault key Y'},
            })
            status2, payload2 = responses[-1]
            self.assertEqual(200, status2, payload2)
            child2 = [n for n in payload2['nodes'] if n['id'] == child['id']][0]
            self.assertEqual('Token rotated', child2['release_hint']['condition'])


if __name__ == '__main__':
    unittest.main()

