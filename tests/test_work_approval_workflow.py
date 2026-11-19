"""Approval workflow (docs/journal/2026/11/18/features/work-approval-workflow/spec.md).

Real backend: the router MCP dispatch (dispatch_work_tool), the session Work
store, the durable side inbox, the device ledger in the isolated config
directory, and the REST controller ctl and the web console use. No network.
"""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support import work_workflow as wf
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
from ciel_runtime_support.side_intake_processing import claim
from ciel_runtime_support.work_mcp_tools import dispatch_work_tool
from ciel_runtime_support.work_plan_store import WorkPlanStore, session_work_plan_store

TOKEN = 'w' * 40
HOST = {'token': TOKEN}
CONVERSATION = '11111111-2222-3333-4444-555555555555'
REQUESTER = 'u-requester'


def cloud_event(kind, data, event_id):
    return json.dumps({'specversion': '1.0', 'id': event_id, 'type': 'com.ciel.walkie.' + kind,
                       'source': '/conversations/' + CONVERSATION, 'data': data})


def request_event(message_id='9001', text='CSV 내보내기 버튼을 추가해 주세요'):
    return cloud_event('message.created', {'conversation_id': CONVERSATION, 'message_id': message_id,
                                           'sender_id': REQUESTER, 'payload': {'text': text, 'sender_id': REQUESTER}},
                       'evt-created-' + message_id)


def approval_event(message_id, *, approver=REQUESTER, decision='approved', all_approved=True,
                   kind='message.approved', event_id='evt-approved-1', top_sender='u-bot'):
    payload = {'message_id': message_id, 'conversation_id': CONVERSATION, 'approver_id': approver,
               'sender_id': approver, 'decision': decision, 'all_approved': all_approved, 'note': 'ok'}
    return cloud_event(kind, {'conversation_id': CONVERSATION, 'message_id': message_id, 'sender_id': top_sender,
                              'message_context': {'id': message_id}, 'payload': payload}, event_id)


def thread(message_id, code, *, approvals=(), approvers=(REQUESTER,), root='9001', conversation=CONVERSATION):
    return {'source_context': 'verified_thread', 'conversation_id': conversation, 'root_id': root,
            'target_message_id': message_id,
            'messages': [{'id': root, 'text': 'request'},
                         {'id': message_id, 'thread_root_id': root, 'text': 'Proposal summary ' + code,
                          'approvers': list(approvers), 'approvals': list(approvals)}]}


def approval(user=REQUESTER, decision='approved', **extra):
    return {'user_id': user, 'decision': decision, 'superseded_by': None, 'withdrawn_at': None, **extra}


def proposal(*, ui=False, qa_ids=('QA-1', 'QA-2')):
    body = {'request_restated': 'Add a CSV export button to the report page', 'analysis': 'report.py builds rows',
            'alternatives': 'server export vs client export; server chosen', 'risks': 'large reports; revert commit',
            'spec': 'Add /export.csv; do not change the HTML report', 'ui_impact': {'changes': ui, 'detail':
                                                                                    'new button' if ui else 'no UI'},
            'qa': [{'id': qa, 'claim': 'export works', 'method': 'command', 'how': 'pytest tests/test_export.py',
                    'expected': 'exit 0', 'evidence': 'log', 'must_fail_before': True} for qa in qa_ids],
            'steps': [{'id': 'S-1', 'title': 'implement export', 'input': 'report rows', 'procedure': 'write it',
                       'done_when': 'tests pass', 'qa': list(qa_ids)}]}
    if ui:
        body['benchmarks'] = [{'product': 'Sheets', 'screen': 'File > Download', 'source': 'docs', 'lesson': 'menu',
                               'decision': 'adopt'}]
        body['mockups'] = [{'file': 'mockups/export.png', 'state': 'default', 'description': 'button'}]
    return body


class WorkflowCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.identity = 'claude:wf-' + hashlib.sha256(self.tmp.name.encode()).hexdigest()[:12]
        self.config = {'work_mode': True, 'work_side_intake': {'enabled': True},
                       'work_parallel_host': {'token': TOKEN}, 'work_workflow': {'enabled': True}}
        self.store = session_work_plan_store(self.root, self.identity)
        self.inbox = SideIntakeInbox(self.root, self.identity)

    def call(self, name, args, host=HOST, error=False):
        payload = {**args, 'session_id': self.identity}
        if host is not None:
            payload['_host'] = host
        result = dispatch_work_tool(1, name, payload, load_config=lambda: self.config, state_dir=self.root,
                                    respond=lambda _id, text, is_error=False: {'text': text, 'error': is_error})
        self.assertEqual(error, result['error'], result)
        return result['text'] if error else json.loads(result['text'])

    def main(self, name, args, error=False):
        # Main's own call: the mod forwards it with the identity and no host token.
        return self.call(name, args, host=None, error=error)

    def receive(self, text, key):
        return self.inbox.receive(text, key, route={'source_kind': 'external_event'})['receipt_id']

    def claim(self):
        return claim(self.root, self.identity, 'side-test', workflow_events=wf.enabled(self.config))

    def create(self, priority='P2', message_id='9001'):
        receipt = self.receive(request_event(message_id), 'walkie:' + message_id)
        row = self.claim()
        self.assertEqual(receipt, row['receipt_id'])
        decision = {'action': 'create', 'kind': 'request', 'title': 'CSV export', 'summary': 'add a CSV export',
                    'reply': '등록했습니다', 'priority': priority, 'note': 'requested by the PM'}
        done = self.call('work_side_resolve', {'decision': decision, 'expected_version': self.store.read()['version']},
                         host={**HOST, 'receipt_id': receipt, 'generation': row['generation']})
        return done['result']['item_id']

    def submit(self, root, **kw):
        return self.main('work_proposal_submit', {'root_id': root, 'proposal': proposal(**kw)})

    def register(self, root, version, message_id='9100', approvers=(REQUESTER,), **extra):
        return self.main('work_approval_requested', {'root_id': root, 'conversation_id': CONVERSATION,
                                                     'message_id': message_id, 'thread_root_id': '9001',
                                                     'approvers': list(approvers), 'proposal_version': version, **extra})

    def sse(self, text, key, source):
        """The SSE path: the inbox receives the event, the host claims and settles it."""
        receipt = self.receive(text, key)
        row = self.claim()
        self.assertEqual(receipt, row['receipt_id'])
        return self.call('work_side_workflow_event', {'source_message': source},
                         host={**HOST, 'receipt_id': receipt, 'generation': row['generation']})

    def code(self, root):
        return wf.record(self.store.read(), root)['proposal']['code']

    def state(self, root):
        return wf.record(WorkPlanStore(self.store.path).read(), root)['state']

    def runnable(self, root):
        return [row for row in tree.runnable_leaves(self.store.read()) if row['root_id'] == root]

    def p0_runnable(self, root):
        from ciel_runtime_support.work_parallel_mcp import _p0_runnable_leaves, ledger
        return [row for row in _p0_runnable_leaves(ledger(), self.store.read(), self.identity) if row['root_id'] == root]

    def approve(self, root, message_id='9100', event_id='evt-approved-1', key=None):
        source = thread(message_id, self.code(root), approvals=[approval()])
        return self.sse(approval_event(message_id, event_id=event_id), key or 'walkie:' + event_id, source)

    def stored(self, receipt_id):
        return self.inbox._load(receipt_id)


class AcceptanceTests(WorkflowCase):
    def test_ac1_request_registers_goal_and_no_fork_before_proposal_including_p0(self):
        for priority in ('P2', 'P0'):
            with self.subTest(priority=priority):
                root = self.create(priority, message_id='90%s1' % priority[1])
                self.assertEqual('proposal_due', self.state(root))
                self.assertEqual([], self.runnable(root))
                if priority == 'P0':
                    self.assertEqual([], self.p0_runnable(root))
                with self.assertRaises(tree.WorkPlanError) as raised:
                    self.store.transaction(lambda d, r=root: tree.set_phase(d, tree.PHASE_EXECUTING, r))
                self.assertIn("main's technical proposal", str(raised.exception))
                from ciel_runtime_support.work_parallel_mcp import dispatch_refusal
                self.assertIn('technical proposal', dispatch_refusal(self.store.read(), root))
                todo = self.main('work_proposal_status', {})['todo']
                self.assertIn(root, [row['root_id'] for row in todo])
        # P0 is first in main's queue.
        self.assertEqual('P0', self.main('work_proposal_status', {})['todo'][0]['priority'])

    def test_ac2_proposal_and_registered_request_still_no_fork(self):
        root = self.create()
        submitted = self.submit(root)
        self.assertEqual('approval_request_due', submitted['state'])
        self.assertTrue(submitted['approval_packet']['code'].startswith('[proposal ' + root + ' v1 '))
        steps = [n for n in self.store.read()['nodes'] if n.get('ref', '').startswith('wf:' + root)]
        self.assertEqual(1, len(steps))
        self.assertEqual([], self.runnable(root))
        # Main cannot skip the approval with work_execute either (spec 5.2),
        # even with a plan deep enough to be ready.
        self.assertEqual(root, self.store.read()['active_root'])
        self.store.transaction(lambda d: tree.add_node(d, steps[0]['id'], 'write the writer', within_scope=True))
        refused = self.main('work_execute', {'expected_version': self.store.read()['version']}, error=True)
        self.assertIn('approval', refused)
        self.register(root, 1)
        self.assertEqual('awaiting_approval', self.state(root))
        self.assertEqual([], self.runnable(root))
        self.assertEqual('awaiting_approval', wf.gate(self.store.read(), root))

    def test_ac4_valid_approval_starts_and_replay_adds_nothing(self):
        root = self.create()
        self.submit(root)
        self.register(root, 1)
        first = self.approve(root)
        self.assertEqual(('approved', True, 'executing'), (first['decision'], first['applied'], first['state']))
        self.assertEqual('executing', tree.root_phase(self.store.read(), root))
        self.assertEqual(1, len(self.runnable(root)))
        receipt = first['receipt']
        self.assertEqual('processed', receipt['stage'])
        # The same SSE delivered again is the same durable receipt: no new claim.
        duplicate = self.inbox.receive(approval_event('9100'), 'walkie:evt-approved-1',
                                       route={'source_kind': 'external_event'})
        self.assertEqual(receipt['receipt_id'], duplicate['receipt_id'])
        self.assertIsNone(self.claim())
        # Redelivered under another key, and the periodic check: nothing applies again.
        version = self.store.read()['version']
        again = self.approve(root, key='walkie:redelivery')
        self.assertEqual((False, 'executing'), (again['applied'], again['state']))
        check = self.call('work_side_workflow_event', {'check': True, 'root_id': root, 'source_message':
                                                       thread('9100', self.code(root), approvals=[approval()])})
        self.assertFalse(check['applied'])
        self.assertEqual(1, len(self.runnable(root)))
        self.assertLessEqual(self.store.read()['version'] - version, 2)
        self.assertEqual(1, sum(1 for h in wf.record(self.store.read(), root)['history'] if h['to'] == 'approved'))

    def test_ac4_p0_starts_in_the_p0_lane_after_approval(self):
        root = self.create('P0')
        self.assertEqual([], self.p0_runnable(root))
        self.submit(root)
        self.register(root, 1)
        self.assertEqual([], self.p0_runnable(root))
        self.approve(root)
        self.assertEqual(1, len(self.p0_runnable(root)))

    def test_ac5_rejection_revision_rerequest_approval(self):
        root = self.create()
        self.submit(root)
        self.register(root, 1)
        source = thread('9100', self.code(root), approvals=[approval(decision='rejected', note='too wide')])
        rejected = self.sse(approval_event('9100', decision='rejected', kind='message.rejected', event_id='evt-r1'),
                            'walkie:evt-r1', source)
        self.assertEqual(('rejected', True, 'changes_requested'), (rejected['decision'], rejected['applied'],
                                                                  rejected['state']))
        self.assertEqual('not_applicable', self.stored(rejected['receipt']['receipt_id'])['result']['delivery'])
        self.assertNotIn('final_text', self.stored(rejected['receipt']['receipt_id'])['result'])
        todo = self.main('work_proposal_status', {})['todo'][0]
        self.assertEqual((root, 'changes_requested'), (todo['root_id'], todo['state']))
        revised = self.submit(root, qa_ids=('QA-1', 'QA-2', 'QA-3'))
        self.assertEqual(2, revised['proposal']['version'])
        self.register(root, 2, message_id='9200')
        approved = self.approve(root, message_id='9200', event_id='evt-a2')
        self.assertEqual('executing', approved['state'])
        self.assertEqual(1, len(self.runnable(root)))

    def test_ac11_ui_change_needs_benchmarks_and_mockups_and_the_request_lists_them(self):
        root = self.create()
        broken = proposal(ui=True)
        broken.pop('mockups')
        refused = self.main('work_proposal_submit', {'root_id': root, 'proposal': broken}, error=True)
        self.assertIn('mockups', refused)
        broken = proposal(ui=True)
        broken.pop('benchmarks')
        self.assertIn('benchmarks', self.main('work_proposal_submit', {'root_id': root, 'proposal': broken}, error=True))
        done = self.submit(root, ui=True)
        self.assertEqual('M-1', done['approval_packet']['mockups'][0]['id'])
        self.assertIn('mockups_attached', self.register_error(root))
        self.register(root, 1, mockups_attached=['M-1'])
        self.assertEqual(['M-1'], wf.record(self.store.read(), root)['approval_request']['mockups_attached'])

    def register_error(self, root):
        return self.main('work_approval_requested', {'root_id': root, 'conversation_id': CONVERSATION,
                                                     'message_id': '9100', 'approvers': [REQUESTER],
                                                     'proposal_version': 1}, error=True)

    def test_proposal_requires_every_field(self):
        root = self.create()
        for field in ('request_restated', 'analysis', 'alternatives', 'risks', 'spec', 'ui_impact', 'steps', 'qa'):
            body = proposal()
            body.pop(field)
            with self.subTest(field=field):
                self.assertIn(field.split('_')[0], self.main('work_proposal_submit', {'root_id': root, 'proposal': body},
                                                             error=True))


class ForgedApprovalTests(WorkflowCase):
    def setUp(self):
        super().setUp()
        self.goal = self.create()
        self.submit(self.goal)
        self.register(self.goal, 1)

    def refused(self, text, key, source, reason):
        done = self.sse(text, key, source)
        self.assertFalse(done['applied'], done)
        self.assertIn(reason, done['reason'])
        self.assertEqual('awaiting_approval', self.state(self.goal))
        self.assertEqual([], self.runnable(self.goal))

    def test_plain_reply_is_never_approval(self):
        receipt = self.receive(cloud_event('message.created', {'conversation_id': CONVERSATION, 'message_id': '9101',
                                                               'payload': {'text': '진행해', 'sender_id': REQUESTER}},
                                           'evt-text'), 'walkie:evt-text')
        row = self.claim()
        self.assertEqual(receipt, row['receipt_id'])
        # The claim does not mark it a workflow event, and the host tool refuses it.
        from ciel_runtime_support.work_workflow_tools import claim_workflow_event
        self.assertIsNone(claim_workflow_event(self.config, self.store.read(), row['text']))
        done = self.call('work_side_workflow_event', {'source_message': thread('9100', self.code(self.goal))},
                         host={**HOST, 'receipt_id': receipt, 'generation': row['generation']})
        self.assertFalse(done['applied'])
        self.assertEqual('awaiting_approval', self.state(self.goal))

    def test_approval_of_another_message(self):
        source = thread('9100', self.code(self.goal), approvals=[approval()])
        done = self.sse(approval_event('9999', event_id='evt-other'), 'walkie:evt-other', source)
        self.assertFalse(done['applied'])
        self.assertEqual('awaiting_approval', self.state(self.goal))

    def test_undesignated_approver(self):
        source = thread('9100', self.code(self.goal), approvals=[approval('u-stranger')])
        self.refused(approval_event('9100', approver='u-stranger', event_id='evt-x'), 'walkie:evt-x', source,
                     'not a designated approver')

    def test_top_level_sender_is_not_the_approver(self):
        # 19 of 19 measured events: data.sender_id differs from payload.approver_id.
        source = thread('9100', self.code(self.goal), approvals=[approval('u-stranger')])
        self.refused(approval_event('9100', approver='u-stranger', top_sender=REQUESTER, event_id='evt-top'),
                     'walkie:evt-top', source, 'not a designated approver')

    def test_all_approved_false(self):
        source = thread('9100', self.code(self.goal), approvals=[approval()])
        self.refused(approval_event('9100', all_approved=False, event_id='evt-partial'), 'walkie:evt-partial', source,
                     'all_approved')

    def test_earlier_proposal_version(self):
        old_code = self.code(self.goal)
        self.submit(self.goal, qa_ids=('QA-1',))
        self.register(self.goal, 2)
        source = thread('9100', old_code, approvals=[approval()])
        self.refused(approval_event('9100', event_id='evt-old'), 'walkie:evt-old', source, 'proposal code')

    def test_withdrawn_or_superseded_approval(self):
        for extra, key in (({'withdrawn_at': '2026-11-18T10:00:00Z'}, 'evt-w'), ({'superseded_by': 'a2'}, 'evt-s')):
            source = thread('9100', self.code(self.goal), approvals=[approval(**extra)])
            self.refused(approval_event('9100', event_id=key), 'walkie:' + key, source, 'verified thread')

    def test_unverified_thread_and_contradicting_decision(self):
        self.refused(approval_event('9100', event_id='evt-nothread'), 'walkie:evt-nothread', None, 'verified thread')
        source = thread('9100', self.code(self.goal), approvals=[approval()])
        self.refused(approval_event('9100', decision='rejected', event_id='evt-mix'), 'walkie:evt-mix', source,
                     'contradicts')


class RecoveryTests(WorkflowCase):
    def ready(self, priority='P2', message_id='9001'):
        root = self.create(priority, message_id=message_id)
        self.submit(root)
        self.register(root, 1, message_id='91' + message_id[-2:])
        return root

    def test_dropped_sse_is_recovered_by_the_periodic_check_and_applied_once(self):
        root = self.ready()
        self.config['work_gate_check'] = {'minutes': 1}
        check = self.call('work_side_gate_check', {'idle': False, 'forks_running': 0})
        due = check['approvals_due']
        self.assertEqual([root], [row['root_id'] for row in due])
        self.assertEqual({'server': 'walkie', 'conversation_id': CONVERSATION, 'message_id': '9101',
                          'thread_root_id': '9001', 'reply_cursor': None, 'row_digest': None}, due[0]['request_read'])
        pending = self.call('work_side_workflow_event', {'check': True, 'root_id': root,
                                                         'source_message': thread('9101', self.code(root))})
        self.assertEqual(('pending', False), (pending['decision'], pending['applied']))
        done = self.call('work_side_workflow_event', {'check': True, 'root_id': root, 'source_message':
                                                      thread('9101', self.code(root), approvals=[approval()])})
        self.assertEqual(('approved', True, 'executing'), (done['decision'], done['applied'], done['state']))
        # The late SSE of the same approval changes nothing.
        late = self.approve(root, message_id='9101', event_id='evt-late')
        self.assertFalse(late['applied'])
        self.assertEqual(1, sum(1 for h in wf.record(self.store.read(), root)['history'] if h['to'] == 'approved'))
        # The next round no longer lists it.
        self.assertNotIn('approvals_due', {k: v for k, v in self.call('work_side_gate_check', {'idle': True}).items()
                                           if v})

    def test_sse_first_then_periodic_check_applies_once(self):
        root = self.ready()
        self.assertTrue(self.approve(root, message_id='9101')['applied'])
        again = self.call('work_side_workflow_event', {'check': True, 'root_id': root, 'source_message':
                                                       thread('9101', self.code(root), approvals=[approval()])})
        self.assertFalse(again['applied'])

    def test_gate_check_runs_while_main_is_busy_p0_first(self):
        # KOBE 2026-11-18: the router refused a busy main with not_idle.
        p2 = self.ready('P2', '9021')
        p1 = self.ready('P1', '9022')
        p0 = self.ready('P0', '9023')
        doc, goal = self.store.transaction(lambda d: tree.add_node(d, None, 'ship fix', priority='P3'))
        self.store.transaction(lambda d: tree.add_node(d, goal, 'deploy', waiting_on='CI green'))
        busy = self.call('work_side_gate_check', {'idle': False, 'forks_running': 2})
        self.assertNotEqual('not_idle', busy.get('reason'))
        self.assertTrue(busy['queued'], busy)
        self.assertFalse(busy['main_idle'])
        self.assertEqual([p0, p1, p2], [row['root_id'] for row in busy['approvals_due']])
        self.assertEqual(['P0', 'P1', 'P2'], [row['priority'] for row in busy['approvals_due']])

    def test_restart_in_every_state_resumes_from_the_stored_state(self):
        root = self.create()
        seen = []

        def reopened():
            # A restart: a new store object reads the file; nothing is in memory.
            store = WorkPlanStore(self.store.path)
            seen.append(wf.record(store.read(), root)['state'])
            return store

        reopened()
        self.submit(root)
        reopened()
        self.register(root, 1)
        reopened()
        self.approve(root)
        store = reopened()
        self.assertEqual(1, len([r for r in tree.runnable_leaves(store.read()) if r['root_id'] == root]))
        self.assertEqual(['proposal_due', 'approval_request_due', 'awaiting_approval', 'executing'], seen)
        # Every stored state survives a store reload with its gate.
        for state in wf.STATES:
            with self.subTest(state=state):
                def force(doc, state=state):
                    wf.record(doc, root)['state'] = state
                self.store.transaction(force)
                store = WorkPlanStore(self.store.path)
                self.assertEqual(state, wf.record(store.read(), root)['state'])
                self.assertEqual(state not in wf.RUN_STATES, bool(wf.gate(store.read(), root)))


class SideIntakeFixTests(WorkflowCase):
    def test_p1_approval_events_are_claimed_by_event_not_deduplicated_by_message(self):
        root = self.create()
        self.submit(root)
        self.register(root, 1)
        # The request message's own created event is handled first; then two
        # decisions on the same message both reach the host.
        first = self.receive(cloud_event('message.created', {'conversation_id': CONVERSATION, 'message_id': '9100',
                                                             'payload': {'text': 'proposal'}}, 'evt-own'), 'walkie:own')
        row = self.claim()
        self.assertEqual(first, row['receipt_id'])
        from ciel_runtime_support.side_intake_processing import finish
        finish(self.root, self.identity, first, row['generation'], {'action': 'answer', 'kind': 'status',
                                                                    'summary': 's', 'reply': 'r'})
        a = self.receive(approval_event('9100', approver='u-other', event_id='evt-a'), 'walkie:a')
        b = self.receive(approval_event('9100', event_id='evt-b'), 'walkie:b')
        claimed = {self.claim()['receipt_id'], self.claim()['receipt_id']}
        self.assertEqual({a, b}, claimed)

    def test_p1_off_keeps_todays_source_deduplication(self):
        self.config['work_workflow'] = {}
        first = self.receive(request_event('9300'), 'walkie:9300')
        row = self.claim()
        from ciel_runtime_support.side_intake_processing import finish
        finish(self.root, self.identity, first, row['generation'], {'action': 'answer', 'kind': 'status',
                                                                    'summary': 's', 'reply': 'r'})
        self.receive(approval_event('9300', event_id='evt-c'), 'walkie:c')
        self.assertIsNone(self.claim())

    def test_p3_no_receipt_ack_for_a_registered_approval_event(self):
        from ciel_runtime_support.side_ack_outbox import prepare
        root = self.create()
        self.submit(root)
        self.register(root, 1)
        receipt = self.receive(approval_event('9100', event_id='evt-ack'), 'walkie:ack')
        state = prepare(self.root, self.identity, receipt, config=self.config)
        self.assertEqual('not_applicable', state['state'])
        self.assertEqual('workflow_event', self.inbox._load(receipt)['ack_outbox']['reason'])
        approved = self.approve(root, event_id='evt-ack2')
        result = self.stored(approved['receipt']['receipt_id'])['result']
        self.assertIn('승인을 확인했습니다', result['final_text'])
        self.assertEqual('pending', result['delivery'])

    def test_p2_side_note_does_not_change_the_approved_scope(self):
        from ciel_runtime_support.side_work_decision import apply
        from ciel_runtime_support.work_parallel_mcp import _root_scope_digest
        # Hotfix 9cba321 owns this: on an approved (executing) goal a side
        # observation goes to ``progress``, outside the approval scope digest.
        root = self.create()
        self.submit(root)
        self.register(root, 1)
        self.assertTrue(self.approve(root)['applied'])
        self.assertEqual('executing', self.state(root))
        before = _root_scope_digest(self.store.read(), root)
        key = hashlib.sha256(b'note').hexdigest()
        apply(self.store, key, {'action': 'update', 'kind': 'request', 'item_id': root, 'summary': 'more detail',
                                'reply': 'noted', 'note': 'use semicolons'}, self.store.read()['version'],
              workflow={'source': {}, 'prompt': ''})
        node = next(n for n in self.store.read()['nodes'] if n['id'] == root)
        self.assertIn('use semicolons', node['progress'])
        self.assertNotIn('use semicolons', node.get('note', ''))
        self.assertEqual(before, _root_scope_digest(self.store.read(), root))

    def test_workflow_off_matches_todays_create_and_update(self):
        self.config['work_workflow'] = {}
        root = self.create()
        self.assertNotIn('workflows', self.store.read())
        self.assertEqual('', wf.gate(self.store.read(), root))
        from ciel_runtime_support.side_work_decision import apply
        apply(self.store, hashlib.sha256(b'off').hexdigest(), {'action': 'update', 'kind': 'request', 'item_id': root,
              'summary': 's', 'reply': 'r', 'note': 'plain note'}, self.store.read()['version'])
        node = next(n for n in self.store.read()['nodes'] if n['id'] == root)
        self.assertIn('plain note', node['note'])
        self.assertNotIn('side_note', node)

    def test_ask_main_hands_a_simple_question_to_main(self):
        receipt = self.receive(request_event('9400', 'what is the deploy window?'), 'walkie:9400')
        row = self.claim()
        done = self.call('work_side_resolve', {'decision': {'action': 'answer', 'kind': 'info', 'summary':
                                                            'when is the deploy window', 'reply': '메인에게 확인합니다',
                                                            'ask_main': True},
                                               'expected_version': self.store.read()['version']},
                         host={**HOST, 'receipt_id': receipt, 'generation': row['generation']})
        # 2026-11-18: ask_main is one host notice to main, never a handoff main must acknowledge.
        self.assertTrue(done['result']['ask_main'])
        self.assertNotIn(receipt, self.store.read().get('side_handoffs') or {})
        self.assertEqual([], [n for n in self.store.read()['nodes']])


if __name__ == '__main__':
    os.environ.setdefault('CIEL_RUNTIME_TEST_ISOLATED', '1')
    unittest.main()
