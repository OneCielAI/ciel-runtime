"""Every side operator response is the bounded public projection, never the raw record.

continue-ctl found that the live response of side-reply-retry was the raw inbox
record (inbox._receipt: the full original text, the claim generation, the
side's reply, context) on REST, web and ctl. team-lead: return only the bounded
redacted projection GET /side uses, plus the new state and pins, and check every
other side operator response. Each response below is scanned for the
fixture's raw original, a credential inside it, its tail past the public recap,
the side's reply text, the claim generation, and every attempt id, owner nonce
and reply token the fixture wrote.
"""
import json
import tempfile
import unittest

from ciel_runtime_support import side_reply_outbox, side_wake
from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
from ciel_runtime_support.side_redaction import redact_text
from ciel_runtime_support.work_plan_store import session_work_plan_store
from test_side_notice_claim import NOTICE_TEXT, Session, rest

CREDENTIAL = 'sk-ant-api03-' + 'Z' * 40
TAIL = 'TAIL-PHRASE-QX9-ONLY-IN-THE-ORIGINAL'
ORIGINAL = 'approval 7 granted. ' + 'context ' * 80 + 'key ' + CREDENTIAL + ' ' + TAIL
REPLY = 'REPLY-TEXT-PRIVATE-77'
REVIEW_FIELDS = {'receipt_id', 'state', 'kind', 'action', 'item_id', 'summary', 'recorded_at', 'reviewed_at',
                 'verdict'}


class LeakSession(Session):
    """test_side_notice_claim.Session with a long original holding a credential."""

    def __init__(self, raw, *, release):
        from pathlib import Path
        self.root = Path(raw)
        self.identity = 'claude:test'
        self.config = {'work_mode': True, 'work_side_intake': {'enabled': True, 'context_mode': 'isolated'},
                       'work_parallel_host': {'token': 'x' * 40}}
        self.store = session_work_plan_store(self.root, self.identity)
        _, self.item = self.store.transaction(lambda d: tree.add_node(d, None, 'report'))
        _, self.gate = self.store.transaction(lambda d: tree.add_node(d, self.item, 'gate', waiting_on='approval 7'))
        SideIntakeInbox(self.root, self.identity).receive(ORIGINAL, 'approval')
        self.claim = self.call('work_side_claim', {'owner': 'actor'})['claim']
        self.event = self.claim['receipt_id']
        decision = {'action': 'update', 'item_id': self.gate, 'summary': 'approval received',
                    'reply': REPLY, 'note': 'approval 7 granted'}
        if release:
            decision['release_waits'] = [{'id': self.gate, 'waiting_on': 'approval 7'}]
        self.result = self.call('work_side_resolve', {'decision': decision, 'expected_version': self.version()},
                                {'receipt_id': self.event, 'generation': self.claim['generation']})['result']


class SideOperatorResponseRedactionTests(unittest.TestCase):
    def setUp(self):
        self.assertNotEqual(CREDENTIAL, redact_text(CREDENTIAL))  # the fixture credential is credential-shaped
        self.secrets = []

    def scan(self, name, session, body, *, detail=False):
        text = json.dumps(body, ensure_ascii=False)
        forbidden = [ORIGINAL, CREDENTIAL, str(session.claim['generation']), 'token-secret', *self.secrets]
        if not detail:
            # Only the receipt detail (GET /side/receipt and side-drain rows)
            # carries the redacted original and the side's decision.
            forbidden += [TAIL, REPLY]
        self.assertEqual([], [secret for secret in forbidden if secret and secret in text], name)

    def listed(self, session, section):
        with rest(session) as call:
            status, page = call('side')
        self.assertEqual(200, status, page)
        return next(row for row in page[section] if row['receipt_id'] == session.event)

    def command(self, session, name, body):
        with rest(session) as call:
            status, answer = call('commands/' + name, body)
        self.assertEqual(200, status, answer)
        return answer

    def test_reply_retry_returns_the_public_receipt_with_state_and_pins(self):
        with tempfile.TemporaryDirectory() as raw:
            session = LeakSession(raw, release=False)
            self.assertEqual('route_unavailable',
                             side_reply_outbox.prepare(session.root, session.identity, session.event)['state'])
            answer = self.command(session, 'side-reply-retry', {
                'receipt_id': session.event, 'expected_state': 'route_unavailable', 'revision': 0})
            self.scan('side-reply-retry', session, answer)
            result = answer['result']
            self.assertEqual(('pending', 1), (result['delivery'], result['reply_revision']))
            for raw_field in ('original', 'processing', 'context', 'result', 'main_fallback', 'route', 'outbox'):
                self.assertNotIn(raw_field, result)
            # The same projection GET /side lists for this receipt.
            listed = self.listed(session, 'receipts')
            self.assertLessEqual(set(result), set(listed))
            self.assertEqual(*[{key: row.get(key) for key in ('delivery', 'reply_revision', 'reply_attempt_ref',
                                                              'notice_attempt_ref', 'notice_revision', 'stage')}
                               for row in (result, listed)])

    def test_notice_retry_returns_no_text_or_attempt(self):
        with tempfile.TemporaryDirectory() as raw:
            session = LeakSession(raw, release=True)
            claim = session.notice('notice_prepare', owner_nonce='notice-owner-secret', text=NOTICE_TEXT)
            session.notice('notice_uncertain', attempt_id=claim['attempt_id'], owner_nonce='notice-owner-secret')
            self.secrets += [claim['attempt_id'], 'notice-owner-secret', NOTICE_TEXT]
            answer = self.command(session, 'side-notice-retry',
                                  {'receipt_id': session.event, 'confirmed': True, **session.notice_pins()})
            self.scan('side-notice-retry', session, answer)

    def test_review_returns_only_the_public_review_fields(self):
        with tempfile.TemporaryDirectory() as raw:
            session = LeakSession(raw, release=False)
            answer = self.command(session, 'side-review', {'receipt_ids': [session.event], 'verdict': 'ok',
                                                           'note': '', 'expected_version': session.version()})
            self.scan('side-review', session, answer)
            for row in answer['result']['reviews']:
                self.assertLessEqual(set(row), REVIEW_FIELDS | {'delivery', 'reply_revision', 'reply_attempt_ref'})
            # Exactly the row GET /side lists after the review, delivery and pins included.
            self.assertEqual([self.listed(session, 'reviews')], answer['result']['reviews'])
            self.assertIn('reply_revision', answer['result']['reviews'][0])

    def test_wake_retry_reappend_retry_and_handoff_return_no_attempt_or_text(self):
        with tempfile.TemporaryDirectory() as raw:
            session = LeakSession(raw, release=False)
            if session.event not in session.store.read().get('side_handoffs', {}):
                self.skipTest('fixture event did not request a handoff')
            wake = side_wake.prepare(session.store, session.event, protocol=2, owner_nonce='wake-owner-secret',
                                     channel='append')
            side_wake.start(session.store, session.event, wake['attempt_id'], 'wake-owner-secret', 'append')
            side_wake.record(session.store, session.event, 'uncertain', wake['attempt_id'],
                             owner_nonce='wake-owner-secret')
            self.secrets += [wake['attempt_id'], 'wake-owner-secret']
            public = side_wake.redact_wake(session.store.read()['side_handoffs'][session.event]['wake'])
            answer = self.command(session, 'side-wake-retry', {
                'event_id': session.event, 'attempt_ref': public['attempt_ref'], 'revision': public['revision'],
                'confirmed': True})
            self.scan('side-wake-retry', session, answer)
            # Delivered now, then the conversation changes and its re-append is unknown.
            again = side_wake.prepare(session.store, session.event, protocol=2, owner_nonce='wake-owner-2',
                                      channel='append')
            side_wake.start(session.store, session.event, again['attempt_id'], 'wake-owner-2', 'append')
            side_wake.record(session.store, session.event, 'appended', again['attempt_id'],
                             owner_nonce='wake-owner-2', receipt='uuid-receipt-secret',
                             conversation_id='claude:conversation-a')
            lease = side_wake.prepare_reappend(session.store, session.event, 'holder-secret', 'claude:conversation-b')
            side_wake.record_reappend(session.store, session.event, lease['attempt_id'], 'holder-secret', 'uncertain')
            self.secrets += [again['attempt_id'], 'wake-owner-2', 'uuid-receipt-secret', 'claude:conversation-a',
                             'claude:conversation-b', lease['attempt_id'], 'holder-secret']
            public = side_wake.redact_wake(session.store.read()['side_handoffs'][session.event]['wake'])
            answer = self.command(session, 'side-reappend-retry', {
                'event_id': session.event, 'reappend_ref': public['reappend_ref'], 'revision': public['revision'],
                'confirmed': True})
            self.scan('side-reappend-retry', session, answer)
            answer = self.command(session, 'side-handoff', {'event_id': session.event, 'answer': '',
                                                            'expected_version': session.version()})
            self.scan('side-handoff', session, answer)

    def test_drain_rows_are_the_redacted_receipt_detail(self):
        with tempfile.TemporaryDirectory() as raw:
            session = LeakSession(raw, release=False)
            answer = self.command(session, 'side-drain', {'limit': 10})
            self.scan('side-drain', session, answer, detail=True)
            self.assertEqual([session.event], [row['receipt_id'] for row in answer['result']['rows']])


if __name__ == '__main__':
    unittest.main()
