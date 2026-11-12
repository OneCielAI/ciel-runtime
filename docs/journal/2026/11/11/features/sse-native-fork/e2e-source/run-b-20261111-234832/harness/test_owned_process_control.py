"""Unit tests for owned_process_control with a fake Windows process table.

No real process is created or killed: snapshot_all and taskkill are replaced by an
in-memory table. Run: py -3.11 -B -m unittest test_owned_process_control -v
"""
import sys
sys.dont_write_bytecode = True
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import owned_process_control as opc


def date(ms):
    return '/Date(%d)/' % ms


class FakeTable:
    """Process table plus a taskkill that removes exactly one PID (no tree walk)."""

    def __init__(self, rows):
        self.rows = [dict(r) for r in rows]
        self.kills = []
        self.before_kill = None  # hook(pid) run before a kill takes effect

    def snapshot(self):
        return [dict(r) for r in self.rows]

    def run(self, argv, **_):
        assert argv[0] == 'taskkill', argv
        assert '/T' not in argv, 'taskkill /T must never be used'
        pid = int(argv[argv.index('/PID') + 1])
        self.kills.append(list(argv))
        if self.before_kill:
            self.before_kill(pid)
        found = [r for r in self.rows if int(r['ProcessId']) == pid]
        self.rows = [r for r in self.rows if int(r['ProcessId']) != pid]
        return mock.Mock(returncode=0 if found else 128,
                         stdout='SUCCESS: The process with PID %d has been terminated.' % pid if found else '',
                         stderr='' if found else 'ERROR: The process "%d" not found.' % pid)


def row(pid, ppid, ms, name='p.exe'):
    return {'ProcessId': pid, 'ParentProcessId': ppid, 'Name': name, 'CreationDate': date(ms)}


class OwnedProcessControlTest(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory(prefix='opc-test-')
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name)

    def kill(self, table, root, protected=frozenset()):
        with mock.patch.object(opc, 'snapshot_all', table.snapshot), \
             mock.patch.object(opc.subprocess, 'run', table.run), \
             mock.patch.object(opc.time, 'sleep', lambda _s: None):
            return opc.kill_owned_leaf_first(root, set(protected), self.tmp / 'cleanup.jsonl', timeout=5)

    def test_child_older_than_parent_is_refused(self):
        # PID 30 names parent 10 but was created before 10 existed: a reused-PID
        # relationship, so it is never owned and never killed.
        table = FakeTable([row(10, 1, 1000), row(20, 10, 1100), row(30, 10, 900, 'foreign.exe')])
        ledger, events = opc.discover_owned((10, date(1000)), table.snapshot())
        self.assertEqual(sorted(i[0] for i in ledger), [10, 20])
        self.assertTrue(any(e['event'] == 'reject_child_time' and e['identity'][0] == 30 for e in events))
        report = self.kill(table, (10, date(1000)))
        self.assertTrue(report['completed'])
        self.assertEqual([k[2] for k in table.kills], ['20', '10'])
        self.assertTrue(any(int(r['ProcessId']) == 30 for r in table.rows), 'foreign older child must survive')

    def test_leaf_first_order_and_no_tree_flag(self):
        table = FakeTable([row(10, 1, 1000, 'root'), row(20, 10, 1100, 'a'), row(21, 20, 1200, 'a1'),
                           row(22, 20, 1210, 'a2'), row(30, 10, 1300, 'b'), row(99, 1, 500, 'unrelated')])
        report = self.kill(table, (10, date(1000)))
        order = [int(k[2]) for k in table.kills]
        self.assertTrue(report['completed'])
        self.assertEqual(sorted(order), [10, 20, 21, 22, 30])
        self.assertEqual(order[-1], 10, 'root is killed last')
        self.assertLess(order.index(21), order.index(20))
        self.assertLess(order.index(22), order.index(20))
        self.assertTrue(all(k == ['taskkill', '/PID', k[2], '/F'] for k in table.kills))
        self.assertEqual([int(r['ProcessId']) for r in table.rows], [99])
        log = (self.tmp / 'cleanup.jsonl').read_text(encoding='utf-8')
        self.assertIn('SUCCESS: The process with PID 21 has been terminated.', log)

    def test_protected_identity_refuses_before_any_kill(self):
        table = FakeTable([row(10, 1, 1000), row(20, 10, 1100), row(21, 20, 1200)])
        with self.assertRaises(RuntimeError):
            self.kill(table, (10, date(1000)), protected={(21, date(1200))})
        self.assertEqual(table.kills, [])
        self.assertIn('protected_intersection', (self.tmp / 'cleanup.jsonl').read_text(encoding='utf-8'))

    def test_same_pid_with_other_creation_date_is_not_protected_match(self):
        # Protection is by exact identity: a protected (21, other date) does not block
        # killing the owned (21, 1200), and an owned identity is never matched by PID alone.
        table = FakeTable([row(10, 1, 1000), row(21, 10, 1200)])
        report = self.kill(table, (10, date(1000)), protected={(21, date(7777))})
        self.assertTrue(report['completed'])
        self.assertEqual([int(k[2]) for k in table.kills], [21, 10])

    def test_pid_reused_before_kill_is_not_killed(self):
        # Owned leaf 21 exits and its PID is reused by a foreign process (new
        # CreationDate, foreign parent) before the kill loop reaches it.
        table = FakeTable([row(10, 1, 1000), row(20, 10, 1100), row(21, 20, 1200)])
        original = table.snapshot
        state = {'swapped': False}

        def snapshot():
            if not state['swapped']:
                state['swapped'] = True
                rows = original()
                table.rows = [r for r in table.rows if int(r['ProcessId']) != 21] + [row(21, 5, 5000, 'reused.exe')]
                return rows
            return original()
        with mock.patch.object(opc, 'snapshot_all', snapshot), \
             mock.patch.object(opc.subprocess, 'run', table.run), \
             mock.patch.object(opc.time, 'sleep', lambda _s: None):
            report = opc.kill_owned_leaf_first((10, date(1000)), set(), self.tmp / 'c.jsonl', timeout=5)
        self.assertTrue(report['completed'])
        self.assertNotIn('21', [k[2] for k in table.kills], 'a reused PID must never be killed')
        self.assertTrue(any(int(r['ProcessId']) == 21 and r['Name'] == 'reused.exe' for r in table.rows))

    def test_child_spawned_during_cleanup_is_killed_before_parent(self):
        table = FakeTable([row(10, 1, 1000), row(20, 10, 1100)])

        def spawn(pid):
            if pid == 20 and not any(int(r['ProcessId']) == 40 for r in table.rows) and not table.spawned:
                table.spawned = True
                table.rows.append(row(40, 10, 1400, 'late-child'))
        table.spawned = False
        table.before_kill = spawn
        report = self.kill(table, (10, date(1000)))
        order = [int(k[2]) for k in table.kills]
        self.assertTrue(report['completed'])
        self.assertIn([40, date(1400)], report['dynamic_identities'])
        self.assertEqual(order[-1], 10)
        self.assertLess(order.index(40), order.index(10))
        self.assertEqual(table.rows, [])

    def test_cim_json_with_raw_control_character_parses(self):
        # run-a-20261111-223508: a raw control character in one command line made the
        # strict parser fail before launch. Identity fields are unaffected.
        text = ('[{"ProcessId":10,"ParentProcessId":1,"Name":"a.exe","CreationDate":"/Date(1000)/",'
                '"CommandLine":"x\tz \x1b[0m"},{"ProcessId":11,"ParentProcessId":10,"Name":"b.exe",'
                '"CreationDate":"/Date(1100)/","CommandLine":null}]')
        with self.assertRaises(ValueError):
            opc.json.loads(text)
        rows = opc.parse_cim_json(text)
        self.assertEqual([opc.identity(r) for r in rows], [(10, date(1000)), (11, date(1100))])
        self.assertEqual(rows[0]['CommandLine'], 'x\tz \x1b[0m')
        single = opc.parse_cim_json('{"ProcessId":5,"ParentProcessId":1,"Name":"c.exe","CreationDate":null}')
        self.assertEqual(opc.identity(single[0]), (5, ''))
        self.assertEqual(opc.parse_cim_json('  '), [])
        with self.assertRaises(ValueError):
            opc.parse_cim_json('[{"ProcessId":5,')

    def _protected_rows(self):
        return [
            dict(row(23728, 1, 100, 'py.exe'), CommandLine='launcher A'),
            dict(row(39984, 1, 110, 'py.exe'), CommandLine='launcher B'),
            dict(row(21912, 1, 120, 'py.exe'), CommandLine='launcher C'),
            dict(row(30340, 21912, 900, 'uv.exe'), CommandLine='uv run pytest'),
            dict(row(20276, 30340, 901, 'python.exe'), CommandLine='pytest -q tests/test_work_parallel_finalization.py'),
            dict(row(12240, 21912, 200, 'claude.exe'), CommandLine='claude'),
        ]

    def test_protected_process_that_exits_by_itself_is_ended_not_killed(self):
        # run-a-20261111-223807: a live session's foreground pytest (20276) and its uv.exe
        # parent (30340) exited by themselves during the run; neither was ever owned or killed.
        before = self._protected_rows()
        after = [r for r in before if int(r['ProcessId']) not in (20276, 30340)]
        report = opc.classify_protected_changes(before, after, [23728, 39984, 21912, 30340, 20276, 12240],
                                                [23728, 39984, 21912], owned=[(79908, date(5000))], killed=[(79908, date(5000))])
        self.assertTrue(report['passed'])
        self.assertTrue(report['core_roots_intact'])
        self.assertEqual(report['ended_owned_or_killed'], [])
        self.assertEqual([(e['identity'][0], e['name']) for e in report['ended_not_killed']], [(20276, 'python.exe'), (30340, 'uv.exe')])
        self.assertEqual(report['ended_not_killed'][0]['command_line'], 'pytest -q tests/test_work_parallel_finalization.py')

    def test_protected_identity_in_kill_log_or_owned_tree_fails(self):
        before = self._protected_rows()
        after = [r for r in before if int(r['ProcessId']) != 20276]
        roots = [23728, 39984, 21912]
        killed = opc.classify_protected_changes(before, after, [20276], roots, owned=[], killed=[(20276, date(901))])
        self.assertFalse(killed['passed'])
        self.assertTrue(killed['ended_owned_or_killed'][0]['in_kill_log'])
        owned = opc.classify_protected_changes(before, after, [20276], roots, owned=[(20276, date(901))], killed=[])
        self.assertFalse(owned['passed'])
        self.assertTrue(owned['ended_owned_or_killed'][0]['ever_owned'])
        # The same PID with another CreationDate in the kill log is a different process.
        other = opc.classify_protected_changes(before, after, [20276], roots, owned=[], killed=[(20276, date(7777))])
        self.assertTrue(other['passed'])

    def test_core_root_missing_or_reused_fails(self):
        before = self._protected_rows()
        missing = [r for r in before if int(r['ProcessId']) != 39984]
        report = opc.classify_protected_changes(before, missing, [39984], [23728, 39984, 21912], owned=[], killed=[])
        self.assertFalse(report['passed'])
        self.assertEqual([r['pid'] for r in report['core_roots'] if not r['intact']], [39984])
        reused = [r for r in before if int(r['ProcessId']) != 21912] + [dict(row(21912, 1, 9999, 'py.exe'), CommandLine='other')]
        report = opc.classify_protected_changes(before, reused, [21912], [23728, 39984, 21912], owned=[], killed=[])
        self.assertFalse(report['passed'])
        root = next(r for r in report['core_roots'] if r['pid'] == 21912)
        self.assertEqual((root['before'], root['after'], root['intact']), (date(120), date(9999), False))

    def test_reused_pid_of_ended_protected_identity_is_ended(self):
        before = self._protected_rows()
        after = [r for r in before if int(r['ProcessId']) != 12240] + [dict(row(12240, 4, 8000, 'svchost.exe'), CommandLine='svc')]
        report = opc.classify_protected_changes(before, after, [12240], [23728, 39984, 21912], owned=[], killed=[])
        self.assertTrue(report['passed'])
        self.assertEqual(report['ended_not_killed'][0]['pid_reused_after'], date(8000))

    def test_absent_root_is_refused(self):
        with self.assertRaises(RuntimeError):
            opc.discover_owned((10, date(1000)), [row(10, 1, 2000)])


if __name__ == '__main__':
    unittest.main(verbosity=2)
