"""Backend side-wake protocol: election, fencing, crash windows, ACK, mixed versions."""
import concurrent.futures
from contextlib import contextmanager
import json
import re
import subprocess
import sys
import tempfile
import threading
import types
import unittest
import urllib.request
from pathlib import Path

from ciel_runtime_support import side_wake
from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.side_handoff import acknowledge
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
from ciel_runtime_support.work_mcp_tools import dispatch_work_tool
from ciel_runtime_support.work_plan_store import WorkPlanConflict, session_work_plan_store

ROOT = Path(__file__).resolve().parents[1]
OLD_READERS = ('57dbfb3ac9c8d9f1a42e89a038d27e16f4fab409',
               '8e08b97bf29878a9cf7302c5e945c0307675aa06')
FAMILIAR = {'pending', 'failed', 'submitting', 'queued', 'appended', 'uncertain', 'acknowledged'}
LEGACY_FILES = ('side_intake_limits.py', 'work_plan_tree.py', 'work_plan_store.py', 'side_wake.py')


def _legacy_sources(commit, names=LEGACY_FILES):
    sources = {}
    for name in names:
        done = subprocess.run(['git', 'show', f'{commit}:ciel_runtime_support/{name}'], cwd=ROOT,
                              capture_output=True, text=True, encoding='utf-8', check=False)
        if done.returncode:
            return None
        sources[name] = done.stdout
    return sources


@contextmanager
def legacy_wake_modules(commit):
    """Load one historical store/tree/wake set without replacing current modules."""
    sources = _legacy_sources(commit)
    if sources is None:
        yield None
        return
    import ciel_runtime_support.channel_message_repository  # noqa: F401 - bound by the old absolute import
    import ciel_runtime_support.durable_json_file as durable_json_file
    import ciel_runtime_support.work_session_lineage as work_session_lineage
    prefix = '_legacy_wake_' + commit[:7]
    package = types.ModuleType(prefix)
    package.__path__ = []
    modules = {name[:-3]: types.ModuleType(prefix + '.' + name[:-3]) for name in LEGACY_FILES}
    # The reader/normalizer, store and side_wake are exact old source. Their
    # file-write and session-path helpers are bound to the current modules; the
    # old store resolves them lazily through this isolated package name.
    aliases = {prefix: package, **{module.__name__: module for module in modules.values()},
               prefix + '.durable_json_file': durable_json_file,
               prefix + '.work_session_lineage': work_session_lineage}
    saved = {name: sys.modules.get(name) for name in aliases}
    sys.modules.update(aliases)
    try:
        for name in ('side_intake_limits', 'work_plan_tree', 'work_plan_store', 'side_wake'):
            module = modules[name]
            module.__package__ = prefix
            # The old store's absolute `from ciel_runtime_support.work_plan_tree`
            # must bind the old generation, only while that source executes.
            current_tree = sys.modules['ciel_runtime_support.work_plan_tree']
            if name == 'work_plan_store':
                sys.modules['ciel_runtime_support.work_plan_tree'] = modules['work_plan_tree']
            try:
                exec(compile(sources[name + '.py'], f'{commit}:{name}.py', 'exec'), module.__dict__)
            finally:
                sys.modules['ciel_runtime_support.work_plan_tree'] = current_tree
            setattr(package, name, module)
        assert modules['work_plan_store'].normalize_document is modules['work_plan_tree'].normalize_document
        yield modules
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


class Board:
    """One requested handoff on a real session store, driven via MCP dispatch."""

    def __init__(self, raw, *, kind='advise'):
        self.root = Path(raw)
        self.identity = 'claude:test'
        self.config = {'work_mode': True, 'work_side_intake': {'enabled': True, 'context_mode': 'isolated'},
                       'work_parallel_host': {'token': 'x' * 40}}
        self.store = session_work_plan_store(self.root, self.identity)
        _, self.item = self.store.transaction(lambda d: tree.add_node(d, None, 'report'))
        _, self.gate = self.store.transaction(lambda d: tree.add_node(d, self.item, 'gate', waiting_on='approval 7'))
        SideIntakeInbox(self.root, self.identity).receive('approval 7 granted', 'approval')
        claim = self.call('work_side_claim', {'owner': 'actor'})['claim']
        self.event = claim['receipt_id']
        # Gated updates are handled by side without a main handoff (2026-11-18).
        # Exercise the wake protocol with an explicit side escalation instead.
        decision = {'action': kind, 'item_id': self.gate, 'summary': 'main decision required',
                    'reply': 'please review the approval', 'note': 'approval 7 granted'}
        result = self.call('work_side_resolve', {'decision': decision, 'expected_version': self.version()},
                           {'receipt_id': claim['receipt_id'], 'generation': claim['generation']})
        assert result['result']['handoff'] == 'requested', result

    def call(self, name, args, extra=None, error=False, text=False):
        result = dispatch_work_tool(1, name, {**args, 'session_id': self.identity,
                                              '_host': {'token': 'x' * 40, **(extra or {})}},
                                    load_config=lambda: self.config, state_dir=self.root,
                                    respond=lambda _id, text, error=False: {'text': text, 'error': error})
        if result['error'] != error:
            raise AssertionError(result)
        return result['text'] if error or text else json.loads(result['text'])

    def version(self):
        return self.store.read()['version']

    def wake(self):
        return self.store.read()['side_handoffs'][self.event]['wake']

    def offered(self):
        return [w['event_id'] for w in self.call('work_side_claim', {'owner': 'actor'})['main_wakes']]

    def elect(self, owner='A', channel='prompt', now=None):
        return side_wake.prepare(self.store, self.event, protocol=2, owner_nonce=owner, channel=channel, now=now)


def _rest_outputs(board):
    """Browser-facing REST reads plus the REST ACK, on a real loopback socket."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from ciel_runtime_support.http_response import HttpResponseAdapter
    from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices
    controller = WorkHttpController(WorkHttpServices(
        board.root, lambda: board.config, lambda: board.identity,
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
    base = 'http://127.0.0.1:' + str(server.server_port)
    outputs = {}
    try:
        for name in ('side', 'board', 'state', 'queue'):
            with urllib.request.urlopen(base + '/ca/work/api/v1/' + name, timeout=5) as response:
                outputs['rest_' + name] = json.load(response)
        body = json.dumps({'event_id': board.event, 'answer': '', 'expected_version': board.version()}).encode()
        request = urllib.request.Request(base + '/ca/work/api/v1/commands/side-handoff', data=body,
                                         headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=5) as response:
            outputs['rest_handoff_ack'] = json.load(response)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
    return outputs


class SideWakeProtocolTests(unittest.TestCase):
    def test_stop_channel_is_a_fenced_protocol_two_delivery_channel(self):
        with tempfile.TemporaryDirectory() as raw:
            board = Board(raw)
            prepared = board.elect('stop-owner', channel='stop')
            self.assertEqual(('submitting', 'intent', 'stop'),
                             (prepared['state'], prepared['phase'], prepared['channel']))
            started = side_wake.start(board.store, board.event, prepared['attempt_id'], 'stop-owner', 'stop')
            self.assertEqual(('submitting', 'started', 'stop'),
                             (started['state'], started['phase'], started['channel']))
            recorded = side_wake.record(board.store, board.event, 'queued', prepared['attempt_id'],
                                        owner_nonce='stop-owner')
            self.assertEqual('queued', recorded['state'])
            self.assertNotIn(board.event, board.offered())

    def test_parallel_prepares_elect_exactly_one_started_delivery(self):
        with tempfile.TemporaryDirectory() as raw:
            board = Board(raw)
            barrier = threading.Barrier(8, timeout=10)

            def contender(index):
                store = session_work_plan_store(board.root, board.identity)
                owner = 'owner-' + str(index)
                barrier.wait()
                prepared = side_wake.prepare(store, board.event, protocol=2, owner_nonce=owner, channel='prompt')
                if prepared['state'] != 'submitting':
                    return 'lost'
                try:
                    side_wake.start(store, board.event, prepared['attempt_id'], owner, 'prompt')
                    return 'started'
                except tree.WorkPlanError:
                    return 'fenced'

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                outcomes = list(pool.map(contender, range(8)))
            self.assertEqual(1, outcomes.count('started'), outcomes)
            self.assertEqual('started', board.wake()['phase'])

    def test_crash_matrix_before_intent_lost_intent_old_start_and_started(self):
        with tempfile.TemporaryDirectory() as raw:
            board = Board(raw)
            # Before intent: nothing durable; the wake is still offered.
            self.assertEqual([board.event], board.offered())
            # Intent committed, response lost: the same owner adopts it.
            first = board.elect('A')
            adopted = board.elect('A')
            self.assertEqual(first['attempt_id'], adopted['attempt_id'])
            # Inside A's lease another owner cannot supersede it.
            inside = board.elect('B')
            self.assertEqual('already_intent', inside['state'])
            self.assertNotIn('attempt_id', inside)
            # After the lease (crashed or reloaded owner), B supersedes; A is fenced.
            superseding = board.elect('B', now=board.wake()['intent_until'])
            self.assertNotEqual(first['attempt_id'], superseding['attempt_id'])
            with self.assertRaisesRegex(tree.WorkPlanError, 'stale side wake intent'):
                side_wake.start(board.store, board.event, first['attempt_id'], 'A', 'prompt')
            side_wake.start(board.store, board.event, superseding['attempt_id'], 'B', 'prompt')
            # Started is never superseded, re-elected, or failed automatically.
            for owner in ('A', 'C'):
                self.assertEqual('already_submitting', board.elect(owner)['state'])
            self.assertEqual([board.event], board.offered())  # offered for reconciliation only
            status = board.call('work_side_status', {})['main_handoffs'][0]
            self.assertEqual((True, 'submitting', 'started'),
                             (status['delivery_uncertain'], status['wake_state'], status['wake_phase']))
            self.assertNotIn('owner_nonce', json.dumps(board.call('work_side_claim', {'owner': 'x'})['main_wakes']))

    def test_only_exact_append_receipt_settles_and_prompts_never_reconcile(self):
        with tempfile.TemporaryDirectory() as raw:
            board = Board(raw)
            prompt = board.elect('A', 'prompt')
            side_wake.start(board.store, board.event, prompt['attempt_id'], 'A', 'prompt')
            with self.assertRaisesRegex(tree.WorkPlanError, 'only an append receipt'):
                side_wake.reconcile(board.store, board.event, prompt['attempt_id'], receipt='u', seen_receipt='u')
            self.assertEqual(('submitting', 'started'), (board.wake()['state'], board.wake()['phase']))
            other = Board(tempfile.mkdtemp(dir=raw))
            append = other.elect('A', 'append')
            side_wake.start(other.store, other.event, append['attempt_id'], 'A', 'append')
            before = other.wake()['revision']
            for receipt, seen in (('uuid-1', 'uuid-2'), ('uuid-1', None), ('', '')):
                with self.assertRaisesRegex(tree.WorkPlanError, 'exact append receipt'):
                    side_wake.reconcile(other.store, other.event, append['attempt_id'],
                                        receipt=receipt, seen_receipt=seen)
            self.assertEqual(('submitting', before), (other.wake()['state'], other.wake()['revision']))
            with self.assertRaisesRegex(tree.WorkPlanError, 'stale side wake attempt'):
                side_wake.reconcile(other.store, other.event, '0' * 32, receipt='uuid-1', seen_receipt='uuid-1')
            side_wake.reconcile(other.store, other.event, append['attempt_id'], receipt='uuid-1', seen_receipt='uuid-1')
            self.assertEqual(('appended', 'receipt', 'uuid-1'),
                             (other.wake()['state'], other.wake()['reconciled'], other.wake()['receipt']))

    def test_appended_without_receipt_keeps_owner_uuid_and_refuses_a_different_one(self):
        # Reviewer sequence: the row settles appended with no receipt, then the
        # original owner's record arrives with the uuid. It used to return
        # idempotent success and drop the uuid.
        with tempfile.TemporaryDirectory() as raw:
            board = Board(raw)
            prepared = board.elect('A', 'append')
            side_wake.start(board.store, board.event, prepared['attempt_id'], 'A', 'append')
            side_wake.record(board.store, board.event, 'appended', prepared['attempt_id'], owner_nonce='A')
            self.assertNotIn('receipt', board.wake())
            version = board.version()
            revision = board.wake()['revision']
            side_wake.record(board.store, board.event, 'appended', prepared['attempt_id'],
                             owner_nonce='A', receipt='uuid-7')
            self.assertEqual(('uuid-7', revision + 1), (board.wake()['receipt'], board.wake()['revision']))
            self.assertEqual(version, board.version())  # wake-only write
            # The identical uuid again is idempotent (no new revision).
            side_wake.record(board.store, board.event, 'appended', prepared['attempt_id'],
                             owner_nonce='A', receipt='uuid-7')
            self.assertEqual(revision + 1, board.wake()['revision'])
            # A different uuid for the same attempt conflicts.
            with self.assertRaisesRegex(tree.WorkPlanError, 'recorded differently'):
                side_wake.record(board.store, board.event, 'appended', prepared['attempt_id'],
                                 owner_nonce='A', receipt='uuid-8')
            # Another owner can never attach a receipt.
            with self.assertRaisesRegex(tree.WorkPlanError, 'stale side wake attempt'):
                side_wake.record(board.store, board.event, 'appended', prepared['attempt_id'],
                                 owner_nonce='B', receipt='uuid-9')
            self.assertEqual('uuid-7', board.wake()['receipt'])

    def test_every_result_echoes_protocol_two(self):
        with tempfile.TemporaryDirectory() as raw:
            board = Board(raw)
            prepared = board.elect('A', 'append')
            started = side_wake.start(board.store, board.event, prepared['attempt_id'], 'A', 'append')
            recorded = side_wake.record(board.store, board.event, 'uncertain', prepared['attempt_id'], owner_nonce='A')
            duplicate = board.elect('B')
            legacy = Board(tempfile.mkdtemp(dir=raw))
            legacy_result = legacy.call('work_side_wake', {'event_id': legacy.event, 'state': 'prepare'})
            for result in (prepared, started, recorded, duplicate, legacy_result):
                self.assertEqual(2, result['protocol'])

    def test_intent_lease_bounds_reviewer_adversarial_schedule(self):
        # Reviewer schedule: A prepares; B prepares before A starts; A starts;
        # repeat. Without a lease B's prepare superseded A every round and A's
        # start was always stale, so nobody ever started.
        with tempfile.TemporaryDirectory() as raw:
            board = Board(raw)
            clock = 1000.0
            started = None
            for _ in range(4):
                a = board.elect('A', now=clock)
                b = board.elect('B', now=clock + 1)
                owner, attempt = ('A', a) if b['state'] == 'already_intent' else ('B', b)
                try:
                    started = side_wake.start(board.store, board.event, attempt['attempt_id'], owner, 'prompt')
                    break
                except tree.WorkPlanError:
                    clock += 2
            self.assertIsNotNone(started)
            self.assertEqual(('started', 'A'), (started['phase'], started['owner_nonce']))

    def test_expired_intent_after_crash_is_recoverable(self):
        with tempfile.TemporaryDirectory() as raw:
            board = Board(raw)
            crashed = board.elect('crashed', now=100.0)
            self.assertEqual(105.0, board.wake()['intent_until'])
            self.assertEqual('already_intent', board.elect('live', now=104.9)['state'])
            live = board.elect('live', now=105.0)
            self.assertEqual(('submitting', 'intent'), (live['state'], live['phase']))
            with self.assertRaisesRegex(tree.WorkPlanError, 'stale side wake intent'):
                side_wake.start(board.store, board.event, crashed['attempt_id'], 'crashed', 'prompt')
            side_wake.start(board.store, board.event, live['attempt_id'], 'live', 'prompt')

    def test_no_model_status_rest_or_browser_output_exposes_wake_secrets(self):
        with tempfile.TemporaryDirectory() as raw:
            board = Board(raw)
            nonce = 'owner-nonce-' + 'f' * 20
            prepared = board.elect(nonce, 'append')
            side_wake.start(board.store, board.event, prepared['attempt_id'], nonce, 'append')
            side_wake.record(board.store, board.event, 'appended', prepared['attempt_id'],
                             owner_nonce=nonce, receipt='receipt-uuid-7777', conversation_id='claude:internal-route')
            prompt = side_wake.prepare_prompt(board.store, board.event, 'prompt-owner-secret', 'claude:internal-route')
            # A conversation change left a reappend lease with its own owner,
            # attempt, conversation and an unknown-outcome receipt nested inside.
            reappend = side_wake.prepare_reappend(board.store, board.event, 'reappend-owner-secret',
                                                  'claude:other-route')
            side_wake.record_reappend(board.store, board.event, reappend['attempt_id'], 'reappend-owner-secret',
                                      'uncertain', receipt='reappend-receipt-4242')
            secrets = (prepared['attempt_id'], nonce, 'receipt-uuid-7777', 'ciel-side-wake',
                       'claude:internal-route', prompt['attempt_id'], 'prompt-owner-secret',
                       'claude:other-route', reappend['attempt_id'], 'reappend-owner-secret', 'reappend-receipt-4242')
            # Host-authenticated internal state keeps the raw nested values.
            self.assertEqual((prepared['attempt_id'], nonce), (board.wake()['attempt_id'], board.wake()['owner_nonce']))
            self.assertEqual(('prompt-owner-secret', 'claude:internal-route'),
                             (board.wake()['prompt']['owner_nonce'], board.wake()['prompt']['conversation_id']))
            self.assertEqual(('uncertain', 'reappend-receipt-4242', 'claude:other-route'),
                             tuple(board.wake()['reappend'][key] for key in ('state', 'receipt', 'conversation_id')))
            # Even the host-authenticated claim never hands out an owner fence or
            # a nested lease: a claim reader cannot record for another owner.
            offered = json.dumps(board.call('work_side_claim', {'owner': 'claim-reader'})['main_wakes'])
            for secret in (nonce, 'prompt-owner-secret', 'reappend-owner-secret', prompt['attempt_id'],
                           reappend['attempt_id'], 'reappend-receipt-4242', 'claude:other-route'):
                self.assertNotIn(secret, offered)
            outputs = {
                'work_status_json': board.call('work_status', {'format': 'json'}),
                'work_status_text': board.call('work_status', {}, text=True),
                'work_side_status': board.call('work_side_status', {}),
            }
            # Model-visible side fork prompt context for a follow-up event.
            SideIntakeInbox(board.root, board.identity).receive('follow up', 'handoff:' + board.event)
            claim = board.call('work_side_claim', {'owner': 'actor'})
            outputs['fork_handoff_context'] = (claim.get('claim') or {}).get('handoff_context')
            outputs.update(_rest_outputs(board))
            outputs['mcp_handoff_ack'] = board.call('work_side_handoff', {
                'event_id': board.event, 'answer': '', 'expected_version': board.version()})
            # Side inbox claim generations are not wake data; the host-only
            # work_side_status receipt summary carries them (outside this fix).
            generations = {(row.get('processing') or {}).get('generation')
                           for row in outputs['work_side_status']['side_inbox_receipts']}
            refs = {side_wake.attempt_ref(attempt) for attempt in
                    (prepared['attempt_id'], prompt['attempt_id'], reappend['attempt_id'])}
            for name, value in outputs.items():
                text = value if isinstance(value, str) else json.dumps(value)
                with self.subTest(output=name):
                    for secret in secrets:
                        self.assertNotIn(secret, text)
                    # One exact exception: an operator command's top-level
                    # operation_id is its server-minted idempotency key
                    # ("server-" + uuid4().hex, work_http_controller.operator),
                    # the caller's receipt for its own command. Only that key, only
                    # that shape, and never equal to a wake secret or ref; it is
                    # removed before the scan, so every other 32-hex token fails.
                    if isinstance(value, dict) and 'operation_id' in value:
                        operation = value['operation_id']
                        self.assertRegex(operation, r'\Aserver-[0-9a-f]{32}\Z')
                        self.assertNotIn(operation[len('server-'):], {*secrets, *refs})
                        text = json.dumps({key: item for key, item in value.items() if key != 'operation_id'})
                    # Attempt ids are uuid4().hex: a standalone 32-hex token. Event
                    # and receipt ids are 64-hex and are allowed.
                    tokens = re.findall(r'(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])', text)
                    # Only the host-only side status may carry inbox generations;
                    # every model-visible and browser output must carry none.
                    allowed = generations if name == 'work_side_status' else set()
                    self.assertEqual([], [token for token in tokens if token not in allowed])
            self.assertTrue(generations and all(isinstance(g, str) and len(g) == 32 for g in generations))
            self.assertIsNotNone(outputs['fork_handoff_context'])
            self.assertFalse(outputs['work_side_status']['main_handoffs'][0]['delivery_uncertain'])

    def test_record_is_fenced_idempotent_and_keeps_receipt(self):
        with tempfile.TemporaryDirectory() as raw:
            board = Board(raw)
            prepared = board.elect('A', 'append')
            with self.assertRaisesRegex(tree.WorkPlanError, 'stale side wake attempt'):
                side_wake.record(board.store, board.event, 'appended', prepared['attempt_id'], owner_nonce='A')
            side_wake.start(board.store, board.event, prepared['attempt_id'], 'A', 'append')
            with self.assertRaisesRegex(tree.WorkPlanError, 'stale side wake attempt'):
                side_wake.record(board.store, board.event, 'appended', prepared['attempt_id'], owner_nonce='B')
            once = side_wake.record(board.store, board.event, 'appended', prepared['attempt_id'],
                                    owner_nonce='A', receipt='uuid-1')
            again = side_wake.record(board.store, board.event, 'appended', prepared['attempt_id'],
                                     owner_nonce='A', receipt='uuid-1')
            self.assertEqual(once['revision'], again['revision'])
            self.assertEqual('uuid-1', board.wake()['receipt'])
            with self.assertRaisesRegex(tree.WorkPlanError, 'stale side wake attempt'):
                side_wake.record(board.store, board.event, 'failed', prepared['attempt_id'], owner_nonce='A')

    def test_wake_writes_keep_work_version_and_ack_forms_still_commit(self):
        for form in ('plain', 'activate_clear'):
            with self.subTest(form=form), tempfile.TemporaryDirectory() as raw:
                board = Board(raw)
                document = board.store.read()
                version, updated = document['version'], document['updated_at']
                nodes, active = document['nodes'], document['active_root']
                prepared = board.elect('A')
                side_wake.start(board.store, board.event, prepared['attempt_id'], 'A', 'prompt')
                side_wake.record(board.store, board.event, 'queued', prepared['attempt_id'], owner_nonce='A')
                after = board.store.read()
                self.assertEqual((version, updated, nodes, active),
                                 (after['version'], after['updated_at'], after['nodes'], after['active_root']))
                # The ACK carries the pre-wake version and still commits.
                kwargs = {'activate': True, 'clear_wait': [board.gate]} if form == 'activate_clear' else {}
                acked = acknowledge(board.store, board.event, answer='', expected_version=version, **kwargs)
                self.assertEqual(version + 1, acked['version'])

    def test_unrelated_work_change_still_conflicts_first_ack_and_marks_retry(self):
        with tempfile.TemporaryDirectory() as raw:
            board = Board(raw)
            stale = board.version()
            board.store.transaction(lambda d: tree.add_node(d, None, 'unrelated'))
            with self.assertRaises(WorkPlanConflict):
                acknowledge(board.store, board.event, activate=True, clear_wait=[board.gate], expected_version=stale)
            self.assertTrue(board.wake()['needs_ack_retry'])
            self.assertTrue(board.call('work_side_status', {})['main_handoffs'][0]['needs_ack_retry'])
            acknowledge(board.store, board.event, activate=True, clear_wait=[board.gate],
                        expected_version=board.version())
            self.assertFalse(board.wake()['needs_ack_retry'])

    def test_duplicate_identical_stale_ack_succeeds_and_changed_payload_fails(self):
        with tempfile.TemporaryDirectory() as raw:
            board = Board(raw)
            stale = board.version()
            acknowledge(board.store, board.event, answer='done', clear_wait=[board.gate], expected_version=stale)
            again = acknowledge(board.store, board.event, answer='done', clear_wait=[board.gate],
                                expected_version=stale)
            self.assertEqual('answered', again['state'])
            with self.assertRaisesRegex(tree.WorkPlanError, 'different result'):
                acknowledge(board.store, board.event, answer='other', clear_wait=[board.gate], expected_version=stale)
            with self.assertRaisesRegex(tree.WorkPlanError, 'not found'):
                acknowledge(board.store, 'f' * 64, expected_version=stale)

    def test_manual_retry_needs_confirmation_exact_revision_and_is_audited(self):
        with tempfile.TemporaryDirectory() as raw:
            board = Board(raw)
            prepared = board.elect('A')
            side_wake.start(board.store, board.event, prepared['attempt_id'], 'A', 'prompt')
            revision = board.wake()['revision']
            status = board.call('work_side_status', {})['main_handoffs'][0]
            reference = status['wake_attempt_ref']
            self.assertEqual(side_wake.attempt_ref(prepared['attempt_id']), reference)
            self.assertNotIn(prepared['attempt_id'], json.dumps(status))
            with self.assertRaisesRegex(tree.WorkPlanError, 'explicit duplicate'):
                side_wake.retry(board.store, board.event, reference, revision)
            with self.assertRaisesRegex(tree.WorkPlanError, 'refresh before retry'):
                side_wake.retry(board.store, board.event, reference, revision - 1, confirmed=True)
            with self.assertRaisesRegex(tree.WorkPlanError, 'refresh before retry'):
                side_wake.retry(board.store, board.event, '0' * 16, revision, confirmed=True)
            side_wake.retry(board.store, board.event, reference, revision, confirmed=True)
            wake = board.wake()
            self.assertEqual('failed', wake['state'])
            self.assertEqual([{'attempt_id': prepared['attempt_id'], 'attempt_ref': reference, 'revision': revision,
                               'previous_state': 'submitting', 'previous_phase': 'started',
                               'duplicate_confirmed': True}],
                             [{k: v for k, v in row.items() if k != 'at'} for row in wake['retries']])
            self.assertEqual('submitting', board.elect('B')['state'])

    def test_legacy_prepare_is_started_and_never_superseded(self):
        with tempfile.TemporaryDirectory() as raw:
            board = Board(raw)
            legacy = board.call('work_side_wake', {'event_id': board.event, 'state': 'prepare'})
            self.assertEqual(('submitting', 'started', 'legacy'),
                             (legacy['state'], legacy['phase'], legacy['channel']))
            self.assertEqual('already_submitting', board.elect('B')['state'])
            # The old host records with its attempt only; queued is familiar.
            board.call('work_side_wake', {'event_id': board.event, 'state': 'queued',
                                          'attempt_id': legacy['attempt_id']})
            self.assertEqual('queued', board.wake()['state'])
            # A new-host intent makes a later old-host prepare non-authorizing.
            other = Board(tempfile.mkdtemp(dir=raw))
            other.elect('B')
            result = other.call('work_side_wake', {'event_id': other.event, 'state': 'prepare'})
            self.assertNotEqual('submitting', result['state'])

    @staticmethod
    def _new_wake_shape(board, shape):
        """Build one current wake shape only through the current side_wake API."""
        seen = [board.wake()['state']]
        prepared = board.elect('A')
        seen.append(board.wake()['state'])
        side_wake.start(board.store, board.event, prepared['attempt_id'], 'A', 'prompt')
        seen.append(board.wake()['state'])
        # A timer-path append settled from one exact API-view ref row records a
        # non-uuid receipt; side_wake's validator takes any bounded string.
        side_wake.record(board.store, board.event, 'appended', prepared['attempt_id'], owner_nonce='A',
                         receipt='ref:0123456789abcdef', conversation_id='claude:old-conversation')
        seen.append(board.wake()['state'])
        prompt = side_wake.prepare_prompt(board.store, board.event, 'prompt-owner', 'claude:old-conversation')
        side_wake.record_prompt(board.store, board.event, prompt['attempt_id'], 'prompt-owner', 'queued')
        if shape == 'unread':
            # Active prompt and reappend leases: the conversation changed before read.
            side_wake.prepare_reappend(board.store, board.event, 'reappend-owner', 'claude:new-conversation')
            fields = ('receipt', 'conversation_id', 'prompt', 'reappend')
        else:
            side_wake.mark_read(board.store, board.event)
            fields = ('receipt', 'conversation_id', 'read_at')
        seen.append(board.wake()['state'])
        return seen, fields

    def test_old_store_and_side_wake_writers_keep_every_new_wake_field(self):
        # Mixed version: an installed older router process runs its own
        # WorkPlanStore semantic transaction and its own side_wake mutation on a
        # document whose wake rows carry the new fields. Each generation's
        # work_plan_tree, work_plan_store and side_wake run as exact historical
        # source in an isolated module namespace; nothing is mutated by hand.
        unavailable = []
        for old_commit in OLD_READERS:
            for shape in ('unread', 'read'):
                with self.subTest(old_commit=old_commit[:7], shape=shape), tempfile.TemporaryDirectory() as raw:
                    board = Board(raw)
                    seen, fields = self._new_wake_shape(board, shape)
                    self.assertLessEqual(set(seen), FAMILIAR)
                    path = Path(board.store.path)
                    before = json.loads(path.read_text(encoding='utf-8'))
                    expected = before['side_handoffs'][board.event]['wake']
                    for field in fields:
                        self.assertIn(field, expected)
                    self.assertEqual('ref:0123456789abcdef', expected['receipt'])
                    with legacy_wake_modules(old_commit) as old:
                        if old is None:
                            # A shallow checkout (CI) lacks the historical commit.
                            unavailable.append(old_commit)
                            continue
                        # The loaded code is the old generation, not today's.
                        self.assertFalse(hasattr(old['side_wake'], 'prepare_prompt'))
                        self.assertEqual(expected, old['work_plan_tree'].normalize_document(before)
                                         ['side_handoffs'][board.event]['wake'])
                        old_store = old['work_plan_store'].WorkPlanStore(path)
                        old_store.transaction(lambda d: old['work_plan_tree'].add_node(d, None, 'old writer ' + old_commit[:7]))
                        if hasattr(old['side_wake'], 'mark_ack_retry'):
                            old['side_wake'].mark_ack_retry(old_store, board.event)
                            changed = {'needs_ack_retry': True, 'revision': expected['revision'] + 1}
                        else:
                            # 8e08b97 knows only prepare/record. prepare on an
                            # appended wake returns it without changing it.
                            self.assertEqual('appended', old['side_wake'].prepare(old_store, board.event)['state'])
                            changed = {}
                    after = json.loads(path.read_text(encoding='utf-8'))
                    self.assertEqual(before['version'] + 1, after['version'])
                    self.assertIn('old writer ' + old_commit[:7], [node['title'] for node in after['nodes']])
                    self.assertEqual({**expected, **changed}, after['side_handoffs'][board.event]['wake'])
                    # The current host still sees the same fences after old writes.
                    if shape == 'unread':
                        self.assertEqual('already_queued', side_wake.prepare_prompt(
                            board.store, board.event, 'later-owner', 'claude:old-conversation')['state'])
                        self.assertEqual('already_submitting', side_wake.prepare_reappend(
                            board.store, board.event, 'later-owner', 'claude:new-conversation')['state'])
                    else:
                        self.assertEqual('already_read', side_wake.prepare_prompt(
                            board.store, board.event, 'later-owner', 'claude:new-conversation')['state'])
        if len(set(unavailable)) == len(OLD_READERS):
            self.skipTest('no historical commit in this checkout')
        self.assertEqual([], unavailable, 'one historical commit is missing from this checkout')

    def test_concurrent_wake_and_work_writers_preserve_each_other(self):
        with tempfile.TemporaryDirectory() as raw:
            board = Board(raw)
            barrier = threading.Barrier(2, timeout=10)

            def wake_writer():
                barrier.wait()
                for _ in range(20):
                    side_wake.mark_ack_retry(session_work_plan_store(board.root, board.identity), board.event)

            def work_writer():
                barrier.wait()
                for index in range(20):
                    session_work_plan_store(board.root, board.identity).transaction(
                        lambda d, i=index: tree.add_node(d, None, 'w' + str(i)))

            threads = [threading.Thread(target=wake_writer), threading.Thread(target=work_writer)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(60)
            final = board.store.read()
            self.assertEqual(20, sum(node['title'].startswith('w') for node in final['nodes']))
            self.assertEqual(20, final['side_handoffs'][board.event]['wake']['revision'])


if __name__ == '__main__':
    unittest.main()
