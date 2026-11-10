import hashlib
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.work_plan_store import WorkPlanStore
from ciel_runtime_support.side_work_decision import apply
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
from ciel_runtime_support.side_intake_processing import claim, finish


class SideDecisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = WorkPlanStore(self.root/'board.json')
        self.key = hashlib.sha256(b'event').hexdigest()
        self.base = {'action': 'answer', 'summary': 'Question answered', 'reply': 'The answer'}

    def test_answer_does_not_add_work_and_replay_is_idempotent(self):
        first = apply(self.store, self.key, self.base, 0)
        self.assertEqual([], self.store.read()['nodes'])
        self.assertFalse(first['duplicate'])
        again = apply(self.store, self.key, self.base, 0)
        self.assertTrue(again['duplicate'])
        self.assertEqual(1, self.store.read()['version'])

    def test_new_contract_composes_no_queue_and_committed_queue_messages(self):
        answered = apply(self.store, self.key, {**self.base, 'kind': 'status'}, 0)
        self.assertEqual([], self.store.read()['nodes'])
        self.assertEqual('The answer\n\n큐에 등록하지 않았습니다.', answered['final_text'])
        self.assertNotIn('queue', answered)

        doc, active = self.store.transaction(lambda d: tree.add_node(d, None, 'active'))
        self.store.transaction(lambda d: tree.add_node(d, None, 'older', priority='P3'))
        created = apply(self.store, hashlib.sha256(b'new-contract').hexdigest(),
                        {**self.base, 'kind': 'request', 'action': 'create', 'title': 'queued request',
                         'note': 'restated request', 'priority': 'P2'}, doc['version'])
        self.assertEqual(active, self.store.read()['active_root'])
        self.assertEqual(('queued', 1, 0), (created['queue']['state'], created['queue']['position'], created['queue']['ahead']))
        self.assertTrue(created['final_text'].startswith('ACK — The answer'))
        self.assertIn(created['item_id'] + ' 등록 · P2 · 대기 1번째 (앞 0건).', created['final_text'])

    def test_existing_request_can_update_priority_and_queue_place_atomically(self):
        doc, active = self.store.transaction(lambda d: tree.add_node(d, None, 'active'))
        doc, first = self.store.transaction(lambda d: tree.add_node(d, None, 'first'))
        doc, target = self.store.transaction(lambda d: tree.add_node(d, None, 'target'))
        doc, last = self.store.transaction(lambda d: tree.add_node(d, None, 'last'))
        result = apply(self.store, self.key, {**self.base, 'kind': 'request', 'action': 'update',
                       'item_id': target, 'note': 'new facts', 'priority': 'P1', 'queue_position': 3}, doc['version'])
        self.assertEqual(active, self.store.read()['active_root'])
        self.assertEqual([first, last, target], [n['id'] for n in tree.queued_roots(self.store.read())])
        self.assertEqual(('P1', 3, 2), (result['priority'], result['queue']['position'], result['queue']['ahead']))
        self.assertIn(target + ' 갱신 · P1 · 대기 3번째 (앞 2건).', result['final_text'])

    def test_create_rechecks_once_when_unfinished_goals_changed(self):
        from ciel_runtime_support.side_work_decision import WorkChanged, unfinished_digest
        doc, _ = self.store.transaction(lambda d: tree.add_node(d, None, 'active'))
        observed = unfinished_digest(doc)
        self.store.transaction(lambda d: tree.add_node(d, None, 'main added same work'))
        with self.assertRaises(WorkChanged):
            apply(self.store, self.key, {**self.base, 'kind': 'request', 'action': 'create',
                  'title': 'same work', 'note': 'details'}, doc['version'], observed_digest=observed)
        self.assertEqual(2, len(self.store.read()['nodes']))
        self.assertNotIn('side_decisions', self.store.read())

    def test_missing_create_title_and_update_note_fall_back_to_summary(self):
        created = apply(self.store, self.key, {**self.base, 'kind': 'request', 'action': 'create'}, 0)
        self.assertEqual('Question answered', created['title'])
        next_key = hashlib.sha256(b'summary-update').hexdigest()
        updated = apply(self.store, next_key, {**self.base, 'kind': 'request', 'action': 'update',
                        'item_id': created['item_id']}, created['version'])
        self.assertEqual(created['item_id'], updated['item_id'])
        self.assertIn('Side request: Question answered', self.store.read()['nodes'][0]['note'])

    def test_existing_update_and_priority_preserve_identity(self):
        doc, item = self.store.transaction(lambda d: tree.add_node(d, None, 'existing'))
        result = apply(self.store, self.key, {**self.base, 'action': 'update', 'item_id': item, 'note': 'new detail'}, doc['version'])
        self.assertEqual(1, len(self.store.read()['nodes']))
        self.assertEqual(item, result['item_id'])
        next_key = hashlib.sha256(b'priority').hexdigest()
        apply(self.store, next_key, {**self.base, 'action': 'prioritize', 'item_id': item, 'priority': 'P1'}, result['version'])
        self.assertEqual('P1', self.store.read()['nodes'][0]['priority'])
        self.assertEqual('planning', self.store.read()['phase'])

    def test_approval_for_waiting_item_requests_main_without_demoting_goal(self):
        # Andy 2026-11-08: a side update on an executing goal cleared its approval
        # and left the n993 wait in place while main stayed idle.
        doc, goal = self.store.transaction(lambda d: tree.add_node(d, None, 'implement fix'))
        doc, gate = self.store.transaction(lambda d: tree.add_node(d, goal, 'approval', waiting_on='Daniel approval 118269'))
        self.store.transaction(lambda d: tree.set_phase(d, tree.PHASE_EXECUTING, goal))
        result = apply(self.store, self.key, {**self.base, 'action': 'update', 'item_id': gate,
                       'note': '118269 approved', 'reply': 'main resume pending'}, self.store.read()['version'])
        doc = self.store.read()
        self.assertEqual(tree.PHASE_EXECUTING, doc['phase'])
        self.assertEqual('requested', result['handoff'])
        self.assertEqual({'kind': 'resume', 'waits': [{'id': gate, 'waiting_on': 'Daniel approval 118269'}], 'question': ''},
                         result['main_handoff'])
        row = doc['side_handoffs'][self.key]
        self.assertEqual(('resume', 'pending'), (row['kind'], row['wake']['state']))
        self.assertEqual('Daniel approval 118269', next(n for n in doc['nodes'] if n['id'] == gate)['waiting_on'])
        self.assertIn('118269 approved', next(n for n in doc['nodes'] if n['id'] == gate)['note'])

    def test_plain_answer_and_unrelated_update_do_not_wake_main(self):
        self.assertEqual('not_required', apply(self.store, self.key, self.base, 0)['handoff'])
        doc, item = self.store.transaction(lambda d: tree.add_node(d, None, 'idle goal'))
        other = hashlib.sha256(b'other').hexdigest()
        result = apply(self.store, other, {**self.base, 'action': 'update', 'item_id': item, 'note': 'fyi'}, doc['version'])
        self.assertEqual('not_required', result['handoff'])
        self.assertEqual({}, self.store.read().get('side_handoffs', {}))

    def test_p1_create_requests_durable_main_wake(self):
        result = apply(self.store, self.key, {**self.base, 'action': 'create', 'title': 'urgent',
                       'note': 'impact', 'priority': 'P1'}, 0)
        row = self.store.read()['side_handoffs'][self.key]
        self.assertEqual(('requested', 'p1', 'pending'), (result['handoff'], row['kind'], row['wake']['state']))

    def test_handoff_clears_only_recorded_unchanged_waits(self):
        from ciel_runtime_support.side_handoff import acknowledge
        doc, goal = self.store.transaction(lambda d: tree.add_node(d, None, 'goal'))
        doc, gate = self.store.transaction(lambda d: tree.add_node(d, goal, 'gate', waiting_on='approval A'))
        doc, other = self.store.transaction(lambda d: tree.add_node(d, goal, 'other', waiting_on='approval B'))
        result = apply(self.store, self.key, {**self.base, 'action': 'update', 'item_id': gate, 'note': 'A approved'},
                       doc['version'])
        with self.assertRaisesRegex(tree.WorkPlanError, 'unrecorded wait'):
            acknowledge(self.store, self.key, clear_wait=[other], expected_version=result['version'])
        self.store.transaction(lambda d: tree.update_node(d, gate, waiting_on='approval A2'))
        with self.assertRaisesRegex(tree.WorkPlanError, 'changed'):
            acknowledge(self.store, self.key, clear_wait=[gate], expected_version=self.store.read()['version'])
        self.store.transaction(lambda d: tree.update_node(d, gate, waiting_on='approval A'))
        self.assertEqual('requested', self.store.read()['side_handoffs'][self.key]['state'])
        ack = acknowledge(self.store, self.key, clear_wait=[gate], expected_version=self.store.read()['version'])
        doc = self.store.read()
        self.assertEqual(('acknowledged', 'acknowledged'), (ack['state'], ack['wake']['state']))
        self.assertEqual('', next(n for n in doc['nodes'] if n['id'] == gate)['waiting_on'])
        self.assertEqual('approval B', next(n for n in doc['nodes'] if n['id'] == other)['waiting_on'])
        again = acknowledge(self.store, self.key, clear_wait=[gate], expected_version=ack['version'])
        self.assertEqual(ack['version'], again['version'])

    def test_answer_or_clarify_naming_p1_gate_never_creates_handoff(self):
        doc, goal = self.store.transaction(lambda d: tree.add_node(d, None, 'P1 goal', priority='P1'))
        doc, gate = self.store.transaction(lambda d: tree.add_node(d, goal, 'gate', waiting_on='approval'))
        for index, action in enumerate(('answer', 'clarify')):
            key = hashlib.sha256(('relay-' + action).encode()).hexdigest()
            result = apply(self.store, key, {**self.base, 'action': action, 'item_id': gate}, self.store.read()['version'])
            self.assertEqual('not_required', result['handoff'])
        self.assertEqual({}, self.store.read().get('side_handoffs', {}))

    def test_main_reply_followup_never_wakes_main_again(self):
        # Owned proof 2026-11-08: main's handoff answer came back as a side event,
        # the side updated the same P1 gate, and main was woken four times.
        doc, goal = self.store.transaction(lambda d: tree.add_node(d, None, 'P1 goal', priority='P1'))
        doc, gate = self.store.transaction(lambda d: tree.add_node(d, goal, 'gate', waiting_on='approval'))
        result = apply(self.store, self.key, {**self.base, 'action': 'update', 'item_id': gate, 'note': 'relay'},
                       doc['version'], main_reply=True)
        self.assertEqual('not_required', result['handoff'])
        self.assertEqual({}, self.store.read().get('side_handoffs', {}))

    def test_wait_gate_kind_takes_precedence_over_p1(self):
        doc, goal = self.store.transaction(lambda d: tree.add_node(d, None, 'P1 goal', priority='P1'))
        doc, gate = self.store.transaction(lambda d: tree.add_node(d, goal, 'gate', waiting_on='approval'))
        result = apply(self.store, self.key, {**self.base, 'action': 'update', 'item_id': gate, 'note': 'ok'}, doc['version'])
        self.assertEqual('resume', result['main_handoff']['kind'])

    def test_priority_handoff_selects_existing_goal_without_approving(self):
        from ciel_runtime_support.side_handoff import acknowledge
        doc, first = self.store.transaction(lambda d: tree.add_node(d, None, 'main'))
        doc, target = self.store.transaction(lambda d: tree.add_node(d, None, 'urgent existing'))
        result = apply(self.store, self.key, {**self.base, 'action': 'prioritize', 'item_id': target, 'priority': 'P1'}, doc['version'])
        self.assertEqual(first, self.store.read()['active_root'])
        self.assertEqual([first,target],[n['id'] for n in self.store.read()['nodes'] if n['parent_id'] is None])
        self.assertEqual('requested', self.store.read()['side_handoffs'][self.key]['state'])
        ack = acknowledge(self.store, self.key, activate=True, expected_version=result['version'])
        self.assertEqual(target, self.store.read()['active_root'])
        self.assertEqual('planning', self.store.read()['phase'])
        self.assertTrue(ack['activated'])
        self.assertEqual(2, len(self.store.read()['nodes']))

    def test_gate_handoff_activation_selects_owning_goal_and_clears_recorded_wait(self):
        # ciel-walkie 2026-11-10: the P1 handoff for gate n1235 failed
        # "handoff target is not an unfinished goal" and stayed requested.
        from ciel_runtime_support.side_handoff import acknowledge
        doc, main = self.store.transaction(lambda d: tree.add_node(d, None, 'main'))
        doc, goal = self.store.transaction(lambda d: tree.add_node(d, None, 'P1 goal', priority='P1'))
        doc, gate = self.store.transaction(lambda d: tree.add_node(d, goal, 'gate', waiting_on='approval'))
        result = apply(self.store, self.key, {**self.base, 'action': 'update', 'item_id': gate, 'note': 'approved'},
                       doc['version'])
        self.assertEqual(gate, self.store.read()['side_handoffs'][self.key]['item_id'])
        ack = acknowledge(self.store, self.key, activate=True, clear_wait=[gate], expected_version=result['version'])
        doc = self.store.read()
        self.assertEqual(('acknowledged', True, [gate]), (ack['state'], ack['activated'], ack['clear_wait']))
        self.assertEqual(goal, doc['active_root'])
        self.assertEqual('', next(n for n in doc['nodes'] if n['id'] == gate)['waiting_on'])
        self.assertNotEqual(main, doc['active_root'])

    def test_side_releases_the_wait_its_event_satisfies_without_a_main_handoff(self):
        doc, goal = self.store.transaction(lambda d: tree.add_node(d, None, 'release goal'))
        self.store.transaction(lambda d: tree.update_node(d, goal, status='in_progress'))
        doc, gate = self.store.transaction(lambda d: tree.add_node(d, goal, 'deploy gate', waiting_on='approval from Andy via Walkie'))
        before_phase = self.store.read()['phase']
        result = apply(self.store, self.key, {**self.base, 'kind': 'request', 'action': 'update', 'item_id': gate,
                       'note': 'Andy approved', 'release_waits': [{'id': gate, 'waiting_on': 'approval from Andy via Walkie'}]},
                       doc['version'])
        doc = self.store.read()
        node = next(n for n in doc['nodes'] if n['id'] == gate)
        self.assertEqual('', node['waiting_on'])
        self.assertIn('Side released wait "approval from Andy via Walkie"', node['note'])
        self.assertEqual([{'id': gate, 'waiting_on': 'approval from Andy via Walkie'}], result['released_waits'])
        self.assertEqual('not_required', result['handoff'])
        self.assertEqual({}, doc.get('side_handoffs', {}))
        self.assertFalse(result['execution_approved'])
        self.assertEqual(before_phase, doc['phase'])
        # The same decision replayed is the committed result, not a second release.
        self.assertTrue(apply(self.store, self.key, {**self.base, 'kind': 'request', 'action': 'update', 'item_id': gate,
                        'note': 'Andy approved', 'release_waits': [{'id': gate, 'waiting_on': 'approval from Andy via Walkie'}]},
                        doc['version'])['duplicate'])

    def test_changed_or_foreign_wait_is_not_released_and_still_reaches_main(self):
        doc, goal = self.store.transaction(lambda d: tree.add_node(d, None, 'goal'))
        doc, gate = self.store.transaction(lambda d: tree.add_node(d, goal, 'gate', waiting_on='CI build 41 result'))
        doc, other_goal = self.store.transaction(lambda d: tree.add_node(d, None, 'other'))
        doc, foreign = self.store.transaction(lambda d: tree.add_node(d, other_goal, 'foreign', waiting_on='review'))
        result = apply(self.store, self.key, {**self.base, 'kind': 'request', 'action': 'update', 'item_id': gate,
                       'note': 'build 40 passed', 'release_waits': [{'id': gate, 'waiting_on': 'CI build 40 result'},
                                                                     {'id': foreign, 'waiting_on': 'review'}]},
                       doc['version'])
        doc = self.store.read()
        self.assertEqual('CI build 41 result', next(n for n in doc['nodes'] if n['id'] == gate)['waiting_on'])
        self.assertEqual('review', next(n for n in doc['nodes'] if n['id'] == foreign)['waiting_on'])
        self.assertNotIn('released_waits', result)
        self.assertEqual([{'id': gate, 'reason': 'wait changed since side observation'},
                          {'id': foreign, 'reason': 'not an unfinished node of this goal'}], result['unreleased_waits'])
        # The unreleased gate keeps the ordinary resume handoff for main.
        self.assertEqual('requested', result['handoff'])
        self.assertEqual('resume', result['main_handoff']['kind'])

    def test_release_waits_outside_update_is_dropped_not_fatal(self):
        from ciel_runtime_support.side_work_decision import validate
        answer = validate({**self.base, 'kind': 'status', 'release_waits': [{'id': 'n1', 'waiting_on': 'x'}]})
        self.assertNotIn('release_waits', answer)
        with self.assertRaises(tree.WorkPlanError):
            validate({**self.base, 'action': 'update', 'item_id': 'n1', 'release_waits': [{'id': 'n1'}]})

    def test_gate_handoff_activation_refuses_finished_goal_without_mutation(self):
        from ciel_runtime_support.side_handoff import acknowledge
        doc, goal = self.store.transaction(lambda d: tree.add_node(d, None, 'P1 goal', priority='P1'))
        doc, gate = self.store.transaction(lambda d: tree.add_node(d, goal, 'gate', waiting_on='approval'))
        apply(self.store, self.key, {**self.base, 'action': 'update', 'item_id': gate, 'note': 'approved'},
              doc['version'])
        def finish_goal(d):
            for node in d['nodes']:
                node.update(status='done', evidence='verified', waiting_on='')
        doc, _ = self.store.transaction(finish_goal)
        with self.assertRaisesRegex(tree.WorkPlanError, 'not an unfinished goal'):
            acknowledge(self.store, self.key, activate=True, expected_version=doc['version'])
        after = self.store.read()
        self.assertEqual(doc['version'], after['version'])
        self.assertEqual('requested', after['side_handoffs'][self.key]['state'])

    def test_prioritizing_child_moves_its_existing_goal_without_new_root(self):
        doc, main = self.store.transaction(lambda d: tree.add_node(d, None, 'active'))
        self.store.transaction(lambda d: tree.add_node(d, None, 'other queued'))
        doc, goal = self.store.transaction(lambda d: tree.add_node(d, None, 'target goal'))
        doc, child = self.store.transaction(lambda d: tree.add_node(d, goal, 'urgent child'))
        result = apply(self.store, self.key, {**self.base, 'action': 'prioritize',
                       'item_id': child, 'priority': 'P1'}, doc['version'])
        doc = self.store.read()
        self.assertEqual(goal, result['item_id'])
        self.assertEqual('P1', next(n for n in doc['nodes'] if n['id'] == goal)['priority'])
        self.assertEqual(main, doc['active_root'])
        self.assertEqual(goal, tree.queued_roots(doc)[0]['id'])
        self.assertEqual('planning', doc['phase'])
        self.assertEqual(4, len(doc['nodes']))

    def test_unknown_target_does_not_mutate_but_done_target_becomes_status(self):
        doc, item = self.store.transaction(lambda d: tree.add_node(d, None, 'existing'))
        self.store.transaction(lambda d: tree.update_node(d, item, status='done', evidence='verified'))
        with self.assertRaises(tree.WorkPlanError):
            apply(self.store, self.key, {**self.base, 'action': 'update', 'item_id': 'missing', 'note': 'x'}, 1)
        result = apply(self.store, self.key, {**self.base, 'kind': 'request', 'action': 'prioritize',
                       'item_id': item, 'priority': 'P1'}, 1)
        self.assertEqual(('answer', 'status', 'not_required'),
                         (result['action'], result['kind'], result['handoff']))
        self.assertEqual('done', self.store.read()['nodes'][0]['status'])
        self.assertEqual('The answer\n\n큐에 등록하지 않았습니다.', result['final_text'])

    def test_classification_used_as_action_is_normalized_to_answer(self):
        for kind in ('status', 'info'):
            key = hashlib.sha256(('class-action-' + kind).encode()).hexdigest()
            result = apply(self.store, key, {**self.base, 'action': kind, 'kind': kind},
                           self.store.read()['version'])
            self.assertEqual(('answer', kind), (result['action'], result['kind']))

    def test_status_answer_ignores_copied_work_edit_fields(self):
        result = apply(self.store, self.key, {**self.base, 'kind': 'status', 'item_id': 'n1',
                       'title': 'copied', 'note': 'copied', 'priority': 'P1', 'queue_position': 2}, 0)
        self.assertEqual(('answer', 'status', 'not_required'),
                         (result['action'], result['kind'], result['handoff']))
        self.assertEqual([], self.store.read()['nodes'])

    def test_decision_from_older_snapshot_commits_on_current_board(self):
        # Observed 2026-11-07: main committed 1-2 versions while each fork
        # decided, and every stale-version decision was lost.
        self.store.transaction(lambda d: tree.add_node(d, None, 'active', priority='P3'))
        seen = self.store.read()['version']
        self.store.transaction(lambda d: tree.add_node(d, None, 'main queued meanwhile', priority='P3'))
        self.store.transaction(lambda d: tree.add_node(d, None, 'another', priority='P2'))
        result = apply(self.store, self.key, {**self.base, 'action': 'create', 'title': 'Urgent external',
                       'note': 'deadline today', 'priority': 'P1'}, seen)
        doc = self.store.read()
        self.assertEqual(seen, result['based_on_version'])
        self.assertEqual(doc['version'], result['version'])
        self.assertEqual(('P1', 'Urgent external'), (result['priority'], result['title']))
        self.assertEqual(result['item_id'], tree.queued_roots(doc)[0]['id'])
        answered = apply(self.store, hashlib.sha256(b'q').hexdigest(), self.base, seen)
        self.assertFalse(answered['duplicate'])
        self.assertEqual(seen, answered['based_on_version'])
        self.assertEqual(4, len(self.store.read()['nodes']))

    def test_answer_receipt_consumes_pending_without_item(self):
        inbox = SideIntakeInbox(self.root, 'claude:test')
        row = inbox.receive('question', 'event')
        lease = claim(self.root, 'claude:test', 'actor', now=100)
        self.assertEqual(row['receipt_id'], lease['receipt_id'])
        self.assertIsNone(claim(self.root, 'claude:test', 'other', now=101))
        receipt = finish(self.root, 'claude:test', row['receipt_id'], lease['generation'], self.base, now=102)
        self.assertEqual('processed', receipt['stage'])
        self.assertIsNone(receipt['item_id'])
        self.assertEqual([], inbox.pending())
        self.assertTrue(inbox.receive('question', 'event')['duplicate'])

    def test_events_for_one_source_message_get_one_side_actor_and_one_reply(self):
        # Prody 2026-11-09 18:40Z: message.created and task.auto_assigned for
        # Walkie 119649 each started an actor, and two replies were sent.
        import json
        from ciel_runtime_support.side_reply_outbox import pending
        def walkie(kind, event_id):
            return json.dumps({'id': event_id, 'type': 'com.ciel.walkie.support.message',
                'data': {'event_type': kind, 'conversation': '3c720143-016f-4061-850e-4546bb8da35c',
                         'message_id': '119649', 'text': 'list the Work queue'}})
        inbox = SideIntakeInbox(self.root, 'claude:test')
        first = inbox.receive(walkie('message.created', 'a'), 'source:a')
        second = inbox.receive(walkie('task.auto_assigned', 'b'), 'source:b')
        lease = claim(self.root, 'claude:test', 'actor-1', now=100)
        handled = lease['receipt_id']
        other = ({first['receipt_id'], second['receipt_id']} - {handled}).pop()
        # The first actor still holds its source: the duplicate waits instead of
        # starting a concurrent second actor.
        self.assertIsNone(claim(self.root, 'claude:test', 'actor-2', now=101))
        finish(self.root, 'claude:test', handled, lease['generation'],
               {**self.base, 'kind': 'status', 'delivery': 'pending', 'final_text': 'queue'}, now=102)
        self.assertIsNone(claim(self.root, 'claude:test', 'actor-2', now=103))
        duplicate = inbox._load(other)
        self.assertEqual(('processed', 'duplicate_source', handled),
                         (duplicate['stage'], duplicate['result']['delivery'], duplicate['result']['duplicate_of']))
        self.assertEqual([handled], [row['receipt_id'] for row in pending(self.root, 'claude:test')])

    def test_side_answer_leaves_a_main_review_note_until_reviewed(self):
        from ciel_runtime_support.side_review import review
        result = apply(self.store, self.key, {**self.base, 'kind': 'status'}, 0,
                       source='walkie:3c720143:119649')
        row = self.store.read()['side_reviews'][self.key]
        self.assertEqual(('pending', 'walkie:3c720143:119649', 'status'),
                         (row['state'], row['source'], row['kind']))
        self.assertEqual(result['final_text'], row['reply'])
        reviewed = review(self.store, [self.key], verdict='ok', note='', expected_version=self.store.read()['version'])
        self.assertEqual('reviewed', reviewed['reviews'][0]['state'])
        self.assertEqual('reviewed', self.store.read()['side_reviews'][self.key]['state'])
        with self.assertRaises(tree.WorkPlanError):
            review(self.store, ['0' * 64], verdict='ok', note='', expected_version=self.store.read()['version'])

    def test_structured_p1_is_picked_before_ordinary_backlog(self):
        inbox=SideIntakeInbox(self.root,'claude:test')
        inbox.receive('ordinary request','ordinary')
        urgent=inbox.receive('{"data":{"priority":"P1","text":"urgent"}}','urgent')
        picked=claim(self.root,'claude:test','actor')
        self.assertEqual(urgent['receipt_id'],picked['receipt_id'])

    def test_p1_precedes_aged_backlog_and_aged_precedes_fresh_ranks(self):
        # Observed 2026-11-07: urgent #116793 waited behind every aged event.
        import time
        inbox=SideIntakeInbox(self.root,'claude:test')
        ordinary=inbox.receive('ordinary request','ordinary')
        stamp=time.time()
        row=inbox._load(ordinary['receipt_id'])
        row['received_at']=stamp-120
        inbox._save_file(inbox._event_path('pending',ordinary['receipt_id']),row)
        fresh=inbox.receive('{"data":{"priority":"P2","text":"soon"}}','fresh-p2')
        urgent=inbox.receive('{"data":{"priority":"P1","text":"urgent"}}','urgent')
        picked=[claim(self.root,'claude:test','actor',now=stamp+1)['receipt_id'] for _ in range(3)]
        self.assertEqual([urgent['receipt_id'],ordinary['receipt_id'],fresh['receipt_id']],picked)

    def test_walkie_thread_task_priority_indexes_source_urgency(self):
        from ciel_runtime_support.side_intake_inbox import source_priority
        import json
        def walkie(value):
            return json.dumps({'specversion':'1.0','type':'com.ciel.walkie.message',
                'data':{'event_type':'message.created','text':'fix it now',
                        'message_context':{'id':'116793','thread_task':{'priority':value,'status':'open'}}}})
        self.assertEqual('P1',source_priority(walkie(1)))
        self.assertEqual('P3',source_priority(walkie(3)))
        self.assertEqual('P2',source_priority(walkie('P2')))
        self.assertIsNone(source_priority(walkie(7)))
        self.assertIsNone(source_priority(walkie(True)))
        self.assertIsNone(source_priority('plain text'))
        self.assertEqual('P4',source_priority('{"data":{"priority":4}}'))
        inbox=SideIntakeInbox(self.root,'claude:test')
        ordinary=inbox.receive(walkie(3),'ordinary')
        urgent=inbox.receive(walkie(1),'urgent')
        self.assertTrue((inbox.directory/'priority'/'P1'/urgent['receipt_id'][:2]/(urgent['receipt_id']+'.json')).exists())
        # Both slots full: the reserved claim takes only P1, and none is left.
        first=claim(self.root,'claude:test','reserved',urgent_only=True)
        self.assertEqual((urgent['receipt_id'],'P1'),(first['receipt_id'],first['source_priority']))
        self.assertIsNone(claim(self.root,'claude:test','reserved',urgent_only=True))
        self.assertEqual(ordinary['receipt_id'],claim(self.root,'claude:test','actor')['receipt_id'])

    def test_missing_priority_index_recovers_from_durable_pending(self):
        from unittest.mock import patch
        inbox = SideIntakeInbox(self.root, 'claude:test')
        inbox.receive('ordinary request', 'ordinary')
        text = '{"data":{"priority":"P1","text":"urgent"}}'
        with patch.object(inbox, '_index_priority', side_effect=OSError('index write interrupted')):
            with self.assertRaises(OSError):
                inbox.receive(text, 'urgent')
        key = hashlib.sha256(b'urgent').hexdigest()
        marker = inbox.directory / 'priority' / 'P1' / key[:2] / (key + '.json')
        self.assertFalse(marker.exists())
        self.assertEqual(text, inbox._load(key)['text'])
        picked = claim(self.root, 'claude:test', 'restarted-actor')
        self.assertEqual(key, picked['receipt_id'])
        self.assertTrue(marker.exists())
        finish(self.root, 'claude:test', key, picked['generation'], self.base)
        self.assertFalse(marker.exists())
        self.assertFalse(inbox._event_path('pending', key).exists())
        self.assertEqual('processed', inbox._load(key)['stage'])

    def test_failed_receipt_commit_preserves_priority_and_original(self):
        from unittest.mock import patch
        inbox = SideIntakeInbox(self.root, 'claude:test')
        text = '{"priority":"P1","text":"urgent"}'
        key = inbox.receive(text, 'urgent')['receipt_id']
        picked = claim(self.root, 'claude:test', 'actor')
        original_save = SideIntakeInbox._save_file

        def fail_receipt(path, row):
            if path.parent.parent.name == 'receipts':
                raise OSError('receipt disk full')
            return original_save(path, row)

        with patch.object(SideIntakeInbox, '_save_file', side_effect=fail_receipt):
            with self.assertRaises(OSError):
                finish(self.root, 'claude:test', key, picked['generation'], self.base)
        self.assertTrue(inbox._event_path('pending', key).exists())
        self.assertTrue((inbox.directory / 'priority' / 'P1' / key[:2] / (key + '.json')).exists())
        self.assertEqual(text, inbox._load(key)['text'])
        finish(self.root, 'claude:test', key, picked['generation'], self.base)
        self.assertFalse(inbox._event_path('pending', key).exists())

    def test_heartbeat_extends_live_claim_but_cannot_revive_expired_actor(self):
        from ciel_runtime_support.side_intake_processing import renew
        inbox = SideIntakeInbox(self.root, 'claude:test')
        inbox.receive('question', 'event')
        event = claim(self.root, 'claude:test', 'actor', now=100)
        renewal = renew(self.root, 'claude:test', event['receipt_id'], event['generation'], now=200)
        self.assertEqual(320, renewal['lease_until'])
        self.assertIsNone(claim(self.root, 'claude:test', 'other', now=230))
        with self.assertRaises(tree.WorkPlanError):
            renew(self.root, 'claude:test', event['receipt_id'], 'wrong', now=240)
        with self.assertRaises(tree.WorkPlanError):
            renew(self.root, 'claude:test', event['receipt_id'], event['generation'], now=321)
        newer = claim(self.root, 'claude:test', 'other', now=322)
        self.assertNotEqual(event['generation'], newer['generation'])

    def test_expired_claim_cannot_commit_after_reclaim(self):
        SideIntakeInbox(self.root, 'claude:test').receive('question', 'event')
        old = claim(self.root, 'claude:test', 'a', now=100, lease_seconds=1)
        new = claim(self.root, 'claude:test', 'b', now=102)
        with self.assertRaises(tree.WorkPlanError):
            finish(self.root, 'claude:test', old['receipt_id'], old['generation'], self.base, now=103)
        finish(self.root, 'claude:test', new['receipt_id'], new['generation'], self.base, now=103)
