"""Offline tests of driver assertions on recorded run evidence (read-only).

time_limit_handoff_related_work (team-lead 2026-11-11): accepted when every time-limit
handoff names the seeded goal or a descendant seed_board created, with the seeded goal
as root. The seeded tree is reproduced by running the driver's own seed_board against
a temp store with the recorded run's product snapshot.
Run: py -3.11 -B -m unittest test_driver_assertions -v
"""
import sys
sys.dont_write_bytecode = True
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = Path(os.environ.get('RTI_RECHECK_REPO', r'C:\Users\djlov\ciel-runtime\.local\tmp\work-parallel'))
E2E_SOURCE = REPO / 'docs/journal/2026/11/11/features/sse-native-fork/e2e-source'
SNAP_ROOT = REPO / '.local/sse-true-fork/e2e'
RUN_N1 = E2E_SOURCE / 'run-b-20261111-162135'
RUN_N2 = E2E_SOURCE / 'run-b-20261111-233151'


def load_driver():
    # DRIVER_UNDER_TEST selects the driver whose assertion functions are tested
    # (walkie_e2e_driver_installed.py sets it for its own unit gate). Default: v4.
    name = os.environ.get('DRIVER_UNDER_TEST', 'walkie_e2e_driver_v4.py')
    if Path(name).name != name or not name.endswith('.py'):
        raise ValueError('DRIVER_UNDER_TEST must be a driver file name in this folder')
    spec = importlib.util.spec_from_file_location('driver_under_test', HERE / name)
    module = importlib.util.module_from_spec(spec)
    argv = sys.argv
    try:
        sys.argv = [argv[0]]
        spec.loader.exec_module(module)
    finally:
        sys.argv = argv
    return module


D = load_driver()


def result(run):
    return json.loads((run / 'result.json').read_text(encoding='utf-8'))


def reproduce_seed(snapshot):
    if not snapshot.exists():
        raise unittest.SkipTest('recorded snapshot not present: ' + str(snapshot))
    D.SNAP = snapshot
    with tempfile.TemporaryDirectory(prefix='seed-repro-') as tmp:
        item, seed = D.seed_board(Path(tmp), 'claude:offline-seed-test')
    return item, D.seeded_tree_ids(seed, item), seed


class RelatedWorkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        snapshot = SNAP_ROOT / Path(result(RUN_N2)['snapshot']).name
        cls.item, cls.seeded, cls.seed = reproduce_seed(snapshot)

    def test_seed_board_reproduces_the_recorded_seed(self):
        self.assertEqual(self.item, 'n1')
        self.assertEqual(self.seeded, ['n1', 'n2'])
        titles = {n['id']: n.get('title') for n in self.seed['nodes']}
        self.assertEqual(titles, {'n1': 'Synthetic Walkie readiness review', 'n2': 'Review owned Walkie target thread'})
        for run in (RUN_N1, RUN_N2):
            r = result(run)
            self.assertEqual((r['related_work'], r['seed_board_version']), ('n1', self.seed['version']))

    def test_recorded_n1_handoff_is_related(self):
        handoffs = result(RUN_N1)['time_limit_handoffs']
        self.assertEqual([(v['item_id'], v['root_id']) for v in handoffs.values()], [('n1', 'n1')])
        ok, rows = D.related_work_check(handoffs, self.item, self.seeded)
        self.assertTrue(ok, rows)

    def test_recorded_n2_handoff_is_related(self):
        r = result(RUN_N2)
        handoffs = r['time_limit_handoffs']
        self.assertEqual([(v['item_id'], v['root_id']) for v in handoffs.values()], [('n2', 'n1')])
        self.assertFalse(r['facts']['time_limit_handoff_related_work'])  # the too-strict recorded verdict
        ok, rows = D.related_work_check(handoffs, self.item, self.seeded)
        self.assertTrue(ok, rows)

    def _mutated(self, **fields):
        handoffs = json.loads(json.dumps(result(RUN_N2)['time_limit_handoffs']))
        for row in handoffs.values():
            for key, value in fields.items():
                if value is None:
                    row.pop(key, None)
                else:
                    row[key] = value
        return D.related_work_check(handoffs, self.item, self.seeded)[0]

    def test_forged_unrelated_item_is_rejected(self):
        self.assertFalse(self._mutated(item_id='n9'))
        self.assertFalse(self._mutated(item_id='n10', root_id='n1'))

    def test_missing_item_is_rejected(self):
        self.assertFalse(self._mutated(item_id=None))
        self.assertFalse(self._mutated(item_id=''))

    def test_wrong_root_is_rejected(self):
        self.assertFalse(self._mutated(root_id='n2'))
        self.assertFalse(self._mutated(root_id=None))

    def test_no_handoff_is_rejected(self):
        self.assertFalse(D.related_work_check({}, self.item, self.seeded)[0])

    def test_one_bad_handoff_among_good_is_rejected(self):
        handoffs = json.loads(json.dumps(result(RUN_N2)['time_limit_handoffs']))
        handoffs['forged'] = {'item_id': 'n7', 'root_id': 'n1'}
        self.assertFalse(D.related_work_check(handoffs, self.item, self.seeded)[0])

    def test_descendant_added_after_seed_is_not_seeded(self):
        # Run 162135's main later replaced the plan (n3..n10 under n1); those are not seed_board items.
        later = result(RUN_N1)['board_after']['nodes']
        self.assertIn('n4', [n['id'] for n in later])
        self.assertFalse(D.related_work_check({'x': {'item_id': 'n4', 'root_id': 'n1'}}, self.item, self.seeded)[0])


def transcript(run):
    return [json.loads(line) for line in (run / 'evidence/main-transcript.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]


def time_limit_event(run):
    return next(iter(result(run)['time_limit_handoffs']))


RUN_A_ACCEPTED = E2E_SOURCE / 'run-a-20261111-232257'
RUN_B_ACCEPTED = E2E_SOURCE / 'run-b-20261111-234832'


class AckFirstTryTest(unittest.TestCase):
    def first_try(self, run, rows=None, final=True):
        attempts = D.handoff_ack_attempts(rows if rows is not None else transcript(run), time_limit_event(run))
        return attempts, D.ack_first_try(final, attempts)

    def test_final_state_alone_is_not_first_try(self):
        # Final acknowledged but first attempt refused (234832): false. First attempt ok but not final: false.
        self.assertFalse(self.first_try(RUN_B_ACCEPTED, final=True)[1])
        self.assertFalse(self.first_try(RUN_A_ACCEPTED, final=False)[1])
        self.assertTrue(self.first_try(RUN_A_ACCEPTED, final=True)[1])

    def test_run_b_234832_first_ack_was_a_version_conflict(self):
        attempts, ok = self.first_try(RUN_B_ACCEPTED)
        self.assertFalse(ok)
        self.assertEqual([(a['call_row'], a['result_row'], a['is_error'], a['expected_version']) for a in attempts],
                         [(84, 85, True, 2), (89, 90, False, 3)])
        self.assertIn('the plan changed (stored version 3, expected 2)', attempts[0]['result_head'])
        # The recorded verdict came from the final-state-only rule.
        self.assertTrue(result(RUN_B_ACCEPTED)['facts']['ack_succeeded_first_try'])

    def test_first_try_acks_in_other_runs(self):
        for run in (RUN_A_ACCEPTED, RUN_N1, RUN_N2):
            with self.subTest(run=run.name):
                attempts, ok = self.first_try(run)
                self.assertTrue(ok, attempts)
                self.assertEqual(len(attempts), 1)

    def test_no_ack_call_is_not_first_try(self):
        rows = [r for r in transcript(RUN_A_ACCEPTED) if 'work_side_handoff' not in json.dumps(r)]
        attempts, ok = self.first_try(RUN_A_ACCEPTED, rows)
        self.assertEqual(attempts, [])
        self.assertFalse(ok)

    def test_call_without_result_is_not_first_try(self):
        rows = transcript(RUN_A_ACCEPTED)
        result_rows = [i for i, r in enumerate(rows) if r.get('type') == 'user' and 'tool_result' in json.dumps(r)
                       and any(a['result_row'] == i for a in D.handoff_ack_attempts(rows, time_limit_event(RUN_A_ACCEPTED)))]
        trimmed = [r for i, r in enumerate(rows) if i not in result_rows]
        attempts, ok = self.first_try(RUN_A_ACCEPTED, trimmed)
        self.assertEqual([a['is_error'] for a in attempts], [None])
        self.assertFalse(ok)

    def test_other_event_ack_is_ignored(self):
        attempts = D.handoff_ack_attempts(transcript(RUN_B_ACCEPTED), 'not-this-event')
        self.assertEqual(attempts, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
