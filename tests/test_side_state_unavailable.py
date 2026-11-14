"""Corrupt or unreadable side and review-delivery state is a 503, never a 422.

continue-work-backend's typed classification (work_http_controller._error):
OSError, WorkStateUnavailable and WorkParallelStateUnavailable become 503
"state_unavailable" with the fixed message "Work state unreadable"; anything
else stays a 422 with its message. The side inbox (receipts, pending rows, the
legacy document) and the review_delivery sidecar raised plain WorkPlanError on
a corrupt read, so an operator saw a 422 "invalid_request" carrying the raw
stored-state detail. Each REST path below must now answer 503 without it.
"""
import json
import tempfile
import unittest
from unittest.mock import patch

import test_work_parallel_finalization as fin
from test_side_external_parity import ReviewDeliveryRestTests, _Handler
from test_side_notice_claim import Session, rest
from ciel_runtime_support import work_parallel_mcp as m
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox

UNAVAILABLE = {'ok': False, 'error': 'state_unavailable', 'message': 'Work state unreadable'}


def corrupt(path, data):
    path.write_bytes(data)


class SideInboxStateUnavailableTests(unittest.TestCase):
    CASES = (
        ('unreadable', b'{not json'),
        ('schema', b'[1, 2, 3]'),
        ('invalid row', json.dumps({'text': '', 'event_key': 'x', 'stage': 'processed'}).encode()),
        ('over limit', b' ' * (2 * 1024 * 1024 + 1)),
    )

    def test_a_corrupt_receipt_is_503_on_the_detail_and_the_reply_retry(self):
        for name, data in self.CASES:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as raw:
                session = Session(raw, release=False)
                inbox = SideIntakeInbox(session.root, session.identity)
                corrupt(inbox._event_path('receipts', session.event), data)
                with rest(session) as call:
                    status, detail = call('side/receipt?receipt_id=' + session.event)
                    self.assertEqual((503, UNAVAILABLE), (status, {k: detail.get(k) for k in UNAVAILABLE}), detail)
                    status, retried = call('commands/side-reply-retry', {
                        'receipt_id': session.event, 'expected_state': 'route_unavailable', 'revision': 0})
                    self.assertEqual((503, UNAVAILABLE), (status, {k: retried.get(k) for k in UNAVAILABLE}),
                                     retried)
                for body in (detail, retried):
                    self.assertNotIn('side inbox', json.dumps(body))

    def test_a_corrupt_legacy_inbox_document_is_503(self):
        with tempfile.TemporaryDirectory() as raw:
            session = Session(raw, release=False)
            inbox = SideIntakeInbox(session.root, session.identity)
            # An unmigrated legacy document whose rows are not objects.
            (inbox.directory / 'mailbox-v2.json').unlink()
            corrupt(inbox.path, json.dumps({'version': 1, 'requests': {'k': 'not a row'}}).encode())
            with rest(session) as call:
                status, detail = call('side/receipt?receipt_id=' + session.event)
            self.assertEqual((503, UNAVAILABLE), (status, {k: detail.get(k) for k in UNAVAILABLE}), detail)
            self.assertNotIn('side inbox', json.dumps(detail))


class ReviewDeliveryStateUnavailableTests(unittest.TestCase):
    setUp = fin.MainReviewTests.setUp
    call = fin.MainReviewTests.call
    completed = fin.MainReviewTests.completed
    idle = fin.MainReviewTests.idle
    controller = ReviewDeliveryRestTests.controller
    published = ReviewDeliveryRestTests.published
    sidecar = ReviewDeliveryRestTests.sidecar

    def post(self, path, body):
        handler = _Handler('/ca/work/api/v1/commands/' + path)
        with patch.object(m, 'ledger', return_value=self.store):
            self.assertTrue(self.controller().post(handler, '/ca/work/api/v1/commands/' + path, body))
        return handler.status, json.loads(handler.body)

    def test_a_corrupt_review_delivery_sidecar_is_503_on_its_release(self):
        cases = (('unreadable', b'{not json'),
                 ('another producer', json.dumps({'producer': 'other', 'schema': 1, 'version': 0,
                                                   'deliveries': {}}).encode()),
                 ('over limit', b' ' * (4 * 1024 * 1024 + 1)))
        lease, _ = self.published()
        path = self.sidecar().path
        for name, data in cases:
            with self.subTest(case=name):
                corrupt(path, data)
                status, body = self.post('review-reappend-retry', {
                    'lease_id': lease, 'reappend_ref': '0' * 16, 'confirmed': True})
                self.assertEqual((503, UNAVAILABLE), (status, {k: body.get(k) for k in UNAVAILABLE}), body)
                self.assertNotIn('sidecar', json.dumps(body))
                # Left unchanged: nothing writes over a sidecar it cannot read.
                self.assertEqual(data, path.read_bytes())


if __name__ == '__main__':
    unittest.main()
