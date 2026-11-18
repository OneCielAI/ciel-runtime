"""Idle gate re-check and live-item relay (user requirements 2026-11-15).

Real backend: the MCP dispatch (dispatch_work_tool) onto the real session Work
store and the durable side inbox in a temporary directory.
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support import work_gate_check as gate
from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
from ciel_runtime_support.side_work_decision import apply, validate
from ciel_runtime_support.work_mcp_tools import dispatch_work_tool
from ciel_runtime_support.work_plan_store import WorkPlanStore, session_work_plan_store

HOST = {'token': 'x' * 40}


class GateCheckUnitTests(unittest.TestCase):
    def test_interval_default_off_and_bounds(self):
        config = {}
        self.assertEqual(5, gate.minutes(config))
        gate.set_minutes(config, '10')
        self.assertEqual({'work_gate_check': {'minutes': 10}}, config)
        gate.set_minutes(config, 'off')
        self.assertEqual(0, gate.minutes(config))
        gate.set_minutes(config, 'reset')
        self.assertEqual({}, config)
        for bad in ('1441', '-1', 'soon'):
            with self.assertRaises(ValueError):
                gate.set_minutes(config, bad)
        self.assertEqual(5, gate.minutes({'work_gate_check': {'minutes': 99999}}))
        self.assertEqual(5, gate.minutes({'work_gate_check': {'minutes': True}}))

    def _doc(self):
        store = WorkPlanStore(Path(self.tmp.name) / 'board.json')
        doc, first = store.transaction(lambda d: tree.add_node(d, None, 'active goal'))
        doc, wait = store.transaction(lambda d: tree.add_node(d, first, 'deploy', waiting_on='CI run 42 green'))
        doc, other = store.transaction(lambda d: tree.add_node(d, None, 'queued goal'))
        doc, blocked = store.transaction(lambda d: tree.add_node(d, other, 'needs key'))
        store.transaction(lambda d: tree.update_node(d, blocked, status='blocked'))
        doc, done = store.transaction(lambda d: tree.add_node(d, first, 'old wait', waiting_on='x'))
        store.transaction(lambda d: tree.update_node(d, done, status='done', evidence='checked'))
        return store, {'first': first, 'wait': wait, 'other': other, 'blocked': blocked, 'done': done}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_the_blocker_instruction_matches_the_side_contract_and_reaches_main(self):
        # Stage 3, 2026-11-17: the event text asked for "action advise" while the
        # side instruction says "Legacy advise is not used" and a request must
        # create/update/prioritize; advise with kind request is refused.
        from ciel_runtime_support.side_intake_settings import DEFAULT_SIDE_INSTRUCTION
        store, ids = self._doc()
        text = gate.event_text(gate.gates(store.read()), 5)
        self.assertNotIn("action advise", text)
        self.assertIn("Legacy advise is not used", DEFAULT_SIDE_INSTRUCTION)
        self.assertIn("action update on that node, kind request,", text)
        with self.assertRaisesRegex(tree.WorkPlanError, "a request must create, update or prioritize Work"):
            validate({"action": "advise", "kind": "request", "item_id": ids["blocked"], "summary": "s", "reply": "r"})
        key = hashlib.sha256(b"gate-blocker").hexdigest()
        result = apply(store, key, {"action": "update", "kind": "request", "item_id": ids["blocked"],
                                    "note": "key now issued (ticket 9)", "summary": "key issued",
                                    "reply": "Key issued; main can resume."}, store.read()["version"],
                       gate_check=True)
        self.assertEqual("requested", result["handoff"])
        self.assertEqual("resume", result["main_handoff"]["kind"])
        self.assertEqual("blocked", next(n for n in store.read()["nodes"] if n["id"] == ids["blocked"])["status"])

    def test_gates_list_waits_and_blocks_active_goal_first(self):
        store, ids = self._doc()
        rows = gate.gates(store.read())
        self.assertEqual([ids['wait'], ids['blocked']], [row['id'] for row in rows])
        self.assertEqual(('CI run 42 green', False, ids['first']),
                         (rows[0]['waiting_on'], rows[0]['blocked'], rows[0]['root_id']))
        self.assertTrue(rows[1]['blocked'])
        # A finished goal's leftover waits are not gates.
        store.transaction(lambda d: tree.update_node(d, ids['blocked'], status='done', evidence='checked'))
        store.transaction(lambda d: tree.update_node(d, ids['other'], status='done', evidence='checked'))
        self.assertEqual([ids['wait']], [row['id'] for row in gate.gates(store.read())])

    def test_gates_are_ordered_p0_then_p1_then_the_rest(self):
        # KOBE hotfix 2026-11-18: the active goal used to come first whatever its
        # priority. Effective priority now orders the gates; within a rank the
        # active goal leads, then board order.
        store = WorkPlanStore(Path(self.tmp.name) / 'prio.json')
        doc, active = store.transaction(lambda d: tree.add_node(d, None, 'active ordinary goal'))
        doc, a_wait = store.transaction(lambda d: tree.add_node(d, active, 'a', waiting_on='x'))
        doc, p1 = store.transaction(lambda d: tree.add_node(d, None, 'urgent goal', priority='P1'))
        doc, p1_wait = store.transaction(lambda d: tree.add_node(d, p1, 'b', waiting_on='approval from Kim'))
        doc, p0 = store.transaction(lambda d: tree.add_node(d, None, 'emergency goal'))
        doc, p0_wait = store.transaction(lambda d: tree.add_node(d, p0, 'c', waiting_on='approval from Lee'))
        self.assertEqual(active, store.read()['active_root'])
        canonical = gate.gates(store.read())
        self.assertEqual([p1_wait, a_wait, p0_wait], [row['id'] for row in canonical])
        # The P0 marker lives beside the ledger; the router passes its effective view.
        effective = gate.gates(store.read(), lambda goal: 'P0' if goal['id'] == p0 else str(goal.get('priority') or ''))
        self.assertEqual([p0_wait, p1_wait, a_wait], [row['id'] for row in effective])
        self.assertEqual(['P0', 'P1', ''], [row['priority'] for row in effective])
        event = gate.due({}, store.read(), None, now=1000.0,
                         priority=lambda goal: 'P0' if goal['id'] == p0 else str(goal.get('priority') or ''))
        self.assertEqual([p0_wait, p1_wait, a_wait], event['gates'])
        text = event['text']
        self.assertLess(text.index('[P0] ' + p0_wait), text.index('[P1] ' + p1_wait))
        self.assertLess(text.index('[P1] ' + p1_wait), text.index('- ' + a_wait))
        self.assertIn('also while main is busy', text)
        self.assertNotIn('while main is idle', text)
        self.assertIn('approval or answer that may have been lost on the network', text)
        # A priority reader that fails keeps the canonical order.
        def broken(goal):
            raise OSError('ledger unreadable')
        self.assertEqual([p1_wait, a_wait, p0_wait], [row['id'] for row in gate.gates(store.read(), broken)])

    def test_due_now_then_after_interval_or_change_only(self):
        store, ids = self._doc()
        config = {'work_gate_check': {'minutes': 5}}
        first = gate.due(config, store.read(), None, now=1000.0)
        self.assertTrue(first['event_key'].startswith('gate-check:' + first['digest'] + ':'))
        self.assertEqual([ids['wait'], ids['blocked']], first['gates'])
        self.assertIn('waiting on: CI run 42 green', first['text'])
        self.assertIn('release_waits', first['text'])
        state = {'at': first['at'], 'digest': first['digest']}
        self.assertIsNone(gate.due(config, store.read(), state, now=1000.0 + 299))
        later = gate.due(config, store.read(), state, now=1000.0 + 300)
        self.assertNotEqual(first['event_key'], later['event_key'])
        # A changed gate set is checked at once, inside the interval.
        store.transaction(lambda d: tree.update_node(d, ids['wait'], waiting_on='CI run 43 green'))
        changed = gate.due(config, store.read(), state, now=1000.0 + 10)
        self.assertNotEqual(first['digest'], changed['digest'])
        self.assertIsNone(gate.due({'work_gate_check': {'minutes': 0}}, store.read(), None, now=1.0))
        self.assertIsNone(gate.due(config, {'nodes': []}, None, now=1.0))


class GateBatchTests(unittest.TestCase):
    """Every gate is checked each interval (Walkie 2026-11-16: 270 gates, 24 checked)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = WorkPlanStore(Path(self.tmp.name) / 'board.json')
        doc, self.goal = self.store.transaction(lambda d: tree.add_node(d, None, 'goal'))
        self.ids = []
        for i in range(60):
            doc, node = self.store.transaction(lambda d, i=i: tree.add_node(d, self.goal, 'step %d' % i,
                                                                            waiting_on='signal %d' % i))
            self.ids.append(node)

    def test_sixty_gates_become_three_events_covering_all(self):
        event = gate.due({}, self.store.read(), None, now=1000.0)
        parts = event['events']
        self.assertEqual([24, 24, 12], [len(part['gates']) for part in parts])
        self.assertEqual(self.ids, [g for part in parts for g in part['gates']])
        self.assertEqual(3, len({part['event_key'] for part in parts}))
        self.assertIn('gates 25-48 of 60 (event 2 of 3)', parts[1]['text'])
        self.assertEqual(self.ids, event['gates'])

    def test_mcp_queues_one_receipt_per_batch(self):
        root = Path(self.tmp.name) / 'state'
        identity = 'claude:batch'
        store = session_work_plan_store(root, identity)
        store.transaction(lambda d: d.update(self.store.read()) or None)
        config = {'work_mode': True, 'work_side_intake': {'enabled': True}, 'work_parallel_host': {'token': 'x' * 40}}
        result = dispatch_work_tool(1, 'work_side_gate_check', {'idle': True, 'forks_running': 0,
                                    'session_id': identity, '_host': HOST},
                                    load_config=lambda: config, state_dir=root,
                                    respond=lambda _id, text, error=False: {'text': text, 'error': error})
        self.assertFalse(result['error'], result)
        body = json.loads(result['text'])
        self.assertEqual(3, body['events'])
        self.assertEqual(3, len(SideIntakeInbox(root, identity).pending()))


class GateCheckMcpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.identity = 'claude:parent'
        self.config = {'work_mode': True, 'work_side_intake': {'enabled': True},
                       'work_parallel_host': {'token': 'x' * 40}}
        self.store = session_work_plan_store(self.root, self.identity)

    def invoke(self, name, args, host=None):
        return dispatch_work_tool(1, name, {**args, 'session_id': self.identity, '_host': host or HOST},
                                  load_config=lambda: self.config, state_dir=self.root,
                                  respond=lambda _id, text, error=False: {'text': text, 'error': error})

    def check(self, **args):
        result = self.invoke('work_side_gate_check', {'idle': True, 'forks_running': 0, **args})
        self.assertFalse(result['error'], result)
        return json.loads(result['text'])

    def notice(self, receipt_id):
        result = self.invoke('work_side_wake', {'event_id': receipt_id, 'state': 'notice_prepare',
                                                'owner_nonce': 'host-1', 'text': 'notice'})
        self.assertFalse(result['error'], result)
        return json.loads(result['text'])

    def _waiting(self, waiting_on='CI run 42 green'):
        doc, goal = self.store.transaction(lambda d: tree.add_node(d, None, 'ship fix'))
        doc, wait = self.store.transaction(lambda d: tree.add_node(d, goal, 'deploy', waiting_on=waiting_on))
        return goal, wait

    def test_conditions_gate_the_check(self):
        self.assertEqual('no_gates', self.check()['reason'])
        _, wait = self._waiting()
        # KOBE hotfix 2026-11-18 (lead requirement A): main's busy turn no longer
        # defers it (HEAD answered not_idle here); see test_main_busy_no_longer_defers_the_check.
        # Stage 3 (2026-11-17): side intake off is a host-only round, not a refusal.
        self.config['work_side_intake']['enabled'] = False
        off = self.check()
        self.assertEqual(('side_intake_off', True, True, [wait]),
                         (off['reason'], off['host_only'], off['host_due'], off['gates']))
        self.assertEqual([], SideIntakeInbox(self.root, self.identity).pending())
        self.config['work_side_intake']['enabled'] = True
        self.config['work_gate_check'] = {'minutes': 0}
        self.assertEqual({'interval_minutes': 0, 'queued': False, 'side_intake': True, 'reason': 'off'}, self.check())
        fork = self.invoke('work_side_gate_check', {'idle': True, 'forks_running': 0}, {**HOST, 'agent_id': 'a1'})
        self.assertTrue(fork['error'])
        self.assertEqual([], SideIntakeInbox(self.root, self.identity).pending())

    def test_the_router_orders_by_effective_priority(self):
        # P1 by the board's own priority; P0 by the P0 marker the router reads
        # beside the ledger (work_parallel_p0.effective_priority).
        doc, plain = self.store.transaction(lambda d: tree.add_node(d, None, 'plain goal'))
        doc, plain_wait = self.store.transaction(lambda d: tree.add_node(d, plain, 'p', waiting_on='x'))
        doc, urgent = self.store.transaction(lambda d: tree.add_node(d, None, 'urgent', priority='P1'))
        doc, urgent_wait = self.store.transaction(lambda d: tree.add_node(d, urgent, 'u', waiting_on='y'))
        doc, emergency = self.store.transaction(lambda d: tree.add_node(d, None, 'emergency'))
        doc, emergency_wait = self.store.transaction(lambda d: tree.add_node(d, emergency, 'e', waiting_on='z'))
        from unittest import mock
        from ciel_runtime_support.work_parallel_p0 import WorkParallelP0Store
        def effective(store, scope, root, canonical_priority=None):
            self.assertEqual(self.identity, scope)
            return 'P0' if root['id'] == emergency else (canonical_priority or '')
        with mock.patch.object(WorkParallelP0Store, 'effective_priority', effective):
            queued = self.check(idle=False)
        self.assertEqual([emergency_wait, urgent_wait, plain_wait], queued['gates'])
        row = SideIntakeInbox(self.root, self.identity)._load(queued['receipt_ids'][0])
        self.assertLess(row['text'].index('[P0] ' + emergency_wait), row['text'].index('[P1] ' + urgent_wait))

    def test_the_contract_and_the_menu_say_it_runs_while_main_is_busy(self):
        from ciel_runtime_support.work_mcp_tools import work_tool_schemas
        from ciel_runtime_support.work_settings_menu import DESCRIPTIONS
        schema = next(row for row in work_tool_schemas(enabled=True) if row['name'] == 'work_side_gate_check')
        self.assertIn('also while main is busy', schema['description'])
        self.assertIn('P0 first, then P1', schema['description'])
        self.assertNotIn('main idle', schema['description'])
        english, korean = DESCRIPTIONS['work_gate_check_minutes'][:2]
        self.assertIn('also while main is busy', english)
        self.assertIn('P0 first, then P1', english)
        self.assertNotIn('While main is idle', english)
        self.assertIn('메인이 작업 중', korean)

    def test_main_busy_no_longer_defers_the_check(self):
        # KOBE 2026-11-18: main stayed busy and no check ran for over an hour. The
        # check backs up an approval lost on the network, so it runs while main is
        # busy, one round at a time.
        _, wait = self._waiting()
        busy = self.check(idle=False)
        self.assertEqual((True, False, [wait]), (busy['queued'], busy['main_idle'], busy['gates']))
        self.assertEqual(1, len(SideIntakeInbox(self.root, self.identity).pending()))
        held = self.check(idle=False)
        self.assertEqual(('previous_round_pending', False), (held['reason'], held['queued']))
        self.assertEqual(1, len(SideIntakeInbox(self.root, self.identity).pending()))

    def test_running_forks_no_longer_defer_the_check(self):
        # Stage 3 (2026-11-17): HEAD answered not_idle while any Work fork ran.
        _, wait = self._waiting()
        queued = self.check(forks_running=3)
        self.assertTrue(queued['queued'], queued)
        self.assertEqual([wait], queued['gates'])

    def test_host_round_claims_one_notice_per_changed_diagnosis_and_is_recorded(self):
        self._waiting()
        digest = 'a' * 16
        claim = {'claim': 'diagnosis', 'digest': digest}
        self.assertTrue(self.check(host_report=claim)['claimed'])
        self.assertFalse(self.check(host_report=claim)['claimed'])
        self.assertTrue(self.check(host_report={'claim': 'diagnosis', 'digest': 'b' * 16})['claimed'])
        self.assertTrue(self.check(host_report={'claim': 'discovery'})['claimed'])
        self.assertFalse(self.check(host_report={'claim': 'discovery'})['claimed'])
        recorded = self.check(forks_running=2, host_report={'round': {
            'repumped': True, 'attention': ['P2'], 'notified': True, 'summary': 'P2 0/2 no candidates'}})
        self.assertEqual((True, 2, ['P2'], True), (recorded['recorded'], recorded['host_round']['forks_running'],
                                                   recorded['host_round']['attention'],
                                                   recorded['host_round']['repumped']))
        # The side round queued next keeps the host record (no host round due yet).
        queued = self.check()
        self.assertEqual((True, False), (queued['queued'], queued['host_due']))
        self.assertFalse(self.check(host_report={'claim': 'diagnosis', 'digest': 'b' * 16})['claimed'])
        # An empty attention ends the notified diagnosis: the same state later is new again.
        self.check(host_report={'round': {'repumped': True, 'attention': []}})
        self.assertTrue(self.check(host_report={'claim': 'diagnosis', 'digest': 'b' * 16})['claimed'])
        bad = self.invoke('work_side_gate_check', {'idle': True, 'forks_running': 0,
                                                   'host_report': {'claim': 'diagnosis', 'digest': 'XYZ'}})
        self.assertTrue(bad['error'])

    def test_due_check_is_one_durable_side_event_without_source_reply(self):
        goal, wait = self._waiting()
        queued = self.check()
        self.assertTrue(queued['queued'])
        self.assertEqual(5, queued['interval_minutes'])
        self.assertEqual([wait], queued['gates'])
        inbox = SideIntakeInbox(self.root, self.identity)
        row = inbox._load(queued['receipt_id'])
        self.assertTrue(row['event_key'].startswith('gate-check:'))
        self.assertEqual({'source_kind': 'gate_check'}, row['route'])
        self.assertIsNone(row['item_id'])
        self.assertEqual('previous_round_pending', self.check()['reason'])
        self.assertEqual(1, len(inbox.pending()))

        claimed = json.loads(self.invoke('work_side_claim', {'owner': 'actor'})['text'])['claim']
        self.assertEqual(queued['receipt_id'], claimed['receipt_id'])
        self.assertIn('waiting on: CI run 42 green', claimed['text'])
        host = {**HOST, 'receipt_id': claimed['receipt_id'], 'generation': claimed['generation'],
                'work_digest': claimed['work_digest']}
        resolved = self.invoke('work_side_resolve', {'decision': {
            'action': 'update', 'kind': 'request', 'item_id': wait, 'summary': 'CI run 42 passed',
            'note': 'gh run 42 conclusion success', 'reply': 'released',
            'release_waits': [{'id': wait, 'waiting_on': 'CI run 42 green'}]},
            'expected_version': claimed['workload']['version']}, host)
        self.assertFalse(resolved['error'], resolved)
        result = json.loads(resolved['text'])['result']
        self.assertEqual([{'id': wait, 'waiting_on': 'CI run 42 green'}],
                         [{'id': r['id'], 'waiting_on': r['waiting_on']} for r in result['released_waits']])
        doc = self.store.read()
        self.assertEqual('', next(n for n in doc['nodes'] if n['id'] == wait).get('waiting_on', ''))
        # No person sent it: no side review row for main, and no source reply.
        self.assertNotIn(claimed['receipt_id'], doc.get('side_reviews', {}))
        prepared = json.loads(self.invoke('work_side_reply', {'receipt_id': claimed['receipt_id'],
                                                              'state': 'prepare'})['text'])
        self.assertEqual('not_applicable', prepared['state'])
        # The released wait reaches main through one host notice claim.
        notice = self.notice(claimed['receipt_id'])
        self.assertEqual('claimed', notice['state'])
        self.assertTrue(self.notice(claimed['receipt_id'])['state'].startswith('already_'))
        self.assertEqual('no_gates', self.check()['reason'])

    def test_next_round_waits_for_the_last_and_then_the_interval(self):
        import time as clock
        self._waiting()
        first = self.check()
        self.assertTrue(first['queued'])
        self.assertEqual('previous_round_pending', self.check()['reason'])
        claimed = json.loads(self.invoke('work_side_claim', {'owner': 'actor'})['text'])['claim']
        host = {**HOST, 'receipt_id': claimed['receipt_id'], 'generation': claimed['generation'],
                'work_digest': claimed['work_digest']}
        self.invoke('work_side_resolve', {'decision': {'action': 'answer', 'kind': 'status', 'summary': 'no change',
                    'reply': 'checked'}, 'expected_version': claimed['workload']['version']}, host)
        self.assertEqual('not_due', self.check()['reason'])
        record = next(Path(self.root, 'sessions').glob('*/gate-check.json'))
        row = json.loads(record.read_text(encoding='utf-8'))
        row['at'] = clock.time() - 301
        record.write_text(json.dumps(row), encoding='utf-8')
        self.assertTrue(self.check()['queued'])

    def test_quarantined_or_retrying_rows_end_or_hold_the_round(self):
        import time as clock
        self._waiting()
        first = self.check()
        claimed = json.loads(self.invoke('work_side_claim', {'owner': 'actor'})['text'])['claim']
        host = {**HOST, 'receipt_id': claimed['receipt_id'], 'generation': claimed['generation']}
        # A retryable failure keeps the round open (the side retries it).
        retry = json.loads(self.invoke('work_side_fail', {'category': 'turn_error', 'model': 'valid'}, host)['text'])
        self.assertFalse(retry['receipt']['side_failure']['quarantined'])
        self.assertEqual('previous_round_pending', self.check()['reason'])
        # A quarantined row is finished: it never blocks later rounds. (The
        # backoff is skipped so the retry is claimable now.)
        inbox = SideIntakeInbox(self.root, self.identity)
        stored = inbox._load(first['receipt_id'])
        stored['side_failure']['retry_not_before'] = 0
        inbox._save_file(inbox._event_path('pending', first['receipt_id']), stored)
        again = json.loads(self.invoke('work_side_claim', {'owner': 'actor2'})['text'])['claim']
        self.assertEqual(first['receipt_id'], again['receipt_id'])
        host = {**HOST, 'receipt_id': again['receipt_id'], 'generation': again['generation']}
        failed = json.loads(self.invoke('work_side_fail', {'category': 'invalid_answer', 'model': 'main'}, host)['text'])
        self.assertTrue(failed['receipt']['side_failure']['quarantined'])
        record = next(Path(self.root, 'sessions').glob('*/gate-check.json'))
        row = json.loads(record.read_text(encoding='utf-8'))
        row['at'] = clock.time() - 301
        record.write_text(json.dumps(row), encoding='utf-8')
        self.assertTrue(self.check()['queued'])

    def test_no_change_answer_wakes_nobody(self):
        self._waiting()
        queued = self.check()
        claimed = json.loads(self.invoke('work_side_claim', {'owner': 'actor'})['text'])['claim']
        host = {**HOST, 'receipt_id': claimed['receipt_id'], 'generation': claimed['generation'],
                'work_digest': claimed['work_digest']}
        resolved = self.invoke('work_side_resolve', {'decision': {
            'action': 'answer', 'kind': 'status', 'summary': 'no change',
            'reply': 'gh run 42 still in_progress'}, 'expected_version': claimed['workload']['version']}, host)
        self.assertFalse(resolved['error'], resolved)
        result = json.loads(resolved['text'])['result']
        self.assertEqual('not_required', result['handoff'])
        self.assertNotIn('released_waits', result)
        doc = self.store.read()
        self.assertNotIn(queued['receipt_id'], doc.get('side_reviews', {}))
        self.assertNotIn(queued['receipt_id'], doc.get('side_handoffs', {}))

    def test_settings_row_and_set_on_shared_path(self):
        from ciel_runtime_support.work_live_settings import known_keys, settings
        self.assertIn('work_gate_check_minutes', known_keys())
        row = next(r for r in settings(self.config, {}, None)['settings'] if r['key'] == 'work_gate_check_minutes')
        self.assertEqual((5, 'number'), (row['value'], row['kind']))
        saved = []
        written = settings(self.config, {'key': 'work_gate_check_minutes', 'value': '15'}, saved.append)
        row = next(r for r in written['settings'] if r['key'] == 'work_gate_check_minutes')
        self.assertEqual(15, row['value'])
        self.assertEqual(15, gate.minutes(self.config))
        self.assertEqual(1, len(saved))
        with self.assertRaises(tree.WorkPlanError):
            settings(self.config, {'key': 'work_gate_check_minutes', 'value': '2000'}, saved.append)
        self.assertEqual(15, gate.minutes(self.config))
        # Through MCP, as the web dashboard, REST and ctl reach it.
        result = dispatch_work_tool(1, 'work_live_settings', {'key': 'work_gate_check_minutes', 'value': 'off',
                                                               'session_id': self.identity, '_host': HOST},
                                    load_config=lambda: self.config, state_dir=self.root, save_config=saved.append,
                                    respond=lambda _id, text, error=False: {'text': text, 'error': error})
        self.assertFalse(result['error'], result)
        self.assertEqual(0, gate.minutes(self.config))
        self.assertEqual('off', self.check()['reason'])

    def test_relay_reaches_main_once_with_the_item(self):
        doc, goal = self.store.transaction(lambda d: tree.add_node(d, None, 'implement export'))
        doc, step = self.store.transaction(lambda d: tree.add_node(d, goal, 'write csv writer'))
        inbox = SideIntakeInbox(self.root, self.identity)
        receipt = inbox.receive('the csv delimiter must be a semicolon', 'walkie:relay')['receipt_id']
        claimed = json.loads(self.invoke('work_side_claim', {'owner': 'actor'})['text'])['claim']
        self.assertEqual(receipt, claimed['receipt_id'])
        host = {**HOST, 'receipt_id': receipt, 'generation': claimed['generation'],
                'work_digest': claimed['work_digest']}
        resolved = self.invoke('work_side_resolve', {'decision': {
            'action': 'update', 'kind': 'request', 'item_id': step, 'summary': 'delimiter is ;',
            'note': 'csv delimiter must be ;', 'reply': 'forwarded', 'relay': True},
            'expected_version': claimed['workload']['version']}, host)
        self.assertFalse(resolved['error'], resolved)
        result = json.loads(resolved['text'])['result']
        self.assertEqual({'item_id': step}, result['relay'])
        self.assertEqual('write csv writer', result['title'])
        self.assertEqual('claimed', self.notice(receipt)['state'])
        self.assertTrue(self.notice(receipt)['state'].startswith('already_'))


class RelayDecisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = WorkPlanStore(Path(self.tmp.name) / 'board.json')
        self.key = hashlib.sha256(b'relay').hexdigest()
        doc, self.goal = self.store.transaction(lambda d: tree.add_node(d, None, 'implement export'))
        doc, self.step = self.store.transaction(lambda d: tree.add_node(d, self.goal, 'write csv writer'))
        self.base = {'action': 'update', 'kind': 'request', 'item_id': self.step,
                     'summary': 'use semicolons', 'note': 'delimiter must be ;', 'reply': 'noted'}

    def test_update_with_relay_names_the_item(self):
        result = apply(self.store, self.key, {**self.base, 'relay': True}, self.store.read()['version'])
        self.assertEqual({'item_id': self.step}, result['relay'])
        self.assertEqual('write csv writer', result['title'])
        self.assertIn('delimiter must be ;', next(n for n in self.store.read()['nodes']
                                                   if n['id'] == self.step)['note'])

    def test_relay_only_on_update_and_must_be_boolean(self):
        result = apply(self.store, self.key, {**self.base, 'relay': False}, self.store.read()['version'])
        self.assertNotIn('relay', result)
        other = hashlib.sha256(b'answer').hexdigest()
        answered = apply(self.store, other, {'action': 'answer', 'kind': 'status', 'item_id': self.step,
                                             'summary': 's', 'reply': 'r', 'relay': True},
                         self.store.read()['version'])
        self.assertNotIn('relay', answered)
        with self.assertRaises(tree.WorkPlanError):
            validate({**self.base, 'relay': 'yes'})

    def test_plain_update_is_not_a_notice(self):
        from ciel_runtime_support import side_notice
        state = Path(self.tmp.name) / 'state'
        inbox = SideIntakeInbox(state, 'claude:parent')
        receipt = inbox.receive('please use semicolons', 'relay-event')['receipt_id']
        with self.assertRaises(tree.WorkPlanError):
            side_notice.prepare(inbox, receipt, 'host-1')


if __name__ == '__main__':
    unittest.main()
