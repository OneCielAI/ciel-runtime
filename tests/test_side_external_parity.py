"""External control parity for SSE delivery state (REST and browser).

The Claude Side pane and the host already see every main-wake state and the
review_delivery sidecar. A person outside Claude must see the same, read-only
and bounded: REST GET /side and the browser Side tab. No conversation id,
attempt id, owner nonce, receipt, marker or text is ever projected.

REST cases run the real controller over the real backend pipeline (reviews
published by test_work_parallel_finalization's real reserve/bind/report path).
Browser cases are opt-in (CIEL_TEST_BROWSER=<chromium executable>) at desktop
and 390 px, the same pattern as test_work_web_ui.
"""
from contextlib import contextmanager
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_work_parallel_finalization as fin
from ciel_runtime_support import review_delivery, side_wake
from ciel_runtime_support import work_parallel_mcp as m
from ciel_runtime_support.http_response import HttpResponseAdapter
from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices
from ciel_runtime_support.work_parallel_finalization import WorkParallelFinalizationStore

SECRETS = ('attempt_id', 'owner_nonce', 'conversation_id', 'receipt', 'marker', 'attempt_ref')


class _Handler:
    """The parts of BaseHTTPRequestHandler the controller and writer use."""

    def __init__(self, path):
        self.path = path
        self.headers = {}
        self.status = None
        self.body = b''

    def send_response(self, status):
        self.status = status

    def send_header(self, *_):
        pass

    def end_headers(self):
        pass

    @property
    def wfile(self):
        handler = self

        class _Sink:
            def write(self, data):
                handler.body += data
        return _Sink()


class ReviewDeliveryRestTests(unittest.TestCase):
    setUp = fin.MainReviewTests.setUp
    call = fin.MainReviewTests.call
    completed = fin.MainReviewTests.completed
    idle = fin.MainReviewTests.idle

    def controller(self, with_parallel=True):
        return WorkHttpController(WorkHttpServices(
            self.tmp / 'state', lambda: {'work_mode': True}, lambda: fin.SCOPE,
            lambda identity: {'session_id': identity, 'last_observed': None}, HttpResponseAdapter.write_json,
            'fixture', **({'parallel_status': lambda identity, board: m.project(self.store, identity, board),
                           'parallel_store': lambda: self.store} if with_parallel else {})))

    def side(self, controller=None):
        handler = _Handler('/ca/work/api/v1/side?session_id=' + fin.SCOPE)
        with patch.object(m, 'ledger', return_value=self.store):
            (controller or self.controller()).get(handler, '/ca/work/api/v1/side')
        self.assertEqual(200, handler.status, handler.body)
        return json.loads(handler.body)

    def published(self):
        reserve, _, _ = self.completed()
        self.idle(reserve['lease_id'])
        lease = reserve['lease_id']
        wake = self.call('event', {'internal': True}, event='review_wake', operation='prepare', lease_id=lease)
        self.call('event', {'internal': True}, event='review_wake', operation='record', lease_id=lease,
                  attempt_id=wake['attempt_id'], state='appended')
        return lease, wake['attempt_id']

    def sidecar(self):
        return review_delivery.ReviewDeliveryStore.beside(
            WorkParallelFinalizationStore(self.tmp / 'work-parallel-finalization.json'))

    def test_rest_side_shows_each_pending_reviews_delivery_state_without_secrets(self):
        lease, attempt = self.published()
        # No sidecar row yet (an older host, or before the append record).
        view = self.side()['review_delivery']
        self.assertEqual((True, False), (view['available'], view['truncated']))
        self.assertEqual([{'lease_id': lease, 'state': 'unknown'}], view['rows'])
        side = self.sidecar()
        side.appended(lease, attempt, fin.SCOPE, 'uuid-secret-receipt')
        row = self.side()['review_delivery']['rows'][0]
        self.assertEqual(('appended', True, 0), (row['state'], row['in_this_conversation'], row['reappends']))
        self.assertIsInstance(row['appended_at'], float)
        # A conversation change: the row is elsewhere until one re-append lands.
        lease_row = side.reappend_prepare(lease, 'owner-secret', 'claude:other-conversation')
        row = self.side()['review_delivery']['rows'][0]
        self.assertEqual(('reappending', 'submitting'), (row['state'], row['reappend_state']))
        side.reappend_record(lease, lease_row['attempt_id'], 'owner-secret', 'appended', receipt='uuid-secret-2')
        row = self.side()['review_delivery']['rows'][0]
        self.assertEqual(('reappended', False, 1), (row['state'], row['in_this_conversation'], row['reappends']))
        self.assertIsInstance(row['reappended_at'], float)
        side.read(lease)
        page = self.side()
        row = page['review_delivery']['rows'][0]
        self.assertEqual('read', row['state'])
        self.assertIsInstance(row['read_at'], float)
        text = json.dumps(page['review_delivery'])
        for secret in (attempt, lease_row['attempt_id'], 'owner-secret', 'uuid-secret-receipt', 'uuid-secret-2',
                       'claude:other-conversation', review_delivery.attempt_ref(attempt), *SECRETS):
            self.assertNotIn(secret, text)

    def test_rest_side_reports_unavailable_without_a_parallel_store(self):
        self.published()
        view = self.side(self.controller(with_parallel=False))['review_delivery']
        self.assertEqual({'available': False, 'rows': [], 'truncated': False}, view)


class MainWakeRestTests(unittest.TestCase):
    """Every wake state is already in REST handoffs.wake (redact_wake); pinned here."""

    def test_rest_side_exports_every_main_wake_state_bounded(self):
        from test_side_notice_claim import Session, rest
        with tempfile.TemporaryDirectory() as raw:
            session = Session(raw, release=False)
            self.assertEqual('requested', session.result['handoff'])
            prepared = side_wake.prepare(session.store, session.event, protocol=2, owner_nonce='owner-secret',
                                         channel='append')
            side_wake.start(session.store, session.event, prepared['attempt_id'], 'owner-secret', 'append')
            side_wake.record(session.store, session.event, 'appended', prepared['attempt_id'],
                             owner_nonce='owner-secret', receipt='uuid-secret', conversation_id=session.identity)
            with rest(session) as call:
                status, page = call('side')
            self.assertEqual(200, status, page)
            wake = next(row for row in page['handoffs'] if row['event_id'] == session.event)['wake']
            self.assertEqual(('appended', False), (wake['state'], wake['delivery_uncertain']))
            for secret in (prepared['attempt_id'], 'owner-secret', 'uuid-secret', 'conversation_id', 'prompt'):
                self.assertNotIn(secret, json.dumps(page['handoffs']))


@contextmanager
def stream_server(session):
    """The REST controller of a test_side_notice_claim.Session on loopback."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    controller = WorkHttpController(WorkHttpServices(
        session.root, lambda: session.config, lambda: session.identity,
        lambda identity: {'session_id': identity, 'last_observed': None},
        HttpResponseAdapter.write_json, 'parity-instance'))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if not controller.get(self, self.path.split('?')[0]):
                self.send_error(404)

        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:' + str(server.server_port) + '/ca/work/api/v1/'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def frames(response):
    """Yield one SSE frame (its non-empty lines) at a time."""
    while True:
        lines = []
        while True:
            line = response.readline().decode().strip()
            if not line:
                break
            lines.append(line)
        yield lines


class NoticeStateAndRefreshTests(unittest.TestCase):
    def notice_rows(self, session):
        from test_side_notice_claim import rest
        with rest(session) as call:
            status, page = call('side')
            self.assertEqual(200, status, page)
            row = next(item for item in page['receipts'] if item['receipt_id'] == session.event)
            status, detail = call('side/receipt?receipt_id=' + session.event)
            self.assertEqual(200, status, detail)
        return row, detail['receipt'], json.dumps([page, detail])

    def test_every_notice_state_word_is_on_the_receipt_row_and_detail_without_secrets(self):
        from test_side_notice_claim import Session, NOTICE_TEXT
        for outcome in ('issuing', 'appended', 'failed', 'uncertain', 'released'):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as raw:
                session = Session(raw, release=True)
                claim = session.notice('notice_prepare', owner_nonce='owner-secret', text=NOTICE_TEXT)
                if outcome in ('appended', 'failed', 'uncertain', 'released'):
                    recorded = 'uncertain' if outcome == 'released' else outcome
                    session.notice('notice_' + recorded, attempt_id=claim['attempt_id'], owner_nonce='owner-secret',
                                   **({'receipt': 'uuid-secret'} if recorded == 'appended' else {}))
                if outcome == 'released':
                    session.call('work_side_wake', {'event_id': session.event, 'state': 'notice_retry',
                                                    'confirmed': True, **session.notice_pins()}, {'internal': True})
                row, detail, text = self.notice_rows(session)
                self.assertEqual((outcome, outcome), (row['notice_state'], detail['notice_state']))
                self.assertEqual(outcome == 'uncertain', row['notice_delivery_uncertain'])
                for secret in (claim['attempt_id'], 'owner-secret', 'uuid-secret', NOTICE_TEXT):
                    self.assertNotIn(secret, text)

    def test_an_issuing_notice_crossing_its_grace_refreshes_an_open_side_tab(self):
        import urllib.request
        from test_side_notice_claim import Session, NOTICE_TEXT, later
        with tempfile.TemporaryDirectory() as raw:
            session = Session(raw, release=True)
            session.notice('notice_prepare', owner_nonce='vanished', text=NOTICE_TEXT)
            with stream_server(session) as base:
                # Longer than the 15 s heartbeat: without a side.changed the
                # stream yields a heartbeat frame, never a hang.
                response = urllib.request.urlopen(base + 'events', timeout=25)
                try:
                    stream = frames(response)
                    self.assertIn('event: work.snapshot', next(stream))
                    with later(61):
                        frame = next(stream)
                    self.assertIn('event: side.changed', frame, frame)
                    self.assertNotIn(NOTICE_TEXT, json.dumps(frame))
                finally:
                    response.close()

    def test_a_reappend_turning_uncertain_by_age_refreshes_an_open_side_tab(self):
        import urllib.request
        from test_side_notice_claim import Session
        with tempfile.TemporaryDirectory() as raw:
            session = Session(raw, release=False)
            prepared = side_wake.prepare(session.store, session.event, protocol=2, owner_nonce='A', channel='append')
            side_wake.start(session.store, session.event, prepared['attempt_id'], 'A', 'append')
            side_wake.record(session.store, session.event, 'appended', prepared['attempt_id'], owner_nonce='A',
                             conversation_id='claude:conversation-a')
            side_wake.prepare_reappend(session.store, session.event, 'vanished', 'claude:conversation-b')
            with stream_server(session) as base:
                response = urllib.request.urlopen(base + 'events', timeout=25)
                try:
                    stream = frames(response)
                    self.assertIn('event: work.snapshot', next(stream))
                    import time as real_time
                    shifted = real_time.time() + side_wake.REAPPEND_UNCERTAIN_SECONDS + 1
                    with patch.object(side_wake.time, 'time', return_value=shifted):
                        frame = next(stream)
                    self.assertIn('event: side.changed', frame, frame)
                finally:
                    response.close()


class ReviewDeliveryRefreshTests(unittest.TestCase):
    """Gap 3 (continue-ctl, measured): review_delivery sidecar changes sent no
    Work /events signal, so an open Side tab kept a stale Review delivery
    section. A change written by this or another process, a re-append turning
    uncertain by age alone, and a person's re-append release each now send one
    side.changed. Before the fix the stream only heartbeats."""
    setUp = fin.MainReviewTests.setUp
    call = fin.MainReviewTests.call
    completed = fin.MainReviewTests.completed
    idle = fin.MainReviewTests.idle
    controller = ReviewDeliveryRestTests.controller
    published = ReviewDeliveryRestTests.published
    sidecar = ReviewDeliveryRestTests.sidecar

    @contextmanager
    def stream(self):
        import threading
        import urllib.request
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        controller = self.controller()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if not controller.get(self, self.path.split('?')[0]):
                    self.send_error(404)

            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with patch.object(m, 'ledger', return_value=self.store):
            thread.start()
            # Longer than the 15 s heartbeat: without a signal the stream
            # yields a heartbeat, never a hang.
            response = urllib.request.urlopen(
                'http://127.0.0.1:' + str(server.server_port) + '/ca/work/api/v1/events', timeout=25)
            try:
                stream = frames(response)
                self.assertIn('event: work.snapshot', next(stream))
                yield stream
            finally:
                response.close()
                server.shutdown()
                server.server_close()
                thread.join(3)

    def signal(self, stream):
        """side.changed, or the first heartbeat when nothing told the tab."""
        for _ in range(20):
            frame = next(stream)
            if 'event: side.changed' in frame:
                return 'side.changed'
            if frame == [': heartbeat']:
                return 'heartbeat'
        return 'other frames only'

    def test_a_review_delivery_write_in_this_process_refreshes_an_open_side_tab(self):
        lease, attempt = self.published()
        with self.stream() as stream:
            self.sidecar().appended(lease, attempt, fin.SCOPE, 'uuid-secret')
            self.assertEqual('side.changed', self.signal(stream))

    def test_a_review_delivery_write_from_another_process_refreshes_an_open_side_tab(self):
        import subprocess
        import sys
        lease, attempt = self.published()
        self.sidecar().appended(lease, attempt, fin.SCOPE, 'uuid-secret')
        code = ('import sys\n'
                'from pathlib import Path\n'
                'from ciel_runtime_support import review_delivery\n'
                'from ciel_runtime_support.work_parallel_finalization import WorkParallelFinalizationStore\n'
                'store = review_delivery.ReviewDeliveryStore.beside(WorkParallelFinalizationStore(Path(sys.argv[1])))\n'
                'store.reappend_prepare(sys.argv[2], "owner-other-process", "claude:other-conversation")\n'
                'print("written")\n')
        with self.stream() as stream:
            done = subprocess.run([sys.executable, '-c', code, str(self.tmp / 'work-parallel-finalization.json'),
                                   lease], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
                                  timeout=120, check=False)
            self.assertEqual((0, 'written'), (done.returncode, done.stdout.strip()), done.stderr)
            self.assertEqual('side.changed', self.signal(stream))
        row = ReviewDeliveryRestTests.side(self)['review_delivery']['rows'][0]
        self.assertEqual(('reappending', 'submitting'), (row['state'], row['reappend_state']))

    def test_a_reappend_turning_uncertain_by_age_refreshes_an_open_side_tab(self):
        import time as real_time
        lease, attempt = self.published()
        side = self.sidecar()
        side.appended(lease, attempt, fin.SCOPE, 'uuid-secret')
        side.reappend_prepare(lease, 'vanished-holder', 'claude:other-conversation')
        with self.stream() as stream:
            shifted = real_time.time() + review_delivery.REAPPEND_UNCERTAIN_SECONDS + 1
            with patch.object(review_delivery.time, 'time', return_value=shifted):
                self.assertEqual('side.changed', self.signal(stream))

    def test_a_review_reappend_release_refreshes_an_open_side_tab(self):
        lease, attempt = self.published()
        side = self.sidecar()
        side.appended(lease, attempt, fin.SCOPE, 'uuid-secret')
        held = side.reappend_prepare(lease, 'owner-secret', 'claude:other-conversation')
        side.reappend_record(lease, held['attempt_id'], 'owner-secret', 'uncertain')
        reference = review_delivery.attempt_ref(held['attempt_id'])
        with self.stream() as stream:
            side.reappend_retry(lease, reference, confirmed=True)
            self.assertEqual('side.changed', self.signal(stream))


class ReplyRetryOutsideReviewTests(unittest.TestCase):
    """#27: a reply retry belongs to the receipt's delivery state, not to the
    pending side-review list. Once main reviewed the side answer, a failed or
    unknown reply must still be retryable from REST (and so from the web)."""

    def seeded(self, raw, outcome):
        from ciel_runtime_support import side_reply_outbox
        from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
        from test_side_notice_claim import Session
        session = Session(raw, release=False)
        inbox = SideIntakeInbox(session.root, session.identity)
        # Fixture: the event arrived from web chat, so the outbox has a route.
        row = inbox._load(session.event)
        row['route'] = {'source_kind': 'web_chat', 'reply_token': 'token-secret', 'channel': 'web'}
        inbox._save_file(inbox._event_path('receipts', session.event), row)
        prepared = side_reply_outbox.prepare(session.root, session.identity, session.event)
        side_reply_outbox.record(session.root, session.identity, session.event, outcome,
                                 attempt_id=prepared['attempt_id'])
        # Main reviewed the side answer: it leaves the pending side-review list.
        session.call('work_side_review', {'receipt_ids': [session.event], 'verdict': 'ok', 'note': '',
                                          'expected_version': session.version()})
        return session

    def test_a_reviewed_receipts_failed_or_unknown_reply_is_retryable_from_rest(self):
        from test_side_notice_claim import rest
        for outcome, confirm in (('failed', False), ('uncertain', True)):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as raw:
                session = self.seeded(raw, outcome)
                with rest(session) as call:
                    status, page = call('side')
                    self.assertNotIn(session.event, [row['receipt_id'] for row in page['reviews']
                                                     if row.get('state') == 'pending'])
                    row = next(item for item in page['receipts'] if item['receipt_id'] == session.event)
                    self.assertEqual(outcome, row['delivery'])
                    # The exact attempt the person saw (retry pins from the same row).
                    body = {'receipt_id': session.event, 'expected_state': outcome,
                            'revision': row['reply_revision'], 'attempt_ref': row['reply_attempt_ref']}
                    if confirm:
                        status, error = call('commands/side-reply-retry', body)
                        self.assertEqual(422, status, error)
                        self.assertIn('explicit duplicate-send confirmation', error['message'])
                    status, done = call('commands/side-reply-retry', {**body, 'confirmed': confirm})
                    self.assertEqual(200, status, done)
                    # The public receipt row (as GET /side lists it), never the raw record.
                    self.assertEqual('pending', done['result']['delivery'])
                    self.assertNotIn('original', done['result'])
                    self.assertNotIn('token-secret', json.dumps(done))
                    status, page = call('side')
                    row = next(item for item in page['receipts'] if item['receipt_id'] == session.event)
                    self.assertEqual('pending', row['delivery'])


class BrowserParityTests(unittest.TestCase):
    """The browser Side tab renders every wake state and the review delivery view."""

    def html(self):
        from ciel_runtime_support.work_web_ui import render_work_web_ui
        return render_work_web_ui()

    def test_side_tab_renders_every_wake_state_and_review_delivery(self):
        html = self.html()
        # Every requested handoff gets its wake state line, not only uncertain ones.
        self.assertIn("'Main delivery: '+wakeLabel(row.wake)", html)
        for word in ("appended", "queued", "failed", "read by main"):
            self.assertIn(word, html)
        # The review_delivery view, bounded fields only.
        self.assertIn("'Review delivery ('", html)
        self.assertIn('data.review_delivery', html)
        for field in ('in_this_conversation', 'reappends', 'reappend_state'):
            self.assertIn(field, html)
        for secret in ('conversation_id', 'owner_nonce', 'attempt_id'):
            self.assertNotIn('row.' + secret, html)

    @unittest.skipUnless(os.environ.get("CIEL_TEST_BROWSER"), "opt-in browser verification")
    def test_side_tab_browser_shows_wake_and_review_delivery_at_desktop_and_390px(self):
        from playwright.sync_api import sync_playwright
        def handoff(event, state, **extra):
            return {
                'event_id': event * 64, 'state': 'requested', 'kind': 'resume', 'item_id': 'n2', 'summary': 'event ' + state,
                'question': '', 'waits': [], 'wake': {'state': state, 'revision': 3, 'delivery_uncertain': state == 'uncertain',
                                                      **extra}, 'delivery_uncertain': state == 'uncertain'}
        side = {'ok': True, 'schema_version': 1, 'session_id': 'claude:browser', 'version': 7, 'enabled': True,
                'reviews': [], 'receipts': [], 'next_cursor': None, 'has_more': False, 'total': 0, 'counts': {},
                'counts_complete': True, 'quarantined_count': 0, 'watermark': None,
                'handoffs': [handoff('a', 'appended'), handoff('b', 'appended', read_at=1.0), handoff('c', 'queued'),
                             handoff('d', 'failed'), handoff('e', 'uncertain', attempt_ref='0' * 16)],
                'review_delivery': {'available': True, 'truncated': False, 'rows': [
                    {'lease_id': 'L-here', 'state': 'appended', 'in_this_conversation': True, 'reappends': 0,
                     'appended_at': 1.0},
                    {'lease_id': 'L-moved', 'state': 'reappended', 'in_this_conversation': False, 'reappends': 1,
                     'appended_at': 1.0, 'reappended_at': 2.0},
                    {'lease_id': 'L-busy', 'state': 'reappending', 'in_this_conversation': False, 'reappends': 0,
                     'reappend_state': 'submitting', 'appended_at': 1.0},
                    {'lease_id': 'L-read', 'state': 'read', 'in_this_conversation': True, 'reappends': 0,
                     'appended_at': 1.0, 'read_at': 3.0},
                    {'lease_id': 'L-old', 'state': 'unknown'}]}}
        out = Path(os.environ.get('CIEL_TEST_SCREENSHOT_DIR') or tempfile.mkdtemp(prefix='side-parity-'))
        out.mkdir(parents=True, exist_ok=True)
        html = self.html()
        board = {'session_id': 'claude:browser', 'version': 7, 'nodes': [], 'phase': 'idle'}

        def route(request):
            url = request.request.url
            if url.endswith('/ca/work/ui'):
                request.fulfill(content_type='text/html', body=html)
            elif '/events' in url:
                request.fulfill(content_type='text/event-stream', body=': ready\n\n')
            elif '/side?' in url:
                request.fulfill(json=side)
            elif '/board' in url:
                request.fulfill(json=board)
            else:
                request.fulfill(json={'reviews': [], 'handoffs': []})
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ["CIEL_TEST_BROWSER"], headless=True)
            try:
                for label, viewport in (('desktop', {'width': 1280, 'height': 1400}),
                                        ('mobile', {'width': 390, 'height': 1600})):
                    with self.subTest(viewport=label):
                        page = browser.new_page(viewport=viewport)
                        page.route('http://fixture.test/**', route)
                        page.goto('http://fixture.test/ca/work/ui')
                        page.get_by_role('tab', name='Side').click()
                        page.get_by_text('Review delivery (5)').wait_for()
                        body = page.inner_text('body')
                        for expected in ('Main delivery: appended', 'Main delivery: appended · read by main',
                                         'Main delivery: queued', 'Main delivery: failed', 'Main delivery: uncertain',
                                         'L-moved', 'reappended', 'L-busy', 'reappending', 'L-read', 'L-old'):
                            self.assertIn(expected, body)
                        width = page.evaluate('document.documentElement.scrollWidth')
                        self.assertLessEqual(width, viewport['width'])
                        page.screenshot(path=str(out / ('side-parity-' + label + '.png')), full_page=True)
                        page.close()
            finally:
                browser.close()

    @unittest.skipUnless(os.environ.get("CIEL_TEST_BROWSER"), "opt-in browser verification")
    def test_side_tab_browser_retry_controls_send_the_exact_commands_at_desktop_and_390px(self):
        """W3-W7: the notice state and uncertain marker on the receipt row, the
        may-duplicate re-append releases, and the reply retry outside the
        pending side-review list, each posting exactly the REST command."""
        from playwright.sync_api import sync_playwright
        failed_id, unknown_id = 'f' * 64, 'e' * 64
        # Retry pins (team-lead defects 2/3): every retry names the exact attempt.
        def receipt(rid, delivery, notice, recap, ref, revision):
            return {
                'receipt_id': rid, 'priority': 'P2', 'stage': 'processed', 'delivery': delivery, 'recap': recap,
                'received_at': 1, 'notice_state': notice, 'notice_delivery_uncertain': notice == 'uncertain',
                'notice_attempt_ref': '7' * 16, 'notice_revision': 2, 'reply_attempt_ref': ref, 'reply_revision': revision}
        receipts = [receipt(failed_id, 'failed', 'uncertain', 'reply failed request', '5' * 16, 0),
                    receipt(unknown_id, 'uncertain', 'appended', 'reply unknown request', '6' * 16, 3)]
        def wake(**extra):
            return {'state': 'appended', 'revision': 3, 'delivery_uncertain': False, **extra}
        side = {'ok': True, 'schema_version': 1, 'session_id': 'claude:browser', 'version': 7, 'enabled': True,
                'reviews': [], 'receipts': receipts, 'next_cursor': None, 'has_more': False, 'total': 2,
                'counts': {}, 'counts_complete': True, 'quarantined_count': 0, 'watermark': None,
                'handoffs': [{'event_id': 'a' * 64, 'state': 'requested', 'kind': 'resume', 'item_id': 'n2',
                              'summary': 'stuck re-append', 'question': '', 'waits': [], 'delivery_uncertain': False,
                              'wake': wake(reappend_state='uncertain', reappend_ref='1' * 16, reappend_uncertain=True)},
                             {'event_id': 'b' * 64, 'state': 'requested', 'kind': 'resume', 'item_id': 'n3',
                              'summary': 'live re-append', 'question': '', 'waits': [], 'delivery_uncertain': False,
                              'wake': wake(reappend_state='submitting', reappend_ref='2' * 16,
                                           reappend_uncertain=False)}],
                'review_delivery': {'available': True, 'truncated': False, 'rows': [
                    {'lease_id': 'L-stuck', 'state': 'reappending', 'in_this_conversation': False, 'reappends': 0,
                     'reappend_state': 'uncertain', 'reappend_ref': '3' * 16, 'reappend_uncertain': True},
                    {'lease_id': 'L-live', 'state': 'reappending', 'in_this_conversation': False, 'reappends': 0,
                     'reappend_state': 'submitting', 'reappend_ref': '4' * 16, 'reappend_uncertain': False}]}}
        details = {rid: {**row, 'original': row['recap']} for rid, row in
                   ((row['receipt_id'], row) for row in receipts)}
        out = Path(os.environ.get('CIEL_TEST_SCREENSHOT_DIR') or tempfile.mkdtemp(prefix='side-parity-'))
        out.mkdir(parents=True, exist_ok=True)
        html = self.html()
        board = {'session_id': 'claude:browser', 'version': 7, 'nodes': [], 'phase': 'idle'}

        def route(request):
            url = request.request.url
            if url.endswith('/ca/work/ui'):
                request.fulfill(content_type='text/html', body=html)
            elif '/events' in url:
                request.fulfill(content_type='text/event-stream', body=': ready\n\n')
            elif '/commands/' in url:
                request.fulfill(json={'ok': True, 'result': {}})
            elif '/side/receipt' in url:
                rid = url.split('receipt_id=')[1].split('&')[0]
                request.fulfill(json={'ok': True, 'receipt': details[rid]})
            elif '/side?' in url:
                request.fulfill(json=side)
            elif '/board' in url:
                request.fulfill(json=board)
            else:
                request.fulfill(json={'reviews': [], 'handoffs': []})

        def posted(page, name, click, accept):
            with page.expect_request(lambda request: request.url.split('?')[0].endswith('/commands/' + name)
                                     and request.method == 'POST') as sent:
                click()
                if accept:
                    page.locator('#confirm-accept').click()
            body = sent.value.post_data_json
            body.pop('operation_id'), body.pop('expected_version')
            return body

        def open_detail(page, rid, recap):
            row = page.locator('.receipt-row', has_text=recap)
            row.get_by_role('button', name=re.compile('Show redacted detail')).click()
            page.locator('.receipt-detail').get_by_text('notice state').wait_for()
            return row
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ["CIEL_TEST_BROWSER"], headless=True)
            try:
                for label, viewport in (('desktop', {'width': 1280, 'height': 1400}),
                                        ('mobile', {'width': 390, 'height': 1800})):
                    with self.subTest(viewport=label):
                        page = browser.new_page(viewport=viewport)
                        page.route('http://fixture.test/**', route)
                        page.goto('http://fixture.test/ca/work/ui')
                        page.get_by_role('tab', name='Side').click()
                        page.get_by_text('Review delivery (2)').wait_for()
                        # W3/W4 on the list row, without opening the detail.
                        failed_row = page.locator('.receipt-row', has_text='reply failed request').inner_text()
                        unknown_row = page.locator('.receipt-row', has_text='reply unknown request').inner_text()
                        self.assertIn('Release notice: uncertain', failed_row)
                        self.assertIn('Release notice uncertain', failed_row)
                        self.assertIn('Release notice: appended', unknown_row)
                        self.assertNotIn('Release notice uncertain', unknown_row)
                        # W5/W6 only for the uncertain lease, each behind the confirmation.
                        reappend = page.get_by_role('button', name='Retry re-append (may duplicate)', exact=True)
                        review = page.get_by_role('button', name='Retry review re-append (may duplicate)', exact=True)
                        self.assertEqual((1, 1), (reappend.count(), review.count()))
                        # Each lease id stays on one line, also at 390 px.
                        lines = page.eval_on_selector_all(
                            '.review-delivery .item-head strong',
                            'els => els.map(e => {const r = document.createRange(); r.selectNodeContents(e);'
                            ' return r.getClientRects().length})')
                        self.assertEqual([1, 1], lines)
                        # W7 a reviewed receipt's reply retry, by its delivery state.
                        row = open_detail(page, failed_id, 'reply failed request')
                        self.assertEqual(0, row.get_by_role('button', name='Resend reply (may duplicate)').count())
                        page.screenshot(path=str(out / ('side-retry-' + label + '.png')), full_page=True)
                        self.assertEqual({'receipt_id': failed_id, 'expected_state': 'failed', 'revision': 0,
                                          'attempt_ref': '5' * 16},
                                         posted(page, 'side-reply-retry',
                                                row.get_by_role('button', name='Retry reply send').click, False))
                        page.get_by_text('Review delivery (2)').wait_for()
                        # The retry reloaded the Side tab: the first Details click on the
                        # row that was open must open it again (toggleReceiptDetail).
                        row = open_detail(page, failed_id, 'reply failed request')
                        notice = row.get_by_role('button', name='Retry release notice (may duplicate)')
                        self.assertEqual({'receipt_id': failed_id, 'attempt_ref': '7' * 16, 'revision': 2,
                                          'confirmed': True},
                                         posted(page, 'side-notice-retry', notice.click, True))
                        page.get_by_text('Review delivery (2)').wait_for()
                        self.assertEqual(
                            {'event_id': 'a' * 64, 'reappend_ref': '1' * 16, 'revision': 3, 'confirmed': True},
                            posted(page, 'side-reappend-retry', reappend.click, True))
                        page.get_by_text('Review delivery (2)').wait_for()
                        self.assertEqual({'lease_id': 'L-stuck', 'reappend_ref': '3' * 16, 'confirmed': True},
                                         posted(page, 'review-reappend-retry', review.click, True))
                        page.get_by_text('Review delivery (2)').wait_for()
                        row = open_detail(page, unknown_id, 'reply unknown request')
                        self.assertEqual(0, row.get_by_role('button', name='Retry reply send').count())
                        self.assertEqual(
                            {'receipt_id': unknown_id, 'expected_state': 'uncertain', 'revision': 3,
                             'attempt_ref': '6' * 16, 'confirmed': True},
                            posted(page, 'side-reply-retry',
                                   row.get_by_role('button', name='Resend reply (may duplicate)').click, True))
                        page.get_by_text('Review delivery (2)').wait_for()
                        # W8 the console control of REST side-drain (a read that admits nothing).
                        drain = page.get_by_role('button', name='Re-check side inbox', exact=True)
                        self.assertEqual({'limit': 10}, posted(page, 'side-drain', drain.click, False))
                        width = page.evaluate('document.documentElement.scrollWidth')
                        self.assertLessEqual(width, viewport['width'])
                        page.close()
            finally:
                browser.close()


if __name__ == '__main__':
    unittest.main()
