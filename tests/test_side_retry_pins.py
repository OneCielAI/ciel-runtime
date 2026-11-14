"""Exact attempt pins for the person-confirmed notice and reply retries.

continue-work-backend's replay-pin audit (team-lead defects 2 and 3):
- side_notice.retry checked only "is it uncertain now". A late replay (a
  stale web click, an evicted idempotency key) released a different, later
  uncertain attempt.
- side_reply_outbox.retry was pinned only by the word expected_state. After a
  retry went pending and uncertain again, the same word resent another attempt.

Both now need the one-way attempt_ref of the exact attempt plus a revision (the
notice's retries, the reply's delivery_retries count), exported on every
operator surface, the way the main-wake retry already works. A stale pin is
refused and writes nothing.
"""
import json
import tempfile
import unittest

from ciel_runtime_support import side_reply_outbox
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
from ciel_runtime_support.side_wake import attempt_ref
from test_side_notice_claim import HOST_RETRY, NOTICE_TEXT, Session, rest

STALE_NOTICE = 'side notice changed; refresh before retry'
NEEDS_NOTICE = "side notice retry needs the receipt's notice_attempt_ref and notice_revision"
STALE_REPLY = 'side delivery changed; refresh before retry'
NEEDS_REPLY = "side reply retry needs the receipt's reply_revision and reply_attempt_ref"


def surfaces(session):
    """The receipt as GET /side, GET /side/receipt and host work_side_status show it."""
    with rest(session) as call:
        status, page = call('side')
        assert status == 200, page
        status, detail = call('side/receipt?receipt_id=' + session.event)
        assert status == 200, detail
    row = next(item for item in page['receipts'] if item['receipt_id'] == session.event)
    host = next(item for item in session.call('work_side_status', {})['side_inbox_receipts']
                if item['receipt_id'] == session.event)
    return row, detail['receipt'], host, page


class NoticeRetryPinTests(unittest.TestCase):
    def uncertain(self, session, owner):
        claim = session.notice('notice_prepare', owner_nonce=owner, text=NOTICE_TEXT)
        self.assertEqual('claimed', claim['state'])
        session.notice('notice_uncertain', attempt_id=claim['attempt_id'], owner_nonce=owner)
        return claim['attempt_id']

    def retry(self, session, ref, revision, error=False):
        return session.call('work_side_wake', {'event_id': session.event, 'state': 'notice_retry', 'confirmed': True,
                                               'attempt_ref': ref, 'revision': revision}, HOST_RETRY, error=error)

    def pins(self, session):
        row, detail, host, _ = surfaces(session)
        pins = [(view.get('notice_attempt_ref'), view.get('notice_revision')) for view in (row, detail, host)]
        self.assertEqual(1, len(set(pins)), pins)
        return pins[0]

    def test_a_late_notice_retry_cannot_release_a_newer_uncertain_attempt(self):
        with tempfile.TemporaryDirectory() as raw:
            session = Session(raw, release=True)
            first = self.uncertain(session, 'A')
            self.assertEqual('released', self.retry(session, attempt_ref(first), 0)['state'])
            # The host re-issued it once; that append is unknown again.
            second = self.uncertain(session, 'B')
            before = session.stored_notice()
            # The first click, replayed late (evicted key or a stale page).
            # Before the fix it released attempt B without anyone seeing it.
            self.assertIn(STALE_NOTICE, self.retry(session, attempt_ref(first), 0, error=True))
            self.assertIn(STALE_NOTICE, self.retry(session, attempt_ref(second), 0, error=True))
            self.assertIn(STALE_NOTICE, self.retry(session, attempt_ref(first), 1, error=True))
            self.assertEqual(before, session.stored_notice())
            # The current pins are on every surface, and they release B once.
            self.assertEqual((attempt_ref(second), 1), self.pins(session))
            self.assertEqual('released', self.retry(session, attempt_ref(second), 1)['state'])

    def test_a_retry_without_a_valid_pin_is_refused_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as raw:
            session = Session(raw, release=True)
            first = self.uncertain(session, 'A')
            before = session.stored_notice()
            for ref, revision in ((None, 0), (attempt_ref(first), None), ('XYZ', 0), (attempt_ref(first), -1),
                                  (attempt_ref(first), True), (first, 0)):
                with self.subTest(ref=ref, revision=revision):
                    args = {'event_id': session.event, 'state': 'notice_retry', 'confirmed': True}
                    args.update({key: value for key, value in (('attempt_ref', ref), ('revision', revision))
                                 if value is not None})
                    self.assertIn(NEEDS_NOTICE, session.call('work_side_wake', args, HOST_RETRY, error=True))
            self.assertEqual(before, session.stored_notice())

    def test_pins_are_exported_without_the_attempt(self):
        with tempfile.TemporaryDirectory() as raw:
            session = Session(raw, release=True)
            first = self.uncertain(session, 'A')
            row, detail, host, page = surfaces(session)
            self.assertNotIn(first, json.dumps([page, detail, host]))
            for view in (row, detail, host):
                self.assertEqual((attempt_ref(first), 0), (view['notice_attempt_ref'], view['notice_revision']))


class ReplyRetryPinTests(unittest.TestCase):
    def seeded(self, raw):
        session = Session(raw, release=False)
        inbox = SideIntakeInbox(session.root, session.identity)
        row = inbox._load(session.event)
        # Fixture: the event arrived from web chat, so the outbox has a route.
        row['route'] = {'source_kind': 'web_chat', 'reply_token': 'token-secret', 'channel': 'web'}
        inbox._save_file(inbox._event_path('receipts', session.event), row)
        return session

    def send(self, session, outcome):
        prepared = side_reply_outbox.prepare(session.root, session.identity, session.event)
        self.assertEqual('submitting', prepared['state'])
        side_reply_outbox.record(session.root, session.identity, session.event, outcome,
                                 attempt_id=prepared['attempt_id'])
        return prepared['attempt_id']

    def retry(self, session, state, ref, revision, confirmed=True, error=False):
        args = {'receipt_id': session.event, 'expected_state': state, 'confirmed': confirmed, 'revision': revision}
        if ref is not None:
            args['attempt_ref'] = ref
        return session.call('work_side_retry_reply', args, HOST_RETRY, error=error)

    def stored(self, session):
        return SideIntakeInbox(session.root, session.identity)._load(session.event)

    def pins(self, session):
        row, detail, host, page = surfaces(session)
        review = [item for item in page['reviews'] if item['receipt_id'] == session.event]
        views = [row, detail, host, *review]
        pins = [(view.get('reply_attempt_ref'), view.get('reply_revision')) for view in views]
        self.assertEqual(1, len(set(pins)), pins)
        return pins[0]

    def test_a_late_reply_retry_cannot_resend_a_newer_uncertain_attempt(self):
        with tempfile.TemporaryDirectory() as raw:
            session = self.seeded(raw)
            first = self.send(session, 'uncertain')
            self.assertEqual('pending', self.retry(session, 'uncertain', attempt_ref(first), 0)['receipt']['delivery'])
            # The host sent it again; that outcome is unknown again.
            second = self.send(session, 'uncertain')
            before = self.stored(session)
            # The first click, replayed late. Before the fix the same word
            # 'uncertain' matched and attempt B was queued for a resend.
            for ref, revision in ((attempt_ref(first), 0), (attempt_ref(second), 0), (attempt_ref(first), 1)):
                with self.subTest(ref=ref, revision=revision):
                    self.assertIn(STALE_REPLY, self.retry(session, 'uncertain', ref, revision, error=True))
            self.assertEqual(before, self.stored(session))
            self.assertEqual((attempt_ref(second), 1), self.pins(session))
            self.assertEqual('pending', self.retry(session, 'uncertain', attempt_ref(second), 1)['receipt']['delivery'])

    def test_a_late_retry_of_a_route_unavailable_reply_is_refused_by_its_revision(self):
        with tempfile.TemporaryDirectory() as raw:
            session = Session(raw, release=False)
            self.assertEqual('route_unavailable',
                             side_reply_outbox.prepare(session.root, session.identity, session.event)['state'])
            self.retry(session, 'route_unavailable', None, 0, confirmed=False)
            self.assertEqual('route_unavailable',
                             side_reply_outbox.prepare(session.root, session.identity, session.event)['state'])
            before = self.stored(session)
            # No attempt exists here, so only the revision tells the two apart.
            self.assertIn(STALE_REPLY, self.retry(session, 'route_unavailable', None, 0, confirmed=False, error=True))
            self.assertEqual(before, self.stored(session))
            self.assertEqual((None, 1), self.pins(session))
            self.retry(session, 'route_unavailable', None, 1, confirmed=False)

    def test_a_reply_retry_without_a_valid_pin_is_refused_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as raw:
            session = self.seeded(raw)
            first = self.send(session, 'failed')
            before = self.stored(session)
            for ref, revision in ((attempt_ref(first), None), (None, 0), ('XYZ', 0), (attempt_ref(first), -1),
                                  (attempt_ref(first), True), (first, 0)):
                with self.subTest(ref=ref, revision=revision):
                    args = {'receipt_id': session.event, 'expected_state': 'failed'}
                    args.update({key: value for key, value in (('attempt_ref', ref), ('revision', revision))
                                 if value is not None})
                    self.assertIn(NEEDS_REPLY, session.call('work_side_retry_reply', args, HOST_RETRY, error=True))
            self.assertEqual(before, self.stored(session))
            self.retry(session, 'failed', attempt_ref(first), 0, confirmed=False)

    def test_reply_pins_are_exported_without_the_attempt(self):
        with tempfile.TemporaryDirectory() as raw:
            session = self.seeded(raw)
            first = self.send(session, 'uncertain')
            row, detail, host, page = surfaces(session)
            self.assertNotIn(first, json.dumps([page, detail, host]))
            self.assertNotIn('token-secret', json.dumps([page, detail]))
            self.assertEqual((attempt_ref(first), 0), self.pins(session))


if __name__ == '__main__':
    unittest.main()
