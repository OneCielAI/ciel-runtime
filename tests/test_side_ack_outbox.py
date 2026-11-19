"""One message per side event (user 2026-11-19).

The source used to get three messages: an immediate receipt ACK, the actor's
understanding, then the result. It now gets one: the understanding and the
result together, after processing. Another Ciel session's receipt ACK is still
recognized and settled at admission, so agents on older builds start no loop.
"""
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path

from ciel_runtime_support.side_ack_outbox import RETIRED, prepare, record
from ciel_runtime_support.side_ack_outbox import pending as ack_pending
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
from ciel_runtime_support.side_intake_processing import claim, finish
from ciel_runtime_support.side_reply_connector import RECEIPT_ACK_TEXT
from ciel_runtime_support.side_reply_outbox import prepare as prepare_final
from ciel_runtime_support.side_understanding_outbox import MERGED, save_proposal
from ciel_runtime_support.side_understanding_outbox import prepare as prepare_understanding
from ciel_runtime_support.work_mcp_tools import dispatch_work_tool
from ciel_runtime_support.work_plan_tree import WorkPlanError


CONVERSATION = '256a28fd-f1c3-443a-9782-19405288a8d3'
ROUTE = {'source_kind': 'external_event', 'channel': 'external:default',
         'sender_id': 'http://walkie.invalid/conversations/' + CONVERSATION}
IDENTITY = 'claude:ack'


def walkie_event(text='우선순위가 바뀌었음으로 큐순서 재조절할것'):
    # Shape of Walkie message 134499 (2026-11-16), without its private context.
    return json.dumps({'type': 'com.ciel.walkie.support.message', 'data': {
        'conversation': CONVERSATION, 'message_id': '134499', 'thread_root_id': '131689',
        'reply_to_message_id': '131689', 'event_type': 'message.created', 'text': text,
        'message_context': {'id': '134499', 'thread_task': {'root_id': '131689', 'priority': 1, 'emergency': True}}}},
        ensure_ascii=False)


class OneMessagePerEventTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.inbox = SideIntakeInbox(self.root, IDENTITY)
        self.key = self.inbox.receive(walkie_event(), 'walkie:134499', route=ROUTE)['receipt_id']

    def process(self, understanding='큐 순서를 바꾸라는 요청으로 이해했습니다.'):
        leased = claim(self.root, IDENTITY, 'side')
        self.assertEqual(self.key, leased['receipt_id'])
        decision = {'action': 'answer', 'kind': 'info', 'summary': 's', 'reply': 'r'}
        save_proposal(self.root, IDENTITY, self.key, leased['generation'],
                      {'understanding': understanding, 'decision': decision, 'expected_version': None})
        return leased

    def test_no_receipt_ack_is_owed_or_sent(self):
        self.assertEqual([], ack_pending(self.root, IDENTITY))
        result = prepare(self.root, IDENTITY, self.key)
        self.assertEqual('not_applicable', result['state'])
        self.assertNotIn('arguments', result)
        row = self.inbox._load(self.key)
        self.assertEqual(('not_applicable', RETIRED), (row['external_ack'], row['ack_outbox']['reason']))

    def test_the_source_gets_the_understanding_and_the_result_in_one_message(self):
        leased = self.process()
        # The understanding is not sent on its own.
        alone = prepare_understanding(self.root, IDENTITY, self.key)
        self.assertEqual('not_applicable', alone['state'])
        self.assertNotIn('arguments', alone)
        self.assertEqual(MERGED, self.inbox._load(self.key)['understanding_outbox']['reason'])
        finish(self.root, IDENTITY, self.key, leased['generation'],
               {'action': 'update', 'reply': 'queued', 'final_text': '처리 결과입니다.', 'delivery': 'pending'})
        final = prepare_final(self.root, IDENTITY, self.key)
        self.assertEqual(('submitting', 'walkie', 'walkie_send_message'),
                         (final['state'], final['server'], final['tool']))
        self.assertEqual('큐 순서를 바꾸라는 요청으로 이해했습니다.\n\n처리 결과입니다.', final['arguments']['text'])
        self.assertEqual((CONVERSATION, '134499'),
                         (final['arguments']['conversation_id'], final['arguments']['reply_to_message_id']))

    def test_an_understanding_an_earlier_build_already_sent_is_not_sent_again(self):
        leased = self.process()
        row = self.inbox._load(self.key)
        row['understanding_outbox'] = {'state': 'submitted', 'updated_at': time.time(), 'attempt_id': 'a' * 32}
        self.inbox._save_file(self.inbox._event_path('pending', self.key), row)
        finish(self.root, IDENTITY, self.key, leased['generation'],
               {'action': 'update', 'reply': 'queued', 'final_text': '처리 결과입니다.', 'delivery': 'pending'})
        self.assertEqual('처리 결과입니다.', prepare_final(self.root, IDENTITY, self.key)['arguments']['text'])

    def test_a_marker_an_earlier_build_left_is_settled_without_a_send(self):
        self.inbox.mark_ack_owed(self.key)
        self.assertEqual([self.key], ack_pending(self.root, IDENTITY))
        self.assertEqual('not_applicable', prepare(self.root, IDENTITY, self.key)['state'])
        self.assertEqual([], ack_pending(self.root, IDENTITY))

    def test_a_send_an_earlier_build_started_is_still_closed(self):
        row = self.inbox._load(self.key)
        row['ack_outbox'] = {'state': 'submitting', 'started_at': time.time(), 'attempt_id': 'b' * 32}
        self.inbox._save_file(self.inbox._event_path('pending', self.key), row)
        receipt = record(self.root, IDENTITY, self.key, 'submitted', attempt_id='b' * 32)
        self.assertEqual('submitted', receipt['external_ack'])
        # Closed once; never prepared again.
        self.assertEqual('submitted', prepare(self.root, IDENTITY, self.key)['state'])
        with self.assertRaises(WorkPlanError):
            record(self.root, IDENTITY, self.key, 'submitted', attempt_id='b' * 32)

    def test_another_ciel_receipt_ack_is_settled_at_admission(self):
        # Measured 2026-11-17: 177 of 1,599 inbound Walkie events were another
        # session's receipt ACK, and 118 of those were answered with an ACK.
        legacy = 'ACK — 요청을 받았습니다.\n\n복기: something'
        for index, text in enumerate((RECEIPT_ACK_TEXT, legacy)):
            receipt = self.inbox.receive(walkie_event(text), 'walkie:ack:' + str(index), route=ROUTE)
            row = self.inbox._load(receipt['receipt_id'])
            self.assertEqual('processed', receipt['stage'])
            self.assertEqual(('receipt_ack_inbound', ''), (row['result']['notice'], row['result']['reply']))
        from ciel_runtime_support.side_reply_outbox import pending as reply_pending
        self.assertEqual([], reply_pending(self.root, IDENTITY))

    def test_receipt_ack_starts_no_side_actor(self):
        root = self.root / 'actor'
        inbox = SideIntakeInbox(root, 'claude:actor')
        inbox.receive(walkie_event(RECEIPT_ACK_TEXT), 'walkie:ack-only', route=ROUTE)
        self.assertIsNone(claim(root, 'claude:actor', 'host'))

    def test_claim_settles_a_receipt_ack_an_older_build_left_pending(self):
        root = self.root / 'legacy'
        inbox = SideIntakeInbox(root, 'claude:legacy')
        inbox.receive(walkie_event('first request'), 'walkie:first', route=ROUTE)
        text = walkie_event('ACK — 요청을 받았습니다.\n\n복기: earlier request')
        key = hashlib.sha256(b'walkie:legacy-ack').hexdigest()
        inbox._save_file(inbox._event_path('pending', key), {
            'text': text, 'event_key': 'walkie:legacy-ack', 'digest': hashlib.sha256(text.encode()).hexdigest(),
            'received_at': time.time() - 600, 'stage': 'received', 'item_id': None,
            'external_ack': 'submitted', 'route': dict(ROUTE), 'session_id': 'claude:legacy'})
        claims = [claim(root, 'claude:legacy', owner) for owner in ('host', 'host-2')]
        self.assertEqual([hashlib.sha256(b'walkie:first').hexdigest(), None],
                         [(row or {}).get('receipt_id') for row in claims])
        self.assertEqual('receipt_ack_inbound', inbox._load(key)['result']['notice'])

    def test_router_tool_is_host_only_and_sends_nothing(self):
        config = {'work_mode': True, 'work_side_intake': {'enabled': True},
                  'work_parallel_host': {'token': 'x' * 40}}

        def invoke(state, host=None):
            return dispatch_work_tool(1, 'work_side_ack', {'receipt_id': self.key, 'state': state,
                                                           'session_id': IDENTITY,
                                                           '_host': host or {'token': 'x' * 40}},
                                      load_config=lambda: config, state_dir=self.root,
                                      respond=lambda _id, text, error=False: {'text': text, 'error': error})
        prepared = invoke('prepare')
        self.assertFalse(prepared['error'], prepared)
        self.assertEqual('not_applicable', json.loads(prepared['text'])['state'])
        self.assertNotIn('arguments', json.loads(prepared['text']))
        self.assertTrue(invoke('prepare', {'token': 'wrong' * 8})['error'])


if __name__ == '__main__':
    unittest.main()
