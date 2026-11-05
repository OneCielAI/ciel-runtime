import tempfile
import unittest
from pathlib import Path
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
from ciel_runtime_support.side_intake_service import receive, drain
from ciel_runtime_support.work_plan_store import session_work_plan_store
from ciel_runtime_support import work_plan_tree as tree


class InboxTests(unittest.TestCase):
    def test_full_board_receive_survives_restart_and_drains_once(self):
        with tempfile.TemporaryDirectory() as root:
            state = Path(root)
            sid = 'claude:fixture'
            store = session_work_plan_store(state, sid)
            for i in range(100):
                store.transaction(lambda d: tree.add_node(d, None, 'existing'))
            result = receive(state, sid, 'original request', 'event:one')
            self.assertTrue(result['accepted'])
            self.assertTrue(result['board_pending'])
            self.assertIsNone(result['item_id'])
            self.assertEqual('not_sent', result['external_ack'])
            self.assertEqual('original request', SideIntakeInbox(state, sid).pending()[0]['text'])
            again = receive(state, sid, 'original request', 'event:one')
            self.assertTrue(again['duplicate'])
            store.transaction(lambda d: tree.update_node(d, 'n1', status='done', evidence='verified'))
            summaries = drain(state, sid)
            self.assertEqual('board_queued', summaries[0]['stage'])
            count = len(store.read()['nodes'])
            drain(state, sid)
            self.assertEqual(count, len(store.read()['nodes']))
            self.assertFalse(summaries[0]['execution_approved'])

    def test_runtime_receives_when_work_is_off_without_board_mutation(self):
        from ciel_runtime_support.work_side_intake_runtime import runtime_intake
        with tempfile.TemporaryDirectory() as root:
            state = Path(root)
            result = runtime_intake(lambda: {'work_mode': False, 'work_side_intake': {'enabled': True}},
                                    state, 'claude:fixture', 'request', 'event')
            self.assertTrue(result['accepted'])
            self.assertEqual('received', result['stage'])
            self.assertFalse((state / 'work-sessions').exists())

    def test_crash_after_board_commit_before_link_recovers_without_duplicate(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root:
            state = Path(root)
            sid = 'claude:fixture'
            with patch.object(SideIntakeInbox, 'link', side_effect=OSError('simulated crash')):
                with self.assertRaises(OSError):
                    receive(state, sid, 'request', 'event')
            store = session_work_plan_store(state, sid)
            self.assertEqual(1, len(store.read()['nodes']))
            result = receive(state, sid, 'request', 'event')
            self.assertEqual('board_queued', result['stage'])
            self.assertEqual(1, len(store.read()['nodes']))

    def test_receive_without_work_admission_is_durable(self):
        with tempfile.TemporaryDirectory() as root:
            result = receive(Path(root), 'claude:fixture', 'request', 'event', admit=False)
            self.assertEqual('received', result['stage'])
            self.assertTrue(result['accepted'])
            self.assertIsNone(result['item_id'])
            with self.assertRaisesRegex(tree.WorkPlanError, 'different content'):
                receive(Path(root), 'claude:fixture', 'different', 'event', admit=False)
