"""Which review projection the CURRENT backend gives main's host.

register.js scheduleParallelWake takes its compatibility branch (lifecycle-only
dedup, no durable attempt) only for a pending review row whose projection has no
wake.state. These tests run the real finalization journal and the real
work_parallel_status projection in a temporary directory and read that field in
every state a pending row can be in, including a row of a schema-1 journal that
had no wake at all. No live runtime and no claude.exe.
"""
import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ciel_runtime_support import work_parallel_mcp as m
from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.work_parallel import WorkParallelError, WorkParallelStore
from ciel_runtime_support.work_parallel_finalization import WorkParallelFinalizationStore
from ciel_runtime_support.work_plan_store import WorkPlanStore

SECRET = 'compat-review-host-' * 3
SCOPE = 'claude:compat-review'


def git(cwd, *args):
    return subprocess.run(['git', '-C', str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


class CompatReviewProjectionTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        # An own repository: git run under the temp directory must never
        # resolve to a repository above it.
        self.repo = self.tmp / 'repo'
        git(self.tmp, 'init', str(self.repo))
        git(self.repo, 'config', 'user.name', 'fixture')
        git(self.repo, 'config', 'user.email', 'fixture@example.invalid')
        (self.repo / 'f').write_text('base')
        git(self.repo, 'add', 'f')
        git(self.repo, 'commit', '-m', 'base')
        self.board = WorkPlanStore(self.tmp / 'work-plan.json')
        doc = self.board.read()
        doc, root = self.board.transaction(lambda d: tree.set_plan(d, {
            'title': 'Goal', 'note': '', 'priority': 'P1', 'children': [
                {'title': 'Item', 'note': '', 'ref': '', 'waiting_on': '', 'children': []}]}, None),
            expected_version=doc['version'])
        self.item = next(n['id'] for n in doc['nodes'] if n['parent_id'] == root)
        self.board.transaction(lambda d: tree.set_phase(d, 'executing', root), expected_version=doc['version'])
        self.store = WorkParallelStore(self.tmp / 'work-parallel.json')
        self.config = {'work_parallel_host': {'token': SECRET, 'capability_ready': True}}
        runtime_patch = patch('ciel_runtime_support.runtime_paths.CONFIG_DIR', self.tmp / 'config')
        runtime_patch.start()
        self.addCleanup(runtime_patch.stop)
        self.journal = self.tmp / 'work-parallel-finalization.json'

    def call(self, name, host=None, **args):
        args.setdefault('expected_version', self.store.read()['version'])
        args['_host'] = {'token': SECRET, **(host or {})}
        with patch.object(m, 'ledger', return_value=self.store):
            return json.loads(m.run('work_parallel_' + name, args, self.board, SCOPE, self.config, None))

    def pending_review(self):
        reserve = self.call('event', {'fork': True, 'workspace_repo': str(self.repo)},
                            event='reserve', item_id=self.item, priority='P1')
        lease = reserve['lease_id']
        self.call('event', {'host_id': 'worker-1'}, event='bind', lease_id=lease)
        (Path(reserve['workspace']['path']) / 'result.txt').write_text('result')
        with patch.object(m, 'ledger', return_value=self.store):
            m.report({'lease_id': lease, 'worker_token': reserve['worker_token'], 'event': 'completion',
                      'plan_version': self.board.read()['version'], 'artifact': 'a' * 64, 'evidence': 'tests pass',
                      'detail': {'summary': 'done', 'coverage': [{'item_id': self.item, 'verified': True}],
                                 'verification': 'tests pass'}}, self.board, self.config)
        self.call('event', {'state': 'idle', 'proof': 'native completed'}, event='reconcile', lease_id=lease)
        return lease

    def projected(self):
        rows = self.call('status', {})['review']['pending']
        self.assertEqual(1, len(rows))
        return rows[0]

    def wake(self, operation, lease, **args):
        return self.call('event', {'internal': True}, event='review_wake', operation=operation, lease_id=lease, **args)

    def test_every_pending_review_row_projects_wake_state(self):
        lease = self.pending_review()
        # A new row: the durable wake exists before any host asked for it.
        self.assertEqual({'state': 'pending', 'attempts': 0}, self.projected()['wake'])
        attempt = self.wake('prepare', lease)
        self.assertEqual('submitting', attempt['state'])
        self.assertEqual({'state': 'submitting', 'attempts': 1}, self.projected()['wake'])
        # The durable fence the host's current-projection path relies on.
        with self.assertRaisesRegex(WorkParallelError, 'review wake already submitted'):
            self.wake('prepare', lease)
        self.wake('record', lease, attempt_id=attempt['attempt_id'], state='queued')
        self.assertEqual({'state': 'queued', 'attempts': 1}, self.projected()['wake'])
        with self.assertRaisesRegex(WorkParallelError, 'review wake already submitted'):
            self.wake('prepare', lease)

    def test_each_recorded_outcome_is_projected(self):
        for outcome in ('appended', 'queued', 'uncertain', 'failed'):
            with self.subTest(outcome=outcome):
                self.setUp()  # A fresh journal, board and repository per outcome.
                lease = self.pending_review()
                attempt = self.wake('prepare', lease)
                self.wake('record', lease, attempt_id=attempt['attempt_id'], state=outcome)
                self.assertEqual({'state': outcome, 'attempts': 1}, self.projected()['wake'])

    def test_a_journal_row_written_without_a_wake_is_projected_with_one(self):
        # A schema-1 journal row had no wake, decision digest or launch. The
        # current backend reads it migrated, so the projection still carries
        # wake.state and the host never sees the older shape from this backend.
        lease = self.pending_review()
        doc = json.loads(self.journal.read_text(encoding='utf-8'))
        doc['schema'] = 1
        for row in doc['reviews'].values():
            for key in ('decision_digest', 'wake', 'launch'):
                row.pop(key)
        self.journal.write_text(json.dumps(doc), encoding='utf-8')
        self.assertNotIn('wake', json.loads(self.journal.read_text(encoding='utf-8'))['reviews'][lease])
        self.assertEqual({'state': 'pending', 'attempts': 0}, self.projected()['wake'])
        self.assertEqual('pending', WorkParallelFinalizationStore(self.journal).read()['reviews'][lease]['wake']['state'])
        # Its first wake is a durable attempt like any other.
        self.assertEqual('submitting', self.wake('prepare', lease)['state'])
        self.assertEqual({'state': 'submitting', 'attempts': 1}, self.projected()['wake'])


if __name__ == '__main__':
    unittest.main()
