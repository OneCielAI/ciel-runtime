"""Work REST/SSE Side receipt contract on real loopback HTTP sockets."""
import hashlib
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.http_response import HttpResponseAdapter
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
from ciel_runtime_support.side_intake_processing import claim, finish
from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices
from ciel_runtime_support.work_plan_store import session_work_plan_store

IDENTITY = 'claude:one'


class WorkHttpSideReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name) / 'state'
        self.config = {'work_mode': True}
        controller = WorkHttpController(WorkHttpServices(
            self.state, lambda: self.config, lambda: IDENTITY,
            lambda identity: {'session_id': identity, 'last_observed': None},
            HttpResponseAdapter.write_json, 'test-instance'))

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
        self.inbox = SideIntakeInbox(self.state, IDENTITY)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.temp.cleanup()

    def request(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, headers={'Content-Type': 'application/json'})
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def seed_stages(self):
        received = self.inbox.receive(json.dumps({'type': 'message.created', 'data': {
            'text': 'received event', 'priority': 'P1', 'reply_token': 'tok-received'}}),
            'event:received', route={'source_kind': 'web_chat', 'reply_token': 'route-secret'})
        processing = self.inbox.receive('processing event', 'event:processing')
        processed = self.inbox.receive('processed event', 'event:processed')
        picked = claim(self.state, IDENTITY, 'actor')
        # Claim order is P1 first; complete whichever ordinary rows it reaches.
        claimed = {picked['receipt_id']: picked}
        while len(claimed) < 3:
            extra = claim(self.state, IDENTITY, 'actor')
            claimed[extra['receipt_id']] = extra
        finish(self.state, IDENTITY, processed['receipt_id'], claimed[processed['receipt_id']]['generation'], {
            'action': 'answer', 'kind': 'info', 'item_id': None, 'summary': 'done', 'reply': 'answered',
            'delivery': 'route_unavailable', 'execution_approved': False, 'handoff': 'not_required'})
        # Return the P1 row to received so all three stages are represented.
        row = self.inbox._load(received['receipt_id'])
        row.pop('processing', None)
        self.inbox._save_file(self.inbox._event_path('pending', received['receipt_id']), row)
        return received['receipt_id'], processing['receipt_id'], processed['receipt_id'], claimed

    def test_side_lists_each_stage_with_counts_cursor_and_redaction(self):
        received, processing, processed, claimed = self.seed_stages()
        status, side = self.request('/ca/work/api/v1/side?limit=2')
        self.assertEqual(200, status, side)
        self.assertIn('reviews', side)
        self.assertIn('handoffs', side)
        receipts = side['receipts']
        self.assertEqual(2, len(receipts))
        self.assertTrue(side['has_more'])
        self.assertEqual(3, side['total'])
        status, older = self.request('/ca/work/api/v1/side?limit=2&cursor=' + side['next_cursor'])
        self.assertEqual(200, status, older)
        ids = [row['receipt_id'] for row in receipts + older['receipts']]
        self.assertEqual({received, processing, processed}, set(ids))
        stages = {row['receipt_id']: row['stage'] for row in receipts + older['receipts']}
        self.assertEqual({received: 'received', processing: 'processing', processed: 'processed'}, stages)
        status, full = self.request('/ca/work/api/v1/side?limit=10')
        self.assertEqual(1, full['counts']['received'])
        self.assertEqual(1, full['counts']['processing'])
        self.assertEqual(1, full['counts']['processed'])
        status, p1 = self.request('/ca/work/api/v1/side?priority=P1')
        self.assertEqual([received], [row['receipt_id'] for row in p1['receipts']])
        status, only = self.request('/ca/work/api/v1/side?stage=processing')
        self.assertEqual([processing], [row['receipt_id'] for row in only['receipts']])
        processed_row = next(row for row in full['receipts'] if row['receipt_id'] == processed)
        self.assertEqual('route_unavailable', processed_row['delivery'])
        self.assertGreaterEqual(processed_row['latency_seconds'], 0)

        status, detail = self.request('/ca/work/api/v1/side/receipt?receipt_id=' + received)
        self.assertEqual(200, status, detail)
        self.assertEqual('received', detail['receipt']['stage'])
        self.assertIn('received event', detail['receipt']['original'])
        rendered = json.dumps([side, older, full, detail])
        generations = [row['generation'] for row in claimed.values()]
        for forbidden in ('tok-received', 'route-secret', 'reply_token', '"route"', '"generation"',
                          '"attempt_id"', '"context"', *generations):
            self.assertNotIn(forbidden, rendered)

    def test_reads_of_an_absent_inbox_create_nothing(self):
        status, side = self.request('/ca/work/api/v1/side')
        self.assertEqual(200, status, side)
        self.assertEqual(([], False, 0), (side['receipts'], side['has_more'], side['total']))
        self.assertEqual(404, self.request('/ca/work/api/v1/side/receipt?receipt_id=' + 'b' * 64)[0])
        self.assertFalse(self.state.exists())

    def test_uncertain_main_wake_is_visible_and_retry_requires_confirmation(self):
        from ciel_runtime_support import side_wake
        store = session_work_plan_store(self.state, IDENTITY)
        _, goal = store.transaction(lambda doc: tree.add_node(doc, None, 'goal'))
        event = self.handoff_event('wake', goal, {'kind': 'advise', 'waits': [], 'question': 'go?'})
        prepared = side_wake.prepare(store, event, protocol=2, owner_nonce='owner-secret', channel='prompt')
        side_wake.start(store, event, prepared['attempt_id'], 'owner-secret', 'prompt')
        status, side = self.request('/ca/work/api/v1/side')
        self.assertEqual(200, status, side)
        row = next(handoff for handoff in side['handoffs'] if handoff['event_id'] == event)
        self.assertTrue(row['delivery_uncertain'])
        self.assertEqual(('submitting', 'started'), (row['wake']['state'], row['wake']['phase']))
        self.assertNotIn('owner-secret', json.dumps(side))
        self.assertNotIn(prepared['attempt_id'], json.dumps(side))
        body = {'event_id': event, 'attempt_ref': row['wake']['attempt_ref'], 'revision': row['wake']['revision']}
        status, error = self.request('/ca/work/api/v1/commands/side-wake-retry', body)
        self.assertEqual(422, status, error)
        self.assertIn('explicit duplicate-delivery confirmation', error['message'])
        status, done = self.request('/ca/work/api/v1/commands/side-wake-retry', {**body, 'confirmed': True})
        self.assertEqual(200, status, done)
        self.assertEqual('failed', store.read()['side_handoffs'][event]['wake']['state'])
        # The exact revision is required: replaying the same confirmation fails.
        status, _ = self.request('/ca/work/api/v1/commands/side-wake-retry', {**body, 'confirmed': True})
        self.assertEqual(422, status)

    def test_handoff_rejects_non_text_answer_before_commit(self):
        status, error = self.request('/ca/work/api/v1/commands/side-handoff', {
            'event_id': 'c' * 64, 'answer': 7, 'expected_version': 0})
        self.assertEqual(422, status)
        self.assertEqual('coordinator answer must be text', error['message'])

    def test_detail_errors_and_query_validation(self):
        missing = 'a' * 64
        status, error = self.request('/ca/work/api/v1/side/receipt?receipt_id=' + missing)
        self.assertEqual((404, 'unknown_side_receipt'), (status, error['error']))
        for bad in ('', 'xyz', '../' + 'a' * 61, 'A' * 64):
            status, _ = self.request('/ca/work/api/v1/side/receipt?receipt_id=' + bad)
            self.assertEqual(422, status, bad)
        for query in ('limit=0', 'limit=101', 'limit=x', 'stage=bogus', 'priority=P9',
                      'cursor=bad', 'limit=1&limit=2', 'q=' + 'x' * 201):
            status, _ = self.request('/ca/work/api/v1/side?' + query)
            self.assertEqual(422, status, query)

    def test_receipts_are_current_session_scoped(self):
        SideIntakeInbox(self.state, 'claude:other').receive('other session secret', 'event:other')
        mine = self.inbox.receive('my event', 'event:mine')['receipt_id']
        status, side = self.request('/ca/work/api/v1/side')
        self.assertEqual(200, status)
        self.assertEqual([mine], [row['receipt_id'] for row in side['receipts']])
        self.assertNotIn('other session secret', json.dumps(side))
        other = hashlib.sha256(b'event:other').hexdigest()
        self.assertEqual(404, self.request('/ca/work/api/v1/side/receipt?receipt_id=' + other)[0])
        self.assertEqual(422, self.request('/ca/work/api/v1/side?session_id=claude:other')[0])
        self.assertEqual(422, self.request(
            '/ca/work/api/v1/side/receipt?session_id=claude:other&receipt_id=' + other)[0])

    def test_inbox_receive_emits_content_free_side_changed_without_event_id(self):
        response = urllib.request.urlopen(self.base + '/ca/work/api/v1/events', timeout=6)

        def frame():
            lines = []
            while True:
                line = response.readline().decode().strip()
                if not line:
                    return lines
                lines.append(line)
        self.assertIn('event: work.snapshot', frame())
        self.inbox.receive('a new side event with secret body', 'event:live')
        changed = frame()
        response.close()
        self.assertIn('event: side.changed', changed)
        self.assertFalse(any(line.startswith('id:') for line in changed), changed)
        data = json.loads(next(line for line in changed if line.startswith('data: '))[6:])
        self.assertEqual({'session_id': IDENTITY, 'watermark': self.inbox.watermark(),
                          'refetch': '/ca/work/api/v1/side', 'coalesced': True}, data)
        self.assertTrue(data['watermark'])
        self.assertNotIn('secret body', str(changed))
        # The refetch reports the same watermark, so a client can tell it is current.
        self.assertEqual(data['watermark'], self.request('/ca/work/api/v1/side')[1]['watermark'])

    def handoff_event(self, key, goal, main_handoff):
        receipt = self.inbox.receive('event ' + key, 'event:' + key)['receipt_id']
        picked = claim(self.state, IDENTITY, 'actor-' + key)
        finish(self.state, IDENTITY, receipt, picked['generation'], {
            'action': 'advise' if main_handoff['kind'] == 'advise' else 'update', 'kind': 'request',
            'item_id': goal, 'summary': 'needs main', 'reply': 'question', 'delivery': 'route_unavailable',
            'execution_approved': False, 'handoff': 'requested', 'main_handoff': main_handoff})
        store = session_work_plan_store(self.state, IDENTITY)

        def seed(doc):
            doc.setdefault('side_handoffs', {})[receipt] = {
                'item_id': goal, 'summary': 'needs main', 'question': main_handoff['question'],
                'kind': main_handoff['kind'], 'gates': [], 'waits': [], 'state': 'requested',
                'requested_at': 1}
        store.transaction(seed)
        return receipt

    def answer(self, receipt, text):
        version = session_work_plan_store(self.state, IDENTITY).read_snapshot()['version']
        return self.request('/ca/work/api/v1/commands/side-handoff', {
            'event_id': receipt, 'answer': text, 'expected_version': version})

    def test_rest_handoff_answer_syncs_receipt_and_queues_followup_like_mcp(self):
        store = session_work_plan_store(self.state, IDENTITY)
        _, goal = store.transaction(lambda doc: tree.add_node(doc, None, 'goal'))
        advice = self.handoff_event('advice', goal, {'kind': 'advise', 'waits': [], 'question': 'go?'})
        # Created before any answer, so its claim cannot pick up a follow-up event.
        notice = self.handoff_event('p1', goal, {'kind': 'p1', 'waits': [], 'question': ''})
        status, handed = self.answer(advice, 'main says go')
        self.assertEqual(200, status, handed)
        self.assertEqual('answered', self.inbox._load(advice)['result']['handoff'])
        followup = hashlib.sha256(('handoff:' + advice).encode()).hexdigest()
        relayed = self.inbox._load(followup)
        self.assertIsNotNone(relayed)
        self.assertIn('main says go', relayed['text'])
        status, side = self.request('/ca/work/api/v1/side')
        self.assertIn(followup, [row['receipt_id'] for row in side['receipts']])

        # Same gate as MCP: a P1 notice with no question syncs the receipt but
        # is not answered back to the source as a new side event.
        status, handed = self.answer(notice, 'noted')
        self.assertEqual(200, status, handed)
        self.assertEqual('answered', self.inbox._load(notice)['result']['handoff'])
        self.assertIsNone(self.inbox._load(hashlib.sha256(('handoff:' + notice).encode()).hexdigest()))

    def test_flat_contract_fields_and_q_search(self):
        self.config['work_side_intake'] = {'enabled': True}
        first = self.inbox.receive(json.dumps({'data': {'text': 'alpha needle', 'priority': 'P1'}}),
                                   'event:alpha')['receipt_id']
        self.inbox.receive('beta event', 'event:beta')
        picked = claim(self.state, IDENTITY, 'actor')
        from ciel_runtime_support.side_intake_processing import fail
        fail(self.state, IDENTITY, picked['receipt_id'], picked['generation'],
             {'category': 'invalid_answer', 'reason': 'bad decision'})
        status, side = self.request('/ca/work/api/v1/side?limit=25&q=needle')
        self.assertEqual(200, status, side)
        for key in ('version', 'reviews', 'handoffs', 'receipts', 'next_cursor', 'counts',
                    'quarantined_count', 'enabled', 'watermark'):
            self.assertIn(key, side)
        self.assertIsInstance(side['receipts'], list)
        self.assertEqual((True, 1, None), (side['enabled'], side['quarantined_count'], side['next_cursor']))
        self.assertIsInstance(side['watermark'], str)
        self.assertEqual([first], [row['receipt_id'] for row in side['receipts']])
        row = side['receipts'][0]
        self.assertEqual(('quarantined', True, 'invalid_answer', 1, 'P1'),
                         (row['stage'], row['quarantined'], row['failure_category'],
                          row['attempt_count'], row['priority']))
        self.assertEqual({'received': 1, 'processing': 0, 'processed': 0, 'failed': 0,
                          'quarantined': 1, 'duplicate': 0}, side['counts'])
        self.config['work_side_intake'] = {'enabled': False}
        self.assertFalse(self.request('/ca/work/api/v1/side')[1]['enabled'])

    def test_openapi_and_help_document_receipt_contract(self):
        status, document = self.request('/ca/work/api/v1/openapi')
        self.assertEqual(200, status)
        side = document['paths']['/ca/work/api/v1/side']['get']
        self.assertEqual({'limit', 'cursor', 'stage', 'priority', 'q'},
                         {row['name'] for row in side['parameters']})
        detail = document['paths']['/ca/work/api/v1/side/receipt']['get']
        self.assertEqual('^[0-9a-f]{64}$', detail['parameters'][0]['schema']['pattern'])
        self.assertIn('side.changed', document['paths']['/ca/work/api/v1/events']['get']['description'])
        status, help_payload = self.request('/ca/work/help')
        self.assertIn('side_receipt', help_payload['v1'])
        self.assertIn('never returned', help_payload['side_receipts'])


if __name__ == '__main__':
    unittest.main()
