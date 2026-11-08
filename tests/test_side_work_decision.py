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

    def test_existing_update_and_priority_preserve_identity(self):
        doc, item = self.store.transaction(lambda d: tree.add_node(d, None, 'existing'))
        result = apply(self.store, self.key, {**self.base, 'action': 'update', 'item_id': item, 'note': 'new detail'}, doc['version'])
        self.assertEqual(1, len(self.store.read()['nodes']))
        self.assertEqual(item, result['item_id'])
        next_key = hashlib.sha256(b'priority').hexdigest()
        apply(self.store, next_key, {**self.base, 'action': 'prioritize', 'item_id': item, 'priority': 'P1'}, result['version'])
        self.assertEqual('P1', self.store.read()['nodes'][0]['priority'])
        self.assertEqual('planning', self.store.read()['phase'])

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

    def test_unknown_or_done_target_does_not_mutate(self):
        doc, item = self.store.transaction(lambda d: tree.add_node(d, None, 'existing'))
        self.store.transaction(lambda d: tree.update_node(d, item, status='done', evidence='verified'))
        with self.assertRaises(tree.WorkPlanError):
            apply(self.store, self.key, {**self.base, 'action': 'update', 'item_id': 'missing', 'note': 'x'}, 1)
        with self.assertRaises(tree.WorkPlanError):
            apply(self.store, self.key, {**self.base, 'action': 'prioritize', 'item_id': item, 'priority': 'P1'}, 1)
        self.assertNotIn('side_decisions', self.store.read())

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
