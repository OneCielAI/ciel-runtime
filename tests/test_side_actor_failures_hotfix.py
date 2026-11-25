"""Andy side-actor failures hotfix (2026-11-18).

Measured on Andy's side inbox: 134 page-limit failures, old retrying rows
claimed ahead of 408 new events, note-capacity refusals, and side answers
refused for their shape. The long-thread test lives in test_side_intake_mod.py
(side_persisted_harness.mjs); this file covers the claim order, the note field,
the length clamps and the corrective turn.
"""
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
from ciel_runtime_support.side_intake_processing import claim
from ciel_runtime_support.side_understanding_outbox import MAX_UNDERSTANDING, validate_understanding
from ciel_runtime_support import side_work_decision as decisions
from ciel_runtime_support.side_work_decision import apply, validate
from ciel_runtime_support.work_plan_store import WorkPlanStore

# The router's summary/reply bound (16000 since 2026-11-06).
MAX_SIDE_SUMMARY = getattr(decisions, 'MAX_SIDE_SUMMARY', 16000)

ROOT = Path(__file__).resolve().parents[1]
IDENTITY = 'claude:test'


class ClaimOrderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.inbox = SideIntakeInbox(self.root, IDENTITY)
        self.now = time.time()

    def received(self, text, key, *, age, failed=False):
        row = self.inbox.receive(text, key)
        current = self.inbox._load(row['receipt_id'])
        current['received_at'] = self.now - age
        if failed:
            current['side_failure'] = {'category': 'source_context', 'reason': 'Source thread exceeds page limit',
                                       'attempt_count': 2, 'failed_at': self.now - 60, 'retry_not_before': 0,
                                       'quarantined': False, 'retryable': True}
        self.inbox._save_file(self.inbox._event_path('pending', row['receipt_id']), current)
        return row['receipt_id']

    def test_new_events_interleave_with_old_retries_after_p0_and_p1(self):
        # Andy: the only claim after a restart was a 2-day-old page-limit failure
        # while 408 new events waited. 40 old retrying rows, 6 new ones, a P1
        # and a P0 (all new rows already aged past the 60 s window).
        retries = {self.received('old request %d' % n, 'old-%d' % n, age=2 * 86400 + n, failed=True)
                   for n in range(40)}
        fresh = {self.received('new request %d' % n, 'new-%d' % n, age=120 - n) for n in range(6)}
        p1 = self.received('{"data":{"priority":"P1","text":"urgent"}}', 'p1', age=90)
        p0 = self.received('{"data":{"priority":"P0","text":"emergency"}}', 'p0', age=30)
        picked = [claim(self.root, IDENTITY, 'actor', now=self.now + 1)['receipt_id'] for _ in range(14)]
        self.assertEqual([p0, p1], picked[:2])
        lanes = ['fresh' if key in fresh else 'retry' if key in retries else 'other' for key in picked[2:]]
        self.assertEqual(['fresh', 'retry'] * 6, lanes)
        # Each event has one actor: a claimed row is never claimed again while leased.
        self.assertEqual(len(picked), len(set(picked)))
        # With the new events used up, retries continue one after another.
        rest = [claim(self.root, IDENTITY, 'actor', now=self.now + 1)['receipt_id'] for _ in range(3)]
        self.assertTrue(set(rest) <= retries)

    def test_urgent_only_takes_p0_and_p1_alone(self):
        self.received('old request', 'old', age=86400, failed=True)
        p1 = self.received('{"data":{"priority":"P1","text":"urgent"}}', 'p1', age=90)
        self.assertEqual(p1, claim(self.root, IDENTITY, 'reserved', urgent_only=True, now=self.now + 1)['receipt_id'])
        self.assertIsNone(claim(self.root, IDENTITY, 'reserved', urgent_only=True, now=self.now + 1))


class SideNoteFieldTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = WorkPlanStore(Path(self.tmp.name) / 'board.json')
        doc, self.goal = self.store.transaction(lambda d: tree.add_node(d, None, 'ship export', note='approved scope'))
        doc, self.item = self.store.transaction(lambda d: tree.add_node(d, self.goal, 'writer', note='w' * 1990))

    def update(self, key, note):
        return apply(self.store, key, {'action': 'update', 'kind': 'request', 'item_id': self.item,
                                       'summary': 'follow-up', 'reply': 'noted', 'note': note},
                     self.store.read()['version'])

    def digest(self):
        from ciel_runtime_support.work_parallel_mcp import _root_scope_digest
        return _root_scope_digest(self.store.read(), self.goal)

    def test_on_an_executing_goal_a_side_update_keeps_the_approval_digest(self):
        self.store.transaction(lambda d: tree.set_phase(d, tree.PHASE_EXECUTING, self.goal))
        before = self.digest()
        for n in range(30):
            result = self.update('%064x' % (n + 1), 'follow-up %d ' % n + 'x' * 180)
            self.assertEqual('progress', result['side_note_field'])
        node = next(n for n in self.store.read()['nodes'] if n['id'] == self.item)
        self.assertEqual('w' * 1990, node['note'])
        self.assertLessEqual(len(node['progress']), tree.MAX_TEXT_CHARS)
        self.assertIn('follow-up 29', node['progress'])
        self.assertIn('earlier side requests trimmed for space', node['progress'])
        self.assertEqual(before, self.digest())

    def test_on_a_planning_goal_a_full_note_is_compacted_not_refused(self):
        # Planning: no fork runs on the goal, so the observation stays in the
        # note (requirements main reads), bounded; the update still commits.
        result = self.update('%064x' % 99, 'new requirement ' + 'y' * 300)
        self.assertEqual('update', result['action'])
        self.assertNotIn('side_note_field', result)
        node = next(n for n in self.store.read()['nodes'] if n['id'] == self.item)
        self.assertLessEqual(len(node['note']), tree.MAX_TEXT_CHARS)
        self.assertEqual('', node['progress'])


class LengthClampTests(unittest.TestCase):
    def test_over_long_summary_reply_note_title_are_cut_with_a_marker(self):
        decision = validate({'refs': ['999'], 'action': 'create', 'kind': 'request', 'title': 't' * 500,
                             'summary': 's' * (MAX_SIDE_SUMMARY + 50), 'reply': 'r' * (MAX_SIDE_SUMMARY + 9),
                             'note': 'n' * (tree.MAX_TEXT_CHARS + 1)})
        for key, limit in (('summary', MAX_SIDE_SUMMARY), ('reply', MAX_SIDE_SUMMARY), ('note', tree.MAX_TEXT_CHARS),
                           ('title', tree.MAX_TITLE_CHARS)):
            self.assertLessEqual(len(decision[key]), limit, key)
            self.assertIn('host cut', decision[key], key)

    def test_understanding_is_cut_and_every_other_check_stays(self):
        text = validate_understanding('I understand you want ' + 'z' * (MAX_UNDERSTANDING * 2), 'original')
        self.assertLessEqual(len(text), MAX_UNDERSTANDING)
        self.assertIn('host cut', text)
        for bad in ('', '   ', 'ACK got it', 'original'):
            with self.subTest(bad=bad), self.assertRaises(tree.WorkPlanError):
                validate_understanding(bad, 'original')
        for decision in ({'refs': ['999'], 'action': 'create', 'kind': 'request', 'summary': '', 'reply': 'r'},
                         {'action': 'explode', 'kind': 'request', 'summary': 's', 'reply': 'r'},
                         {'refs': ['999'], 'action': 'create', 'kind': 'status', 'summary': 's', 'reply': 'r'}):
            with self.subTest(decision=decision), self.assertRaises(tree.WorkPlanError):
                validate(dict(decision))


CONVERSATION = '34886995-4a66-4527-a618-814e9cb0a5bf'
HOST = {'token': 'x' * 40}


class GateThreadTests(unittest.TestCase):
    """Andy 2026-11-18 (user design): the gate re-check is one batch per tick and
    reads only what changed in each gate's source thread."""

    def setUp(self):
        from ciel_runtime_support.work_plan_store import session_work_plan_store
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.identity = 'claude:parent'
        self.config = {'work_mode': True, 'work_side_intake': {'enabled': True},
                       'work_parallel_host': {'token': 'x' * 40},
                       # These cases cover one whole tick; the round size has its own tests.
                       'work_gate_check': {'minutes': 5, 'round_gates': 100}}
        self.store = session_work_plan_store(self.root, self.identity)

    def invoke(self, name, args, host=None):
        from ciel_runtime_support.work_mcp_tools import dispatch_work_tool
        result = dispatch_work_tool(1, name, {**args, 'session_id': self.identity, '_host': host or HOST},
                                    load_config=lambda: self.config, state_dir=self.root,
                                    respond=lambda _id, text, error=False: {'text': text, 'error': error})
        self.assertFalse(result['error'], result)
        return json.loads(result['text'])

    def gates(self, count, title):
        doc, goal = self.store.transaction(lambda d: tree.add_node(d, None, title))
        ids = []
        for n in range(count):
            doc, item = self.store.transaction(lambda d: tree.add_node(d, goal, '%s %d' % (title, n),
                                                                       waiting_on='approval %s %d' % (title, n)))
            ids.append(item)
        return ids

    def check(self, **args):
        return self.invoke('work_side_gate_check', {'idle': True, 'forks_running': 0, 'thread_reads': True, **args})

    def pending(self):
        return SideIntakeInbox(self.root, self.identity).pending(100)

    def cursors(self):
        from ciel_runtime_support import work_gate_threads as threads
        return json.loads(threads._path(self.root, self.identity).read_text(encoding='utf-8'))

    def test_twenty_gates_on_three_threads_make_three_reads_and_one_event(self):
        from ciel_runtime_support import work_gate_threads as threads
        t1, t2, t3 = self.gates(8, 'alpha'), self.gates(7, 'beta'), self.gates(3, 'gamma')
        loose = self.gates(2, 'ci')
        for ids, root, cursor in ((t1, '40', '2035'), (t2, '5000', '5003'), (t3, '7000', '7001')):
            for item in ids:
                threads.bind(self.root, self.identity, item, CONVERSATION, root, last_seen=cursor)
        plan = self.check()
        self.assertEqual('read_threads', plan['reason'])
        self.assertEqual([(CONVERSATION + '|40', '2035', 8), (CONVERSATION + '|5000', '5003', 7),
                          (CONVERSATION + '|7000', '7001', 3)],
                         sorted((row['key'], row['after'], len(row['gates'])) for row in plan['read_threads']))
        self.assertEqual([], self.pending())
        reads = [{'key': CONVERSATION + '|40', 'messages': [{'id': '2036', 'text': 'approved by Kim', 'sender': 'Kim'},
                                                            {'id': '2035', 'text': 'old, at the cursor'}]},
                 {'key': CONVERSATION + '|5000', 'messages': []},
                 {'key': CONVERSATION + '|7000', 'messages': [{'id': '7002', 'text': 'still waiting'}]}]
        queued = self.check(threads_read=reads)
        self.assertEqual((True, 1, 3), (queued['queued'], queued['events'], queued['threads_read']))
        # Thread 5000 had nothing new: its 7 gates are not in this tick.
        self.assertEqual(set(t1 + t3 + loose), set(queued['gates']))
        events = self.pending()
        self.assertEqual(1, len(events))
        text = events[0]['text']
        self.assertEqual(1, text.count('#2036 Kim: approved by Kim'))
        self.assertNotIn('old, at the cursor', text)
        self.assertEqual(7, text.count('위의 ' + t1[0] + ' 참고 (같은 스레드)'))
        self.assertIn('스레드 전체나 이력은 읽지 마십시오', text)
        # The cursors moved to the newest message read.
        self.assertEqual({'40': '2036', '5000': '5003', '7000': '7002'},
                         {row['root_id']: row['last_seen'] for row in self.cursors()['threads'].values()})

    def test_nothing_new_means_no_event_and_no_fork(self):
        from ciel_runtime_support import work_gate_threads as threads
        ids = self.gates(3, 'alpha')
        for item in ids:
            threads.bind(self.root, self.identity, item, CONVERSATION, '40', last_seen='100')
        self.assertEqual('read_threads', self.check()['reason'])
        quiet = self.check(threads_read=[{'key': CONVERSATION + '|40', 'messages': []}])
        self.assertEqual(('nothing_new', False), (quiet['reason'], quiet['queued']))
        self.assertEqual([], self.pending())
        # The tick is recorded: inside the interval the next ask is not due.
        self.assertEqual('not_due', self.check()['reason'])

    def test_quiet_thread_preserves_blocked_and_explicit_lookup_gates(self):
        from ciel_runtime_support import work_gate_threads as threads
        blocked, hinted, plain = self.gates(3, 'quiet')
        self.store.transaction(lambda d: tree.update_node(d, blocked, status='blocked'))
        self.store.transaction(lambda d: tree.update_node(d, hinted, release_hint={
            'condition': 'Workflow 42 succeeds', 'lookup': 'Read workflow 42 status'}))
        for item in (blocked, hinted, plain):
            threads.bind(self.root, self.identity, item, CONVERSATION, '40', last_seen='100')
        self.assertEqual('read_threads', self.check()['reason'])
        queued = self.check(threads_read=[{'key': CONVERSATION + '|40', 'messages': []}])
        self.assertEqual([blocked, hinted], queued['gates'])
        self.assertIn('호스트 관측 UTC:', self.pending()[0]['text'])

    def test_a_failed_read_checks_its_gates_like_gates_without_a_thread(self):
        from ciel_runtime_support import work_gate_threads as threads
        ids = self.gates(2, 'alpha')
        for item in ids:
            threads.bind(self.root, self.identity, item, CONVERSATION, '40', last_seen='100')
        self.check()
        queued = self.check(threads_read=[{'key': CONVERSATION + '|40', 'error': 'Source thread read failed'}])
        self.assertEqual(set(ids), set(queued['gates']))
        self.assertNotIn('이후 새 메시지', self.pending()[0]['text'])

    def test_an_sse_event_of_the_thread_advances_its_cursor_and_binds_the_item(self):
        from ciel_runtime_support import work_gate_threads as threads
        text = json.dumps({'specversion': '1.0', 'type': 'com.ciel.walkie.message', 'id': 'e1',
                           'data': {'event_type': 'message.created', 'conversation': CONVERSATION, 'message_id': '2050',
                                    'thread_root_id': '40', 'text': 'please hold the deploy until Kim approves'}})
        SideIntakeInbox(self.root, self.identity).receive(text, 'walkie:e1')
        event = self.invoke('work_side_claim', {'owner': 'actor'})['claim']
        host = {**HOST, 'receipt_id': event['receipt_id'], 'generation': event['generation']}
        resolved = self.invoke('work_side_resolve', {'decision': {'refs': ['999'], 
            'action': 'create', 'kind': 'request', 'title': 'deploy after approval', 'summary': 'deploy waits',
            'reply': 'queued'}, 'expected_version': self.store.read()['version']}, host)
        item = resolved['result']['item_id']
        key = CONVERSATION + '|40'
        self.assertEqual(key, self.cursors()['nodes'][item])
        self.assertEqual('2050', self.cursors()['threads'][key]['last_seen'])
        # A later event of the same thread moves the cursor on; an older id never moves it back.
        for message, expected in (('2061', '2061'), ('2055', '2061')):
            threads.record_event(self.root, self.identity, {'classification': 'thread', 'conversation_id': CONVERSATION,
                                                             'root_id': '40', 'target_message_id': message},
                                 {'action': 'answer'})
            self.assertEqual(expected, self.cursors()['threads'][key]['last_seen'])

    def test_a_host_without_thread_reads_gets_one_event_for_all_gates(self):
        # A mod from before this fix sends no thread_reads: one batch, as before
        # minus the split into 24-gate events.
        for n in range(3):
            self.gates(20, 'g%d' % n)
        queued = self.invoke('work_side_gate_check', {'idle': True, 'forks_running': 0})
        self.assertEqual((True, 1, 60), (queued['queued'], queued['events'], len(queued['gates'])))


@unittest.skipUnless(shutil.which('node'), 'node required')
class CorrectiveTurnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw)
            (path / 'package.json').write_text('{"type":"module"}')
            shutil.copyfile(ROOT / 'tests' / 'side_correction_harness.mjs', path / 'test.mjs')
            shutil.copyfile(ROOT / 'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js', path / 'side-intake.js')
            shutil.copyfile(ROOT / 'ciel_runtime_support/claude_mods/ciel-work/hooks/texts.generated.js', path / 'texts.generated.js')
            done = subprocess.run(['node', str(path / 'test.mjs')], capture_output=True, text=True, encoding='utf8',
                                  check=True, timeout=60)
        cls.result = json.loads(done.stdout)

    def test_no_decision_json_gets_one_correction_then_commits(self):
        row = self.result['no_json_then_valid']
        self.assertEqual([{'to': 'actor-no_json_then_valid', 'mentionsError': True}], row['sends'])
        self.assertEqual((1, 1, 1, []), (row['prepares'], row['understandingSent'], row['resolves'], row['failures']))

    def test_a_second_refusal_counts(self):
        row = self.result['no_json_twice']
        self.assertEqual(1, len(row['sends']))
        self.assertEqual([{'category': 'invalid_answer', 'reason': 'side answer carries no decision JSON'}],
                         row['failures'])
        self.assertEqual(0, row['resolves'])

    def test_a_prepare_refusal_is_corrected_and_the_understanding_goes_out_once(self):
        row = self.result['prepare_refused_then_valid']
        self.assertEqual(1, len(row['sends']))
        self.assertEqual((2, 1, 1, []), (row['prepares'], row['understandingSent'], row['resolves'], row['failures']))

    def test_no_correction_after_the_proposal_was_saved(self):
        row = self.result['resolve_refused']
        self.assertEqual([], row['sends'])
        self.assertEqual(['invalid_answer'], [failure['category'] for failure in row['failures']])

    def test_a_queue_decision_without_refs_is_corrected_then_commits_with_them(self):
        row = self.result['refs_refused_then_valid']
        self.assertEqual([{'to': 'actor-refs_refused_then_valid', 'mentionsError': True}], row['sends'])
        self.assertEqual((2, 1, 1, []), (row['prepares'], row['understandingSent'], row['resolves'], row['failures']))
        self.assertEqual([None, ['41', '40'], ['41', '40']], row['sentRefs'])

    def test_refs_reach_only_a_router_that_accepts_them(self):
        self.assertEqual([['41', '40'], ['41', '40']], self.result['router_with_refs']['sentRefs'])
        before = self.result['router_before_refs']
        self.assertEqual(([None, None], [], 1), (before['sentRefs'], before['failures'], before['resolves']))


class RefsShapeTests(unittest.TestCase):
    def test_positive_integer_refs_are_kept_as_their_exact_decimal_text(self):
        # KOBE 2026-11-25 (f5ae6c68b340): a side actor listed Walkie message ids
        # as JSON numbers; an older loaded mod passes them through unchanged.
        decision = {'action': 'update', 'kind': 'request', 'item_id': 'n29', 'summary': 's', 'reply': 'r',
                    'refs': [138846, '160846', 123456789012345678]}
        validate(decision)
        self.assertEqual(['138846', '160846', '123456789012345678'], decision['refs'])

    def test_other_non_string_refs_are_still_refused(self):
        for bad in ([0], [-5], [True], [1.5], [{'id': '1'}], [None]):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(tree.WorkPlanError, 'invalid side decision refs'):
                    validate({'action': 'update', 'kind': 'request', 'item_id': 'n1', 'summary': 's',
                              'reply': 'r', 'refs': bad})


if __name__ == '__main__':
    unittest.main()
