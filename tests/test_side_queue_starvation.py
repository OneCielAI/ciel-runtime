"""Side queue starvation (Andy and KOBE, 2026-11-19).

Agent status echoes filled the side queue (Andy: 2,728 unclaimed events, 1,700
in six hours on four threads). The gate re-check sat unclaimed for 9.6 h and
held every later round; requests waited behind the echoes; ask_main never
reached the router because the mod dropped the field.
"""
import json
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
from ciel_runtime_support.side_intake_processing import claim, finish, supersede_unclaimed
from ciel_runtime_support.side_reply_outbox import prepare
from ciel_runtime_support.side_work_decision import apply, validate
from ciel_runtime_support.work_plan_store import session_work_plan_store

IDENTITY = 'claude:parent'
SIDE_JS = Path(__file__).resolve().parents[1] / 'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js'


def walkie(message, priority):
    return json.dumps({'data': {'conversation': 'c1', 'message_id': message, 'text': 'status echo',
                                'message_context': {'thread_task': {'priority': priority}}}})


class SideQueueStarvationTests(unittest.TestCase):
    def test_a_gate_recheck_is_claimed_before_p1_events(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            inbox = SideIntakeInbox(root, IDENTITY)
            for n in range(5):
                inbox.receive(walkie(str(100 + n), 1), 'echo:%d' % n, route={'source_kind': 'external_event'})
            gate = inbox.receive('gate text', 'gate-check:x', route={'source_kind': 'gate_check'})['receipt_id']
            self.assertEqual(gate, claim(root, IDENTITY, 'side-1')['receipt_id'])

    def test_an_unclaimed_round_is_superseded_and_a_leased_one_is_kept(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            inbox = SideIntakeInbox(root, IDENTITY)
            old = inbox.receive('gate old', 'gate-check:old', route={'source_kind': 'gate_check'})['receipt_id']
            busy = inbox.receive('gate busy', 'gate-check:busy', route={'source_kind': 'gate_check'})['receipt_id']
            leased = claim(root, IDENTITY, 'side-1')['receipt_id']
            other = busy if leased == old else old
            self.assertEqual([leased], supersede_unclaimed(root, IDENTITY, [old, busy]))
            row = inbox._load(other)
            self.assertTrue(row['side_failure']['quarantined'] and row['side_failure']['superseded'])
            # The superseded snapshot is never claimed again.
            self.assertIsNone(claim(root, IDENTITY, 'side-2'))

    def test_no_reply_is_recorded_and_nothing_is_posted(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            inbox = SideIntakeInbox(root, IDENTITY)
            store = session_work_plan_store(root, IDENTITY)
            key = inbox.receive(walkie('200', 3), 'echo:200', route={'source_kind': 'external_event', 'channel': 'external:default'})['receipt_id']
            leased = claim(root, IDENTITY, 'side-1')
            result = apply(store, key, {'action': 'answer', 'kind': 'status', 'summary': 'echo', 'reply': 'recorded only',
                                        'no_reply': True}, store.read()['version'])
            self.assertTrue(result['no_reply'])
            finish(root, IDENTITY, key, leased['generation'], result)
            config = {'work_side_intake': {'enabled': True, 'reply_connectors': [{
                'source': '*', 'receiver_id': 'default', 'server': 'walkie', 'tool': 'reply', 'arguments': {'message': '$reply'}}]}}
            sent = prepare(root, IDENTITY, key, config=config)
            self.assertEqual('not_applicable', sent['state'])
            self.assertNotIn('arguments', sent)

    def test_no_reply_applies_only_to_a_plain_answer(self):
        request = {'action': 'create', 'kind': 'request', 'title': 't', 'summary': 's', 'reply': 'r', 'no_reply': True}
        validate(request)
        self.assertNotIn('no_reply', request)
        asks = {'action': 'answer', 'kind': 'status', 'summary': 's', 'reply': 'r', 'no_reply': True, 'ask_main': True}
        validate(asks)
        self.assertNotIn('no_reply', asks)

    def test_the_mod_keeps_ask_main_and_no_reply(self):
        source = SIDE_JS.read_text(encoding='utf-8')
        fields = source.split('const DECISION_FIELDS = new Set([', 1)[1].split('])', 1)[0]
        for name in ("'ask_main'", "'no_reply'", "'relay'"):
            self.assertIn(name, fields)


class BlockerCheckActorTests(unittest.TestCase):
    """The blocker-check side actor (user 2026-11-19): its own instruction, releases across goals."""

    def two_goals(self, store, waits):
        from ciel_runtime_support import work_plan_tree as tree
        out = []
        for title, wait in zip(('goal A', 'goal B'), waits):
            _, goal = store.transaction(lambda d, title=title: tree.add_node(d, None, title))
            _, leaf = store.transaction(lambda d, goal=goal, wait=wait: tree.add_node(d, goal, 'step', waiting_on=wait))
            out.append({'id': leaf, 'waiting_on': wait})
        return out

    def test_a_gate_recheck_releases_waits_of_different_goals_in_one_decision(self):
        import hashlib
        key = hashlib.sha256(b'gate').hexdigest()
        with tempfile.TemporaryDirectory() as raw:
            store = session_work_plan_store(Path(raw), IDENTITY)
            waits = self.two_goals(store, ('CI run 1 green', 'Work host cleanup of lease abc12345'))
            result = apply(store, key, {'action': 'update', 'kind': 'request', 'item_id': waits[0]['id'],
                                        'summary': 'checked both', 'reply': 'released', 'release_waits': waits},
                           store.read()['version'], gate_check=True)
            self.assertEqual(sorted(w['id'] for w in waits), sorted(w['id'] for w in result['released_waits']))
            # An ordinary side event stays limited to the goal of its item.
            other = session_work_plan_store(Path(raw), 'claude:other')
            waits = self.two_goals(other, ('x', 'y'))
            result = apply(other, key, {'action': 'update', 'kind': 'request', 'item_id': waits[0]['id'], 'summary': 's',
                                        'reply': 'r', 'release_waits': waits}, other.read()['version'])
            self.assertEqual([waits[0]['id']], [w['id'] for w in result['released_waits']])

    def test_lease_facts_say_how_the_last_fork_ended(self):
        from ciel_runtime_support.work_gate_check import lease_facts
        leases = {'a': {'scope': IDENTITY, 'item_id': 'n1', 'state': 'submitted', 'created_at': 2},
                  'b': {'scope': IDENTITY, 'item_id': 'n2', 'state': 'orphaned', 'created_at': 1},
                  'c': {'scope': IDENTITY, 'item_id': 'n2', 'state': 'unreported', 'created_at': 3},
                  'd': {'scope': 'claude:else', 'item_id': 'n3', 'state': 'running', 'created_at': 1}}
        facts, waiting = lease_facts(leases, IDENTITY)
        self.assertEqual({'n1': 'submitted', 'n2': '없음 (마지막 포크: unreported)'}, facts)
        self.assertEqual(['n1'], waiting)

    def test_the_blocker_check_actor_has_its_own_managed_instruction(self):
        from ciel_runtime_support import work_workflow_prompts as prompts
        with tempfile.TemporaryDirectory() as raw:
            text = prompts.effective(Path(raw), 'gate_actor')
        for phrase in ('블로커 확인 사이드 액터', '현재 상태를 직접', 'ask_main은 메인 검수 대기를 알릴 때만 씁니다', 'release_waits'):
            self.assertIn(phrase, text)
        # Default instructions are workspace-neutral: no agent or person names.
        for name in ('Daniel', 'Andy', 'Celly', 'Bob', 'KOBE', 'Sarah', 'Eric', 'Grant', 'JUNNY', 'Prody'):
            self.assertNotIn(name, text)
            self.assertNotIn(name, prompts.effective(Path(raw), 'gate_recheck'))
        source = SIDE_JS.read_text(encoding='utf-8')
        self.assertIn("event.route?.source_kind === 'gate_check' && typeof settings.gate_instruction === 'string'", source)


if __name__ == '__main__':
    unittest.main()


class ContextBeforeAckTests(unittest.TestCase):
    """An ACK needs the context first (user 2026-11-19: Andy and Celly acknowledged each other without end)."""

    ROUTE = {'source_kind': 'external_event', 'channel': 'external:default', 'receiver_id': 'default'}
    CONFIG = {'work_side_intake': {'enabled': True, 'reply_connectors': [{
        'preset': 'walkie', 'receiver_id': 'default', 'server': 'walkie', 'source': '*'}]}}

    def event(self, message, *, sender='agent-b', mentions=(), reply='100', requester='person-1'):
        return json.dumps({'data': {'event_type': 'message.created', 'conversation': 'c1', 'message_id': message,
                                    'sender': 'B', 'sender_id': sender, 'text': 'recorded as status',
                                    'reply_to_message_id': reply, 'thread_root_id': '90',
                                    'message_context': {'id': message, 'mention_ids': list(mentions),
                                                        'thread_task': {'status': 'complete', 'requester_id': requester,
                                                                        'priority': 3}}}}, ensure_ascii=False)

    def test_no_message_is_owed_a_receipt_ack(self):
        from ciel_runtime_support.side_intake_inbox import unaddressed_reply
        with tempfile.TemporaryDirectory() as raw:
            inbox = SideIntakeInbox(Path(raw), IDENTITY)
            cases = {'echo': self.event('200'), 'mention': self.event('201', mentions=('me',)),
                     'requester': self.event('202', sender='person-1'), 'root': self.event('203', reply=None)}
            owed = {name: inbox.ack_marker(inbox.receive(text, 'm:' + name, route=self.ROUTE)['receipt_id']).exists()
                    for name, text in cases.items()}
        # User 2026-11-19: one message per event, after it is read; no receipt ACK.
        self.assertEqual({'echo': False, 'mention': False, 'requester': False, 'root': False}, owed)
        self.assertTrue(unaddressed_reply(cases['echo']))
        self.assertFalse(unaddressed_reply('Ciel 게이트 재확인'))

    def flow(self, root, text, decision):
        from ciel_runtime_support.side_understanding_outbox import prepare as understanding, save_proposal
        inbox = SideIntakeInbox(root, IDENTITY)
        store = session_work_plan_store(root, IDENTITY)
        key = inbox.receive(text, 'm:' + text[:40], route=self.ROUTE)['receipt_id']
        leased = claim(root, IDENTITY, 'side-1')
        save_proposal(root, IDENTITY, key, leased['generation'],
                      {'understanding': 'B recorded my note as status and asks nothing.', 'decision': dict(decision),
                       'expected_version': store.read()['version']})
        said = understanding(root, IDENTITY, key, config=self.CONFIG)['state']
        result = apply(store, key, dict(decision), store.read()['version'])
        finish(root, IDENTITY, key, leased['generation'], result)
        return said, prepare(root, IDENTITY, key, config=self.CONFIG)['state']

    def test_a_status_answer_to_an_unaddressed_reply_posts_nothing(self):
        with tempfile.TemporaryDirectory() as raw:
            states = self.flow(Path(raw), self.event('300'),
                               {'action': 'answer', 'kind': 'status', 'summary': 'echo', 'reply': 'recorded'})
        self.assertEqual(('not_applicable', 'not_applicable'), states)

    def test_a_question_in_an_unaddressed_reply_is_still_answered(self):
        with tempfile.TemporaryDirectory() as raw:
            states = self.flow(Path(raw), self.event('301'),
                               {'action': 'answer', 'kind': 'info', 'summary': 'asked for the version', 'reply': '0.2.51'})
        # Not withheld: the host goes on to send it (no reply route exists in this fixture).
        self.assertNotEqual('not_applicable', states[1])

    def test_a_requesters_status_message_is_answered_as_before(self):
        with tempfile.TemporaryDirectory() as raw:
            states = self.flow(Path(raw), self.event('302', sender='person-1'),
                               {'action': 'answer', 'kind': 'status', 'summary': 'state asked', 'reply': 'it is running'})
        self.assertNotEqual('not_applicable', states[1])


class RoundSizeTests(unittest.TestCase):
    """A round carries few gates so the actor looks each one up (user 2026-11-19)."""

    def test_p0_gates_lead_every_round_and_the_others_take_turns(self):
        from ciel_runtime_support.work_gate_check import round_gates, round_rows
        rows = [{'id': 'p%d' % i, 'priority': 'P0'} for i in range(4)] + [{'id': 'n%d' % i, 'priority': 'P1'} for i in range(25)]
        seen, offsets = [], None
        for _ in range(5):
            picked, offsets = round_rows(rows, 10, offsets)
            self.assertEqual(['p0', 'p1', 'p2', 'p3'], [row['id'] for row in picked[:4]])
            self.assertEqual(10, len(picked))
            seen += [row['id'] for row in picked[4:]]
        # Five rounds of six cover all 25 other gates and start over.
        self.assertEqual({'n%d' % i for i in range(25)}, set(seen))
        self.assertEqual((10, 25, 10), (round_gates({}), round_gates({'work_gate_check': {'round_gates': 25}}),
                                        round_gates({'work_gate_check': {'round_gates': 0}})))


class ReviewPendingTests(unittest.TestCase):
    """The blocker-check actor tells main about results awaiting its review (user 2026-11-19)."""

    def test_the_event_names_the_results_awaiting_main_and_the_actor_may_ask_main(self):
        import time
        from ciel_runtime_support import work_gate_check as gate
        from ciel_runtime_support.work_workflow_prompts import DEFAULTS
        rows = [{'id': 'n1098', 'priority': 'P0', 'title': 't', 'root_id': 'n1006', 'root_title': 'g', 'status': 'pending',
                 'waiting_on': 'n1097 reviewed by main', 'evidence': '', 'note': ''}]
        told = gate.tick_event({}, rows, 'd', time.time(), review_pending=['n1097', 'n1074'])['text']
        self.assertIn('메인 검수 대기: 포크 결과 2건', told)
        self.assertIn('n1097, n1074', told)
        self.assertNotIn('메인 검수 대기', gate.tick_event({}, rows, 'd', time.time())['text'])
        self.assertIn('"ask_main": true', DEFAULTS['gate_actor'])
        self.assertIn('submitted이면 포크는 일을 끝냈고 메인의 검수만 남은', DEFAULTS['gate_actor'])
