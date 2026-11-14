"""review_delivery sidecar: parallel-review payload across a conversation change.

Reviews are published by the real backend pipeline (reserve, bind, completion
report, idle) from test_work_parallel_finalization, so every attempt and
decision the sidecar reads is the real schema-5 journal's. No live runtime.
"""
import concurrent.futures
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_work_parallel_finalization as fin
from ciel_runtime_support import review_delivery
from ciel_runtime_support import work_parallel_mcp as m
from ciel_runtime_support.work_mcp_tools import dispatch_work_tool
from ciel_runtime_support.work_parallel_finalization import WorkParallelFinalizationStore
from ciel_runtime_support.work_plan_tree import WorkPlanError

ROOT = Path(__file__).resolve().parents[1]
FINALIZATION_COMMIT = '57dbfb3ac9c8d9f1a42e89a038d27e16f4fab409'
# The historical journal writer, run unmodified in its own interpreter.
OLD_JOURNAL_WRITER = '''
import hashlib, json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import ciel_runtime_support
from ciel_runtime_support.work_parallel_finalization import WorkParallelFinalizationStore
store = WorkParallelFinalizationStore(Path(sys.argv[2]))
lease = sys.argv[3]
state, wake = store.wake_prepare(lease, expected_version=store.read()['version'])
state, wake = store.wake_record(lease, wake['attempt_id'], 'appended', expected_version=state['version'])
print(json.dumps({'package': ciel_runtime_support.__file__,
                  'attempt_ref': hashlib.sha256(wake['attempt_id'].encode()).hexdigest()[:16],
                  'knows_sidecar': 'review-delivery' in Path(sys.argv[1], 'ciel_runtime_support',
                                                           'work_parallel_finalization.py').read_text('utf-8')}))
'''


class ReviewDeliveryTests(unittest.TestCase):
    setUp = fin.MainReviewTests.setUp
    call = fin.MainReviewTests.call
    completed = fin.MainReviewTests.completed
    idle = fin.MainReviewTests.idle
    pending_version = fin.MainReviewTests.pending_version
    review = fin.MainReviewTests.review

    def journal(self):
        return WorkParallelFinalizationStore(self.tmp / 'work-parallel-finalization.json')

    def refused(self, pattern, fn, *args, **kwargs):
        with self.assertRaisesRegex(WorkPlanError, pattern):
            fn(*args, **kwargs)

    def sidecar(self):
        return review_delivery.ReviewDeliveryStore.beside(self.journal())

    def published(self, outcome='appended'):
        """One real pending review whose wake attempt recorded `outcome`."""
        reserve, _, _ = self.completed()
        self.idle(reserve['lease_id'])
        lease = reserve['lease_id']
        wake = self.call('event', {'internal': True}, event='review_wake', operation='prepare', lease_id=lease)
        self.call('event', {'internal': True}, event='review_wake', operation='record', lease_id=lease,
                  attempt_id=wake['attempt_id'], state=outcome)
        return lease, wake['attempt_id']

    def wake_tool(self, state, lease, **args):
        config = {'work_mode': True, 'work_side_intake': {'enabled': True}, 'work_parallel_host': {'token': 'x' * 40}}
        with patch.object(m, 'ledger', return_value=self.store):
            result = dispatch_work_tool(1, 'work_side_wake', {'event_id': lease, 'state': state, **args,
                                                              'session_id': 'claude:host', '_host': {'token': 'x' * 40}},
                                        load_config=lambda: config, state_dir=self.tmp / 'state',
                                        respond=lambda _id, text, error=False: {'text': text, 'error': error})
        return result['text'] if result['error'] else json.loads(result['text'])

    def test_where_a_review_was_appended_decides_one_reappend_per_conversation(self):
        lease, attempt = self.published()
        side = self.sidecar()
        # The schema-5 journal is untouched by every sidecar operation.
        journal_before = (self.tmp / 'work-parallel-finalization.json').read_bytes()
        self.assertEqual('unknown', side.status(lease, 'claude:c1')['state'])
        self.refused('stale review wake attempt', side.appended, lease, 'f' * 32, 'claude:c1')
        side.appended(lease, attempt, 'claude:c1', 'uuid-1')
        side.appended(lease, attempt, 'claude:other', 'uuid-2')  # idempotent per attempt: first wins
        self.assertEqual(('here', 'elsewhere'), (side.status(lease, 'claude:c1')['state'],
                                                 side.status(lease, 'claude:c2')['state']))
        self.assertEqual('already_appended', side.reappend_prepare(lease, 'A', 'claude:c1')['state'])
        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            leases = list(pool.map(lambda index: side.reappend_prepare(lease, 'owner-%d' % index, 'claude:c2'), range(8)))
        winners = [row for row in leases if row['state'] == 'submitting']
        self.assertEqual(1, len(winners))
        self.assertEqual(7, sum(row['state'] == 'already_submitting' for row in leases))
        for row in leases:
            if row['state'] != 'submitting':
                self.assertNotIn('attempt_id', row)
        winner = winners[0]
        self.refused('stale review reappend attempt', side.reappend_record, lease, winner['attempt_id'],
                     'intruder', 'appended')
        side.reappend_record(lease, winner['attempt_id'], winner['owner_nonce'], 'appended', receipt='uuid-c2')
        self.assertEqual(('here', 'elsewhere'), (side.status(lease, 'claude:c2')['state'],
                                                 side.status(lease, 'claude:c1')['state']))
        # A denied re-append stored nothing: exactly one later lease for it.
        denied = side.reappend_prepare(lease, 'B', 'claude:c3')
        side.reappend_record(lease, denied['attempt_id'], 'B', 'failed')
        again = side.reappend_prepare(lease, 'C', 'claude:c3')
        self.assertEqual('submitting', again['state'])
        self.assertEqual('already_submitting', side.reappend_prepare(lease, 'D', 'claude:c3')['state'])
        # Read ends it for every conversation.
        side.read(lease)
        for conversation in ('claude:c1', 'claude:c2', 'claude:c3'):
            self.assertEqual('read', side.status(lease, conversation)['state'])
        self.assertEqual('already_read', side.reappend_prepare(lease, 'E', 'claude:c4')['state'])
        self.assertEqual(journal_before, (self.tmp / 'work-parallel-finalization.json').read_bytes())
        raw = json.loads((self.tmp / review_delivery.FILE_NAME).read_text(encoding='utf-8'))
        self.assertEqual(('ciel-runtime/review_delivery', 1), (raw['producer'], raw['schema']))
        self.assertEqual(hashlib.sha256(attempt.encode()).hexdigest()[:16], raw['deliveries'][lease]['attempt_ref'])
        self.assertNotIn(attempt, json.dumps(raw))

    def test_a_new_journal_attempt_or_a_decision_retires_the_sidecar_row(self):
        lease, attempt = self.published('failed')
        side = self.sidecar()
        # A failed journal attempt put nothing in a conversation.
        self.refused('not appended', side.appended, lease, attempt, 'claude:c1')
        wake = self.call('event', {'internal': True}, event='review_wake', operation='prepare', lease_id=lease)
        self.call('event', {'internal': True}, event='review_wake', operation='record', lease_id=lease,
                  attempt_id=wake['attempt_id'], state='appended')
        self.refused('stale review wake attempt', side.appended, lease, attempt, 'claude:c1')
        side.appended(lease, wake['attempt_id'], 'claude:c2')
        self.assertEqual(('here', 'elsewhere'), (side.status(lease, 'claude:c2')['state'],
                                                 side.status(lease, 'claude:c1')['state']))
        # A journal repair that resets the wake to pending (new facts) starts a
        # new attempt later: the row of the old attempt no longer decides anything.
        self.journal()._change(lambda state: state['reviews'][lease]['wake'].update(
            state='pending', attempt_id=None) or {}, self.journal().read()['version'])
        self.assertEqual('unknown', side.status(lease, 'claude:c3')['state'])
        self.assertEqual('unknown', side.reappend_prepare(lease, 'A', 'claude:c3')['state'])
        wake = self.call('event', {'internal': True}, event='review_wake', operation='prepare', lease_id=lease)
        self.call('event', {'internal': True}, event='review_wake', operation='record', lease_id=lease,
                  attempt_id=wake['attempt_id'], state='appended')
        side.appended(lease, wake['attempt_id'], 'claude:c3')
        self.review(lease, 'approve')
        self.assertEqual('decided', side.status(lease, 'claude:c9')['state'])
        self.refused('only an undecided review', side.appended, lease, wake['attempt_id'], 'claude:c9')

    def test_another_producer_or_schema_is_never_written_over(self):
        lease, attempt = self.published()
        path = self.tmp / review_delivery.FILE_NAME
        for foreign in ({'producer': 'someone-else', 'schema': 1, 'version': 3, 'deliveries': {}},
                        {'producer': 'ciel-runtime/review_delivery', 'schema': 2, 'version': 1, 'deliveries': {}}):
            with self.subTest(foreign=foreign):
                path.write_text(json.dumps(foreign), encoding='utf-8')
                before = path.read_bytes()
                side = self.sidecar()
                self.assertEqual('unknown', side.status(lease, 'claude:c1')['state'])
                with self.assertRaisesRegex(WorkPlanError, 'another producer or schema'):
                    side.appended(lease, attempt, 'claude:c1')
                self.assertEqual(before, path.read_bytes())
        path.unlink()

    def test_host_operations_go_through_work_side_wake_with_protocol_two(self):
        lease, attempt = self.published()
        self.assertEqual({'protocol': 2, 'lease_id': lease, 'state': 'unknown'},
                         self.wake_tool('review_status', lease, seen_receipt='claude:c1'))
        self.assertEqual('appended', self.wake_tool('review_appended', lease, attempt_id=attempt,
                                                    seen_receipt='claude:c1', receipt='uuid-1')['state'])
        self.assertEqual('elsewhere', self.wake_tool('review_status', lease, seen_receipt='claude:c2')['state'])
        lease_row = self.wake_tool('review_reappend_prepare', lease, owner_nonce='host-A', seen_receipt='claude:c2')
        self.assertEqual((2, 'submitting'), (lease_row['protocol'], lease_row['state']))
        self.assertEqual('appended', self.wake_tool('review_reappend_appended', lease, attempt_id=lease_row['attempt_id'],
                                                    owner_nonce='host-A', receipt='uuid-2')['state'])
        self.assertEqual('here', self.wake_tool('review_status', lease, seen_receipt='claude:c2')['state'])
        self.assertEqual('read', self.wake_tool('review_read', lease)['state'])
        self.assertIn('invalid side wake state', self.wake_tool('review_nonsense', lease))

    def test_the_historical_journal_writer_ignores_the_sidecar_and_retires_its_row(self):
        # 57dbfb3 is the only old commit with the schema-5 journal. Its own
        # package, unmodified, runs in its own interpreter and writes a new
        # wake attempt; the sidecar bytes are untouched and its row (keyed by the
        # previous attempt) stops deciding anything.
        lease, attempt = self.published('appended')
        side = self.sidecar()
        side.appended(lease, attempt, 'claude:c1')
        # A workspace repair reset the journal wake to pending (the frozen
        # journal's own repair path), so the old writer may prepare again.
        self.journal()._change(lambda state: state['reviews'][lease]['wake'].update(
            state='pending', attempt_id=None) or {}, self.journal().read()['version'])
        sidecar_path = self.tmp / review_delivery.FILE_NAME
        before = sidecar_path.read_bytes()
        done = subprocess.run(['git', '-c', 'core.autocrlf=false', '-c', 'core.eol=lf', 'archive',
                               FINALIZATION_COMMIT, '--', 'ciel_runtime_support'], cwd=ROOT, capture_output=True,
                              check=False)
        if done.returncode:
            self.skipTest('historical journal commit is not in this checkout')
        with tempfile.TemporaryDirectory() as raw:
            old = Path(raw) / 'old'
            old.mkdir()
            with tarfile.open(fileobj=io.BytesIO(done.stdout)) as archive:
                archive.extractall(old, filter='data') if hasattr(tarfile, 'data_filter') else archive.extractall(old)
            run = subprocess.run([sys.executable, '-I', '-c', OLD_JOURNAL_WRITER, str(old.resolve()),
                                  str(self.tmp / 'work-parallel-finalization.json'), lease],
                                 capture_output=True, text=True, encoding='utf-8', timeout=120, check=False)
            self.assertEqual(0, run.returncode, run.stderr)
            report = json.loads(run.stdout.strip().splitlines()[-1])
            self.assertTrue(Path(report['package']).resolve().is_relative_to(old.resolve()))
        self.assertFalse(report['knows_sidecar'])
        self.assertEqual(before, sidecar_path.read_bytes())
        self.assertEqual('appended', self.journal().read()['reviews'][lease]['wake']['state'])
        self.assertNotEqual(hashlib.sha256(attempt.encode()).hexdigest()[:16], report['attempt_ref'])
        self.assertEqual('unknown', side.status(lease, 'claude:c2')['state'])


if __name__ == '__main__':
    unittest.main()
