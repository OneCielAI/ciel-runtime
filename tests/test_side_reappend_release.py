"""Person-confirmed release of a re-append whose outcome nobody can know.

A conversation change re-appends an unread payload once under a fenced lease
(side_wake.prepare_reappend, review_delivery.reappend_prepare). If its append
was recorded `uncertain`, or its holder vanished while `submitting`, every
later prepare answers `already_<state>` and nothing could ever release it. The
same retry semantics as a main wake apply: a person confirms one possibly
duplicate re-append. MCP needs the verified host marker; REST goes through the
operator controller. Real backend stores and real dispatch throughout.
"""
import concurrent.futures
import json
import tempfile
import time
import unittest
from unittest.mock import patch

import test_side_notice_claim as claim
import test_work_parallel_finalization as fin
from ciel_runtime_support import review_delivery, side_wake
from ciel_runtime_support import work_parallel_mcp as m
from ciel_runtime_support.http_response import HttpResponseAdapter
from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices
from ciel_runtime_support.work_mcp_tools import dispatch_work_tool
from ciel_runtime_support.work_parallel_finalization import WorkParallelFinalizationStore
from ciel_runtime_support.work_plan_tree import WorkPlanError

HOST_RETRY = {'internal': True}


def appended_wake(session):
    """An appended main wake in conversation A, plus a re-append lease into B."""
    prepared = side_wake.prepare(session.store, session.event, protocol=2, owner_nonce='A', channel='append')
    side_wake.start(session.store, session.event, prepared['attempt_id'], 'A', 'append')
    side_wake.record(session.store, session.event, 'appended', prepared['attempt_id'], owner_nonce='A',
                     receipt='uuid-a', conversation_id='claude:conversation-a')
    lease = side_wake.prepare_reappend(session.store, session.event, 'holder-secret', 'claude:conversation-b')
    return lease


def wake(session):
    return session.store.read()['side_handoffs'][session.event]['wake']


class SideWakeReappendReleaseTests(unittest.TestCase):
    def refused(self, pattern, fn, *args, **kwargs):
        with self.assertRaisesRegex(WorkPlanError, pattern):
            fn(*args, **kwargs)

    def test_an_uncertain_reappend_is_released_once_by_a_person_and_admits_one_lease(self):
        with tempfile.TemporaryDirectory() as raw:
            session = claim.Session(raw, release=False)
            lease = appended_wake(session)
            side_wake.record_reappend(session.store, session.event, lease['attempt_id'], 'holder-secret', 'uncertain',
                                      receipt='uuid-unknown')
            # Stuck before this change: every later prepare is refused.
            self.assertEqual('already_uncertain', side_wake.prepare_reappend(
                session.store, session.event, 'other', 'claude:conversation-b')['state'])
            public = side_wake.redact_wake(wake(session))
            self.assertEqual(('uncertain', True), (public['reappend_state'], public['reappend_uncertain']))
            reference, revision = public['reappend_ref'], public['revision']
            self.assertEqual(side_wake.attempt_ref(lease['attempt_id']), reference)
            for secret in (lease['attempt_id'], 'holder-secret', 'claude:conversation-b', 'uuid-unknown'):
                self.assertNotIn(secret, json.dumps(public))
            self.refused('explicit duplicate', side_wake.retry_reappend, session.store, session.event, reference,
                         revision)
            self.refused('refresh before retry', side_wake.retry_reappend, session.store, session.event, '0' * 16,
                         revision, confirmed=True)
            self.refused('refresh before retry', side_wake.retry_reappend, session.store, session.event, reference,
                         revision - 1, confirmed=True)
            released = side_wake.retry_reappend(session.store, session.event, reference, revision, confirmed=True)
            self.assertNotIn('attempt_id', released)
            self.assertFalse(side_wake.redact_wake(wake(session))['reappend_uncertain'])
            self.refused('refresh before retry', side_wake.retry_reappend, session.store, session.event, reference,
                         released['revision'], confirmed=True)
            audit = wake(session)['reappend_retries']
            self.assertEqual([('uncertain', reference, True)],
                             [(row['previous_state'], row['attempt_ref'], row['duplicate_confirmed']) for row in audit])
            # Exactly one of eight racing hosts in conversation B takes the new lease.
            with concurrent.futures.ThreadPoolExecutor(8) as pool:
                grants = list(pool.map(lambda index: side_wake.prepare_reappend(
                    session.store, session.event, 'host-%d' % index, 'claude:conversation-b'), range(8)))
            self.assertEqual(1, sum(grant['state'] == 'submitting' for grant in grants))
            # The vanished holder's late record cannot overwrite the new lease.
            self.refused('stale side wake reappend attempt', side_wake.record_reappend, session.store, session.event,
                         lease['attempt_id'], 'holder-secret', 'appended')

    def test_a_submitting_reappend_whose_holder_vanished_is_shown_after_60s_and_released(self):
        with tempfile.TemporaryDirectory() as raw:
            session = claim.Session(raw, release=False)
            appended_wake(session)
            # A live holder appends and records within one call: not uncertain yet.
            self.assertFalse(side_wake.redact_wake(wake(session))['reappend_uncertain'])
            self.refused('refresh before retry', side_wake.retry_reappend, session.store, session.event,
                         side_wake.redact_wake(wake(session))['reappend_ref'], wake(session)['revision'],
                         confirmed=True)
            later = time.time() + side_wake.REAPPEND_UNCERTAIN_SECONDS + 1
            public = side_wake.redact_wake(wake(session), now=later)
            self.assertEqual(('submitting', True), (public['reappend_state'], public['reappend_uncertain']))
            with patch.object(side_wake.time, 'time', return_value=later):
                side_wake.retry_reappend(session.store, session.event, public['reappend_ref'], public['revision'],
                                         confirmed=True)
            self.assertEqual('submitting', side_wake.prepare_reappend(session.store, session.event, 'new-holder',
                                                                      'claude:conversation-b')['state'])

    def test_mcp_release_needs_the_hosts_marker_and_rest_releases_through_the_operator_controller(self):
        with tempfile.TemporaryDirectory() as raw:
            session = claim.Session(raw, release=False)
            lease = appended_wake(session)
            side_wake.record_reappend(session.store, session.event, lease['attempt_id'], 'holder-secret', 'uncertain')
            public = side_wake.redact_wake(wake(session))
            args = {'event_id': session.event, 'state': 'reappend_retry', 'reappend_ref': public['reappend_ref'],
                    'revision': public['revision'], 'confirmed': True}
            before = json.dumps(wake(session), sort_keys=True)
            self.assertIn('requires the verified Ciel Work host', session.call('work_side_wake', args, error=True))
            self.assertEqual(before, json.dumps(wake(session), sort_keys=True))
            self.assertEqual('appended', session.call('work_side_wake', args, HOST_RETRY)['state'])
            self.assertFalse(side_wake.redact_wake(wake(session))['reappend_uncertain'])
        with tempfile.TemporaryDirectory() as raw:
            session = claim.Session(raw, release=False)
            lease = appended_wake(session)
            side_wake.record_reappend(session.store, session.event, lease['attempt_id'], 'holder-secret', 'uncertain')
            with claim.rest(session) as call:
                status, page = call('side')
                row = next(item for item in page['handoffs'] if item['event_id'] == session.event)['wake']
                self.assertEqual((True, 'uncertain'), (row['reappend_uncertain'], row['reappend_state']))
                body = {'event_id': session.event, 'reappend_ref': row['reappend_ref'], 'revision': row['revision']}
                status, error = call('commands/side-reappend-retry', body)
                self.assertEqual(422, status, error)
                self.assertIn('explicit duplicate', error['message'])
                status, done = call('commands/side-reappend-retry', {**body, 'confirmed': True})
                self.assertEqual(200, status, done)
                self.assertNotIn(lease['attempt_id'], json.dumps(done))
                status, _ = call('commands/side-reappend-retry', {**body, 'confirmed': True})
                self.assertEqual(422, status)


class ReviewReappendReleaseTests(unittest.TestCase):
    setUp = fin.MainReviewTests.setUp
    call = fin.MainReviewTests.call
    completed = fin.MainReviewTests.completed
    idle = fin.MainReviewTests.idle

    def published(self):
        reserve, _, _ = self.completed()
        self.idle(reserve['lease_id'])
        lease = reserve['lease_id']
        wake_ = self.call('event', {'internal': True}, event='review_wake', operation='prepare', lease_id=lease)
        self.call('event', {'internal': True}, event='review_wake', operation='record', lease_id=lease,
                  attempt_id=wake_['attempt_id'], state='appended')
        return lease, wake_['attempt_id']

    def sidecar(self):
        return review_delivery.ReviewDeliveryStore.beside(
            WorkParallelFinalizationStore(self.tmp / 'work-parallel-finalization.json'))

    def controller(self):
        return WorkHttpController(WorkHttpServices(
            self.tmp / 'state', lambda: {'work_mode': True}, lambda: fin.SCOPE,
            lambda identity: {'session_id': identity, 'last_observed': None}, HttpResponseAdapter.write_json,
            'fixture', parallel_status=lambda identity, board: m.project(self.store, identity, board),
            parallel_store=lambda: self.store))

    def wake_tool(self, args, host=None):
        config = {'work_mode': True, 'work_side_intake': {'enabled': True}, 'work_parallel_host': {'token': 'x' * 40}}
        with patch.object(m, 'ledger', return_value=self.store):
            result = dispatch_work_tool(1, 'work_side_wake', {**args, 'session_id': fin.SCOPE,
                                                              '_host': {'token': 'x' * 40, **(host or {})}},
                                        load_config=lambda: config, state_dir=self.tmp / 'state',
                                        respond=lambda _id, text, error=False: {'text': text, 'error': error})
        return result['text'] if result['error'] else json.loads(result['text'])

    def uncertain(self):
        lease, attempt = self.published()
        side = self.sidecar()
        side.appended(lease, attempt, fin.SCOPE)
        reappend = side.reappend_prepare(lease, 'holder-secret', 'claude:other')
        side.reappend_record(lease, reappend['attempt_id'], 'holder-secret', 'uncertain')
        return lease, side, reappend

    def test_an_uncertain_review_reappend_is_released_once_and_admits_one_lease(self):
        lease, side, reappend = self.uncertain()
        self.assertEqual('already_uncertain', side.reappend_prepare(lease, 'other', 'claude:other')['state'])
        view = review_delivery.public_view(side, [lease], fin.SCOPE)['rows'][0]
        self.assertEqual(('uncertain', True), (view['reappend_state'], view['reappend_uncertain']))
        reference = view['reappend_ref']
        self.assertEqual(review_delivery.attempt_ref(reappend['attempt_id']), reference)
        with self.assertRaisesRegex(WorkPlanError, 'explicit duplicate'):
            side.reappend_retry(lease, reference)
        with self.assertRaisesRegex(WorkPlanError, 'refresh before retry'):
            side.reappend_retry(lease, '0' * 16, confirmed=True)
        args = {'event_id': lease, 'state': 'review_reappend_retry', 'reappend_ref': reference, 'confirmed': True}
        self.assertIn('requires the verified Ciel Work host', self.wake_tool(args))
        self.assertTrue(review_delivery.public_view(side, [lease], fin.SCOPE)['rows'][0]['reappend_uncertain'])
        self.assertEqual('released', self.wake_tool(args, HOST_RETRY)['state'])
        view = review_delivery.public_view(side, [lease], fin.SCOPE)['rows'][0]
        self.assertFalse(view['reappend_uncertain'])
        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            grants = list(pool.map(lambda index: side.reappend_prepare(lease, 'host-%d' % index, 'claude:other'),
                                   range(8)))
        self.assertEqual(1, sum(grant['state'] == 'submitting' for grant in grants))

    def test_rest_releases_only_this_sessions_review(self):
        lease, side, reappend = self.uncertain()
        reference = review_delivery.public_view(side, [lease], fin.SCOPE)['rows'][0]['reappend_ref']
        controller = self.controller()
        with self.assertRaisesRegex(WorkPlanError, 'unknown review for this session'):
            controller.review_reappend_retry('claude:another-session', {'lease_id': lease, 'reappend_ref': reference,
                                                                         'confirmed': True})
        with self.assertRaisesRegex(WorkPlanError, 'explicit duplicate'):
            controller.review_reappend_retry(fin.SCOPE, {'lease_id': lease, 'reappend_ref': reference})
        done = controller.review_reappend_retry(fin.SCOPE, {'lease_id': lease, 'reappend_ref': reference,
                                                            'confirmed': True})
        self.assertEqual('released', done['state'])
        for secret in (reappend['attempt_id'], 'holder-secret', 'claude:other'):
            self.assertNotIn(secret, json.dumps(done))
        self.assertEqual('submitting', side.reappend_prepare(lease, 'new', 'claude:other')['state'])


if __name__ == '__main__':
    unittest.main()
