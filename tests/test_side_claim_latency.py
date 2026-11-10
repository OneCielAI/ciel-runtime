import tempfile
import time
import unittest
from pathlib import Path

from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
from ciel_runtime_support.side_intake_processing import claim, fail


class SideClaimLatencyTests(unittest.TestCase):
    """Prody 2026-11-10: 230 never-claimable rows delayed fresh events."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.identity = 'claude:latency'
        self.inbox = SideIntakeInbox(self.root, self.identity)

    def _legacy_quarantine(self, count):
        # The pending shape an older build left: quarantined, never indexed.
        for index in range(count):
            key = self.inbox.receive('old %d' % index, 'old-%d' % index)['receipt_id']
            row = self.inbox._load(key)
            row['side_failure'] = {'category': 'invalid_answer', 'attempt_count': 1, 'failed_at': time.time(),
                                   'retry_not_before': time.time() + 5, 'quarantined': True, 'retryable': False}
            self.inbox._save_file(self.inbox._event_path('pending', key), row)

    def _claims_until(self, receipt_id, limit=10):
        for calls in range(1, limit + 1):
            event = claim(self.root, self.identity, 'actor')
            if event and event['receipt_id'] == receipt_id:
                return calls, event
        return None, None

    def test_invalid_answer_quarantine_is_indexed_and_leaves_the_claim_window(self):
        key = self.inbox.receive('question', 'q')['receipt_id']
        event = claim(self.root, self.identity, 'actor')
        fail(self.root, self.identity, key, event['generation'], {'category': 'invalid_answer'})
        self.assertIn(key, self.inbox.quarantined_keys())
        # The original stays for the pane and the operator.
        self.assertEqual('received', self.inbox._load(key)['stage'])
        self.assertEqual([], self.inbox.pending(100, skip=self.inbox.quarantined_keys()))

    def test_fresh_event_is_claimed_on_the_first_call_behind_a_quarantined_backlog(self):
        self._legacy_quarantine(230)
        # Legacy rows are indexed as claim windows open them, then never reopened.
        for _ in range(4):
            claim(self.root, self.identity, 'actor')
        self.assertEqual(230, len(self.inbox.quarantined_keys()))
        for trial in range(10):
            receipt = self.inbox.receive('fresh %d' % trial, 'fresh-%d' % trial)['receipt_id']
            calls, event = self._claims_until(receipt)
            self.assertEqual(1, calls)
            fail(self.root, self.identity, receipt, event['generation'], {'category': 'invalid_answer'})

    def test_transient_failure_is_not_indexed_and_retries_after_backoff(self):
        key = self.inbox.receive('question', 'q')['receipt_id']
        event = claim(self.root, self.identity, 'actor')
        fail(self.root, self.identity, key, event['generation'], {'category': 'turn_error'})
        self.assertNotIn(key, self.inbox.quarantined_keys())
        self.assertIsNone(claim(self.root, self.identity, 'actor'))
        later = claim(self.root, self.identity, 'actor', now=time.time() + 6)
        self.assertEqual(key, later['receipt_id'])

    def test_unreadable_index_skips_nothing_and_never_blocks_claims(self):
        key = self.inbox.receive('question', 'q')['receipt_id']
        self.inbox.directory.mkdir(parents=True, exist_ok=True)
        (self.inbox.directory / 'quarantine-index.json').write_text('{broken', encoding='utf-8')
        self.assertEqual(set(), self.inbox.quarantined_keys())
        self.assertEqual(key, claim(self.root, self.identity, 'actor')['receipt_id'])


if __name__ == '__main__':
    unittest.main()
