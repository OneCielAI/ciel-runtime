"""Work REST main-review contract on real loopback HTTP sockets.

The finalization saga itself is backend scope (tests/test_work_parallel_finalization.py);
these tests pin the browser projection and the operator command mapping.
"""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from ciel_runtime_support.http_response import HttpResponseAdapter
from ciel_runtime_support.work_application import openapi_document
from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices
from ciel_runtime_support.work_parallel import WorkParallelConflict, WorkParallelError

IDENTITY = 'claude:one'


def review_row(**extra):
    row = {'lease_id': 'L1', 'item_id': 'n2', 'root_id': 'n1', 'priority': 'P1', 'state': 'pending',
           'decision': None, 'decision_id': None, 'evidence': None, 'replacement_lease_id': None,
           'replacement_generation': None, 'stages': {'accepted': False},
           'artifact': 'a' * 64, 'completion_receipt': 'd' * 64, 'fingerprint': 'f' * 64,
           'item_title': 'Fix retry', 'item_path': ['Goal', 'Fix retry'], 'summary': 'retry fixed',
           'coverage': [{'item_id': 'n2', 'verified': True}], 'verification': 'unit tests 12 passed',
           'changed_before_bind': False,
           'workspace': {'repo': 'C:/secret/repo', 'path': 'C:/secret/repo/.wt/n2', 'branch': 'work/n2',
                         'head': 'abc123', 'dirty': False, 'changed_files': ['src/retry.py'],
                         'diff_stat': '1 file changed', 'snapshot_digest': 'e' * 64, 'receipt_digest': 'c' * 64},
           'transcript': 'SECRET-TRANSCRIPT', 'agent_id': 'SECRET-AGENT'}
    row.update(extra)
    return row


class WorkHttpParallelReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name) / 'state'
        self.config = {'work_mode': True}
        self.reviews = {'version': 4, 'pending': [review_row()], 'truncated': False}
        ledger = mock.Mock()
        ledger.path = str(Path(self.temp.name) / 'work-parallel.json')
        self.ledger = ledger
        controller = WorkHttpController(WorkHttpServices(
            self.state, lambda: self.config, lambda: IDENTITY,
            lambda identity: {'session_id': identity, 'last_observed': None},
            HttpResponseAdapter.write_json, 'test-instance',
            parallel_status=lambda identity, board: self.projection(),
            parallel_store=lambda: ledger))
        self.controller = controller

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if not controller.get(self, self.path.split('?')[0]):
                    self.send_error(404)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['content-length'])))
                if not controller.post(self, self.path, body):
                    self.send_error(404)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.temp.cleanup()

    def projection(self):
        empty = {name: [] for name in ('confirmed', 'waiting', 'reservation', 'uncertain', 'validating', 'ended')}
        return {'version': 9, 'policy': {'enabled': True, 'paused': False, 'priority_limits': {'P1': 1},
                                         'launches_per_minute': 4, 'max_rework': 2},
                'forks': {'schema': 'native-fork-evidence/1', 'available': True, 'confirmed_count': 0, **empty},
                'device': {'active_slots': 0}, 'runtime': 'claude', 'limitations': [],
                'leases': {'L1': {'worker_token': 'SECRET-TOKEN'}}, 'review': self.reviews}

    def request(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, headers={'Content-Type': 'application/json'})
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def test_parallel_exposes_bounded_review_queue_without_paths_or_fork_content(self):
        status, body = self.request('/ca/work/api/v1/parallel')
        self.assertEqual(200, status, body)
        self.assertEqual(4, body['review_version'])
        row = body['parallel_reviews'][0]
        self.assertEqual(('L1', 'n2', 'pending', 'Fix retry', 'retry fixed', 'unit tests 12 passed', False),
                         (row['lease_id'], row['item_id'], row['state'], row['item_title'], row['summary'],
                          row['verification'], row['changed_before_bind']))
        self.assertEqual({'branch': 'work/n2', 'head': 'abc123', 'dirty': False, 'changed_files': ['src/retry.py'],
                          'changed_count': 1, 'diff_stat': '1 file changed'}, row['workspace'])
        raw = json.dumps(body)
        for leaked in ('C:/secret', 'SECRET-TRANSCRIPT', 'SECRET-AGENT', 'SECRET-TOKEN', 'e' * 64, 'c' * 64,
                       'f' * 64, 'stages', 'replacement_lease_id', 'decision_id'):
            self.assertNotIn(leaked, raw)

    def test_parallel_preserves_true_changed_count_while_clipping_file_sample(self):
        changed = [f'src/file-{index:02d}.py' for index in range(37)]
        row = review_row()
        row['workspace']['changed_files'] = changed
        row['workspace']['changed_count'] = 37
        self.reviews = {'version': 4, 'pending': [row], 'truncated': False}
        status, body = self.request('/ca/work/api/v1/parallel')
        self.assertEqual(200, status, body)
        workspace = body['parallel_reviews'][0]['workspace']
        self.assertEqual(37, workspace['changed_count'])
        self.assertEqual(changed[:20], workspace['changed_files'])

    def test_parallel_passes_only_a_contract_shaped_review_pin(self):
        # R1 (backend 170c6e63): the browser sends back the pin of the row it rendered.
        pin = 'rp1:' + 'ab' * 32
        self.reviews = {'version': 4, 'truncated': False, 'pending': [
            review_row(lease_id='L1', review_pin=pin), review_row(lease_id='L2', review_pin='rp1:C:/secret'),
            review_row(lease_id='L3', review_pin=7)]}
        status, body = self.request('/ca/work/api/v1/parallel')
        self.assertEqual(200, status, body)
        self.assertEqual([('L1', pin), ('L2', None), ('L3', None)],
                         [(row['lease_id'], row.get('review_pin')) for row in body['parallel_reviews']])
        self.assertNotIn('C:/secret', json.dumps(body))

    def test_parallel_projects_owner_offline_as_its_blocked_code(self):
        self.reviews = {'version': 4, 'pending': [review_row(state='awaiting_capacity', decision='rework',
            blocked={'code': 'owner_offline', 'message': 'rework queued: waiting for the conversation to resume'})],
            'truncated': False}
        status, body = self.request('/ca/work/api/v1/parallel')
        self.assertEqual(200, status, body)
        self.assertEqual({'code': 'owner_offline'}, body['parallel_reviews'][0]['blocked'])

    def test_parallel_projects_blocked_code_without_message(self):
        self.reviews = {'version': 4, 'pending': [review_row(state='awaiting_capacity', decision='rework',
            blocked={'code': 'paused', 'message': 'C:/secret/host/path failed'})], 'truncated': False}
        status, body = self.request('/ca/work/api/v1/parallel')
        self.assertEqual(200, status, body)
        self.assertEqual({'code': 'paused'}, body['parallel_reviews'][0]['blocked'])
        self.assertNotIn('C:/secret', json.dumps(body))

    def test_parallel_projects_stranded_fixed_words_without_reason_or_placeholder_facts(self):
        reason = 'workspace ownership failed: C:/secret/repo/.wt/n2 is missing'
        self.reviews = {'version': 4, 'truncated': False, 'pending': [review_row(
            artifact=None, completion_receipt=None, coverage=[],
            summary='No worker report: the native fork ended without a durable exit receipt.',
            stranded={'kind': 'no_report', 'reason': reason, 'approve': 'refused', 'rework': 'fresh_workspace',
                      'counts_against_rework_limit': False},
            blocked={'code': 'no_report', 'message': reason},
            workspace={'path': 'C:/secret/repo/.wt/n2', 'branch': 'work/n2', 'head': 'abc123',
                       'unavailable': True, 'reason': reason})]}
        status, body = self.request('/ca/work/api/v1/parallel')
        self.assertEqual(200, status, body)
        row = body['parallel_reviews'][0]
        self.assertEqual({'kind': 'no_report', 'approve': 'refused', 'rework': 'fresh_workspace',
                          'counts_against_rework_limit': False}, row['stranded'])
        self.assertEqual({'code': 'no_report'}, row['blocked'])
        self.assertEqual({'branch': 'work/n2', 'head': 'abc123'}, row['workspace'])
        self.assertNotIn('artifact', row)
        self.assertNotIn('C:/secret', json.dumps(body))
        self.assertNotIn('ownership failed', json.dumps(body))

    def test_parallel_exports_the_sidecar_phase_as_a_fixed_word_only(self):
        rows = []
        for lease, phase in (('L1', 'deciding'), ('L2', 'recovered'), ('L3', 'invented')):
            rows.append(review_row(lease_id=lease, stranded={'kind': 'workspace_unavailable', 'reason': 'C:/secret/x',
                                   'phase': phase, 'approve': 'refused', 'rework': 'fresh_workspace',
                                   'counts_against_rework_limit': False}))
        self.reviews = {'version': 4, 'truncated': False, 'pending': rows}
        status, body = self.request('/ca/work/api/v1/parallel')
        self.assertEqual(200, status, body)
        phases = [row['stranded'].get('phase') for row in body['parallel_reviews']]
        self.assertEqual(['deciding', 'recovered', None], phases)
        self.assertNotIn('C:/secret', json.dumps(body))

    def test_parallel_moves_superseded_rows_to_a_bounded_ended_list_without_controls_or_facts(self):
        # checkpoint3: an ended audit row. It is never a decidable review.
        rows = [review_row(lease_id='live')]
        for index in range(23):
            rows.append(review_row(lease_id='S' + str(index), state='superseded', artifact=None, completion_receipt=None,
                                   summary='Stranded result superseded by another execution of this Work item.',
                                   verification='Not decidable: another execution owns this item.',
                                   stranded={'kind': 'no_report', 'phase': 'superseded', 'approve': 'refused',
                                             'rework': 'fresh_workspace', 'counts_against_rework_limit': False,
                                             'superseding_lease_id': 'beta' + str(index) + 'f' * 28,
                                             'superseding_state': 'absent' if index % 2 else 'running',
                                             'observed_at': 1794529000.0 + index, 'reason': 'C:/secret/x'},
                                   workspace={'path': 'C:/secret/repo/.wt/n2', 'branch': 'work/n2', 'head': 'abc'}))
        rows.append(review_row(lease_id='odd-time', state='superseded', stranded={
            'kind': 'workspace_unavailable', 'phase': 'superseded', 'superseding_lease_id': 'b' * 80,
            'observed_at': 'yesterday'}))
        self.reviews = {'version': 4, 'truncated': False, 'pending': rows}
        status, body = self.request('/ca/work/api/v1/parallel')
        self.assertEqual(200, status, body)
        self.assertEqual(['live'], [row['lease_id'] for row in body['parallel_reviews']])
        ended = body['parallel_reviews_ended']
        # Newest first, at most 20; a row without a valid time sorts last and is dropped.
        self.assertEqual(['S' + str(index) for index in range(22, 2, -1)], [row['lease_id'] for row in ended])
        self.assertEqual({'lease_id': 'S22', 'item_id': 'n2', 'priority': 'P1', 'item_title': 'Fix retry',
                          'kind': 'no_report', 'phase': 'superseded', 'superseding_lease_id': 'beta22' + 'f' * 28,
                          'superseding_state': 'running', 'observed_at': 1794529022.0}, ended[0])
        self.assertEqual('absent', ended[1]['superseding_state'])
        text = json.dumps(body)
        self.assertNotIn('C:/secret', text)
        self.assertNotIn('Not decidable', text)
        self.assertNotIn('work/n2', json.dumps(ended))

    def test_parallel_ended_row_keeps_only_a_bounded_superseding_id_and_a_numeric_time(self):
        self.reviews = {'version': 4, 'truncated': False, 'pending': [review_row(lease_id='odd', state='superseded', stranded={
            'kind': 'invented', 'phase': 'superseded', 'superseding_lease_id': 'b' * 80, 'observed_at': 'yesterday',
            'superseding_state': 'C:/not a state word'})]}
        status, body = self.request('/ca/work/api/v1/parallel')
        self.assertEqual(200, status, body)
        self.assertEqual([], body['parallel_reviews'])
        self.assertEqual([{'lease_id': 'odd', 'item_id': 'n2', 'priority': 'P1', 'item_title': 'Fix retry',
                           'phase': 'superseded', 'superseding_lease_id': 'b' * 64}], body['parallel_reviews_ended'])

    def test_parallel_reads_ended_rows_from_the_host_only_review_ended(self):
        # Lead ruling: review.pending holds decidable rows only; superseded rows
        # arrive in the host-authenticated review.ended (final backend shape).
        def ended_row(lease, winner, state, at):
            return {'lease_id': lease, 'item_id': 'n9', 'root_id': 'n1', 'priority': 'P2', 'artifact': None,
                    'completion_receipt': None, 'summary': 'Stranded result superseded by another execution.',
                    'coverage': [], 'verification': 'Not decidable.', 'state': 'superseded', 'decision': None,
                    'evidence': None, 'blocked': None, 'item_title': 'Webhook', 'item_ref': '', 'goal_title': 'Goal',
                    'item_path': ['Goal', 'Webhook'], 'coverage_count': 0, 'changed_before_bind': 'not_applicable',
                    'stranded': {'kind': 'no_report', 'phase': 'superseded', 'approve': 'refused',
                                 'rework': 'fresh_workspace', 'counts_against_rework_limit': False,
                                 'superseding_lease_id': winner, 'observed_at': at, 'superseding_state': state}}
        self.reviews = {'version': 4, 'truncated': False, 'pending': [review_row(lease_id='live')],
                        'ended': [ended_row('E1', 'w' * 32, 'absent', 1794529500.0),
                                  ended_row('E2', 'v' * 32, 'done', 1794529600.0),
                                  # Not an ended audit row: never decidable from here.
                                  review_row(lease_id='stray')]}
        status, body = self.request('/ca/work/api/v1/parallel')
        self.assertEqual(200, status, body)
        self.assertEqual(['live'], [row['lease_id'] for row in body['parallel_reviews']])
        self.assertEqual([('E2', 'done'), ('E1', 'absent')],
                         [(row['lease_id'], row['superseding_state']) for row in body['parallel_reviews_ended']])
        self.assertEqual({'lease_id', 'item_id', 'priority', 'item_title', 'kind', 'phase', 'superseding_lease_id',
                          'superseding_state', 'observed_at'}, set(body['parallel_reviews_ended'][0]))
        self.assertNotIn('Not decidable', json.dumps(body))

    def test_parallel_ended_row_in_both_lists_is_shown_once(self):
        row = review_row(lease_id='D1', state='superseded', stranded={
            'kind': 'no_report', 'phase': 'superseded', 'superseding_lease_id': 'w' * 32, 'superseding_state': 'running',
            'observed_at': 1794529500.0})
        self.reviews = {'version': 4, 'truncated': False, 'pending': [row], 'ended': [dict(row)]}
        status, body = self.request('/ca/work/api/v1/parallel')
        self.assertEqual(200, status, body)
        self.assertEqual(['D1'], [entry['lease_id'] for entry in body['parallel_reviews_ended']])
        self.assertEqual([], body['parallel_reviews'])

    def test_parallel_omits_stranded_for_a_normal_or_unknown_kind_row(self):
        self.reviews = {'version': 4, 'truncated': False, 'pending': [
            review_row(), review_row(lease_id='L2', stranded={'kind': 'invented', 'approve': 'refused'})]}
        status, body = self.request('/ca/work/api/v1/parallel')
        self.assertEqual(200, status, body)
        self.assertEqual([False, False], ['stranded' in row for row in body['parallel_reviews']])

    def test_parallel_without_review_projection_is_an_empty_queue(self):
        self.reviews = None
        status, body = self.request('/ca/work/api/v1/parallel')
        self.assertEqual(200, status)
        self.assertEqual([], body['parallel_reviews'])
        self.assertIsNone(body['review_version'])

    def test_web_decision_maps_to_main_review_with_deterministic_decision_id(self):
        result = {'decision': 'rework', 'lease_id': 'L1', 'state': 'rework_ready', 'finalization_version': 5}
        with mock.patch('ciel_runtime_support.work_parallel_mcp._main_review', return_value=result) as review:
            status, body = self.request('/ca/work/api/v1/commands/parallel-item-review', {
                'lease_id': 'L1', 'decision': 'rework', 'evidence': '  add the missing retry test  ',
                'expected_finalization_version': 4})
        self.assertEqual(200, status, body)
        self.assertEqual(result, body['result'])
        store, _board, identity, host, args = review.call_args.args
        self.assertIs(self.ledger, store)
        self.assertEqual((IDENTITY, {}), (identity, host))
        self.assertEqual({'lease_id': 'L1', 'decision': 'rework', 'decision_id': 'review:L1:rework',
                          'evidence': 'add the missing retry test', 'expected_finalization_version': 4}, args)

    def test_web_approval_without_note_records_operator_evidence(self):
        with mock.patch('ciel_runtime_support.work_parallel_mcp._main_review', return_value={}) as review:
            status, _ = self.request('/ca/work/api/v1/commands/parallel-item-review', {
                'lease_id': 'L1', 'decision': 'approve', 'evidence': '', 'expected_finalization_version': 4})
        self.assertEqual(200, status)
        args = review.call_args.args[4]
        self.assertEqual('review:L1:approve', args['decision_id'])
        self.assertIn('Work web console', args['evidence'])

    def test_web_decision_validation_and_conflicts(self):
        bad = ({'lease_id': 'L1', 'decision': 'accept', 'expected_finalization_version': 4},
               {'lease_id': 'L1', 'decision': 'rework', 'evidence': '  ', 'expected_finalization_version': 4},
               {'lease_id': '', 'decision': 'approve', 'expected_finalization_version': 4},
               {'lease_id': 'L1', 'decision': 'approve', 'expected_finalization_version': '4'})
        with mock.patch('ciel_runtime_support.work_parallel_mcp._main_review') as review:
            for body in bad:
                with self.subTest(body=body):
                    status, payload = self.request('/ca/work/api/v1/commands/parallel-item-review', body)
                    self.assertEqual(422, status, payload)
            review.assert_not_called()
        conflict = WorkParallelConflict('Work finalization changed; reread')
        with mock.patch('ciel_runtime_support.work_parallel_mcp._main_review', side_effect=conflict):
            status, payload = self.request('/ca/work/api/v1/commands/parallel-item-review', {
                'lease_id': 'L1', 'decision': 'approve', 'expected_finalization_version': 3})
        self.assertEqual((409, 'version_conflict'), (status, payload['error']))
        refused = WorkParallelError('review already decided differently')
        with mock.patch('ciel_runtime_support.work_parallel_mcp._main_review', side_effect=refused):
            status, payload = self.request('/ca/work/api/v1/commands/parallel-item-review', {
                'lease_id': 'L1', 'decision': 'approve', 'expected_finalization_version': 4})
        self.assertEqual(422, status)
        self.assertIn('decided differently', payload['message'])

    def test_review_changes_reach_the_parallel_fingerprint(self):
        before, _ = self.controller._parallel_fingerprint(IDENTITY, {'nodes': [], 'version': 1})
        self.reviews = {'version': 5, 'pending': [review_row(state='deciding', decision='approve')], 'truncated': False}
        after, _ = self.controller._parallel_fingerprint(IDENTITY, {'nodes': [], 'version': 1})
        self.assertNotEqual(before, after)
        self.assertIn('work-parallel-finalization.json', [p.name for p in self.controller._parallel_paths()])

    def test_side_projection_keeps_bounded_time_limit_handoff_fields(self):
        projected = WorkHttpController._side_projection(IDENTITY, {'version': 3, 'side_reviews': {}, 'side_handoffs': {
            'limit-1': {'state': 'requested', 'kind': 'time_limit', 'item_id': 'n9', 'summary': 'window',
                        'time_limit': {'text': 'Finish before close', 'due_at': '2026-11-11T03:00:00Z',
                                       'evidence': 'ticket CHG-42'}, 'queue': {'position': 1},
                        'raw_thread': 'SECRET-THREAD', 'sse_event': 'SECRET-SSE'}}})
        row = projected['handoffs'][0]
        self.assertEqual('time_limit', row['kind'])
        self.assertEqual('Finish before close', row['time_limit']['text'])
        self.assertEqual({'position': 1}, row['queue'])
        self.assertNotIn('SECRET', json.dumps(projected))

    def test_openapi_lists_the_review_command(self):
        self.assertIn('/ca/work/api/v1/commands/parallel-item-review', openapi_document()['paths'])
        self.assertIn('/ca/work/api/v1/commands/parallel-launch-resolve', openapi_document()['paths'])

    def journal(self, launch, version=6):
        row = review_row(state='rework_ready', decision='rework', replacement_lease_id='R2', replacement_generation=2,
                         scope=IDENTITY)
        row['launch'] = {'state': 'none', 'dispatch_name': None, 'attempt': 0, 'generation': None,
                         'agent_id': None, 'proof': None, 'host_nonce': None, 'manual_from': None,
                         'audit': [], 'updated_at': 0, **launch}
        store = mock.Mock()
        store.read.return_value = {'version': version, 'reviews': {'L1': row}}
        self.reviews = {'version': version, 'pending': [review_row(state='rework_ready', decision='rework')],
                        'truncated': False}
        return store

    REF = '0123456789abcdef0123456789abcdef'

    def test_parallel_projects_stuck_launch_without_host_identity(self):
        launch = {'state': 'launch_intent', 'dispatch_name': 'SECRET-DISPATCH', 'attempt': 1,
                  'host_nonce': 'SECRET-NONCE', 'agent_id': None, 'updated_at': 1000.0, 'launch_ref': self.REF}
        with mock.patch('ciel_runtime_support.work_parallel_mcp.finalization', return_value=self.journal(launch)), \
                mock.patch('ciel_runtime_support.work_http_controller.time.time', return_value=1045.0):
            status, body = self.request('/ca/work/api/v1/parallel')
        self.assertEqual(200, status, body)
        self.assertEqual({'state': 'launch_intent', 'attempt': 1, 'launch_ref': self.REF, 'unresolved_seconds': 45,
                          'stuck': True}, body['parallel_reviews'][0]['launch'])
        self.assertNotIn('SECRET', json.dumps(body))
        fresh = {**launch, 'updated_at': 1040.0}
        with mock.patch('ciel_runtime_support.work_parallel_mcp.finalization', return_value=self.journal(fresh)), \
                mock.patch('ciel_runtime_support.work_http_controller.time.time', return_value=1045.0):
            _, body = self.request('/ca/work/api/v1/parallel')
            # An unresolved launch below the threshold keeps the 5 s SSE re-read armed.
            _, live = self.controller._parallel_fingerprint(IDENTITY, {'nodes': [], 'version': 1})
        self.assertFalse(body['parallel_reviews'][0]['launch']['stuck'])
        self.assertTrue(live)

    def test_parallel_reports_a_refused_request_as_agent_present(self):
        launch = {'state': 'bound', 'dispatch_name': 'SECRET-DISPATCH', 'attempt': 1, 'agent_id': 'SECRET-AGENT-ID',
                  'proof': 'agent present', 'updated_at': 1000.0, 'launch_ref': self.REF,
                  'audit': [{'kind': 'manual_requested', 'dispatch_name': 'SECRET-DISPATCH', 'attempt': 1,
                             'observer_nonce': None, 'at': 1, 'detail': 'x'},
                            {'kind': 'present', 'dispatch_name': 'SECRET-DISPATCH', 'attempt': 1,
                             'observer_nonce': None, 'at': 2, 'detail': 'host found it'}]}
        with mock.patch('ciel_runtime_support.work_parallel_mcp.finalization', return_value=self.journal(launch)):
            _, body = self.request('/ca/work/api/v1/parallel')
        self.assertEqual({'state': 'bound', 'attempt': 1, 'agent_present': True}, body['parallel_reviews'][0]['launch'])
        self.assertNotIn('SECRET', json.dumps(body))
        self.assertNotIn(self.REF, json.dumps(body))

    def test_web_launch_resolution_forwards_lease_and_ref_and_never_reads_the_name(self):
        recorded = {'lease_id': 'L1', 'state': 'manual_requested', 'attempt': 1, 'launch_ref': self.REF,
                    'finalization_version': 7, 'dispatch_name': 'SECRET-NOT-RETURNED'}
        good = {'lease_id': 'L1', 'launch_ref': self.REF, 'confirmed': True, 'expected_finalization_version': 6}
        with mock.patch('ciel_runtime_support.work_parallel_mcp.finalization') as journal, \
                mock.patch('ciel_runtime_support.work_parallel_mcp.request_rework_launch_manual',
                           return_value=recorded) as request:
            status, body = self.request('/ca/work/api/v1/commands/parallel-launch-resolve', good)
            self.assertEqual(200, status, body)
            self.assertEqual({'lease_id': 'L1', 'state': 'manual_requested', 'attempt': 1, 'launch_ref': self.REF,
                              'review_journal': 'finalization', 'finalization_version': 7}, body['result'])
            args, kwargs = request.call_args
            self.assertEqual((self.ledger, IDENTITY, 'L1', self.REF), args)
            self.assertEqual({'confirmed': True, 'expected_version': 6}, kwargs)
            # The controller never reads the journal (and so never derives a launch name).
            journal.assert_not_called()
            request.reset_mock()
            for bad in ({**good, 'confirmed': False}, {**good, 'confirmed': 'yes'},
                        {k: v for k, v in good.items() if k != 'confirmed'},
                        {k: v for k, v in good.items() if k != 'launch_ref'},
                        {**good, 'launch_ref': 'work-rework-R2-a1'}, {**good, 'launch_ref': self.REF.upper()},
                        {**good, 'lease_id': ''}, {**good, 'expected_finalization_version': '6'}):
                with self.subTest(body=bad):
                    status, payload = self.request('/ca/work/api/v1/commands/parallel-launch-resolve', bad)
                    self.assertEqual(422, status, payload)
            request.assert_not_called()
        with mock.patch('ciel_runtime_support.work_parallel_mcp.request_rework_launch_manual',
                        side_effect=WorkParallelConflict('Work finalization changed; reread')):
            status, payload = self.request('/ca/work/api/v1/commands/parallel-launch-resolve', good)
        self.assertEqual((409, 'version_conflict'), (status, payload['error']))
        with mock.patch('ciel_runtime_support.work_parallel_mcp.request_rework_launch_manual',
                        side_effect=WorkParallelError('stale rework launch reference')):
            status, payload = self.request('/ca/work/api/v1/commands/parallel-launch-resolve', good)
        self.assertEqual(422, status, payload)
        self.assertIn('stale rework launch reference', payload['message'])


if __name__ == '__main__':
    unittest.main()
