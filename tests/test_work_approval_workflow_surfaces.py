"""Approval workflow surfaces: REST, ciel-runtimectl, menu, settings, web console, prompts, proposal fork.

The REST controller is the one the router mounts; ctl and the menu build it in
process (work_ctl.WorkCtl), so every surface runs the same operator code.
"""
import json
import types
import unittest
from pathlib import Path

from ciel_runtime_support import work_workflow as wf
from ciel_runtime_support.work_ctl import CtlError, WorkCtl
from ciel_runtime_support.work_workflow_prompts import DEFAULTS, render
from ciel_runtime_support.work_workflow_tools import delegated_event, delegations_due, dispatch, status

from test_work_approval_workflow import CONVERSATION, REQUESTER, WorkflowCase, approval, proposal, thread


class SurfaceTests(WorkflowCase):
    def setUp(self):
        super().setUp()
        self.saved = []
        self.ctl = WorkCtl(lambda: self.config, self.saved.append, self.root)

    def known_session(self):
        # The conversation is known to this workspace the way a live router
        # records it (router-instances/*/compaction-scope.json).
        from ciel_runtime_support import runtime_paths
        folder = Path(runtime_paths.CONFIG_DIR) / 'router-instances' / self.identity.split(':')[1]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'compaction-scope.json').write_text(json.dumps({'runtime': 'claude', 'session_id':
                                                                  self.identity.split(':')[1]}), encoding='utf-8')

    def args(self, **kw):
        return types.SimpleNamespace(**{"key": None, "value": None, "confirm": False, "operation_id": None,
                                        "expected_version": None, **kw})

    def test_ac10_every_surface_shows_the_same_state(self):
        root = self.create('P1')
        self.submit(root)
        self.register(root, 1)
        mcp = self.main('work_proposal_status', {'root_id': root})['goal']
        rest = self.ctl.get(self.identity, '/ca/work/api/v1/workflow', root_id=root)['goal']
        ctl = self.ctl.view('workflow', self.identity, root)['goal']
        parallel = self.call('work_parallel_status', {})
        from ciel_runtime_support.work_workflow_menu import goals
        from ciel_runtime_support import work_ctl
        self.known_session()
        original = work_ctl.state_dir
        work_ctl.state_dir = lambda: self.root
        try:
            menu = next(row for row in goals(lambda: self.config) if row['root_id'] == root)
        finally:
            work_ctl.state_dir = original
        for view in (mcp, rest, ctl, menu):
            self.assertEqual(('awaiting_approval', 'requester', 'awaiting_approval', 1, '9100'),
                             (view['state'], view['owner'], view['blocked_reason'], view['proposal']['version'],
                              view['approval_request']['message_id']))
        self.assertEqual({'awaiting_approval': 1}, parallel['workflow']['counts'])
        self.assertEqual([root], parallel['workflow']['awaiting_approval'])
        diagnosis = parallel['priority_diagnosis']['rows']
        self.assertEqual(1, next(r for r in diagnosis if r['priority'] == 'P1')['blocked']['awaiting_approval'])

    def test_operations_run_the_same_code_on_rest_ctl_and_menu(self):
        root = self.create()
        self.submit(root)
        self.register(root, 1)
        # resend through ctl: back to approval_request_due, main sends again.
        self.ctl.act('workflow-resolve', self.identity, self.args(key=root, value='resend'))
        self.assertEqual('approval_request_due', self.state(root))
        # cancel needs the danger confirmation, like the web console.
        with self.assertRaises(CtlError) as raised:
            self.ctl.act('workflow-resolve', self.identity, self.args(key=root, value='cancel'))
        self.assertEqual('confirmation_required', raised.exception.code)
        self.assertEqual('approval_request_due', self.state(root))
        # The menu row applies through the settings menu's write path (ctl -> REST).
        from ciel_runtime_support import work_ctl
        from ciel_runtime_support.work_settings_menu import apply as menu_apply
        original = work_ctl.state_dir
        work_ctl.state_dir = lambda: self.root
        self.known_session()
        try:
            from ciel_runtime_support.work_workflow_menu import action_choices, summary
            choices = dict((value, label) for label, value in action_choices(lambda: self.config))
            cancel = next(value for value in choices if json.loads(value)['action'] == 'cancel')
            self.assertIn('approval_request_due 1', summary(lambda: self.config))
            menu_apply(self.config, 'work_workflow_actions', cancel, self.saved.append)
        finally:
            work_ctl.state_dir = original
        self.assertEqual('cancelled', self.state(root))
        audit = self.ctl.get(self.identity, '/ca/work/api/v1/audit')
        commands = [row.get('command') for row in audit.get('events') or []]
        self.assertIn('workflow_resolve', commands)

    def test_settings_on_every_surface(self):
        from ciel_runtime_support.work_live_settings import settings
        self.config['work_workflow'] = {}
        self.assertFalse(wf.enabled(self.config))
        settings(self.config, {'key': 'work_workflow_enabled', 'value': 'on'}, self.saved.append)
        self.assertTrue(wf.enabled(self.config))
        settings(self.config, {'key': 'work_workflow_delegate_pending', 'value': '3'}, self.saved.append)
        self.assertEqual(3, wf.settings(self.config)['delegate_pending'])
        keys = {row['key']: row for row in settings(self.config, {}, None)['settings']}
        self.assertEqual((True, 'boolean'), (keys['work_workflow_enabled']['value'],
                                             keys['work_workflow_enabled']['kind']))
        from ciel_runtime_support.work_settings_menu import choices, description
        self.assertIn('on', choices(self.config, 'work_workflow_enabled')[1])
        self.assertIn('Walkie', description(self.config, 'work_workflow_enabled'))
        self.assertIn('also while main', description(self.config, 'work_gate_check_minutes'))
        # REST settings_update (web Settings tab) uses the same key.
        from ciel_runtime_support.work_live_settings import settings_fingerprint
        result = self.ctl.post(self.identity, 'settings-update', {
            'key': 'work_workflow_enabled', 'value': 'off',
            'expected_settings_fingerprint': settings_fingerprint(self.config)}, None)
        self.assertTrue(result['ok'])
        self.assertFalse(wf.enabled(self.config))

    def test_ac13_prompts_edit_apply_to_the_next_request_reset_and_conflict(self):
        root = self.create()
        rows = {row['name']: row for row in self.ctl.get(self.identity, '/ca/work/api/v1/workflow/prompts')['prompts']}
        self.assertEqual(set(DEFAULTS), set(rows))
        # REST (web) edit with the fingerprint it read.
        self.ctl.post(self.identity, 'workflow-prompt-set', {'name': 'main_proposal', 'value': 'CUSTOM PROPOSAL RULES',
                                                              'expected_fingerprint': rows['main_proposal']['fingerprint']},
                      None)
        notice = status(self.store, self.root, self.identity, self.config)['next_for_main']
        self.assertIn('CUSTOM PROPOSAL RULES', notice['text'])
        # A second editor holding the old fingerprint is refused (409 prompt_conflict).
        from ciel_runtime_support.work_ctl import CtlError as Refused
        with self.assertRaises(Refused) as raised:
            self.ctl.post(self.identity, 'workflow-prompt-set', {'name': 'main_proposal', 'value': 'other',
                                                                  'expected_fingerprint': rows['main_proposal']['fingerprint']},
                          None)
        self.assertIn('prompt_conflict', str(raised.exception.payload) + raised.exception.code + str(raised.exception))
        # ctl reset restores the default; the next request uses it again.
        self.ctl.act('prompt-reset', self.identity, self.args(key='main_proposal'))
        self.assertEqual(DEFAULTS['main_proposal'], render(self.root, 'main_proposal')[0])
        # The submit transition records the digest of the prompt it used.
        self.submit(root)
        history = wf.record(self.store.read(), root)['history']
        self.assertEqual(render(self.root, 'main_proposal')[1][:16], history[-1]['prompt'])
        listing = self.ctl.view('workflow-prompts', self.identity, None)
        self.assertEqual(['set', 'reset'], [row['action'] for row in listing['history']])

    def test_ac12_proposal_fork_runs_on_mains_model_and_is_recorded_as_author(self):
        root = self.create()
        self.ctl.post(self.identity, 'workflow-delegate', {'root_id': root, 'role': 'proposal'}, None)
        offered = delegations_due(self.store, self.root, self.config, now=1e9)
        self.assertEqual('proposal', offered[0]['role'])
        self.assertIn('planning fork running on main', offered[0]['prompt'])
        # A fork that is not the bound proposal fork cannot submit.
        with self.assertRaises(Exception):
            delegated_event({'root_id': root, 'role': 'proposal', 'state': 'answer', 'agent_id': 'fork-x',
                             'proposal': proposal()}, self.store, self.root)
        delegated_event({'root_id': root, 'role': 'proposal', 'state': 'bound', 'agent_id': 'fork-p'},
                        self.store, self.root)
        done = delegated_event({'root_id': root, 'role': 'proposal', 'state': 'answer', 'agent_id': 'fork-p',
                                'proposal': proposal()}, self.store, self.root)
        self.assertEqual('approval_request_due', done['state'])
        record = wf.record(self.store.read(), root)
        self.assertEqual({'kind': 'fork', 'id': 'fork-p', 'model': 'main'}, record['proposal']['author'])
        self.assertEqual('done', record['delegation']['state'])
        # A Work fork cannot call main's tools directly (no host token, no delegation).
        with self.assertRaises(Exception):
            dispatch('work_approval_requested', {'root_id': root}, self.store, self.root, self.identity, self.config,
                     {'agent_id': 'fork-p'}, host_verified=True)

    def test_auto_delegation_by_pending_count_and_wait(self):
        self.config['work_workflow'] = {'enabled': True, 'delegate_pending': 2}
        first = self.create(message_id='9501')
        second = self.create(message_id='9502')
        offered = delegations_due(self.store, self.root, self.config, now=1e12)
        self.assertEqual([second], [row['root_id'] for row in offered])
        self.assertIsNotNone(first)

    def test_metrics_and_web_console(self):
        from ciel_runtime_support.work_web_ui import render_work_web_ui
        page = render_work_web_ui()
        for needle in ('id="tab-workflow"', 'loadWorkflow', '/workflow/prompts', 'workflow-resolve',
                       'workflow-prompt-set', 'Quality by fork model', 'Main’s queue'):
            self.assertIn(needle, page)
        metrics = self.ctl.view('workflow-metrics', self.identity, None) if self.create() else None
        self.assertEqual([], metrics['models'])

    def test_periodic_check_reads_the_registered_thread_in_order(self):
        root = self.create('P1')
        self.submit(root)
        self.register(root, 1)
        self.config['work_gate_check'] = {'minutes': 1}
        due = self.call('work_side_gate_check', {'idle': True})['approvals_due'][0]
        self.assertEqual(('9100', '9001'), (due['request_read']['message_id'], due['request_read']['thread_root_id']))
        done = self.call('work_side_workflow_event', {'check': True, 'root_id': root, 'source_message':
                                                      thread('9100', self.code(root), approvals=[approval()])})
        self.assertTrue(done['applied'])
        self.assertEqual(CONVERSATION, wf.record(self.store.read(), root)['approval_request']['conversation_id'])
        self.assertEqual(REQUESTER, wf.record(self.store.read(), root)['approval']['approver_id'])


if __name__ == '__main__':
    unittest.main()
