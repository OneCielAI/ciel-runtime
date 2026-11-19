"""Approval re-check reads only the registered request message (user decision 2026-11-18).

The whole-thread read (200 replies a page, 8 pages) failed with "Source thread
exceeds page limit" on long Walkie threads (Andy: 134 side events). The mod part
runs under Node (tests/workflow_approval_read_harness.mjs); the router part uses
the real MCP dispatch and session store.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support import work_workflow as wf

from test_work_approval_workflow import CONVERSATION, REQUESTER, WorkflowCase, approval

ROOT = Path(__file__).resolve().parents[1]


class ApprovalReadHarnessTests(unittest.TestCase):
    def run_harness(self, scenario):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw)
            (path / 'package.json').write_text('{"type":"module"}')
            shutil.copyfile(ROOT / 'tests/workflow_approval_read_harness.mjs', path / 'test.mjs')
            shutil.copyfile(ROOT / 'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js', path / 'side-intake.js')
            done = subprocess.run(['node', str(path / 'test.mjs'), scenario], capture_output=True, text=True,
                                  encoding='utf8', check=True, timeout=60)
        return json.loads(done.stdout)

    def test_a_request_on_a_2000_reply_thread_is_checked_with_one_read(self):
        row = self.run_harness('long-thread')
        self.assertEqual((1, ['walkie_read_messages'], '9099'), (row['reads'], row['tools'], row['after']))
        self.assertEqual((1, 1, 1), (row['checks'], row['rows'], row['approvals']))

    def test_requests_of_one_conversation_are_batched_in_the_router_order(self):
        row = self.run_harness('batch')
        self.assertEqual(1, row['reads'])
        self.assertEqual(['p0', 'p1', 'p3'], row['order'])

    def test_a_row_without_approvals_reads_replies_after_its_cursor_only_when_changed(self):
        row = self.run_harness('no-approvals')
        # At most two reply pages, starting after the recorded cursor; the cursor advances.
        self.assertEqual((3, 2, '11000', '11400', 400), (row['firstReads'], row['firstReplyCalls'], row['firstReplyAfter'],
                                                        row['cursor'], row['replyRows']))
        # Unchanged since the last check: one read and nothing else.
        self.assertEqual((1, ['walkie_read_messages']), (row['unchangedReads'], row['unchangedTools']))

    def test_rows_outside_the_registered_thread_or_conversation_are_refused(self):
        row = self.run_harness('forged')
        self.assertEqual(0, row['checks'])
        self.assertEqual([('a', 'thread'), ('b', 'conversation'), ('c', 'thread')],
                         [(item['root_id'], item['reason']) for item in row['refused']])


def request_read(message_id, row, *replies):
    return {'source_context': 'verified_request', 'conversation_id': CONVERSATION, 'message_id': message_id,
            'messages': [row, *replies]}


class RouterRecheckTests(WorkflowCase):
    def ready(self):
        root = self.create()
        self.submit(root)
        self.register(root, 1)
        return root

    def request_row(self, root, **extra):
        return {'id': '9100', 'thread_root_id': '9001', 'text': 'summary ' + self.code(root), 'approvers': [REQUESTER],
                **extra}

    def test_a_lost_approval_is_recovered_from_the_request_message_alone(self):
        root = self.ready()
        version = self.store.read()['version']
        pending = self.call('work_side_workflow_event', {'check': True, 'checks': [{
            'root_id': root, 'source_message': request_read('9100', self.request_row(root, approvals=[])),
            'reply_cursor': '9100', 'row_digest': 'a' * 64}]})['results'][0]
        self.assertEqual(('pending', False), (pending['decision'], pending['applied']))
        # The recorded cursor is not plan data: the board version did not move.
        self.assertEqual(version, self.store.read()['version'])
        due = self.call('work_side_gate_check', {'idle': True})['approvals_due'][0]['request_read']
        self.assertEqual(('9100', 'a' * 64), (due['reply_cursor'], due['row_digest']))
        done = self.call('work_side_workflow_event', {'check': True, 'checks': [{
            'root_id': root, 'source_message': request_read('9100', self.request_row(root, approvals=[approval()])),
            'reply_cursor': '9100', 'row_digest': 'b' * 64}]})['results'][0]
        self.assertEqual(('approved', True, 'executing'), (done['decision'], done['applied'], done['state']))
        self.assertEqual('check', wf.record(self.store.read(), root)['approval']['via'])

    def test_the_reads_return_with_the_same_ticks_gate_check_and_the_host_decides(self):
        # One batch per tick (2026-11-18): approvals_due comes with the tick's gate
        # check; the reads go back with its second call; no side event, no fork.
        root = self.ready()
        self.config['work_gate_check'] = {'minutes': 1}
        first = self.call('work_side_gate_check', {'idle': False, 'forks_running': 0})
        self.assertEqual([root], [row['root_id'] for row in first['approvals_due']])
        pending_events = len(self.inbox.pending())
        second = self.call('work_side_gate_check', {'idle': False, 'forks_running': 0, 'approvals_read': [{
            'root_id': root, 'row_digest': 'd' * 64, 'reply_cursor': '9100',
            'source_message': request_read('9100', self.request_row(root, approvals=[approval()]))}]})
        self.assertEqual([(root, 'approved', True)], [(row['root_id'], row['decision'], row['applied'])
                                                      for row in second['approval_results']])
        self.assertNotIn('approvals_due', second)
        self.assertEqual(pending_events, len(self.inbox.pending()))
        self.assertEqual('executing', self.state(root))

    def test_twenty_waiting_items_on_three_threads_and_two_approvals_make_one_tick(self):
        # Lead 2026-11-18: the approval re-check is part of the one batched gate
        # tick. 20 waiting items on 3 threads plus 2 awaiting approvals: 1 tick
        # (2 calls), 3 thread reads, 2 request reads, 1 side event (at most 1 fork).
        from ciel_runtime_support import work_gate_threads as threads
        from ciel_runtime_support import work_plan_tree as tree
        other = '99999999-2222-3333-4444-555555555555'
        first_root = self.ready()
        second_root = self.create(message_id='9002')
        self.submit(second_root)
        self.main('work_approval_requested', {'root_id': second_root, 'conversation_id': other, 'message_id': '9200',
                                              'thread_root_id': '9002', 'approvers': [REQUESTER], 'proposal_version': 1})
        self.config['work_gate_check'] = {'minutes': 5}
        gates = []
        for title, count, root, cursor in (('alpha', 8, '40', '2035'), ('beta', 7, '5000', '5003'),
                                           ('gamma', 5, '7000', '7001')):
            _, goal = self.store.transaction(lambda d, title=title: tree.add_node(d, None, title))
            for n in range(count):
                _, item = self.store.transaction(lambda d, goal=goal, title=title, n=n: tree.add_node(
                    d, goal, '%s %d' % (title, n), waiting_on='approval %s %d' % (title, n)))
                threads.bind(self.root, self.identity, item, CONVERSATION, root, last_seen=cursor)
                gates.append(item)
        pending_before = len(self.inbox.pending())
        plan = self.call('work_side_gate_check', {'idle': True, 'forks_running': 0, 'thread_reads': True})
        self.assertEqual('read_threads', plan['reason'])
        self.assertEqual(3, len(plan['read_threads']))
        self.assertEqual(20, sum(len(row['gates']) for row in plan['read_threads']))
        due = plan['approvals_due']
        self.assertEqual([(first_root, CONVERSATION, '9100'), (second_root, other, '9200')],
                         [(row['root_id'], row['request_read']['conversation_id'], row['request_read']['message_id'])
                          for row in due])
        # The host's reads: one per thread after its cursor, one per request message.
        thread_reads = [{'key': CONVERSATION + '|40', 'messages': [{'id': '2036', 'text': 'approved by Kim',
                                                                     'sender': 'Kim'}]},
                        {'key': CONVERSATION + '|5000', 'messages': [{'id': '5004', 'text': 'still waiting'}]},
                        {'key': CONVERSATION + '|7000', 'messages': [{'id': '7002', 'text': 'ok'}]}]
        approvals_read = [
            {'root_id': first_root, 'row_digest': 'e' * 64, 'reply_cursor': '9100',
             'source_message': request_read('9100', self.request_row(first_root, approvals=[approval()]))},
            {'root_id': second_root, 'row_digest': 'f' * 64, 'reply_cursor': '9200',
             'source_message': {'source_context': 'verified_request', 'conversation_id': other, 'message_id': '9200',
                                'messages': [{'id': '9200', 'thread_root_id': '9002', 'approvers': [REQUESTER],
                                              'text': 'summary ' + self.code(second_root),
                                              'approvals': [approval()]}]}}]
        done = self.call('work_side_gate_check', {'idle': True, 'forks_running': 0, 'thread_reads': True,
                                                  'threads_read': thread_reads, 'approvals_read': approvals_read})
        self.assertEqual((True, 1, 3), (done['queued'], done['events'], done['threads_read']))
        self.assertEqual(set(gates), set(done['gates']) & set(gates))
        self.assertEqual([(first_root, 'approved', True), (second_root, 'approved', True)],
                         [(row['root_id'], row['decision'], row['applied']) for row in done['approval_results']])
        self.assertNotIn('approvals_due', done)
        self.assertNotIn('workflow_delegations', done)
        # One side event for the whole tick: at most one side fork.
        self.assertEqual(pending_before + 1, len(self.inbox.pending()))
        self.assertEqual(('executing', 'executing'), (self.state(first_root), self.state(second_root)))
        # The tick is spent: the next ask inside the interval is not due and lists nothing.
        again = self.call('work_side_gate_check', {'idle': True, 'forks_running': 0, 'thread_reads': True})
        self.assertNotEqual('read_threads', again['reason'])
        self.assertFalse(again.get('approvals_due'))

    def test_a_decision_recorded_as_a_reply_to_the_request_counts(self):
        root = self.ready()
        reply = {'id': '9150', 'thread_root_id': '9001', 'reply_to_message_id': '9100', 'approvals': [approval()]}
        done = self.call('work_side_workflow_event', {'check': True, 'root_id': root, 'row_digest': 'c' * 64,
                                                      'source_message': request_read('9100', self.request_row(root), reply)})
        self.assertTrue(done['applied'], done)

    def test_a_forged_row_outside_the_thread_or_conversation_is_refused(self):
        root = self.ready()
        forged = [
            request_read('9100', self.request_row(root, thread_root_id='7777', approvals=[approval()])),
            request_read('9100', self.request_row(root, thread_root_id=None, approvals=[approval()])),
            request_read('9100', self.request_row(root, conversation_id='99999999-2222-3333-4444-555555555555',
                                                  approvals=[approval()])),
            {**request_read('9100', self.request_row(root, approvals=[approval()])),
             'conversation_id': '99999999-2222-3333-4444-555555555555'},
            request_read('9999', self.request_row(root, id='9999', approvals=[approval()])),
            # A reply that approves another message does not approve the request.
            request_read('9100', self.request_row(root), {'id': '9150', 'thread_root_id': '9001',
                                                          'reply_to_message_id': '9101', 'approvals': [approval()]}),
        ]
        results = self.call('work_side_workflow_event', {'check': True, 'checks': [
            {'root_id': root, 'source_message': source} for source in forged]})['results']
        self.assertEqual([False] * len(forged), [row['applied'] for row in results])
        self.assertEqual('awaiting_approval', self.state(root))
        self.assertEqual([], self.runnable(root))


if __name__ == '__main__':
    unittest.main()
