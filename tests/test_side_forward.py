"""Originals of side events that failed for good reach main (user requirement 2026-11-16)."""
import json
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support import side_notice
from ciel_runtime_support.side_forward import offered
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
from ciel_runtime_support.side_intake_processing import claim, fail
from ciel_runtime_support.work_mcp_tools import dispatch_work_tool


IDENTITY = 'claude:forward'


class SideForwardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.inbox = SideIntakeInbox(self.root, IDENTITY)
        self.config = {'work_mode': True, 'work_side_intake': {'enabled': True},
                       'work_parallel_host': {'token': 'x' * 40}}

    def invoke(self, name, args):
        result = dispatch_work_tool(1, name, {**args, 'session_id': IDENTITY, '_host': {'token': 'x' * 40}},
                                    load_config=lambda: self.config, state_dir=self.root,
                                    respond=lambda _id, text, error=False: {'text': text, 'error': error})
        self.assertFalse(result['error'], result)
        return json.loads(result['text'])

    def quarantine(self, text, category='turn_error'):
        # Celly 2026-11-16: 152 side forks failed with the gateway's pre-dispatch
        # 503; after 6 attempts the event was quarantined and never answered.
        key = self.inbox.receive(text, 'event:' + str(abs(hash(text))))['receipt_id']
        now = 1000.0
        # invalid_answer is terminal at once; the others after 6 attempts.
        for _ in range(1 if category == 'invalid_answer' else 6):
            leased = claim(self.root, IDENTITY, 'side', now=now)
            self.assertEqual(key, leased['receipt_id'])
            receipt = fail(self.root, IDENTITY, key, leased['generation'], {'category': category}, now=now)
            now += 400
        self.assertTrue(receipt['side_failure']['quarantined'])
        return key

    def test_quarantined_event_offers_its_full_original_once(self):
        original = '원문 ' + 'x' * 31000
        key = self.quarantine(original)
        forwards = offered(self.inbox)
        self.assertEqual([key], [row['receipt_id'] for row in forwards])
        self.assertTrue(forwards[0]['text'].endswith('\n\n' + original))
        self.assertIn('turn_error', forwards[0]['text'])
        self.assertIn('6회 시도', forwards[0]['text'])
        claimed = side_notice.prepare(self.inbox, key, 'owner-a', text=forwards[0]['text'][:8000])
        self.assertEqual('claimed', claimed['state'])
        # A second host cannot issue it while the first holds the claim.
        self.assertEqual('already_issuing', side_notice.prepare(self.inbox, key, 'owner-b')['state'])
        self.assertEqual([], offered(self.inbox))
        side_notice.record(self.inbox, key, 'appended', claimed['attempt_id'], owner_nonce='owner-a', receipt='u1')
        self.assertEqual([], offered(self.inbox))
        self.assertEqual('appended', self.inbox._load(key)['release_notice']['state'])
        # The event stays quarantined: it is never claimed by a side fork again.
        self.assertIsNone(claim(self.root, IDENTITY, 'side', now=10_000))

    def test_failed_forward_is_offered_again_with_the_full_original(self):
        original = 'retry me ' + 'y' * 20000
        key = self.quarantine(original, category='invalid_answer')
        text = offered(self.inbox)[0]['text']
        claimed = side_notice.prepare(self.inbox, key, 'owner-a', text=text[:8000])
        side_notice.record(self.inbox, key, 'failed', claimed['attempt_id'], owner_nonce='owner-a')
        again = offered(self.inbox)
        self.assertEqual(key, again[0]['receipt_id'])
        self.assertTrue(again[0]['text'].endswith(original))
        # The truncated claim copy is not offered through the released-wait index.
        self.assertEqual([], side_notice.offered(self.inbox))

    def test_uncertain_forward_waits_for_a_person_then_is_offered_once_more(self):
        key = self.quarantine('uncertain original')
        claimed = side_notice.prepare(self.inbox, key, 'owner-a', text=offered(self.inbox)[0]['text'])
        side_notice.record(self.inbox, key, 'uncertain', claimed['attempt_id'], owner_nonce='owner-a')
        self.assertEqual([], offered(self.inbox))
        pin = side_notice.retry_pin(self.inbox._load(key)['release_notice'])
        side_notice.retry(self.inbox, key, attempt_ref=pin['notice_attempt_ref'],
                          revision=pin['notice_revision'], confirmed=True)
        self.assertEqual([key], [row['receipt_id'] for row in offered(self.inbox)])

    def test_legacy_quarantine_without_forward_marker_is_backfilled(self):
        key = self.inbox.receive('legacy dropped original', 'event:legacy')['receipt_id']
        row = self.inbox._load(key)
        row['side_failure'] = {'category': 'turn_error', 'attempt_count': 6, 'quarantined': True}
        self.inbox._save_file(self.inbox._event_path('pending', key), row)
        self.assertNotIn('main_forward', row)
        rows = offered(self.inbox)
        self.assertEqual([key], [item['receipt_id'] for item in rows])
        self.assertTrue(rows[0]['text'].endswith('legacy dropped original'))
        self.assertTrue(self.inbox._load(key)['main_forward'])

    def test_retryable_failure_is_not_forwarded(self):
        key = self.inbox.receive('still retrying', 'event:retry')['receipt_id']
        leased = claim(self.root, IDENTITY, 'side', now=1000)
        fail(self.root, IDENTITY, key, leased['generation'], {'category': 'turn_error'}, now=1000)
        self.assertEqual([], offered(self.inbox))

    def test_claim_tool_offers_forwards_and_notice_tools_deliver_them(self):
        key = self.quarantine('claim surface original')
        answer = self.invoke('work_side_claim', {'owner': 'side'})
        self.assertEqual([key], [row['receipt_id'] for row in answer['main_forwards']])
        prepared = self.invoke('work_side_wake', {'event_id': key, 'state': 'notice_prepare',
                                                  'owner_nonce': 'host-1', 'text': answer['main_forwards'][0]['text']})
        self.assertEqual('claimed', prepared['state'])
        self.invoke('work_side_wake', {'event_id': key, 'state': 'notice_appended', 'owner_nonce': 'host-1',
                                       'attempt_id': prepared['attempt_id'], 'receipt': 'u-main'})
        self.assertEqual([], self.invoke('work_side_claim', {'owner': 'side'})['main_forwards'])


if __name__ == '__main__':
    unittest.main()
