"""Real backend: the released-wait notice claim and the appended-wake read mark.

Both go through the real MCP dispatch (dispatch_work_tool) onto the real
session Work store and side inbox in a temporary directory.
"""
import concurrent.futures
from contextlib import contextmanager
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support import side_wake
from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
from ciel_runtime_support.work_mcp_tools import dispatch_work_tool
from ciel_runtime_support.work_plan_store import session_work_plan_store

ROOT = Path(__file__).resolve().parents[1]
# What the mod's own person-confirmed retry call adds to its authenticated _host.
HOST_RETRY = {'internal': True}
OLD_READERS = ('57dbfb3ac9c8d9f1a42e89a038d27e16f4fab409',
               '8e08b97bf29878a9cf7302c5e945c0307675aa06')
# Runs inside the historical package: its own SideIntakeInbox load + whole-row
# save, then its own reply-outbox prepare, which rewrites the processed receipt.
OLD_INBOX_WRITER = '''
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import ciel_runtime_support
from ciel_runtime_support import side_reply_outbox
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
state, identity, event = Path(sys.argv[2]), sys.argv[3], sys.argv[4]
inbox = SideIntakeInbox(state, identity)
row = inbox._load(event)
inbox._save_file(inbox._event_path('receipts', event), row)
prepared = side_reply_outbox.prepare(state, identity, event)
print(json.dumps({'package': ciel_runtime_support.__file__, 'loaded_notice': row.get('release_notice'),
                  'prepared': prepared['state'],
                  'has_side_notice': (Path(ciel_runtime_support.__file__).parent / 'side_notice.py').exists()}))
'''


def legacy_package(commit, into):
    """Extract one historical ciel_runtime_support, or None in a shallow checkout."""
    done = subprocess.run(['git', '-c', 'core.autocrlf=false', '-c', 'core.eol=lf', 'archive', commit, '--',
                           'ciel_runtime_support'], cwd=ROOT, capture_output=True, check=False)
    if done.returncode:
        return None
    target = Path(into) / commit[:12]
    target.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(done.stdout)) as archive:
        if hasattr(tarfile, 'data_filter'):
            archive.extractall(target, filter='data')
        else:
            archive.extractall(target)
    return target.resolve()


class Session:
    """One real session: a goal with a waiting gate and one processed side event."""

    def __init__(self, raw, *, release):
        self.root = Path(raw)
        self.identity = 'claude:test'
        self.config = {'work_mode': True, 'work_side_intake': {'enabled': True, 'context_mode': 'isolated'},
                       'work_parallel_host': {'token': 'x' * 40}}
        self.store = session_work_plan_store(self.root, self.identity)
        _, self.item = self.store.transaction(lambda d: tree.add_node(d, None, 'report'))
        _, self.gate = self.store.transaction(lambda d: tree.add_node(d, self.item, 'gate', waiting_on='approval 7'))
        SideIntakeInbox(self.root, self.identity).receive('approval 7 granted', 'approval')
        self.claim = self.call('work_side_claim', {'owner': 'actor'})['claim']
        self.event = self.claim['receipt_id']
        # A wait release is an update; an unreleased gate alone no longer asks
        # main to act. Wake/read tests need a real explicit escalation.
        decision = {'action': 'update' if release else 'advise', 'item_id': self.gate,
                    'summary': 'approval received' if release else 'main decision required',
                    'reply': 'ok', 'note': 'approval 7 granted'}
        if release:
            decision['release_waits'] = [{'id': self.gate, 'waiting_on': 'approval 7'}]
        self.result = self.call('work_side_resolve', {'decision': decision, 'expected_version': self.version()},
                                {'receipt_id': self.event, 'generation': self.claim['generation']})['result']
        assert self.result['handoff'] == ('not_required' if release else 'requested'), self.result

    def call(self, name, args, extra=None, error=False):
        result = dispatch_work_tool(1, name, {**args, 'session_id': self.identity,
                                              '_host': {'token': 'x' * 40, **(extra or {})}},
                                    load_config=lambda: self.config, state_dir=self.root,
                                    respond=lambda _id, text, error=False: {'text': text, 'error': error})
        if result['error'] != error:
            raise AssertionError(result)
        return result['text'] if error else json.loads(result['text'])

    def version(self):
        return self.store.read()['version']

    def notice(self, state, **args):
        return self.call('work_side_wake', {'event_id': self.event, 'state': state, **args})

    def stored_notice(self):
        return SideIntakeInbox(self.root, self.identity)._load(self.event).get('release_notice')

    def notice_pins(self):
        """The exact-attempt pins a notice retry sends back (the receipt's
        notice_attempt_ref and notice_revision, as every surface shows them)."""
        from ciel_runtime_support.side_notice import retry_pin
        pin = retry_pin(self.stored_notice())
        return {'attempt_ref': pin.get('notice_attempt_ref'), 'revision': pin.get('notice_revision')}


class SideNoticeClaimTests(unittest.TestCase):
    def test_notice_of_one_receipt_is_granted_once_and_survives_a_new_process(self):
        # Item 1. The claim lives on the event's processed receipt, so nothing
        # the host can lose (module state, $.state, its process) reopens it.
        with tempfile.TemporaryDirectory() as raw:
            session = Session(raw, release=True)
            self.assertEqual([{'id': session.gate, 'waiting_on': 'approval 7'}], session.result['released_waits'])
            first = session.notice('notice_prepare', owner_nonce='module-A')
            self.assertEqual(('claimed', 2), (first['state'], first['protocol']))
            attempt = first['attempt_id']
            # A second host instance, and the same one again: no attempt id at all.
            for owner in ('module-B', 'module-A'):
                again = session.notice('notice_prepare', owner_nonce=owner)
                self.assertEqual('already_issuing', again['state'])
                self.assertNotIn('attempt_id', again)
            # Only the granted attempt and its owner record the outcome.
            self.assertIn('stale side notice attempt',
                          session.call('work_side_wake', {'event_id': session.event, 'state': 'notice_appended',
                                                          'attempt_id': attempt, 'owner_nonce': 'module-B',
                                                          'receipt': 'uuid-1'}, error=True))
            self.assertIn('stale side notice attempt',
                          session.call('work_side_wake', {'event_id': session.event, 'state': 'notice_appended',
                                                          'attempt_id': 'f' * 32, 'owner_nonce': 'module-A'}, error=True))
            recorded = session.notice('notice_appended', attempt_id=attempt, owner_nonce='module-A', receipt='uuid-1')
            self.assertEqual('appended', recorded['state'])
            # Identical repeat is idempotent; a different outcome is refused.
            self.assertEqual('appended', session.notice('notice_appended', attempt_id=attempt, owner_nonce='module-A',
                                                         receipt='uuid-1')['state'])
            self.assertIn('already recorded differently',
                          session.call('work_side_wake', {'event_id': session.event, 'state': 'notice_failed',
                                                          'attempt_id': attempt, 'owner_nonce': 'module-A'}, error=True))
            # A later process (fresh inbox object, any owner) is still refused.
            self.assertEqual('already_appended', session.notice('notice_prepare', owner_nonce='process-2')['state'])
            self.assertEqual(('appended', 'uuid-1'), (session.stored_notice()['state'], session.stored_notice()['receipt']))

    def test_definite_failure_reopens_once_but_uncertain_does_not(self):
        with tempfile.TemporaryDirectory() as raw:
            session = Session(raw, release=True)
            first = session.notice('notice_prepare', owner_nonce='A')['attempt_id']
            session.notice('notice_failed', attempt_id=first, owner_nonce='A')
            retry = session.notice('notice_prepare', owner_nonce='B')
            self.assertEqual('claimed', retry['state'])
            self.assertNotEqual(first, retry['attempt_id'])
            self.assertEqual('already_issuing', session.notice('notice_prepare', owner_nonce='C')['state'])
        with tempfile.TemporaryDirectory() as raw:
            session = Session(raw, release=True)
            attempt = session.notice('notice_prepare', owner_nonce='A')['attempt_id']
            session.notice('notice_uncertain', attempt_id=attempt, owner_nonce='A')
            self.assertEqual('already_uncertain', session.notice('notice_prepare', owner_nonce='B')['state'])

    def test_crashed_issuer_is_not_reopened_without_delivery_evidence(self):
        with tempfile.TemporaryDirectory() as raw:
            session = Session(raw, release=True)
            session.notice('notice_prepare', owner_nonce='crashed-before-or-after-append')
            # No current API exposes issuer liveness or the append UUID through
            # session.messages. Reopening on timeout could race a late append.
            self.assertEqual('already_issuing', session.notice('notice_prepare', owner_nonce='new-process')['state'])

    def test_concurrent_claims_grant_exactly_one(self):
        with tempfile.TemporaryDirectory() as raw:
            session = Session(raw, release=True)
            with concurrent.futures.ThreadPoolExecutor(8) as pool:
                answers = list(pool.map(lambda index: session.notice('notice_prepare', owner_nonce='owner-' + str(index)),
                                        range(8)))
            self.assertEqual(1, sum(answer['state'] == 'claimed' for answer in answers))
            self.assertEqual(7, sum(answer['state'] == 'already_issuing' for answer in answers))

    def test_only_a_processed_receipt_that_released_a_wait_can_be_claimed(self):
        with tempfile.TemporaryDirectory() as raw:
            session = Session(raw, release=False)
            self.assertNotIn('released_waits', session.result)
            self.assertIn('released-wait receipt required',
                          session.call('work_side_wake', {'event_id': session.event, 'state': 'notice_prepare',
                                                          'owner_nonce': 'A'}, error=True))
            self.assertIn('released-wait receipt required',
                          session.call('work_side_wake', {'event_id': 'a' * 64, 'state': 'notice_prepare',
                                                          'owner_nonce': 'A'}, error=True))
            self.assertIsNone(session.stored_notice())

    def test_the_claim_never_reaches_a_status_or_receipt_projection(self):
        with tempfile.TemporaryDirectory() as raw:
            session = Session(raw, release=True)
            first = session.notice('notice_prepare', owner_nonce='module-owner-nonce')
            session.notice('notice_appended', attempt_id=first['attempt_id'], owner_nonce='module-owner-nonce',
                           receipt='receipt-uuid-77')
            inbox = SideIntakeInbox(session.root, session.identity)
            outputs = json.dumps([session.call('work_side_status', {}), session.call('work_status', {'format': 'json'}),
                                  inbox.receipt_detail(session.event), inbox.receipt_page(),
                                  session.call('work_side_claim', {'owner': 'actor'})])
            for secret in (first['attempt_id'], 'module-owner-nonce', 'receipt-uuid-77', 'release_notice'):
                self.assertNotIn(secret, outputs)

    def test_old_inbox_and_reply_writers_keep_the_release_notice_claim(self):
        # Mixed version: an installed older router process loads and rewrites the
        # processed receipt that carries the claim. Each historical package runs
        # unmodified in its own interpreter (`python -I`, only that tree on
        # sys.path), so its absolute and relative imports are all its own.
        unavailable = []
        for old_commit in OLD_READERS:
            for state in ('issuing', 'appended', 'uncertain'):
                with self.subTest(old_commit=old_commit[:7], notice=state), tempfile.TemporaryDirectory() as raw:
                    session = Session(raw, release=True)
                    attempt = session.notice('notice_prepare', owner_nonce='old-compat-owner')['attempt_id']
                    if state != 'issuing':
                        # appended from one exact API-view ref row: non-uuid receipt.
                        session.notice('notice_' + state, attempt_id=attempt, owner_nonce='old-compat-owner',
                                       **({'receipt': 'ref:fedcba9876543210'} if state == 'appended' else {}))
                    path = SideIntakeInbox(session.root, session.identity)._event_path('receipts', session.event)
                    expected = json.loads(path.read_text(encoding='utf-8'))['release_notice']
                    self.assertEqual(state, expected['state'])
                    old_tree = legacy_package(old_commit, Path(raw) / 'legacy')
                    if old_tree is None:
                        unavailable.append(old_commit)
                        continue
                    done = subprocess.run([sys.executable, '-I', '-c', OLD_INBOX_WRITER, str(old_tree), str(session.root),
                                           session.identity, session.event], capture_output=True, text=True,
                                          encoding='utf-8', timeout=120, check=False)
                    self.assertEqual(0, done.returncode, done.stderr)
                    report = json.loads(done.stdout.strip().splitlines()[-1])
                    # The code that ran is the historical package, not today's.
                    self.assertTrue(Path(report['package']).is_relative_to(old_tree), report['package'])
                    self.assertFalse(report['has_side_notice'])
                    self.assertEqual(expected, report['loaded_notice'])
                    saved = json.loads(path.read_text(encoding='utf-8'))
                    # The old reply writer really rewrote the row...
                    self.assertEqual(report['prepared'], saved['result']['delivery'])
                    # ...and the claim subtree is unchanged, so the fence holds.
                    self.assertEqual(expected, saved['release_notice'])
                    self.assertEqual('already_' + state, session.notice('notice_prepare', owner_nonce='after-old')['state'])
        if len(set(unavailable)) == len(OLD_READERS):
            self.skipTest('no historical commit in this checkout')
        self.assertEqual([], unavailable, 'one historical commit is missing from this checkout')

    def test_which_repeats_of_one_completion_the_real_backend_allows(self):
        # Measured on the real backend. A processed receipt is never claimed
        # again, so a new process gets no second fork for the event. But a
        # repeated resolve of the SAME generation answers the stored result,
        # still marked duplicate False with its released_waits: a host that
        # completed one fork twice would build the same notice text again. The
        # claim is what makes that second notice impossible.
        with tempfile.TemporaryDirectory() as raw:
            session = Session(raw, release=True)
            for owner in ('actor', 'restarted-actor'):
                self.assertIsNone(session.call('work_side_claim', {'owner': owner})['claim'])
            repeat = session.call('work_side_resolve', {'decision': {'action': 'answer', 'summary': 's', 'reply': 'r'},
                                                        'expected_version': session.version()},
                                  {'receipt_id': session.event, 'generation': session.claim['generation']})
            self.assertTrue(repeat['receipt']['duplicate'])
            self.assertIs(False, repeat['result']['duplicate'])
            self.assertEqual(session.result['released_waits'], repeat['result']['released_waits'])
            # A stale generation is refused outright.
            self.assertIn('stale', session.call('work_side_resolve', {
                'decision': {'action': 'answer', 'summary': 's', 'reply': 'r'}, 'expected_version': session.version()},
                {'receipt_id': session.event, 'generation': 'f' * 32}, error=True))
            first = session.notice('notice_prepare', owner_nonce='A')
            self.assertEqual('claimed', first['state'])
            self.assertEqual('already_issuing', session.notice('notice_prepare', owner_nonce='A')['state'])


class SideWakeReadTests(unittest.TestCase):
    def appended(self, session, owner='A'):
        prepared = side_wake.prepare(session.store, session.event, protocol=2, owner_nonce=owner, channel='append')
        side_wake.start(session.store, session.event, prepared['attempt_id'], owner, 'append')
        side_wake.record(session.store, session.event, 'appended', prepared['attempt_id'], owner_nonce=owner,
                         receipt='uuid-append-1', conversation_id=session.identity)
        return prepared['attempt_id']

    def offered(self, session):
        answer = session.call('work_side_claim', {'owner': 'actor'})
        self.assertEqual(2, answer['wake_protocol'])
        return [(wake['event_id'], wake['wake']['state']) for wake in answer['main_wakes']]

    def test_appended_wake_is_offered_until_main_had_a_request_after_it(self):
        # An append starts no turn (owned probe delivery-4f4c90b8), so the row
        # is offered until a host records that main had a request after it.
        with tempfile.TemporaryDirectory() as raw:
            session = Session(raw, release=False)
            self.assertEqual('requested', session.result['handoff'])
            self.appended(session)
            self.assertEqual([(session.event, 'appended')], self.offered(session))
            before = session.store.read()
            read = session.call('work_side_wake', {'event_id': session.event, 'state': 'read'})
            self.assertEqual(('appended', 2), (read['state'], read['protocol']))
            after = session.store.read()
            # Wake-only: the Work version, nodes and updated_at are untouched.
            self.assertEqual((before['version'], before['nodes'], before.get('updated_at')),
                             (after['version'], after['nodes'], after.get('updated_at')))
            wake = after['side_handoffs'][session.event]['wake']
            self.assertTrue(wake['read_at'])
            self.assertEqual([], self.offered(session))
            # Idempotent: the same read again changes nothing.
            revision = wake['revision']
            session.call('work_side_wake', {'event_id': session.event, 'state': 'read'})
            self.assertEqual(revision, session.store.read()['side_handoffs'][session.event]['wake']['revision'])
            # The redacted wake tells readers main has it; no secret is added.
            public = side_wake.redact_wake(wake)
            self.assertTrue(public['read_at'])
            self.assertFalse(public['delivery_uncertain'])
            self.assertNotIn('uuid-append-1', json.dumps(public))

    def test_conversation_change_fences_one_reappend_and_one_wake_prompt(self):
        with tempfile.TemporaryDirectory() as raw:
            session = Session(raw, release=False)
            attempt = self.appended(session)
            self.assertEqual(session.identity, session.store.read()['side_handoffs'][session.event]['wake']['conversation_id'])
            # Two processes in the new conversation race. Exactly one receives a
            # payload re-append lease; exactly one receives a wake-prompt lease.
            with concurrent.futures.ThreadPoolExecutor(8) as pool:
                reappends = list(pool.map(lambda index: side_wake.prepare_reappend(
                    session.store, session.event, 'owner-' + str(index), 'claude:new'), range(8)))
            winners = [row for row in reappends if row['state'] == 'submitting']
            self.assertEqual(1, len(winners))
            winner = winners[0]
            side_wake.record_reappend(session.store, session.event, winner['attempt_id'], winner['owner_nonce'],
                                      'appended', receipt='uuid-new')
            wake = session.store.read()['side_handoffs'][session.event]['wake']
            self.assertEqual(('appended', 'claude:new', 'uuid-new', attempt),
                             (wake['state'], wake['conversation_id'], wake['receipt'], wake['attempt_id']))
            with concurrent.futures.ThreadPoolExecutor(8) as pool:
                prompts = list(pool.map(lambda index: side_wake.prepare_prompt(
                    session.store, session.event, 'prompt-' + str(index), 'claude:new'), range(8)))
            self.assertEqual(1, sum(row['state'] == 'submitting' for row in prompts))
            self.assertEqual(7, sum(row['state'] == 'already_submitting' for row in prompts))

    def test_prompt_lease_timeline_bounds_outstanding_wake_prompts(self):
        # Every step goes through the real work_side_wake dispatch. At any time
        # at most one prompt lease is submitting or queued per event and
        # conversation; only a recorded failed or uncertain outcome, a new
        # conversation, or nothing at all (after read) changes that.
        with tempfile.TemporaryDirectory() as raw:
            session = Session(raw, release=False)
            self.appended(session)
            def ask(owner, conversation):
                return session.notice('prompt_prepare', owner_nonce=owner, receipt=conversation)

            def tell(lease, state, owner=None, error=False):
                return session.call('work_side_wake', {
                    'event_id': session.event, 'state': 'prompt_' + state, 'attempt_id': lease['attempt_id'],
                    'owner_nonce': owner or lease['owner_nonce']}, error=error)

            def lease_now():
                return session.store.read()['side_handoffs'][session.event]['wake'].get('prompt')
            grants, secrets = [], []

            def race(conversation, count=8):
                with concurrent.futures.ThreadPoolExecutor(count) as pool:
                    rows = list(pool.map(lambda index: ask('owner-%s-%d' % (conversation[-2:], index), conversation),
                                         range(count)))
                won = [row for row in rows if row['state'] == 'submitting']
                self.assertEqual(1, len(won), rows)
                for row in rows:
                    if row['state'] != 'submitting':
                        # A refusal never carries anything that could record.
                        self.assertNotIn('attempt_id', row)
                        self.assertNotIn('owner_nonce', row)
                grants.append(won[0])
                secrets.extend((won[0]["attempt_id"], won[0]["owner_nonce"]))
                return won[0]

            # 1 concurrent polls: one lease.
            first = race('claude:c1')
            # 2 held and repeated polls, the holder's own included: no second lease.
            for owner in ('owner-c1-0', 'owner-c1-1', first['owner_nonce'], 'late'):
                self.assertEqual('already_submitting', ask(owner, 'claude:c1')['state'])
            # 3 queued is terminal for this conversation until read or change.
            tell(first, 'queued')
            for owner in ('late-1', first['owner_nonce']):
                self.assertEqual('already_queued', ask(owner, 'claude:c1')['state'])
            self.assertIn('stale side wake prompt attempt', tell(first, 'failed', owner='intruder', error=True))
            self.assertIn('already recorded differently', tell(first, 'failed', error=True))
            # 4 a new conversation gets one lease of its own.
            second = race('claude:c2', 4)
            self.assertEqual('already_submitting', ask('late-2', 'claude:c2')['state'])
            # 5 a definite failure permits exactly one later lease.
            tell(second, 'failed')
            third = race('claude:c2', 4)
            self.assertNotEqual(second['attempt_id'], third['attempt_id'])
            self.assertIn('stale side wake prompt attempt', tell(second, 'queued', error=True))
            # 6 an unknown outcome also permits one later lease. The earlier
            # prompt may still enter: it holds no payload (one extra turn).
            tell(third, 'uncertain')
            fourth = race('claude:c2', 4)
            self.assertEqual(4, len(grants))
            self.assertEqual(fourth['attempt_id'], lease_now()['attempt_id'])
            # 7 read ends the lease; nothing is granted afterwards and the
            # holder's late record changes nothing.
            session.call('work_side_wake', {'event_id': session.event, 'state': 'read'})
            self.assertIsNone(lease_now())
            for conversation in ('claude:c1', 'claude:c2', 'claude:c3'):
                self.assertEqual('already_read', ask('after-read', conversation)['state'])
            self.assertEqual('appended', tell(fourth, 'queued')['state'])
            self.assertIsNone(lease_now())
            # No public reader sees an owner, attempt or conversation.
            inbox = SideIntakeInbox(session.root, session.identity)
            outputs = json.dumps([session.call('work_side_status', {}), session.call('work_status', {'format': 'json'}),
                                  inbox.receipt_page(), session.call('work_side_claim', {'owner': 'actor'})])
            for secret in secrets + ['claude:c1', 'claude:c2']:
                self.assertNotIn(secret, outputs)

    def test_a_prompt_lease_whose_holder_died_expires_once_into_one_later_wake(self):
        # Liveness: the holder of a `submitting` wake-prompt lease dies before it
        # submits (main idle, nothing typed). Within the window every host is
        # refused, as before; after the window the backend treats it as
        # uncertain and grants exactly one new lease. The payload row is the one
        # durable append; nothing re-appends it. A time-limit event.
        from ciel_runtime_support import side_wake as wake_module
        with tempfile.TemporaryDirectory() as raw:
            session = Session(raw, release=False)
            self.appended(session)
            row = session.store.read()['side_handoffs'][session.event]
            self.assertEqual('appended', row['wake']['state'])
            dead = session.notice('prompt_prepare', owner_nonce='dead-holder', receipt=session.identity)
            self.assertEqual('submitting', dead['state'])
            started = session.store.read()['side_handoffs'][session.event]['wake']['prompt']['started_at']
            real = wake_module.time

            class Clock:
                def __init__(self, at):
                    self.at = at

                def time(self):
                    return self.at

                def __getattr__(self, name):
                    return getattr(real, name)
            try:
                wake_module.time = Clock(started + wake_module.PROMPT_LEASE_SECONDS - 0.5)
                self.assertEqual('already_submitting',
                                 session.notice('prompt_prepare', owner_nonce='live-host', receipt=session.identity)['state'])
                wake_module.time = Clock(started + wake_module.PROMPT_LEASE_SECONDS)
                with concurrent.futures.ThreadPoolExecutor(8) as pool:
                    leases = list(pool.map(lambda index: session.notice(
                        'prompt_prepare', owner_nonce='host-%d' % index, receipt=session.identity), range(8)))
            finally:
                wake_module.time = real
            self.assertEqual(1, sum(lease['state'] == 'submitting' for lease in leases), leases)
            prompt = session.store.read()['side_handoffs'][session.event]['wake']['prompt']
            self.assertEqual(('uncertain', True), (prompt['replaced']['state'], prompt['replaced']['expired']))
            self.assertEqual(16, len(prompt['replaced']['attempt_ref']))
            self.assertNotEqual(dead['attempt_id'], prompt['attempt_id'])
            # The dead holder's late record is refused; the new one records.
            self.assertIn('stale side wake prompt attempt', session.call('work_side_wake', {
                'event_id': session.event, 'state': 'prompt_queued', 'attempt_id': dead['attempt_id'],
                'owner_nonce': 'dead-holder'}, error=True))
            winner = next(lease for lease in leases if lease['state'] == 'submitting')
            session.notice('prompt_queued', attempt_id=winner['attempt_id'], owner_nonce=winner['owner_nonce'])
            wake = session.store.read()['side_handoffs'][session.event]['wake']
            # Still the one append: same attempt, same receipt, no second payload.
            self.assertEqual(('appended', 'uuid-append-1'), (wake['state'], wake['receipt']))
            for secret in (dead['attempt_id'], 'dead-holder', winner['attempt_id']):
                self.assertNotIn(secret, json.dumps(side_wake.redact_wake(wake)))

    def test_read_needs_an_appended_wake_of_a_requested_handoff(self):
        with tempfile.TemporaryDirectory() as raw:
            session = Session(raw, release=False)
            self.assertIn('side wake is not appended',
                          session.call('work_side_wake', {'event_id': session.event, 'state': 'read'}, error=True))
            self.appended(session)
            session.call('work_side_handoff', {'event_id': session.event, 'expected_version': session.version()})
            # Acknowledged: nothing left to read or to offer.
            self.assertIn('requested side handoff required',
                          session.call('work_side_wake', {'event_id': session.event, 'state': 'read'}, error=True))
            self.assertEqual([], self.offered(session))

    def test_a_new_attempt_starts_unread(self):
        with tempfile.TemporaryDirectory() as raw:
            session = Session(raw, release=False)
            first = side_wake.prepare(session.store, session.event, protocol=2, owner_nonce='A', channel='append')
            side_wake.start(session.store, session.event, first['attempt_id'], 'A', 'append')
            side_wake.record(session.store, session.event, 'failed', first['attempt_id'], owner_nonce='A')
            # A stale read_at (none can exist here, but the field is cleared on
            # every new attempt) never marks the next attempt read.
            session.store.transaction(
                lambda doc: doc['side_handoffs'][session.event]['wake'].update(read_at=1.0), semantic=False)
            second = side_wake.prepare(session.store, session.event, protocol=2, owner_nonce='B', channel='append')
            self.assertNotEqual(first['attempt_id'], second['attempt_id'])
            self.assertNotIn('read_at', session.store.read()['side_handoffs'][session.event]['wake'])


NOTICE_TEXT = 'Ciel side released recorded wait n3 ("approval 7") — event abc: approval received.'


class _Clock:
    """time with a skew, for the modules that read the issuing grace."""

    def __init__(self, skew):
        import time as real
        self.real, self.skew = real, skew

    def time(self):
        return self.real.time() + self.skew

    def __getattr__(self, name):
        return getattr(self.real, name)


@contextmanager
def later(seconds):
    from ciel_runtime_support import side_intake_inbox as inbox_module, side_notice as notice_module
    saved = inbox_module.time, notice_module.time
    inbox_module.time = notice_module.time = _Clock(seconds)
    try:
        yield
    finally:
        inbox_module.time, notice_module.time = saved


@contextmanager
def rest(session):
    """The browser REST surface on a real loopback socket."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    import urllib.error
    import urllib.request
    from ciel_runtime_support.http_response import HttpResponseAdapter
    from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices
    controller = WorkHttpController(WorkHttpServices(
        session.root, lambda: session.config, lambda: session.identity,
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

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = 'http://127.0.0.1:' + str(server.server_port) + '/ca/work/api/v1/'

    def call(path, body=None):
        request = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                         headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)
    try:
        yield call
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


class SideNoticeVisibilityTests(unittest.TestCase):
    """An unknown notice outcome is shown to a person, and only a person releases it."""

    def public(self, session, call):
        status, page = call('side')
        self.assertEqual(200, status, page)
        row = next(item for item in page['receipts'] if item['receipt_id'] == session.event)
        status, detail = call('side/receipt?receipt_id=' + session.event)
        self.assertEqual(200, status, detail)
        detail = detail.get('receipt') or detail.get('detail') or detail
        status_rows = session.call('work_side_status', {})['side_inbox_receipts']
        host_row = next(item for item in status_rows if item['receipt_id'] == session.event)
        return row, detail, host_row, json.dumps([page, detail, status_rows])

    def test_crash_before_or_after_append_is_one_visible_state_that_only_a_person_releases(self):
        # Measured on the real backend: an issuer that vanished before its
        # append and one that vanished after it (before recording) leave the
        # same durable claim. Nothing reopens it on time alone.
        shapes = []
        for crash in ('before-append', 'after-append'):
            with self.subTest(crash=crash), tempfile.TemporaryDirectory() as raw:
                session = Session(raw, release=True)
                claim = session.notice('notice_prepare', owner_nonce='crashed-issuer', text=NOTICE_TEXT)
                self.assertEqual('claimed', claim['state'])
                # (after-append: the row is in main's conversation, but the
                # backend never hears of it.)
                stored = session.stored_notice()
                shapes.append(sorted(key for key in stored))
                with rest(session) as call:
                    row, detail, host_row, text = self.public(session, call)
                    self.assertEqual((False, False, False), (row['notice_delivery_uncertain'],
                                     detail['notice_delivery_uncertain'], host_row['notice_delivery_uncertain']))
                    pins = session.notice_pins()
                    self.assertEqual((row['notice_attempt_ref'], row['notice_revision']),
                                     (pins['attempt_ref'], pins['revision']))
                    status, error = call('commands/side-notice-retry',
                                         {'receipt_id': session.event, 'confirmed': True, **pins})
                    self.assertEqual(422, status, error)
                    self.assertIn('not uncertain', error['message'])
                    self.assertEqual('already_issuing', session.notice('notice_prepare', owner_nonce='new-process')['state'])
                    with later(61):
                        row, detail, host_row, text = self.public(session, call)
                        self.assertEqual((True, True, True), (row['notice_delivery_uncertain'],
                                         detail['notice_delivery_uncertain'], host_row['notice_delivery_uncertain']))
                        for secret in (claim['attempt_id'], 'crashed-issuer', 'release_notice', NOTICE_TEXT):
                            self.assertNotIn(secret, text)
                        self.assertEqual('already_issuing', session.notice('notice_prepare', owner_nonce='new-process')['state'])
                        status, error = call('commands/side-notice-retry', {'receipt_id': session.event, **pins})
                        self.assertEqual(422, status, error)
                        self.assertIn('explicit duplicate-notice confirmation', error['message'])
                        status, done = call('commands/side-notice-retry',
                                            {'receipt_id': session.event, 'confirmed': True, **pins})
                        self.assertEqual(200, status, done)
                        self.assertEqual(('released', False), (done['result']['state'],
                                                               done['result']['notice_delivery_uncertain']))
                        self.assertNotIn(claim['attempt_id'], json.dumps(done))
                        status, _ = call('commands/side-notice-retry',
                                         {'receipt_id': session.event, 'confirmed': True, **pins})
                        self.assertEqual(422, status)
                    # The router offers the retained text once more; exactly one
                    # of racing hosts is granted it.
                    offered = session.call('work_side_claim', {'owner': 'actor'})['main_notices']
                    self.assertEqual([{'receipt_id': session.event, 'text': NOTICE_TEXT}], offered)
                    with concurrent.futures.ThreadPoolExecutor(8) as pool:
                        grants = list(pool.map(lambda index: session.notice(
                            'notice_prepare', owner_nonce='host-' + str(index), text=NOTICE_TEXT), range(8)))
                    winners = [grant for grant in grants if grant['state'] == 'claimed']
                    self.assertEqual(1, len(winners))
                    self.assertEqual([], session.call('work_side_claim', {'owner': 'actor'})['main_notices'])
                    owner = next(f'host-{i}' for i, grant in enumerate(grants) if grant['state'] == 'claimed')
                    session.notice('notice_appended', attempt_id=winners[0]['attempt_id'], owner_nonce=owner,
                                   receipt='uuid-reissued')
                    row, detail, host_row, text = self.public(session, call)
                    self.assertFalse(row['notice_delivery_uncertain'])
                stored = session.stored_notice()
                self.assertEqual(('appended', 1, 1), (stored['state'], stored['retries'], len(stored['history'])))
                self.assertEqual(('issuing', True), (stored['history'][0]['previous_state'],
                                                     stored['history'][0]['duplicate_confirmed']))
                self.assertEqual(16, len(stored['history'][0]['previous_attempt_ref']))
        self.assertEqual(shapes[0], shapes[1])

    def test_uncertain_is_visible_at_once_failure_is_offered_again_and_no_text_cannot_retry(self):
        with tempfile.TemporaryDirectory() as raw:
            session = Session(raw, release=True)
            attempt = session.notice('notice_prepare', owner_nonce='A', text=NOTICE_TEXT)['attempt_id']
            session.notice('notice_uncertain', attempt_id=attempt, owner_nonce='A')
            with rest(session) as call:
                self.assertTrue(self.public(session, call)[0]['notice_delivery_uncertain'])
            self.assertIn('explicit duplicate-notice confirmation',
                          session.call('work_side_wake', {'event_id': session.event, 'state': 'notice_retry'},
                                       HOST_RETRY, error=True))
            self.assertEqual([], session.call('work_side_claim', {'owner': 'actor'})['main_notices'])
            released = session.call('work_side_wake', {'event_id': session.event, 'state': 'notice_retry',
                                                       'confirmed': True, **session.notice_pins()}, HOST_RETRY)
            self.assertEqual('released', released['state'])
            self.assertEqual([session.event], [row['receipt_id'] for row in
                                               session.call('work_side_claim', {'owner': 'actor'})['main_notices']])
        with tempfile.TemporaryDirectory() as raw:
            # A definite failure stored nothing: offered again with no person.
            session = Session(raw, release=True)
            attempt = session.notice('notice_prepare', owner_nonce='A', text=NOTICE_TEXT)['attempt_id']
            session.notice('notice_failed', attempt_id=attempt, owner_nonce='A')
            self.assertEqual([{'receipt_id': session.event, 'text': NOTICE_TEXT}],
                             session.call('work_side_claim', {'owner': 'actor'})['main_notices'])
            self.assertEqual('claimed', session.notice('notice_prepare', owner_nonce='B')['state'])
            # The retained text survives a reopen that sent none.
            self.assertEqual(NOTICE_TEXT, session.stored_notice()['text'])
        with tempfile.TemporaryDirectory() as raw:
            # An older host claimed without text: nothing can re-issue it.
            session = Session(raw, release=True)
            attempt = session.notice('notice_prepare', owner_nonce='A')['attempt_id']
            session.notice('notice_uncertain', attempt_id=attempt, owner_nonce='A')
            self.assertIn('not kept by its issuer',
                          session.call('work_side_wake', {'event_id': session.event, 'state': 'notice_retry',
                                                          'confirmed': True, **session.notice_pins()},
                                       HOST_RETRY, error=True))

    def test_a_person_confirmed_retry_needs_the_hosts_own_marker_and_writes_nothing_without_it(self):
        # The router authenticates the host token first (a main-model call has
        # none: evidence/before/model-side-tool-probe.json). A retry that may
        # duplicate is accepted only from the mod's own retry call, which also
        # carries _host.internal; any other authenticated caller writes nothing.
        def digest(root):
            hashed = hashlib.sha256()
            for path in sorted(p for p in Path(root).rglob('*') if p.is_file()):
                hashed.update(str(path.relative_to(root)).encode() + b'\0' + path.read_bytes())
            return hashed.hexdigest()
        with tempfile.TemporaryDirectory() as raw:
            session = Session(raw, release=True)
            attempt = session.notice('notice_prepare', owner_nonce='A', text=NOTICE_TEXT)['attempt_id']
            session.notice('notice_uncertain', attempt_id=attempt, owner_nonce='A')
            # The gate runs before the wake row or the receipt is read or written.
            cases = [('work_side_wake', {'event_id': session.event, 'state': 'notice_retry', 'confirmed': True,
                                         **session.notice_pins()}),
                     ('work_side_retry_reply', {'receipt_id': session.event, 'expected_state': 'uncertain',
                                                'confirmed': True}),
                     ('work_side_wake', {'event_id': session.event, 'state': 'retry', 'attempt_ref': '0' * 16,
                                         'revision': 0, 'confirmed': True})]
            for tool, args in cases:
                with self.subTest(tool=tool, state=args.get('state')):
                    before = digest(raw)
                    self.assertIn('requires the verified Ciel Work host', session.call(tool, args, error=True))
                    self.assertIn('requires the verified Ciel Work host',
                                  session.call(tool, args, {'internal': False}, error=True))
                    self.assertEqual(before, digest(raw))
            self.assertEqual('uncertain', session.stored_notice()['state'])
            # The host's own call passes the gate and is then judged as before.
            self.assertEqual('released', session.call('work_side_wake', cases[0][1], HOST_RETRY)['state'])
            self.assertNotIn('requires the verified Ciel Work host',
                             session.call('work_side_retry_reply', cases[1][1], HOST_RETRY, error=True))

    def test_notice_retry_is_reachable_from_the_browser_ui_and_listed(self):
        from ciel_runtime_support.work_application import openapi_document
        from ciel_runtime_support.work_web_ui import render_work_web_ui
        html = render_work_web_ui()
        # The exact attempt the person saw (retry pins, team-lead defect 2).
        self.assertIn("command('side-notice-retry',{receipt_id:detail.receipt_id,attempt_ref:detail.notice_attempt_ref,"
                      "revision:detail.notice_revision,confirmed:true})", html)
        self.assertIn('Retry release notice (may duplicate)', html)
        self.assertIn('detail.notice_delivery_uncertain===true', html)
        self.assertIn('/ca/work/api/v1/commands/side-notice-retry', json.dumps(openapi_document()))


if __name__ == '__main__':
    unittest.main()
