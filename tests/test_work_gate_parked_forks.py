"""Side gate checks see forks parked on a question and can answer them (user 2026-11-23).

Celly 2026-11-23: five Work forks waited for main's answer on a question
(waiting_input, no recorded wait on their item: n1236 since 11-21, n1341,
n1380, n1382, n1384). The periodic side check listed only nodes with
waiting_on / blocked / release_hint (work_gate_check.gates), so the side never
saw them and main had to scan the board by hand. A parked fork is now part of
the check: the event carries its question and the item's latest notes, the side
answers it from a recorded decision (fork_answers) or asks main (ask_main), and
the host resumes that same fork with the answer.
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ciel_runtime_support import work_gate_check as gate
from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.side_work_decision import FIELDS, apply, validate
from ciel_runtime_support.work_mcp_tools import dispatch_work_tool
from ciel_runtime_support.work_plan_store import WorkPlanStore, session_work_plan_store

HOST = {'token': 'x' * 40}
SCOPE = 'claude:parent'


def lease(lease_id, item_id, *, state='waiting_input', scope=SCOPE, slot=False, evidence='question?', updated_at=100.0):
    return {'id': lease_id, 'scope': scope, 'item_id': item_id, 'state': state, 'slot': slot,
            'host_id': 'agent-' + lease_id, 'role': 'worker', 'priority': 'P1', 'evidence': evidence,
            'created_at': 50.0, 'updated_at': updated_at}


class ParkedForkUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = WorkPlanStore(Path(self.tmp.name) / 'board.json')
        s = self.store
        _, self.goal = s.transaction(lambda d: tree.add_node(d, None, 'ship headplane', priority='P1'))
        _, self.ask = s.transaction(lambda d: tree.add_node(d, self.goal, 'merge PR 221'))
        s.transaction(lambda d: tree.update_node(d, self.ask, progress='MAIN DECISION 13:16Z: merge now, no notice needed'))
        _, self.gated = s.transaction(lambda d: tree.add_node(d, self.goal, 'deploy', waiting_on='approval from Daniel'))
        _, self.done = s.transaction(lambda d: tree.add_node(d, self.goal, 'old step'))
        s.transaction(lambda d: tree.update_node(d, self.done, status='done', evidence='ok'))
        _, self.urgent = s.transaction(lambda d: tree.add_node(d, None, 'outage', priority='P1'))
        _, self.urgent_leaf = s.transaction(lambda d: tree.add_node(d, self.urgent, 'restore DERP'))

    def leases(self):
        return {
            'L1': lease('L1', self.ask, evidence='PR #221 is UNSTABLE; merge anyway?', updated_at=200.0),
            'L2': lease('L2', self.gated, evidence='waiting for Daniel'),
            'L3': lease('L3', self.ask, scope='claude:other'),
            'L4': lease('L4', self.ask, state='running', slot=True),
            'L5': lease('L5', self.done),
            'L6': lease('L6', self.urgent_leaf, evidence='which region?', updated_at=300.0),
        }

    def test_question_parked_forks_without_a_recorded_wait_are_listed(self):
        def priority(goal):
            return 'P0' if goal['id'] == self.urgent else str(goal.get('priority') or '')
        rows = gate.parked_forks(self.leases(), SCOPE, self.store.read(), priority)
        # L2 waits on a recorded wait (a gate, released by release_waits); L3 is
        # another conversation; L4 runs; L5's item is finished.
        self.assertEqual(['L6', 'L1'], [row['lease_id'] for row in rows])
        first = rows[1]
        self.assertEqual((self.ask, self.goal, 'P1'), (first['item_id'], first['root_id'], first['priority']))
        self.assertEqual('PR #221 is UNSTABLE; merge anyway?', first['question'])
        self.assertIn('MAIN DECISION 13:16Z', first['progress'])
        self.assertEqual('P0', rows[0]['priority'])

    def test_a_board_with_only_parked_forks_is_checked(self):
        doc = {'nodes': [n for n in self.store.read()['nodes'] if not n.get('waiting_on')],
               'active_root': self.store.read()['active_root']}
        self.assertEqual([], gate.gates(doc))
        parked = gate.parked_forks(self.leases(), SCOPE, doc)
        self.assertIsNone(gate.tick_rows({}, doc, None, now=1000.0))
        tick = gate.tick_rows({}, doc, None, now=1000.0, parked=parked)
        self.assertIsNotNone(tick)
        rows, current, _ = tick
        self.assertEqual([], rows)
        # A different parked set is a different check.
        other = gate.tick_rows({}, doc, None, now=1000.0, parked=parked[:1])
        self.assertNotEqual(current, other[1])

    def test_the_event_carries_each_question_and_the_items_notes(self):
        parked = gate.parked_forks(self.leases(), SCOPE, self.store.read())
        event = gate.tick_event({}, gate.gates(self.store.read()), 'd' * 16, 1000.0, parked=parked)
        text = event['text']
        self.assertIn('질문으로 멈춘 포크', text)
        self.assertIn('lease L1', text)
        self.assertIn('PR #221 is UNSTABLE; merge anyway?', text)
        self.assertIn('MAIN DECISION 13:16Z: merge now', text)
        self.assertEqual(['L1', 'L6'], sorted(event['parked']))
        # The managed instructions tell the side how to answer them.
        self.assertIn('fork_answers', text)


class ForkAnswerDecisionTests(unittest.TestCase):
    def base(self, **extra):
        return {'action': 'answer', 'kind': 'status', 'summary': 's', 'reply': 'r', **extra}

    def test_the_router_lists_fork_answers_as_a_decision_field(self):
        # side_intake_mcp advertises FIELDS as decision_fields; the mod sends no
        # field a router does not list (an older router never receives it).
        self.assertIn('fork_answers', FIELDS)

    def test_fork_answers_shape_is_validated(self):
        good = [{'lease_id': 'L1', 'answer': 'MAIN DECISION: merge now', 'source': 'n2 progress 13:16Z'}]
        decision = self.base(fork_answers=good)
        validate(decision)
        self.assertEqual(good, decision['fork_answers'])
        for bad in ([{'lease_id': 'L1', 'answer': 'x'}],
                    [{'lease_id': 'L1', 'answer': '', 'source': 's'}],
                    [{'lease_id': 'L1', 'answer': 'a' * 4001, 'source': 's'}],
                    [{'lease_id': 'L1', 'answer': 'a', 'source': 's', 'extra': 1}],
                    [{'lease_id': 'L1', 'answer': 'a', 'source': 's'}] * 2,
                    [{'lease_id': 'L%d' % i, 'answer': 'a', 'source': 's'} for i in range(9)],
                    'L1'):
            with self.assertRaises(tree.WorkPlanError, msg=repr(bad)):
                validate(self.base(fork_answers=bad))

    def test_an_answer_reaches_a_worker_so_it_is_never_a_silent_forward(self):
        decision = {'action': 'update', 'kind': 'request', 'item_id': 'n2', 'summary': 's', 'reply': 'r',
                    'no_reply': True, 'fork_answers': [{'lease_id': 'L1', 'answer': 'a', 'source': 's'}]}
        validate(decision)
        self.assertNotIn('no_reply', decision)

    def test_only_parked_leases_of_this_conversation_are_answered(self):
        with tempfile.TemporaryDirectory() as raw:
            store = WorkPlanStore(Path(raw) / 'board.json')
            _, goal = store.transaction(lambda d: tree.add_node(d, None, 'goal'))
            _, leaf = store.transaction(lambda d: tree.add_node(d, goal, 'merge'))
            parked = {'ledger_version': 3, 'rows': [
                {'item_id': leaf, 'root_id': goal, 'lease_id': 'L1', 'state': 'waiting_input', 'slot': False}]}
            key = hashlib.sha256(b'fork-answer').hexdigest()
            result = apply(store, key, self.base(fork_answers=[
                {'lease_id': 'L1', 'answer': 'MAIN DECISION: merge now', 'source': 'n2 progress'},
                {'lease_id': 'L9', 'answer': 'x', 'source': 'y'}]),
                store.read()['version'], parked_projection=parked, gate_check=True)
            self.assertEqual([{'lease_id': 'L1', 'item_id': leaf, 'answer': 'MAIN DECISION: merge now',
                               'source': 'n2 progress'}], result['fork_answers'])
            self.assertEqual([{'lease_id': 'L9', 'reason': 'not a parked worker of this conversation'}],
                             result['unanswered_forks'])
            # An unreadable ledger answers nobody.
            key2 = hashlib.sha256(b'fork-answer-2').hexdigest()
            blind = apply(store, key2, self.base(fork_answers=[{'lease_id': 'L1', 'answer': 'a', 'source': 's'}]),
                          store.read()['version'], parked_projection=None, gate_check=True)
            self.assertNotIn('fork_answers', blind)
            self.assertEqual('the Work ledger was unreadable', blind['unanswered_forks'][0]['reason'])


class ParkedForkMcpTests(unittest.TestCase):
    """The real dispatch onto a temporary session store and side inbox."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = {'work_mode': True, 'work_side_intake': {'enabled': True},
                       'work_parallel_host': {'token': 'x' * 40}}
        self.store = session_work_plan_store(self.root, SCOPE)
        _, self.goal = self.store.transaction(lambda d: tree.add_node(d, None, 'ship headplane'))
        _, self.leaf = self.store.transaction(lambda d: tree.add_node(d, self.goal, 'merge PR 221'))
        self.store.transaction(lambda d: tree.update_node(d, self.leaf, progress='MAIN DECISION: merge now'))
        self.ledger = Mock()
        self.ledger.read.return_value = {'version': 3, 'leases': {
            'L1': lease('L1', self.leaf, evidence='PR #221 is UNSTABLE; merge anyway?')}}
        self.reviews = Mock()
        self.reviews.read.return_value = {'reviews': {}}

    def invoke(self, name, args, host=None):
        return dispatch_work_tool(1, name, {**args, 'session_id': SCOPE, '_host': host or HOST},
                                  load_config=lambda: self.config, state_dir=self.root,
                                  respond=lambda _id, text, error=False: {'text': text, 'error': error})

    def value(self, name, args, host=None):
        """A host result, reassembled when the router paged it (host_result_pages)."""
        result = self.invoke(name, args, host)
        self.assertFalse(result['error'], result)
        value = json.loads(result['text'])
        if isinstance(value, dict) and 'paged' in value:
            pages = [self.value('work_host_page', {'id': value['paged']['id'], 'part': part})['text']
                     for part in range(value['paged']['parts'])]
            value = json.loads(''.join(pages))
        return value

    def test_a_parked_question_is_checked_and_answered_without_main(self):
        with patch('ciel_runtime_support.work_parallel_mcp.ledger', return_value=self.ledger), \
                patch('ciel_runtime_support.work_parallel_mcp.finalization', return_value=self.reviews):
            queued = json.loads(self.invoke('work_side_gate_check', {'idle': True, 'forks_running': 0})['text'])
            self.assertTrue(queued.get('queued'), queued)
            self.assertEqual(['L1'], queued['parked'])
            claimed = self.value('work_side_claim', {'owner': 'actor'})['claim']
            self.assertIn('PR #221 is UNSTABLE; merge anyway?', claimed['text'])
            self.assertIn('fork_answers', claimed['decision_fields'])
            host = {**HOST, 'receipt_id': claimed['receipt_id'], 'generation': claimed['generation'],
                    'work_digest': claimed['work_digest']}
            resolved = self.invoke('work_side_resolve', {'decision': {
                'action': 'answer', 'kind': 'status', 'summary': 'n2 has MAIN DECISION: merge now',
                'reply': 'answered L1',
                'fork_answers': [{'lease_id': 'L1', 'answer': 'MAIN DECISION: merge now', 'source': 'n2 progress'}]},
                'expected_version': claimed['workload']['version']}, host)
        self.assertFalse(resolved['error'], resolved)
        result = json.loads(resolved['text'])['result']
        self.assertEqual(['L1'], [row['lease_id'] for row in result['fork_answers']])
        self.assertEqual(self.leaf, result['fork_answers'][0]['item_id'])
        self.assertNotIn(claimed['receipt_id'], self.store.read().get('side_reviews', {}))


class ParkedForkPromptTests(unittest.TestCase):
    def test_both_side_instructions_say_how_to_answer_a_parked_fork(self):
        from ciel_runtime_support.work_workflow_prompts import DEFAULTS
        for name in ('gate_actor', 'gate_recheck'):
            text = DEFAULTS[name]
            self.assertIn('질문으로 멈춘 포크', text, name)
            self.assertIn('fork_answers', text, name)
            self.assertIn('ask_main', text, name)


if __name__ == '__main__':
    unittest.main()
