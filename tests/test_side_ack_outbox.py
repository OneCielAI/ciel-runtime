"""Immediate ACK/readback before side processing (user requirement 2026-11-16)."""
import json
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.side_ack_outbox import prepare, record
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
from ciel_runtime_support.side_intake_processing import claim, finish
from ciel_runtime_support.side_reply_outbox import prepare as prepare_final
from ciel_runtime_support.work_mcp_tools import dispatch_work_tool
from ciel_runtime_support.work_plan_tree import WorkPlanError


CONVERSATION = '256a28fd-f1c3-443a-9782-19405288a8d3'
ROUTE = {'source_kind': 'external_event', 'channel': 'external:default',
         'sender_id': 'http://walkie.invalid/conversations/' + CONVERSATION}


def walkie_event(text='우선순위가 바뀌었음으로 큐순서 재조절할것'):
    # Shape of Walkie message 134499 (2026-11-16), without its private context.
    return json.dumps({'type': 'com.ciel.walkie.support.message', 'data': {
        'conversation': CONVERSATION, 'message_id': '134499', 'thread_root_id': '131689',
        'reply_to_message_id': '131689', 'event_type': 'message.created', 'text': text,
        'message_context': {'id': '134499', 'thread_task': {'root_id': '131689', 'priority': 1, 'emergency': True}}}},
        ensure_ascii=False)


class SideAckOutboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.inbox = SideIntakeInbox(self.root, 'claude:ack')
        self.key = self.inbox.receive(walkie_event(), 'walkie:134499', route=ROUTE)['receipt_id']

    def test_received_event_gets_readback_before_claim_and_once(self):
        first = prepare(self.root, 'claude:ack', self.key)
        self.assertEqual(('submitting', 'walkie', 'walkie_send_message'),
                         (first['state'], first['server'], first['tool']))
        self.assertEqual((CONVERSATION, '134499'),
                         (first['arguments']['conversation_id'], first['arguments']['reply_to_message_id']))
        self.assertEqual('ACK — 요청을 받았습니다.\n\n복기: 우선순위가 바뀌었음으로 큐순서 재조절할것',
                         first['arguments']['text'])
        # An ACK in flight is never prepared again.
        self.assertNotIn('arguments', prepare(self.root, 'claude:ack', self.key))
        receipt = record(self.root, 'claude:ack', self.key, 'submitted', attempt_id=first['attempt_id'])
        self.assertEqual('submitted', receipt['external_ack'])
        self.assertEqual('received', self.inbox._load(self.key)['stage'])
        self.assertEqual('submitted', prepare(self.root, 'claude:ack', self.key)['state'])
        with self.assertRaises(WorkPlanError):
            record(self.root, 'claude:ack', self.key, 'submitted', attempt_id=first['attempt_id'])

    def test_ack_and_processing_reply_are_distinct_walkie_sends(self):
        ack = prepare(self.root, 'claude:ack', self.key)
        record(self.root, 'claude:ack', self.key, 'submitted', attempt_id=ack['attempt_id'])
        leased = claim(self.root, 'claude:ack', 'side')
        self.assertEqual(self.key, leased['receipt_id'])
        finish(self.root, 'claude:ack', self.key, leased['generation'],
               {'action': 'update', 'reply': 'queued', 'final_text': 'processing result', 'delivery': 'pending'})
        final = prepare_final(self.root, 'claude:ack', self.key)
        self.assertEqual('processing result', final['arguments']['text'])
        self.assertNotEqual(ack['arguments']['client_id'], final['arguments']['client_id'])
        self.assertEqual('submitted', self.inbox._load(self.key)['external_ack'])

    def test_unknown_ack_outcome_is_not_resent(self):
        ack = prepare(self.root, 'claude:ack', self.key)
        record(self.root, 'claude:ack', self.key, 'uncertain', attempt_id=ack['attempt_id'])
        again = prepare(self.root, 'claude:ack', self.key)
        self.assertEqual('uncertain', again['state'])
        self.assertNotIn('arguments', again)

    def test_event_without_a_source_message_needs_no_ack(self):
        reminder = json.dumps({'type': 'com.ciel.walkie.support.message', 'data': {
            'conversation': CONVERSATION, 'message_id': None, 'event_type': 'agent.pending_reminder',
            'text': 'reminder'}})
        key = self.inbox.receive(reminder, 'walkie:reminder', route=ROUTE)['receipt_id']
        result = prepare(self.root, 'claude:ack', key)
        self.assertEqual('not_applicable', result['state'])
        self.assertEqual('not_applicable', self.inbox._load(key)['external_ack'])

    def test_router_tool_is_host_only_and_records_the_attempt(self):
        config = {'work_mode': True, 'work_side_intake': {'enabled': True},
                  'work_parallel_host': {'token': 'x' * 40}}

        def invoke(state, host=None):
            return dispatch_work_tool(1, 'work_side_ack', {'receipt_id': self.key, 'state': state,
                                                           'session_id': 'claude:ack',
                                                           '_host': host or {'token': 'x' * 40}},
                                      load_config=lambda: config, state_dir=self.root,
                                      respond=lambda _id, text, error=False: {'text': text, 'error': error})
        prepared = invoke('prepare')
        self.assertFalse(prepared['error'], prepared)
        prepared = json.loads(prepared['text'])
        recorded = invoke('submitted', {'token': 'x' * 40, 'attempt_id': prepared['attempt_id']})
        self.assertFalse(recorded['error'], recorded)
        self.assertEqual('submitted', json.loads(recorded['text'])['receipt']['external_ack'])
        refused = invoke('prepare', {'token': 'wrong' * 8})
        self.assertTrue(refused['error'])


if __name__ == '__main__':
    unittest.main()
