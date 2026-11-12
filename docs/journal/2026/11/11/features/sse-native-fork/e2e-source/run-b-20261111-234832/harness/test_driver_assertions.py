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
    spec = importlib.util.spec_from_file_location('driver_under_test', HERE / 'walkie_e2e_driver_v4.py')
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


if __name__ == '__main__':
    unittest.main(verbosity=2)
