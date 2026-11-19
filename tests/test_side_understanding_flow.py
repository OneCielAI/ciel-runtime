"""Understanding → board commit → one message to the source (user requirements 2026-11-17, 2026-11-19).

2026-11-19: the receipt ACK, the understanding and the result were three
messages; the source now gets one, the understanding and the result together.

"메시지 수신 ACK, readback을 sse 받은데로 에코하면 안됌. 받은 메시지를 자신의
이해한스타일로 복기하는절차임. 그리고 분석하고 큐를 조절한후에 필요하면
메인스레드에 알리고.. 처리과정을 다시 sse 메시지온거에 적절히 다시 대답해야하는제"
"""
import json
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
from ciel_runtime_support.side_intake_processing import claim, fail
from ciel_runtime_support.side_reply_outbox import FAILURE_TEXT, pending as replies_owed, prepare as reply_prepare
from ciel_runtime_support.work_mcp_tools import dispatch_work_tool
from ciel_runtime_support.work_plan_store import session_work_plan_store

IDENTITY = 'claude:understanding'
CONVERSATION = '256a28fd-f1c3-443a-9782-19405288a8d3'
ROUTE = {'source_kind': 'external_event', 'channel': 'external:default',
         'sender_id': 'http://walkie.invalid/conversations/' + CONVERSATION}
TOKEN = 'u' * 40
MESSAGE = '결제 재시도 버그를 오늘 안에 스테이징에서 고쳐 주세요'


def walkie_event(text=MESSAGE, message_id='200001'):
    return json.dumps({'type': 'com.ciel.walkie.support.message', 'data': {
        'conversation': CONVERSATION, 'message_id': message_id, 'event_type': 'message.created', 'text': text,
        'message_context': {'id': message_id}}}, ensure_ascii=False)


class UnderstandingFlowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = {'work_mode': True, 'work_side_intake': {'enabled': True},
                       'work_parallel_host': {'token': TOKEN}}
        self.inbox = SideIntakeInbox(self.root, IDENTITY)
        self.store = session_work_plan_store(self.root, IDENTITY)
        self.key = self.inbox.receive(walkie_event(), 'walkie:200001', route=ROUTE)['receipt_id']

    def call(self, name, args, host=None):
        reply = dispatch_work_tool(1, name, {**args, 'session_id': IDENTITY, '_host': {'token': TOKEN, **(host or {})}},
                                   load_config=lambda: self.config, state_dir=self.root,
                                   respond=lambda _id, text, error=False: {'text': text, 'error': error})
        return reply

    def claimed(self):
        answer = json.loads(self.call('work_side_claim', {'owner': 'side-1'})['text'])
        self.assertEqual(self.key, answer['claim']['receipt_id'])
        return answer['claim']

    def proposal(self, understanding='결제 재시도 버그 수정 요청으로 이해했습니다. 스테이징에서 오늘 안에 확인이 필요합니다.'):
        return {'understanding': understanding, 'expected_version': self.store.read()['version'],
                'decision': {'action': 'create', 'kind': 'request', 'title': '결제 재시도 버그 수정',
                             'summary': 'payment retry fix on staging today', 'priority': 'P2',
                             'reply': '새 작업으로 등록했습니다.'}}

    def test_one_message_carries_the_understanding_and_the_result(self):
        claim = self.claimed()
        host = {'receipt_id': self.key, 'generation': claim['generation']}
        ack = json.loads(self.call('work_side_ack', {'receipt_id': self.key, 'state': 'prepare'})['text'])
        understood = json.loads(self.call('work_side_understanding', {**self.proposal(), 'state': 'prepare'},
                                          host)['text'])
        # Neither the ACK nor the understanding is sent on its own.
        self.assertEqual(('not_applicable', 'not_applicable'), (ack['state'], understood['state']))
        self.assertNotIn('arguments', ack)
        self.assertNotIn('arguments', understood)
        # The board is unchanged until the decision commits.
        self.assertEqual([], self.store.read()['nodes'])
        resolved = self.call('work_side_resolve', {'decision': {'action': 'answer', 'kind': 'status',
                                                                'summary': 'ignored', 'reply': 'ignored'},
                                                   'expected_version': 0}, {**host, 'work_digest': claim['work_digest']})
        self.assertFalse(resolved['error'], resolved)
        result = json.loads(resolved['text'])['result']
        # The saved proposal is what commits, not a different later payload.
        self.assertEqual(('create', '결제 재시도 버그 수정'), (result['action'], result['title']))
        final = reply_prepare(self.root, IDENTITY, self.key, config=self.config)
        text = final['arguments']['text']
        self.assertTrue(text.startswith(self.proposal()['understanding'] + '\n\n'), text)
        self.assertIn('새 작업으로 등록했습니다.', text)
        self.assertIn(' 등록 · P2', text)
        self.assertNotIn(MESSAGE, text)
        self.assertFalse(text.startswith('ACK'))

    def test_echo_or_ack_understanding_is_refused_and_nothing_commits(self):
        claim = self.claimed()
        host = {'receipt_id': self.key, 'generation': claim['generation']}
        for bad in (MESSAGE, 'ACK — ' + MESSAGE, '', 'ACK 받았습니다'):
            refused = self.call('work_side_understanding', {**self.proposal(bad), 'state': 'prepare'}, host)
            self.assertTrue(refused['error'], bad)
        self.assertIsNone(self.inbox._load(self.key).get('proposal'))
        self.assertEqual([], self.store.read()['nodes'])

    def test_resolve_needs_the_understanding_step_once_a_proposal_exists(self):
        claim = self.claimed()
        host = {'receipt_id': self.key, 'generation': claim['generation']}
        from ciel_runtime_support.side_understanding_outbox import save_proposal
        save_proposal(self.root, IDENTITY, self.key, claim['generation'], self.proposal())
        refused = self.call('work_side_resolve', {'decision': self.proposal()['decision'], 'expected_version': 0},
                            {**host, 'work_digest': claim['work_digest']})
        self.assertTrue(refused['error'])
        self.assertIn('understanding must be prepared', refused['text'])

    def test_a_different_second_proposal_is_refused(self):
        claim = self.claimed()
        host = {'receipt_id': self.key, 'generation': claim['generation']}
        self.call('work_side_understanding', {**self.proposal(), 'state': 'prepare'}, host)
        again = self.call('work_side_understanding', {**self.proposal('다른 이해 내용입니다'), 'state': 'prepare'}, host)
        self.assertTrue(again['error'])
        replay = json.loads(self.call('work_side_understanding', {**self.proposal(), 'state': 'prepare'}, host)['text'])
        self.assertEqual('not_applicable', replay['state'])
        self.assertNotIn('arguments', replay)

    def test_quarantined_event_gets_a_truthful_failure_reply_once(self):
        stamp = 1_000_000.0
        for attempt in range(6):
            leased = claim(self.root, IDENTITY, 'side-x', now=stamp)
            self.assertIsNotNone(leased, attempt)
            fail(self.root, IDENTITY, self.key, leased['generation'], {'category': 'turn_error'}, now=stamp)
            stamp += 400
        self.assertTrue(self.inbox._load(self.key)['side_failure']['quarantined'])
        self.assertIn(self.key, [row['receipt_id'] for row in replies_owed(self.root, IDENTITY)])
        prepared = reply_prepare(self.root, IDENTITY, self.key, config=self.config)
        self.assertEqual(FAILURE_TEXT, prepared['arguments']['text'])
        self.assertFalse(prepared['arguments']['text'].startswith('ACK'))
        self.assertNotIn(MESSAGE, prepared['arguments']['text'])
        self.call('work_side_reply', {'receipt_id': self.key, 'state': 'submitted'},
                  {'attempt_id': prepared['attempt_id']})
        self.assertNotIn(self.key, [row['receipt_id'] for row in replies_owed(self.root, IDENTITY)])
        self.assertNotIn('arguments', reply_prepare(self.root, IDENTITY, self.key, config=self.config))

    def test_a_restart_resumes_the_saved_proposal_without_the_model(self):
        # Plan 2026-11-17: after a restart neither the model runs again nor is
        # the same message sent twice.
        import time
        first = self.claimed()
        self.assertNotIn('proposal', first)
        host = {'receipt_id': self.key, 'generation': first['generation']}
        understood = json.loads(self.call('work_side_understanding', {**self.proposal(), 'state': 'prepare'},
                                          host)['text'])
        self.assertEqual('not_applicable', understood['state'])
        # The session restarts before the decision commits; the lease expires.
        resumed = claim(self.root, IDENTITY, 'side-2', now=time.time() + 1000)
        self.assertEqual(self.key, resumed['receipt_id'])
        self.assertEqual(self.proposal(), resumed['proposal'])
        host = {'receipt_id': self.key, 'generation': resumed['generation']}
        again = json.loads(self.call('work_side_understanding', {**resumed['proposal'], 'state': 'prepare'},
                                     host)['text'])
        self.assertEqual('not_applicable', again['state'])
        self.assertNotIn('arguments', again)
        resolved = self.call('work_side_resolve', {'decision': resumed['proposal']['decision'],
                                                   'expected_version': resumed['proposal']['expected_version']}, host)
        self.assertFalse(resolved['error'], resolved)
        self.assertEqual('create', json.loads(resolved['text'])['result']['action'])
        self.assertEqual(1, len(self.store.read()['nodes']))
        final = reply_prepare(self.root, IDENTITY, self.key, config=self.config)
        # Sent once, after the restart, with the saved understanding leading it.
        self.assertTrue(final['arguments']['text'].startswith(self.proposal()['understanding']))

    def test_a_recheck_decides_again_without_a_second_understanding(self):
        from ciel_runtime_support.side_intake_processing import release_for_recheck
        first = self.claimed()
        host = {'receipt_id': self.key, 'generation': first['generation']}
        self.call('work_side_understanding', {**self.proposal(), 'state': 'prepare'}, host)
        release_for_recheck(self.root, IDENTITY, self.key, first['generation'], 'board changed')
        row = self.inbox._load(self.key)
        self.assertIsNone(row.get('proposal'))
        self.assertEqual('not_applicable', row['understanding_outbox']['state'])
        second = self.claimed()
        self.assertNotIn('proposal', second)
        host = {'receipt_id': self.key, 'generation': second['generation']}
        decided = json.loads(self.call('work_side_understanding',
                                       {**self.proposal('보드가 바뀐 뒤 다시 정리한 이해 내용입니다.'), 'state': 'prepare'},
                                       host)['text'])
        self.assertEqual('not_applicable', decided['state'])
        self.assertNotIn('arguments', decided)

    def test_no_receipt_ack_is_owed_by_claim_or_status(self):
        status = json.loads(self.call('work_side_status', {})['text'])
        self.assertEqual([], status['pending_acks'])
        claimed = json.loads(self.call('work_side_claim', {'owner': 'side-1'})['text'])
        self.assertEqual([], claimed['pending_acks'])


if __name__ == '__main__':
    unittest.main()
