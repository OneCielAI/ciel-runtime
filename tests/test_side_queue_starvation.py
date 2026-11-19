"""Side queue starvation (Andy and KOBE, 2026-11-19).

Agent status echoes filled the side queue (Andy: 2,728 unclaimed events, 1,700
in six hours on four threads). The gate re-check sat unclaimed for 9.6 h and
held every later round; requests waited behind the echoes; ask_main never
reached the router because the mod dropped the field.
"""
import json
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
from ciel_runtime_support.side_intake_processing import claim, finish, supersede_unclaimed
from ciel_runtime_support.side_reply_outbox import prepare
from ciel_runtime_support.side_work_decision import apply, validate
from ciel_runtime_support.work_plan_store import session_work_plan_store

IDENTITY = 'claude:parent'
SIDE_JS = Path(__file__).resolve().parents[1] / 'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js'


def walkie(message, priority):
    return json.dumps({'data': {'conversation': 'c1', 'message_id': message, 'text': 'status echo',
                                'message_context': {'thread_task': {'priority': priority}}}})


class SideQueueStarvationTests(unittest.TestCase):
    def test_a_gate_recheck_is_claimed_before_p1_events(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            inbox = SideIntakeInbox(root, IDENTITY)
            for n in range(5):
                inbox.receive(walkie(str(100 + n), 1), 'echo:%d' % n, route={'source_kind': 'external_event'})
            gate = inbox.receive('gate text', 'gate-check:x', route={'source_kind': 'gate_check'})['receipt_id']
            self.assertEqual(gate, claim(root, IDENTITY, 'side-1')['receipt_id'])

    def test_an_unclaimed_round_is_superseded_and_a_leased_one_is_kept(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            inbox = SideIntakeInbox(root, IDENTITY)
            old = inbox.receive('gate old', 'gate-check:old', route={'source_kind': 'gate_check'})['receipt_id']
            busy = inbox.receive('gate busy', 'gate-check:busy', route={'source_kind': 'gate_check'})['receipt_id']
            leased = claim(root, IDENTITY, 'side-1')['receipt_id']
            other = busy if leased == old else old
            self.assertEqual([leased], supersede_unclaimed(root, IDENTITY, [old, busy]))
            row = inbox._load(other)
            self.assertTrue(row['side_failure']['quarantined'] and row['side_failure']['superseded'])
            # The superseded snapshot is never claimed again.
            self.assertIsNone(claim(root, IDENTITY, 'side-2'))

    def test_no_reply_is_recorded_and_nothing_is_posted(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            inbox = SideIntakeInbox(root, IDENTITY)
            store = session_work_plan_store(root, IDENTITY)
            key = inbox.receive(walkie('200', 3), 'echo:200', route={'source_kind': 'external_event', 'channel': 'external:default'})['receipt_id']
            leased = claim(root, IDENTITY, 'side-1')
            result = apply(store, key, {'action': 'answer', 'kind': 'status', 'summary': 'echo', 'reply': 'recorded only',
                                        'no_reply': True}, store.read()['version'])
            self.assertTrue(result['no_reply'])
            finish(root, IDENTITY, key, leased['generation'], result)
            config = {'work_side_intake': {'enabled': True, 'reply_connectors': [{
                'source': '*', 'receiver_id': 'default', 'server': 'walkie', 'tool': 'reply', 'arguments': {'message': '$reply'}}]}}
            sent = prepare(root, IDENTITY, key, config=config)
            self.assertEqual('not_applicable', sent['state'])
            self.assertNotIn('arguments', sent)

    def test_no_reply_applies_only_to_a_plain_answer(self):
        request = {'action': 'create', 'kind': 'request', 'title': 't', 'summary': 's', 'reply': 'r', 'no_reply': True}
        validate(request)
        self.assertNotIn('no_reply', request)
        asks = {'action': 'answer', 'kind': 'status', 'summary': 's', 'reply': 'r', 'no_reply': True, 'ask_main': True}
        validate(asks)
        self.assertNotIn('no_reply', asks)

    def test_the_mod_keeps_ask_main_and_no_reply(self):
        source = SIDE_JS.read_text(encoding='utf-8')
        fields = source.split('const DECISION_FIELDS = new Set([', 1)[1].split('])', 1)[0]
        for name in ("'ask_main'", "'no_reply'", "'relay'"):
            self.assertIn(name, fields)


if __name__ == '__main__':
    unittest.main()
