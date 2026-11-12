"""Behavior of the packaged ciel-work hook's Edit/Write gate under concurrent agents.

Runs the real register.js under Node with a scripted host (tests/work_mod_harness.mjs);
no Claude Code, router or network is involved.
"""
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support import work_mode_launch as w

NODE = shutil.which("node")
HARNESS = Path(__file__).with_name("work_mod_harness.mjs")
UNAVAILABLE = "deny: Work status unavailable: router connection or snapshot could not be verified."
PLANNING = "deny: Work mode is planning:"


@unittest.skipUnless(NODE, "node is required to run the Work mod")
class WorkModWriteGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory(prefix="work-mod-gate-")
        cls.addClassCleanup(temp.cleanup)
        cls.root = Path(temp.name)
        stub = cls.root / "node_modules" / "claude-code"
        stub.mkdir(parents=True)
        (cls.root / "package.json").write_text('{"type":"module"}', encoding="utf-8")
        (stub / "package.json").write_text('{"name":"claude-code","type":"module","exports":"./index.js"}',
                                           encoding="utf-8")
        (stub / "index.js").write_text(
            "const store = new Map()\n"
            "export const atom = (key, init) => ({ key, init })\n"
            "export const read = async ($, a) => store.has(a.key.plugin + '/' + a.key.key)"
            " ? store.get(a.key.plugin + '/' + a.key.key) : (a.init ?? null)\n"
            "export const update = async ($, a, fn) => { const cur = store.has(a.key.plugin + '/' + a.key.key)"
            " ? store.get(a.key.plugin + '/' + a.key.key) : (a.init ?? null);"
            " store.set(a.key.plugin + '/' + a.key.key, fn(cur)) }\n", encoding="utf-8")
        shutil.copyfile(w.CLAUDE_MOD_DIR / "hooks" / "register.js", cls.root / "register.js")
        shutil.copyfile(w.CLAUDE_MOD_DIR / "hooks" / "side-intake.js", cls.root / "side-intake.js")
        shutil.copyfile(HARNESS, cls.root / HARNESS.name)

    def run_scenario(self, name, *args):
        done = subprocess.run([NODE, HARNESS.name, name, *args], cwd=self.root, capture_output=True, text=True,
                              encoding="utf-8", timeout=60, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_high_priority_advice_prompts_main_at_safe_stop_even_without_auto_continue(self):
        result=self.run_scenario('side-advice-stop')
        self.assertIn('work_side_handoff',result['block'])
        self.assertIn('n2',result['block'])
        self.assertIn('not execution approval',result['block'])
        # Andy 2026-11-08: main must see which recorded wait the event satisfied.
        self.assertIn('clear_wait',result['block'])
        self.assertIn('"kind":"resume"',result['block'])
        self.assertIn('Daniel approval 118269',result['block'])
        self.assertNotIn('requested_at',result['block'])
        self.assertNotIn('block', result['second'])

    def test_side_handoff_conflict_reaches_main_instead_of_identity_error(self):
        # Prody 2026-11-09: the thrown hook was skipped and the engine's call
        # without session_id answered "conversation identity is unavailable".
        result = self.run_scenario('side-handoff-conflict')
        self.assertEqual({'deny': 'work_side_handoff: the plan changed (stored version 1688, expected 1685); '
                                  'call work_status and retry'}, result['result'])
        self.assertEqual('claude:S1', result['sent']['session_id'])
        self.assertEqual(1685, result['sent']['expected_version'])

    def test_queued_side_wake_is_not_blocked_again_at_stop(self):
        # The queued wake prompt delivers it next; blocking here duplicated it.
        self.assertNotIn('block', self.run_scenario('side-advice-stop-queued-wake'))

    def test_stop_presents_only_its_own_started_election_and_records_on_the_next_step(self):
        # Owned probe stopblock-838268bd/d9595ffa (Claude Code 2.1.321): the Stop
        # block continues the same main turn; its next turn.step precedes the
        # request carrying the block. That step is the proof that records queued.
        result = self.run_scenario('side-wake-stop-election')
        self.assertIn('not execution approval', result['first']['block'])
        self.assertIn('work_side_handoff', result['first']['block'])
        for secret in ('a' * 32, 'owner_nonce', 'attempt_id', hashlib.sha256(b'a' * 32).hexdigest()[:16]):
            self.assertNotIn(secret, result['first']['block'])
        self.assertEqual({'state': 'submitting', 'phase': 'started', 'records': 0}, result['beforeStep'])
        self.assertEqual({'state': 'queued', 'records': 1}, result['afterStep'])
        # The continuation's own Stop (stop_hook_active) and a later Stop add nothing.
        self.assertNotIn('block', result['second'])
        self.assertEqual('', result['third'])
        self.assertEqual(['prepare', 'start', 'queued'], result['calls'])

    def test_stop_proof_is_bound_to_the_blocked_turn_and_a_later_step(self):
        # classic.Stop has no turnId: the entry is bound to the last main turnId
        # and step index. Another turn's step, or a step that is not later,
        # never settles it; the next step of the same turn does.
        for proof in ('other-turn', 'same-index'):
            with self.subTest(proof=proof):
                result = self.run_scenario('side-wake-stop-election', proof)
                self.assertIn('block', result['first'])
                self.assertEqual(0, result['early'])
                self.assertEqual({'state': 'queued', 'records': 1}, result['afterStep'])

    def test_turn_end_before_the_proving_step_leaves_the_wake_uncertain(self):
        # An interrupt (turn.complete aborted) before T#1: dropped unsettled, never
        # recorded, never re-presented; the next ordinary Stop says it is uncertain.
        result = self.run_scenario('side-wake-stop-election', 'interrupt')
        self.assertIn('block', result['first'])
        self.assertEqual(0, result['early'])
        self.assertEqual({'state': 'submitting', 'records': 0}, result['afterStep'])
        self.assertNotIn('block', result['second'])
        self.assertIn('delivery to main is uncertain', result['third'])
        self.assertEqual(['prepare', 'start'], result['calls'])

    def test_uncertain_wake_and_ack_retry_are_each_noticed_once(self):
        result = self.run_scenario('side-wake-uncertain-and-ack-retry-once')
        # A Stop-block continuation (stop_hook_active) never adds either notice.
        for name in ('uncertainContinuing', 'ackContinuing', 'ack3'):
            self.assertNotIn('block', result[name], name)
        self.assertIn('delivery to main is uncertain', result['uncertain1']['block'])
        self.assertIn('do not ask for it to be delivered again', result['uncertain1']['block'])
        self.assertNotIn('ref-u', result['uncertain1']['block'])
        self.assertNotIn('block', result['uncertain2'])
        self.assertIn('acknowledge again with the current expected_version', result['ack1']['block'])
        self.assertNotIn('block', result['ack2'])

    def test_old_router_row_gets_no_stop_presentation(self):
        # No redacted delivery_uncertain: an older router. The pump owns its fallback.
        self.assertEqual({'scenario': 'side-wake-stop-legacy-router', 'block': False, 'wakeCalls': [],
                          'submitted': 0}, self.run_scenario('side-wake-stop-legacy-router'))

    def test_stop_and_pump_in_one_process_present_a_wake_exactly_once(self):
        # Stop runs while main is still working, so a pump that wins appends
        # into the running turn; Stop then shows its existing one-time reminder
        # for an appended wake (unchanged), never a second election.
        expected = {'stop-first': (True, 0, 'queued', ['prepare:stop', 'start:stop', 'queued:']),
                    'pump-first': (False, 1, 'appended', ['prepare:append', 'start:append', 'appended:']),
                    'interleaved': (True, 0, 'queued', ['prepare:stop', 'start:stop', 'queued:'])}
        for order, (elected, appended, state, calls) in expected.items():
            with self.subTest(order=order):
                result = self.run_scenario('side-wake-stop-pump-same-owner', order)
                self.assertEqual(elected, result['elected'])
                self.assertEqual(0, result['submitted'])
                self.assertEqual(appended, result['appended'])
                self.assertEqual(1, int(result['elected']) + result['submitted'] + result['appended'])
                self.assertTrue(result['block'])
                self.assertEqual(state, result['wake']['state'])
                self.assertEqual(calls, result['calls'])
                self.assertFalse(result['staleShown'])

    def test_lost_stop_start_is_never_stranded_or_called_uncertain(self):
        for variant, calls in (('uncommitted', ['prepare:stop', 'start:stop', 'prepare:append', 'start:stop', 'queued:']),
                               ('committed', ['prepare:stop', 'start:stop', 'queued:'])):
            with self.subTest(variant=variant):
                result = self.run_scenario('side-wake-stop-start-lost', variant)
                self.assertFalse(result['stopBlock'])
                self.assertEqual('', result['againBlock'])
                self.assertFalse(result['paneUncertain'])
                self.assertEqual(0, result['beforePump']['submitted'])
                self.assertEqual(1, result['submitted'])
                self.assertEqual(0, result['appended'])
                self.assertEqual({'state': 'queued', 'phase': 'started', 'channel': 'stop'}, result['wake'])
                self.assertEqual('', result['laterBlock'])
                self.assertEqual(calls, result['calls'])
                self.assertFalse(result['staleShown'])

    # Adversarial schedules ported from review-side-wake's stop_adversarial.mjs.
    # Each of the defect tests fails on the reviewed draft (register.js 81949d1a,
    # side-intake.js 6da3de4c) and passes here.

    def test_lost_stop_start_answer_is_delivered_under_the_same_started_attempt(self):
        for variant in ('committed', 'uncommitted'):
            with self.subTest(variant=variant):
                result = self.run_scenario('adv-start-lost', variant)
                self.assertFalse(result['stopBlock'])
                self.assertEqual((1, 0), (result['prompts'], result['appends']))
                # One router-side attempt; every start and the one record name it
                # and its owner; no second attempt is ever started.
                self.assertEqual(1, result['attempts'])
                self.assertEqual('queued/stop', result['wake'])
                for row in result['starts'] + result['records']:
                    self.assertEqual((result['routerAttempt'], result['routerOwner']),
                                     (row['attempt_id'], row['owner_nonce']))
                self.assertEqual(1, len(result['records']))
                self.assertEqual(['queued'], [row['state'] for row in result['records']])

    def test_superseded_stop_election_presents_nothing_and_records_nothing(self):
        # Defect A: turn.start(T2) while the Stop's start was in flight.
        result = self.run_scenario('adv-turn-start-during-election')
        self.assertFalse(result['staleStopReturnedBlock'])
        self.assertEqual(0, result['records'])
        self.assertEqual('submitting/started', result['wake'])
        self.assertTrue(result['laterUncertain'])

    def test_record_that_lands_late_never_calls_the_wake_uncertain(self):
        # Defect B: recordFaults=1 and recordFaults=2.
        result = self.run_scenario('adv-record-late-success')
        self.assertTrue(result['first'])
        self.assertEqual(('', '', 'queued'), (result['secondBlock'], result['thirdBlock'], result['wake']))
        self.assertEqual(['work_side_wake:prepare:stop', 'work_side_wake:start:stop', 'work_side_wake:queued',
                          'work_side_wake:queued'], result['calls'])
        result = self.run_scenario('adv-record-fail-after-proof')
        self.assertTrue(result['first'])
        self.assertEqual(('', '', '', 'queued'), (result['second'], result['third'], result['fourth'], result['wake']))
        self.assertEqual(3, result['calls'].count('work_side_wake:queued'))
        self.assertEqual(1, result['calls'].count('work_side_wake:start:stop'))

    def test_stale_knowledge_of_another_attempt_never_hides_uncertainty(self):
        # Defect C: Stop's start lost a real CAS; another process started and died.
        result = self.run_scenario('adv-stale-unpresented')
        self.assertFalse(result['firstStopBlock'])
        self.assertTrue(result['laterStopUncertainBlock'])
        self.assertTrue(result['paneRetryOffered'])
        self.assertEqual(0, result['prompts'])
        control = self.run_scenario('adv-control-uncertain-visible')
        self.assertEqual((True, True), (control['stopUncertainBlock'], control['paneRetryOffered']))

    def test_a_step_after_the_turn_ended_never_settles_a_stop_presentation(self):
        for reason in ('aborted', 'answer'):
            with self.subTest(reason=reason):
                result = self.run_scenario('adv-turn-end-before-step', reason)
                self.assertTrue(result['presented'])
                self.assertEqual(('submitting/started', 0), (result['wake'], result['records']))

    def test_continuation_stop_gates_only_notices_and_elects_a_new_event_once(self):
        result = self.run_scenario('adv-stop-hook-active-loop')
        self.assertEqual((True, False, False, 'queued'),
                         (result['first'], result['second'], result['third'], result['wake']))
        result = self.run_scenario('adv-uncertain-stop-hook-active')
        self.assertEqual([True, False, False, False], result['blocks'])
        self.assertFalse(result['leak'])
        # A new event at a stop_hook_active Stop is presented exactly once and
        # does not block again on the next Stops.
        result = self.run_scenario('adv-continuation-new-event')
        self.assertEqual([True, True, False, False], result['blocks'])
        self.assertEqual([1, 1], result['presented'])
        self.assertEqual(['queued', 'queued'], result['states'])

    def test_ack_conflict_gives_only_the_ack_notice(self):
        # Decision: needs_ack_retry means main called work_side_handoff, so it has
        # the event; the revision bump adds no fresh "uncertain" notice or retry.
        result = self.run_scenario('adv-uncertain-then-ack-conflict')
        self.assertIn('delivery to main is uncertain', result['a'])
        self.assertIn('acknowledge again with the current expected_version', result['b'])
        self.assertFalse(result['c'])
        self.assertFalse(result['paneRetryOffered'])

    def test_pump_between_stop_and_step_and_second_handoff_are_not_presented_twice(self):
        result = self.run_scenario('adv-pump-between-stop-and-step')
        self.assertEqual((True, 0, 0, 'queued'), (result['stop'], result['prompts'], result['appends'], result['wake']))
        result = self.run_scenario('adv-two-handoffs')
        self.assertTrue(result['first'])
        self.assertFalse(result['firstIncludesSecond'])
        result = self.run_scenario('adv-pane-retry')
        self.assertTrue(result['button'])
        self.assertIsNone(result['afterCancel'])
        self.assertEqual({'event_id': 'e' * 64, 'state': 'retry', 'attempt_ref': result['ref'], 'revision': 6,
                          'confirmed': True}, result['retry'])

    # Per-key idle prompts. Main-review E2E run-20261111-161947: a second plugin
    # prompt was issued at 22:22:47.650 before the first (22:22:46.501) started
    # its turn. Each prompt is issued at once under its own key; the engine
    # holds the second (owned probes prompt-gate-0dd78bf3,
    # prompt-registry-e2bed500). Stop must not also present a review whose
    # prompt is issued and unresolved (prompt-stop-28f9d226).

    def test_both_idle_prompts_are_issued_at_once_and_each_records_its_own_outcome(self):
        expected = {'reviews': (['L1', 'L2'], ['L1:queued', 'L2:queued']),
                    'sides': (['E1', 'E2'], ['E1:queued', 'E2:queued']),
                    'review-side': (['L1', 'E1'], ['L1:queued', 'E1:queued']),
                    'side-review': (['E1', 'L1'], ['E1:queued', 'L1:queued'])}
        for mode, (invocations, records) in expected.items():
            with self.subTest(mode=mode):
                result = self.run_scenario('prompt-keys', mode)
                # Both $.prompt.submit calls happen before either resolves.
                self.assertEqual(invocations, result['whileBothPending']['invocations'])
                self.assertEqual(2, result['whileBothPending']['pending'])
                self.assertEqual(2, result['maxActive'])
                # Releasing the first records the first only; the second key
                # stays pending until its own promise resolves.
                self.assertEqual(records[:1], result['afterFirst']['records'])
                self.assertEqual(1, len(result['afterFirst']['pending']))
                self.assertEqual(invocations, result['invocations'])
                self.assertEqual(records, result['records'])
        result = self.run_scenario('prompt-keys', 'reviews:reverse')
        self.assertEqual((['L2:queued'], ['parallel:L1']), (result['afterFirst']['records'], result['afterFirst']['pending']))
        self.assertEqual(['L2:queued', 'L1:queued'], result['records'])

    def test_stop_gate_skips_only_reviews_whose_prompt_is_unresolved(self):
        result = self.run_scenario('prompt-keys', 'reviews')
        # Both issued, neither resolved: no duplicate review data, no hold.
        self.assertEqual([], result['stopWhileBothPending'])
        # L1 resolved: the decision gate covers L1; L2 is still held out.
        self.assertEqual(['L1'], result['stopAfterFirst'])
        self.assertEqual(['L1', 'L2'], result['stopAfterBoth'])
        result = self.run_scenario('prompt-keys', 'reviews:reverse')
        self.assertEqual((['L2'], ['L1', 'L2']), (result['stopAfterFirst'], result['stopAfterBoth']))
        result = self.run_scenario('prompt-keys', 'side-review')
        self.assertEqual(([], [], ['L1']),
                         (result['stopWhileBothPending'], result['stopAfterFirst'], result['stopAfterBoth']))

    def test_prompt_drop_or_throw_records_that_key_and_leaves_the_other_pending(self):
        for fault, outcome in (('drop', 'failed'), ('throw', 'uncertain')):
            with self.subTest(fault=fault):
                result = self.run_scenario('prompt-keys', 'reviews:' + fault)
                self.assertEqual(['L1:' + outcome], result['afterFirst']['records'])
                self.assertEqual(['parallel:L2'], result['afterFirst']['pending'])
                self.assertIn('L2:queued', result['records'])
        result = self.run_scenario('prompt-keys', 'side-review:throw')
        self.assertEqual(['E1:uncertain', 'L1:queued'], result['records'])
        self.assertEqual({'E1': 'uncertain'}, result['sideWakes'])
        result = self.run_scenario('prompt-keys', 'sides:throw:reverse')
        self.assertEqual(['E2:queued', 'E1:uncertain'], result['records'])

    def test_an_issued_prompt_key_is_shared_and_submitted_once(self):
        result = self.run_scenario('prompt-key-dedup')
        self.assertEqual(['first'], result['calls'])
        self.assertTrue(result['sameEntry'])
        self.assertEqual((True, False), (result['pendingBefore'], result['pendingAfter']))
        self.assertEqual(['queued', 'queued'], result['results'])

    def test_latent_prompt_never_blocks_other_keys_and_is_exposed_after_its_idle_window(self):
        result = self.run_scenario('prompt-key-latent')
        # Issue order between independent keys is not part of the contract.
        self.assertEqual(['A', 'B', 'L1'], sorted(result['issued']))
        self.assertEqual(['parallel:L1', 'side:E1', 'side:E2'], sorted(result['registryWhileAll']))
        # B and L1 resolve and record while A has not resolved.
        latent = result['whileALatent']
        self.assertEqual((['E2:queued', 'L1:queued'], ['side:E1'], True),
                         (sorted(latent['records']), latent['registry'], latent['pendingA']))
        # A is hidden from Stop's uncertainty notice and the pane retry.
        self.assertEqual({'review': True, 'uncertain': False, 'retry': False}, latent['view'])
        # No window during the main turn; turn.complete arms a full one.
        self.assertEqual((0, 1), (result['liveInTurn'], result['liveAfterTurn']))
        self.assertEqual(['E2:queued', 'L1:queued'], sorted(result['afterFireInTurn']['records']))
        self.assertTrue(result['afterFireInTurn']['pendingA'])
        after = result['afterWindow']
        # A's uncertain record comes only after the window, after both others.
        self.assertEqual((['E2:queued', 'L1:queued'], 'E1:uncertain', [], False),
                         (sorted(after['records'][:2]), after['records'][2], after['registry'], after['pendingA']))
        self.assertEqual({'review': False, 'uncertain': True, 'retry': True}, after['view'])
        self.assertEqual({'review': True, 'uncertain': False, 'retry': True}, after['viewAgain'])

    def test_compat_review_without_durable_wake_stays_terminal_across_session_start(self):
        result = self.run_scenario("parallel-submitted-wakes-main-without-reviewer", "session-restart")
        self.assertEqual(1, result["beforeRestart"])
        # session.start clears noticedParallelReviews; the terminal prompt
        # record for the same key stops a second raw submit.
        self.assertEqual(1, len(result["submitted"]), result)

    def test_clear_reasserts_the_prompt_fence_and_a_reload_never_submits_again(self):
        # /clear resets $.state and raises classic.SessionStart in the same
        # module instance (measured: nonce-lifetime-probe-44e40778). The
        # re-claim there is harness-only evidence.
        for path in ('review', 'side'):
            with self.subTest(path=path):
                key = 'parallel:L1' if path == 'review' else 'side:E1'
                result = self.run_scenario('prompt-key-clear', path + ':reassert')
                self.assertEqual({'registry': [key], 'sameGeneration': True, 'pendingInFirst': True}, result['afterClear'])
                self.assertEqual((True, 0), (result['reload']['hydrated'], result['reload']['rawSubmits']))
                # session.end before the boundary keeps the entry; re-claim still lands.
                ended = self.run_scenario('prompt-key-clear', path + ':end')
                self.assertEqual({'registry': [key], 'sameGeneration': True, 'pendingInFirst': True}, ended['afterClear'])
                self.assertEqual((True, 0), (ended['reload']['hydrated'], ended['reload']['rawSubmits']))
                self.assertEqual({'review': False, 'uncertain': False, 'retry': False}, result['reload']['view'])
                visible = 'review' if path == 'review' else 'retry'
                self.assertTrue(result['afterWindow'][visible])
                self.assertEqual(1, result['firstRawSubmits'])
                # No re-claim, or a failing $.state: the reload still raw-submits
                # nothing, because the durable submitting/started row fences it;
                # the key is presented at once instead (a possible duplicate
                # presentation if the first prompt later enters).
                for variant in ('skip', 'fails'):
                    control = self.run_scenario('prompt-key-clear', path + ':' + variant)
                    self.assertEqual({'registry': [], 'sameGeneration': False, 'pendingInFirst': True}, control['afterClear'])
                    self.assertEqual((False, 0), (control['reload']['hydrated'], control['reload']['rawSubmits']))
                    self.assertTrue(control['reload']['view'][visible])

    def test_prompt_liveness_table_for_review_side_wake_and_retry(self):
        # visible: Stop gate (review) or pane retry (side wake, retry). The
        # notice row of the table is test_side_intake_mod's
        # test_release_notice_liveness_table.
        hidden = {'review': False, 'uncertain': False, 'retry': False}
        for path in ('review', 'side', 'retry'):
            visible = 'review' if path == 'review' else 'retry'
            for condition in ('finite', 'anomaly', 'restart', 'reload'):
                with self.subTest(path=path, condition=condition):
                    result = self.run_scenario('prompt-key-cell', path + ':' + condition)
                    key = 'parallel:L1' if path == 'review' else 'side:E1'
                    label = 'L1' if path == 'review' else 'E1'
                    self.assertEqual((1, [key], 1), (result['issued']['submits'], result['issued']['registry'],
                                                     result['issued']['live']))
                    # Issued and unresolved: hidden from Stop and the pane.
                    self.assertEqual(hidden, result['before'])
                    records = [r for r in result['records'] if not r.endswith(':retry')]
                    if condition == 'finite':
                        self.assertEqual([label + ':queued'], records)
                        self.assertEqual([], result['registry'])
                        self.assertEqual(path == 'review', result['after'][visible])
                    elif condition == 'anomaly':
                        # The idle window records uncertain and exposes it; the
                        # late engine resolution records nothing more.
                        outcome = label + ':uncertain'
                        self.assertEqual([outcome], records)
                        self.assertEqual([], result['registry'])
                        self.assertTrue(result['after'][visible])
                        if path != 'review':
                            self.assertTrue(result['after']['uncertain'])
                        self.assertEqual([r for r in result['second']['lateRecords'] if not r.endswith(':retry')], [outcome])
                    else:
                        second = result['second']
                        self.assertEqual(0, second['submits'])
                        if condition == 'restart':
                            # Fresh process: no state, visible at once, nothing resolves later.
                            self.assertEqual({'pending': False, 'live': 0}, second['hydrated'])
                            self.assertTrue(second['before'][visible])
                            self.assertEqual([], records)
                        else:
                            # Hot reload: hydrated and hidden until its idle window.
                            self.assertEqual({'pending': True, 'live': 1}, second['hydrated'])
                            self.assertEqual(hidden, second['before'])
                            self.assertTrue(second['after'][visible])
                            self.assertEqual([], second['registryAfterWindow'])
                            # The old environment's late raw resolution reads the
                            # terminal uncertain row the window wrote: never queued.
                            self.assertEqual([label + ':uncertain'], records)
                    if path == 'retry':
                        self.assertEqual('E1:retry', result['records'][0])

    def test_pane_wake_retry_needs_confirmation_and_sends_redacted_fields(self):
        result = self.run_scenario('side-wake-pane-retry')
        self.assertTrue(result['label'].startswith('Main delivery uncertain'))
        self.assertEqual(0, result['afterCancel'])
        self.assertEqual(1, len(result['retries']))
        retry = result['retries'][0]
        self.assertEqual({'event_id': 'r' * 64, 'state': 'retry', 'attempt_ref': 'ref-pane', 'revision': 4,
                          'confirmed': True}, {k: retry[k] for k in ('event_id', 'state', 'attempt_ref',
                                                                    'revision', 'confirmed')})
        self.assertNotIn('attempt_id', retry)
        self.assertNotIn('owner_nonce', retry)

    def test_exact_side_fork_passes_work_spawn_gate_and_look_alike_is_denied(self):
        result = self.run_scenario('side-fork-authorized-before-work-gate')
        observed = result['observed']
        self.assertIsNotNone(observed, result)
        self.assertEqual('side-fork-1', observed['agentId'])
        # The authorized side fork never read the Work ledger or took a Work lease.
        self.assertEqual(0, observed['sideReads'])
        self.assertEqual(1, len(result['routed']), result)
        # Side forks are unnamed native forks pinned by their exact description.
        self.assertNotIn('name', result['routed'][0])
        self.assertTrue(result['routed'][0]['description'].startswith('Side SSE '))
        # A fork in the reserved side description namespace that is not the
        # exact pending row is denied before any Work logic.
        self.assertIn('Side SSE native fork authorization', observed['lookAlike']['deny'])
        # The exact pending description with a failed fork proof gets the side
        # proof deny, not a Work policy result.
        self.assertIn('Side fork proof failed', observed['exactForkFalse']['deny'])
        self.assertIn('fork is not true', observed['exactForkFalse']['deny'])
        self.assertIn('Side fork proof failed', observed['exactGeneral']['deny'])
        self.assertIn('not subagent_type fork', observed['exactGeneral']['deny'])
        # Unnamed contract (sse-native-fork probes, named-fork-d48887c3).
        self.assertIn('side fork must be unnamed', observed['named']['deny'])
        # A non-side description reaches the ordinary Work gate.
        self.assertIn('Work parallel dispatch denied', observed['nonSide']['deny'])
        self.assertNotIn('Side fork proof', observed['nonSide']['deny'])

    def test_pending_side_spawn_with_failed_fork_proof_is_denied_for_every_parallel_mode(self):
        # sse-review reproduction: with Work parallel disabled a look-alike side
        # spawn reached next() and received an agent id.
        for policy in ('off', 'on', 'unavailable'):
            with self.subTest(parallel=policy):
                result = self.run_scenario('side-failed-proof-denied', policy)
                attempts = result['attempts']
                self.assertIn('fork is not true', attempts['forkFalse']['deny'])
                self.assertIn('not subagent_type fork', attempts['generalPurpose']['deny'])
                self.assertIn('Side fork proof failed', attempts['engineLookAlike']['deny'])
                self.assertIn('Side SSE native fork authorization', attempts['reservedName']['deny'])
                self.assertIn('Side SSE native fork authorization', attempts['prefixLookAlike']['deny'])
                # Decided before any Work ledger read; nothing reached a native spawn.
                self.assertEqual(0, attempts['sideReads'])
                self.assertEqual([], result['reached'])
                # Unrelated spawns keep today's behavior.
                if policy == 'off':
                    self.assertEqual({'passed': 'ordinary-fork'}, result['unrelated'])
                elif policy == 'on':
                    self.assertIn('no pending work_parallel_run dispatch', result['unrelated']['deny'])
                else:
                    self.assertIn('parallel ledger unavailable', result['unrelated']['deny'])

    def test_time_limit_handoff_keeps_bounded_deadline_and_evidence(self):
        result = self.run_scenario('side-time-limit-handoff-bounded')
        for text in (result['context'], result['block']):
            self.assertIn('"kind":"time_limit"', text)
            self.assertIn('Complete before maintenance closes', text)
            self.assertIn('2026-11-11T03:00:00Z', text)
            self.assertIn('change ticket CHG-42', text)
            self.assertIn('n9', text)
            self.assertNotIn('SECRET-THREAD', text)
            self.assertNotIn('SECRET-SSE', text)

    def test_owned_side_notification_is_consumed_before_main_queue(self):
        result = self.run_scenario('side-notification-isolation')
        self.assertIn('consumed', result['owned'])
        self.assertEqual({'text':'passed'}, result['foreign'])
        self.assertEqual({'text':'passed'}, result['quoted'])

    def test_hung_mailbox_call_times_out_and_rearms_without_losing_mail(self):
        result = self.run_scenario('side-mailbox-timeout-rearms')
        self.assertEqual(2, result['rearmed'])
        self.assertEqual([], result['notices'])

    def test_mail_claims_continue_during_compact_and_after_failure(self):
        result = self.run_scenario('side-mailbox-compact-recovery')
        self.assertEqual(1, result['during'])
        self.assertEqual(2, result['after'])

    def test_mod_consumes_staged_mail_and_displays_both_stages(self):
        result = self.run_scenario('side-mailbox-mod-drain')
        self.assertIn('work_side_claim', result['tools'])
        self.assertNotIn('work_side_drain', result['tools'])
        self.assertEqual([], result['logs'])
        self.assertTrue(result['claimedBeforeStatus'])
        self.assertTrue(result['statusFinished'])
        self.assertEqual(2, result['rearmed'])

    def test_side_status_refresh_is_single_flight_throttled_and_never_blocks_ticks(self):
        result = self.run_scenario('side-status-single-flight-and-throttled')
        # A hanging pane refresh neither starts a second fetch nor stops the
        # claim ticks from re-arming.
        self.assertEqual(1, result['whileInFlight'])
        self.assertEqual(3, result['ticksWhileInFlight'])
        self.assertEqual(1, result['afterThrottle'])
        self.assertEqual(2, result['afterSignal'])

    def test_one_tick_drains_a_burst_into_every_free_side_slot(self):
        result = self.run_scenario('side-pump-drains-burst-into-free-slots')
        # Two ordinary slots, then the reserved P1-only slot, then no slot left.
        self.assertEqual([{'urgent_only': False}, {'urgent_only': False}, {'urgent_only': True}], result['claims'])
        self.assertEqual(3, len(result['spawned']))
        self.assertEqual(1, result['left'])

    def test_redacted_main_status_does_not_erase_host_side_pane(self):
        result = self.run_scenario('side-receipt-redacted-refresh')
        pane = json.dumps(result['pane'])
        self.assertIn('Private external question', pane)
        self.assertIn('Verified answer', pane)
        self.assertIn('submitted', pane)
        self.assertIn('Receipt: mail1 \\u00b7 Work item: n1', pane)
        # Owned run 2026-11-09: the pane read "external ACK: not_sent" above
        # "Delivery: sent once by the host outbox" for the same receipt.
        self.assertNotIn('external ACK', pane)
        self.assertIn('Delivery: sent once by the host outbox', pane)
        self.assertNotIn('Shadow', pane)

    def test_unknown_reply_retry_requires_explicit_duplicate_confirmation(self):
        result = self.run_scenario('side-uncertain-retry-cancel-and-confirm')
        self.assertEqual(0, result['before'])
        self.assertEqual(1, len(result['retries']))
        self.assertTrue(result['retries'][0]['confirmed'])
        self.assertEqual('uncertain', result['retries'][0]['expected_state'])

    def test_side_receipt_is_visible_in_mod_once(self):
        result = self.run_scenario('side-receipt-visible')
        self.assertEqual([], result['logs'])

    def test_completion_gate_is_target_scoped_and_ignores_terminal_rows(self):
        result = self.run_scenario('parallel-completion-gate-target-scoped')
        self.assertEqual('allow', result['unrelated'], result)
        self.assertIn("this item's parallel validation", result['blocked'])

    def test_work_off_turn_end_does_not_query_parallel(self):
        self.assertEqual(0, self.run_scenario('parallel-complete-off')['parallelCalls'])

    def test_identical_blockers_do_not_rearm_on_unrelated_board_version_change(self):
        result = self.run_scenario('stop-identical-blockers-version-change')
        self.assertEqual('', result['first'])
        self.assertEqual('', result['second'])
        self.assertEqual('', result['changed'])

    def test_background_waits_are_rechecked_once_without_polling_or_approval_bypass(self):
        result = self.run_scenario('stop-background-wait-recheck')
        self.assertEqual('', result['text'])
        self.assertEqual('', result['second'])

    def test_unrelated_background_task_does_not_suppress_bounded_block_recheck(self):
        result = self.run_scenario('stop-background-blocked-recheck')
        self.assertEqual('', result['text'])
        self.assertEqual('', result['second'])
        self.assertFalse(any('still running will wake' in line for line in result['logs']))

    def test_concurrent_agent_gates_each_use_their_own_verified_read(self):
        for name in ("concurrent-check", "concurrent-call"):
            with self.subTest(name):
                self.assertEqual({k: v for k, v in self.run_scenario(name).items() if k != "scenario"},
                                 {"first": "allow", "second": "allow"})

    def test_new_main_turn_does_not_void_a_subagent_gate(self):
        self.assertEqual(self.run_scenario("turn-start-during-check")["first"], "allow")

    def test_approval_gate_is_not_voided_by_a_concurrent_edit(self):
        result = self.run_scenario("execute-during-edit")
        self.assertEqual(result["first"], "allow")
        self.assertTrue(result["second"].startswith(PLANNING), result)

    def test_planning_still_denies_every_concurrent_write(self):
        result = self.run_scenario("concurrent-planning")
        self.assertTrue(result["first"].startswith(PLANNING), result)
        self.assertTrue(result["second"].startswith(PLANNING), result)

    def test_failed_reads_stay_closed_and_name_the_failed_step(self):
        expected = {
            "router-error": "router-error",
            "router-throws": "router-disconnected",
            "router-timeout": "router-timeout",
            "invalid-snapshot": "snapshot-schema",
            "not-json": "snapshot-parse",
            "identity-change": "identity-changed",
            "session-end-during-check": "identity-changed",
            "stale-snapshot": "snapshot-version",
        }
        for name, stage in expected.items():
            with self.subTest(name):
                result = self.run_scenario(name)
                self.assertTrue(result["first"].startswith(UNAVAILABLE + " [" + stage + "; wg-"), result)
                self.assertNotIn("PRIVATE-TOKEN", json.dumps(result))
        mixed = self.run_scenario("concurrent-mixed")
        self.assertTrue(mixed["first"].startswith(UNAVAILABLE + " [router-error; wg-"), mixed)
        self.assertEqual(mixed["second"], "allow")

    def test_reconnect_retries_only_pure_snapshot_after_verified_connection(self):
        result = self.run_scenario('router-reconnected')
        self.assertEqual('allow', result['first'])
        self.assertEqual(2, result['attempts'])
        self.assertEqual(1, result['connections'])
        self.assertNotIn('PRIVATE-TOKEN', json.dumps(result))
        denied = self.run_scenario('router-auth-closed')
        self.assertIn('router-auth', denied['first'])
        self.assertEqual(0, denied['connections'])
        self.assertNotIn('PRIVATE-TOKEN', json.dumps(denied))

    def test_disabled_work_mode_does_not_gate(self):
        self.assertEqual(self.run_scenario("disabled")["first"], "allow")

    def test_display_refresh_does_not_drop_runnable_queued_work_at_stop(self):
        result = self.run_scenario("stop-display-refresh")
        self.assertTrue(result["block"], result)
        self.assertIn("call work_select with root_id n2", result["text"])

    def test_approval_wait_can_discover_without_repeating_or_approving(self):
        result = self.run_scenario("stop-discovery-approval")
        self.assertIn("Work 미결 아이템 탐색", result["first"])
        self.assertIn("기존 승인 유지", result["first"])
        self.assertFalse(result["second"])

    def test_live_settings_command_uses_host_mcp_without_model_turn(self):
        result = self.run_scenario("live-settings-command")
        self.assertEqual("work_mode", result["key"])
        self.assertEqual("on", result["value"])
        self.assertIn("Work mode", result["text"])

    def test_external_side_intake_drops_only_after_queue_ack_without_main_turn(self):
        result = self.run_scenario("side-intake-external-bridge")
        self.assertEqual(1, result["queued"])
        self.assertEqual(0, result["main"])
        self.assertIn("ACK n50", result["drop"])

    def test_side_intake_command_calls_mcp_and_returns_durable_ack(self):
        result = self.run_scenario("side-intake-command")
        self.assertIn("ACK n50", result["text"])
        self.assertIn("work_side_intake", result["tools"])
        self.assertTrue(result["token"])

    def test_stopped_with_only_blocked_work_does_not_demand_recheck(self):
        result = self.run_scenario("stop-blocked-recheck")
        self.assertNotIn("re-verify before reporting no work", result["text"])
        self.assertNotIn("work_node_update", result["text"])
        # The recheck changes nothing, so the same board state must not
        # re-inject the same stop message on the next turn end. Only the
        # time-spaced discovery notice may still fire (it has its own
        # 5-minute floor and is the "look for new work later" path).
        self.assertNotIn("re-verify before reporting no work", result["second"])

    def test_blocked_wait_includes_pickup_guidance(self):
        result = self.run_scenario("stop-discovery-blocked")
        self.assertIn("와키 미결 아이템", result["text"])
        self.assertIn("기존 블록 유지", result["text"])

    def test_focused_item_without_a_runnable_step_continues_other_work(self):
        result = self.run_scenario("stop-focus-unrunnable")
        self.assertIn("The next item is n5", result["text"])
        self.assertIn("call work_select with root_id n3", result["text"])
        self.assertNotIn("stays held", result["text"])

    def test_focus_no_longer_suppresses_discovery_when_nothing_runs(self):
        result = self.run_scenario("stop-discovery-focus")
        self.assertIn("Work 미결 아이템 탐색", result["text"])
        self.assertNotIn("focus only", result["text"])

    def test_parent_restart_rebinds_durable_worker_without_marking_it_idle(self):
        result = self.run_scenario("parallel-restart-rebind")
        self.assertEqual(result["result"], "allow", result)
        reconciles = [event for event in result["events"] if event.get("event") == "reconcile"]
        self.assertEqual(1, len(reconciles), result)
        self.assertEqual(reconciles[0]["_host"]["state"], "unknown")

    def test_parallel_ui_maps_each_item_to_its_fork_agent(self):
        result = self.run_scenario("parallel-assignment-labels")
        self.assertEqual(result["dispatch"]["description"], "Work n229 [ PAY-229 ] Add retry backoff")
        self.assertEqual(result["dispatch"]["dispatch"], "started")
        self.assertEqual(result["spawnedEvent"]["description"], result["dispatch"]["description"])
        self.assertEqual(result["spawned"]["agentId"], "host-9")
        # Without a native evidence projection nothing is presented as a running fork.
        self.assertIn("FORKS: evidence unavailable", result["shown"])
        self.assertIn("LEGACY LEASES (3)", result["shown"])
        self.assertIn("n227 [ PAY-227 ] Fix payment retry → work-alpha", result["shown"])
        self.assertIn("native not observed", result["shown"])
        self.assertIn("path: Product goal → Checkout phase → Fix payment retry", result["shown"])
        self.assertIn("n228 → (native fork not observed)", result["shown"])
        self.assertIn(result["dispatch"]["name"], result["shown"])

    def test_native_observation_records_only_bound_work_forks_outside_main(self):
        result = self.run_scenario("parallel-native-observation")
        self.assertTrue(result["observations"], result)
        rows = result["observations"][-1]["rows"]
        # Only this session's two bound, open Work forks: never main, the side
        # actor, an unrelated agent, a reservation or a finished lease.
        self.assertEqual({("L1", "fork-a", "running"), ("L2", "fork-b", "pending")},
                         {(r["lease_id"], r["agent_id"], r["status"]) for r in rows})
        self.assertTrue(result["observations"][-1]["complete"])
        self.assertEqual(8, result["observations"][-1]["expected_evidence_version"])
        # Content-free: no description/prompt text leaves the host.
        self.assertNotIn("SECRET-PROMPT", json.dumps(result["observations"]))
        self.assertEqual([], result["appended"])
        view = result["view"]
        self.assertIn("FORKS: 2 confirmed by native agent list", view)
        self.assertIn("main is coordinator, never a fork", view)
        self.assertIn("CONFIRMED (2)", view)
        self.assertIn("RESERVATION (1)", view)
        self.assertIn("n3 → (native fork not observed)", view)
        self.assertIn("Work forks confirmed by native agent list: 2", " ".join(result["logs"]))

    def test_work_parallel_run_starts_fork_without_model_agent_call(self):
        result = self.run_scenario("parallel-direct-spawn-started")
        self.assertEqual(result["run"]["dispatch"], "started")
        self.assertEqual(result["run"]["agent_id"], "host-9")
        self.assertEqual(result["run"]["lease_id"], "L9")
        self.assertEqual(result["spawned"]["subagentType"], "fork")
        self.assertEqual(result["spawned"]["description"], "Work n227")
        self.assertIn('"lease_id":"L9"', result["spawned"]["prompt"])
        self.assertIn("work_parallel_report", result["spawned"]["prompt"])
        self.assertEqual([call["event"] for call in result["calls"]], ["reserve", "bind"])

    def test_direct_spawn_through_own_hook_reserves_once_and_starts(self):
        result = self.run_scenario("parallel-direct-spawn-through-own-hook")
        # Owned run 2026-11-09: the hook reserved the same item again, the router
        # refused it, and the fork never started at any cap.
        self.assertEqual(["reserve", "bind"], result["calls"])
        self.assertEqual("started", result["run"]["dispatch"])
        self.assertEqual("host-9", result["run"]["agent_id"])
        self.assertIn('"lease_id":"L9"', result["spawned"]["prompt"])

    def test_work_parallel_run_spawn_denial_releases_and_keeps_item(self):
        result = self.run_scenario("parallel-direct-spawn-denied-releases")
        self.assertEqual(result["run"]["dispatch"], "spawn_denied")
        self.assertTrue(result["run"]["item_kept"])
        self.assertEqual([call["event"] for call in result["calls"]], ["reserve", "release"])
        self.assertEqual(result["calls"][1]["lease_id"], "L9")

    def test_missing_agent_name_is_diagnosed_and_visible_in_parallel_ui(self):
        result = self.run_scenario("parallel-missing-agent-name")
        self.assertEqual(result["dispatch"]["dispatch"], "spawn_denied")
        self.assertIn("omitted the exact name returned by work_parallel_run", result["denied"])
        self.assertIn("Recent parallel failures in this session", result["shown"])
        # The harness host accepts every MCP call, so the scripted note passes
        # here; the production router rejects it (work_parallel_mcp.py
        # "unknown conversation lease"), as Kobi's transcript shows.

    def test_session_start_retains_unknown_reserved_and_keeps_bound_worker(self):
        result = self.run_scenario("parallel-session-start-releases-orphan-reserved")
        self.assertEqual(result["calls"], [])
        self.assertIn("L1", result["leases"])
        self.assertIn("L2", result["leases"])
        self.assertEqual(result["leases"]["L2"]["host_id"], "agent-live")

    def test_host_receipt_signal_claims_immediately_without_reaching_main(self):
        result = self.run_scenario("side-receipt-ready-is-consumed-and-claims-now")
        self.assertGreater(result["after"], result["before"])
        self.assertEqual({"consumed": "Ciel side receipt claimed outside main conversation"}, result["ready"])
        self.assertEqual({"text": "passed"}, result["malformed"])
        self.assertEqual({"text": "agent-passed"}, result["agentTarget"])
        # The router socket delivers it as a peer prompt: dropped, never a main turn.
        self.assertEqual({"drop": "Ciel side receipt claimed outside main conversation"}, result["submitted"])
        self.assertGreater(result["afterSubmit"], result["after"])
        self.assertNotEqual({"text": "leaked-to-main"}, result["submitted"])
        self.assertNotIn("drop", result["typed"])

    def test_side_answer_is_reviewed_once_by_main_with_host_identity(self):
        result = self.run_scenario("side-review-stop-once")
        self.assertIn("Do not answer, ACK or repeat them", result["first"]["block"])
        self.assertIn("message:c:119649", result["first"]["block"])
        self.assertNotIn("block", result["second"])
        self.assertEqual("host-token", result["sent"][0]["_host"]["token"])
        self.assertEqual("S1", result["sent"][0]["session_id"].split(":")[-1])

    def test_not_applicable_side_review_never_asks_main_to_send(self):
        result = self.run_scenario("side-review-not-applicable")
        wording = ('Side actor handled these external events, which referenced no source message, so no reply exists '
                   'or will be sent. Do not send, ACK or answer anything for them. Review only the Work decision; then '
                   'call work_side_review(receipt_ids, verdict="ok", expected_version), or verdict="corrected" with a '
                   'note if you corrected the board:')
        for text in (result["context"], result["block"]):
            self.assertIn(wording, text)
            bucket = text.split(wording, 1)[1].split("\nSide actor", 1)[0]
            self.assertIn("n" * 64, bucket)
            self.assertNotIn("s" * 64, bucket)
            # No composed reply text and no send/correction instruction for it.
            self.assertNotIn("COMPOSED-REPLY-TEXT", text)
            self.assertNotIn("still sending", text)
            self.assertNotIn("could NOT send", text)
            # The submitted row keeps its own bucket.
            self.assertIn("the reply below was sent", text)

    def test_side_review_prompt_follows_real_reply_delivery(self):
        # KOBE 2026-11-09: a committed side answer was never sent
        # (route_unavailable) while main was told it had been.
        result = self.run_scenario("side-review-delivery-states")
        block = result["first"]["block"]
        sent, rest = block.split("the host could NOT send the reply", 1)
        unsent, pending = rest.split("the host is still sending", 1)
        self.assertIn("message:c:a", sent)
        self.assertNotIn("message:c:b", sent)
        self.assertIn("message:c:b", unsent)
        self.assertIn("Do not investigate again", unsent)
        self.assertIn("message:c:c", pending)
        self.assertIn("Do not send, ACK or answer them", pending)
        self.assertNotIn("block", result["repeat"])
        self.assertIn("message:c:c", result["failedLater"]["block"])
        self.assertIn("could NOT send", result["failedLater"]["block"])

    def test_missing_exit_report_resumes_fork_without_taskstop(self):
        result = self.run_scenario("parallel-missing-exit-report-requests-contract")
        self.assertTrue(result["sent"], result)
        self.assertFalse(result["tools"], result)
        self.assertNotIn("unreported", result["calls"])
        self.assertEqual("running", result["lease"]["state"])

    def test_second_missing_exit_salvages_before_stop(self):
        result = self.run_scenario("parallel-second-missing-exit-salvages-before-stop")
        self.assertEqual(["host_salvage", "unreported"],
                         [event for event in result["calls"] if event != "native_observation"], result)
        self.assertEqual({"tool": "TaskStop", "task_id": "agent-old"}, result["tools"][0])
        self.assertEqual("unreported", result["lease"]["state"])

    def test_spawn_denial_releases_unbound_reservation(self):
        result = self.run_scenario("parallel-spawn-denied-releases-unbound")
        events = [call["event"] for call in result["calls"]]
        self.assertEqual(events, ["reserve", "release"])
        self.assertEqual(result["calls"][1]["lease_id"], "L9")
        self.assertIn("native spawn denied before launch", result["calls"][1]["_host"]["proof"])

    def test_inspector_surfaces_router_error_text_not_json_parse_error(self):
        for name in ("details-router-error-plain", "details-router-error-flagged"):
            with self.subTest(name):
                result = self.run_scenario(name)
                self.assertEqual(result["opened"], "work-details", result)
                self.assertIn("Work plan store is locked by another writer", result["shown"])
                self.assertNotIn("Unexpected identifier", json.dumps(result))
                self.assertNotIn("JSON Parse", json.dumps(result))

    def test_parallel_read_error_names_the_read_and_router_text(self):
        self.assertIn("parallel ledger unavailable", self.run_scenario("parallel-error-text")["error"])

    def test_waiting_worker_releases_slot_and_resume_reacquires_it(self):
        result = self.run_scenario("parallel-waiting-releases-slot-and-sendmessage-reacquires")
        self.assertFalse(result["afterWait"]["slot"])
        self.assertEqual("wait", result["afterWait"]["calls"][0])
        self.assertIn("native_observation", result["afterWait"]["calls"])
        self.assertTrue(result["resumed"])
        self.assertTrue(result["slot"])
        self.assertEqual(result["calls"], ["reacquire"])

    def test_pause_after_no_progress_still_instructs_discovery(self):
        result = self.run_scenario("stop-pause-discovery")
        self.assertIn("auto continue paused", result["third"])
        self.assertIn("Work 미결 아이템 탐색", result["third"])
        self.assertIn("이 세션의 검색 수단", result["third"])

    def test_restart_recovery_stays_out_of_main_context_and_blocks_duplicates(self):
        result = self.run_scenario("parallel-recovery-context")
        self.assertNotIn("Work recovery", result["context"])
        self.assertNotIn("agent-old", result["context"])
        self.assertTrue(result["sent"], result)
        self.assertIn("Work recovery", result["sent"][0]["text"])
        self.assertTrue(result["duplicate"].startswith("deny: Work item n2 already has lease L1"), result)

    def test_restart_sendmessage_authorizes_deferred_exact_spawn_once(self):
        # Prody 2026-11-09: SendMessage returned, then agent.spawn arrived after
        # the old finally block had already deleted the resume authorization.
        result = self.run_scenario("parallel-restart-sendmessage-deferred-spawn")
        self.assertTrue(result["sent"], result)
        self.assertEqual("agent-new", result["spawned"], result)
        self.assertIn("no pending work_parallel_run dispatch", result["duplicate"])

    def test_submitted_result_wakes_main_once_without_reviewer_fork(self):
        result = self.run_scenario("parallel-submitted-wakes-main-without-reviewer")
        # No reviewer fork, reservation or slot: only the worker auto-fill probe ran.
        self.assertEqual([], result["spawned"], result)
        self.assertNotIn("reserve:reviewer", result["events"])
        self.assertNotIn("reserve", result["events"])
        # Idle main: one durable prompt wake, never repeated for the same review.
        self.assertEqual([], result["appended"])
        self.assertEqual(1, len(result["submitted"]), result)
        wake = result["submitted"][0]
        for expected in ('"lease_id":"L1"', "n2", "Fix retry", "Goal", "retry fixed", "unit tests 12 passed",
                         "C:/w/wt-n2", "work/n2", "abc123", "src/retry.py", "d" * 64, "a" * 64,
                         '"expected_finalization_version":4', "work_parallel_item_review"):
            self.assertIn(expected, wake)
        # Allow-listed only: no fork answer, transcript, agent/native row, repo
        # root, snapshot digest or saga internals reach main.
        for leaked in ("SECRET-FORK-ANSWER", "SECRET-TRANSCRIPT", "SECRET-AGENT", "SECRET-NATIVE",
                       "e" * 64, "c" * 64, "f" * 64, '"repo"', "stages", "replacement_lease_id"):
            self.assertNotIn(leaked, wake)
        # Event-driven: no review polling timer was scheduled (only the existing
        # side mailbox tick and native observation timers), plus the one 5 s
        # idle anomaly window armed when the review prompt was issued.
        self.assertEqual(set(), set(result["timers"]) - {2000, 5000, 15000})
        self.assertEqual(1, result["timers"].count(5000))

    def test_busy_main_receives_review_by_append_not_prompt(self):
        result = self.run_scenario("parallel-review-busy-main-appends")
        self.assertEqual([], result["submitted"], result)
        self.assertEqual(1, len(result["appended"]), result)
        self.assertIn('"lease_id":"L2"', result["appended"][0])

    def test_durable_review_wake_records_idle_and_busy_delivery(self):
        idle = self.run_scenario("parallel-review-durable-wake", "idle")
        self.assertEqual((1, 0, "queued"), (len(idle["prompts"]), len(idle["appended"]), idle["wakeState"]))
        self.assertEqual(["prepare", "record"], [row["operation"] for row in idle["calls"] if row.get("event") == "review_wake"])
        busy = self.run_scenario("parallel-review-durable-wake", "busy")
        self.assertEqual((0, 1, "appended"), (len(busy["prompts"]), len(busy["appended"]), busy["wakeState"]))
        self.assertEqual(["prepare", "record"], [row["operation"] for row in busy["calls"] if row.get("event") == "review_wake"])

    def test_restart_never_rewakes_submitted_or_unknown_review_wake(self):
        for state in ("queued", "appended", "uncertain", "submitting"):
            with self.subTest(state=state):
                result = self.run_scenario("parallel-review-durable-wake", "restart-" + state)
                self.assertEqual([], result["prompts"], result)
                self.assertEqual([], result["appended"], result)
                self.assertEqual([], [row for row in result["calls"] if row.get("event") == "review_wake"], result)

    def test_failed_review_wake_is_prepared_and_recorded_once(self):
        result = self.run_scenario("parallel-review-durable-wake", "failed")
        self.assertEqual(1, len(result["prompts"]), result)
        self.assertEqual("queued", result["wakeState"])
        self.assertEqual(["prepare", "record"], [row["operation"] for row in result["calls"] if row.get("event") == "review_wake"])

    def test_pump_rolls_forward_only_stored_blocked_decisions(self):
        result = self.run_scenario("parallel-roll-forward-selection")
        # awaiting_capacity and a blocked deciding saga; never undecided, ready or running rows.
        self.assertEqual(["LA", "LB"], result["rolled"])
        self.assertIn("Rework (waiting for slot)", result["shown"])
        self.assertIn("blocked paused: dispatch paused", result["shown"])
        self.assertIn("blocked stage_failed", result["shown"])

    def test_stop_rereads_and_holds_pending_review_until_explicit_decision(self):
        result = self.run_scenario("parallel-review-stop-gate")
        self.assertIn("work_parallel_item_review", result["first"]["block"])
        self.assertIn('"lease_id":"L3"', result["first"]["block"])
        self.assertNotIn("SECRET-TRANSCRIPT", result["first"]["block"])
        self.assertIn("work_parallel_item_review", result["second"]["block"])
        # Wake delivery is not a review decision: every Stop remains gated.
        self.assertIn("work_parallel_item_review", result["third"]["block"])
        decision = result["decision"]
        self.assertEqual("approve", decision["decision"])
        self.assertEqual("L3", decision["lease_id"])
        self.assertEqual("review:L3:approve", decision["decision_id"])
        self.assertEqual(5, decision["expected_finalization_version"])
        self.assertEqual("tests ok and diff matches scope", decision["evidence"])
        self.assertEqual("claude:S1", decision["session_id"])
        self.assertEqual("host-token", decision["_host"]["token"])
        self.assertIn("finalized", result["decided"]["result"])
        # A decided row is the backend's finalization saga; Stop no longer holds main.
        self.assertNotIn("block", result["after"])

    def test_owned_work_fork_notifications_never_reach_main(self):
        result = self.run_scenario("parallel-owned-fork-notifications-suppressed")
        self.assertIn("consumed", result["busy"])
        self.assertIn("drop", result["idle"])
        self.assertIn("consumed", result["late"])
        self.assertEqual({"text": "passed"}, result["foreign"])
        self.assertNotIn("drop", result["typed"])

    def test_reviewer_role_dispatch_is_refused_without_reservation(self):
        result = self.run_scenario("parallel-reviewer-run-denied")
        self.assertTrue(result["run"].startswith("deny: Work parallel starts workers only"), result)
        self.assertEqual([], result["events"])

    def test_rework_decision_starts_resume_fork_from_retained_workspace(self):
        result = self.run_scenario("parallel-rework-dispatch-started")
        # Hydration reads the host launch view for the unbound resume lease, then the pump does.
        self.assertEqual(["rework_launch_status", "rework_launch_status", "rework_dispatch", "rework_launch_intent",
                          "rework_spawn_observed", "bind", "reserve_next"],
                         [row["event"] for row in result["calls"]])
        self.assertEqual(1, len(result["spawned"]), result)
        spawn = result["spawned"][0]
        self.assertEqual(("fork", "C:/w/rework"), (spawn["subagentType"], spawn["cwd"]))
        self.assertIn("as required rework", spawn["prompt"])
        self.assertIn("add jitter test", spawn["prompt"])
        self.assertIn('"role":"resume"', spawn["prompt"])
        self.assertIn('"generation":2', spawn["prompt"])

    def test_denied_rework_spawn_keeps_durable_replacement_reservation(self):
        result = self.run_scenario("parallel-rework-spawn-denied-retains-reservation")
        self.assertIn("rework_dispatch", result["calls"])
        self.assertIn("rework_launch_intent", result["calls"])
        self.assertIn("rework_launch_denied", result["calls"])
        self.assertNotIn("release", result["calls"])

    def test_rework_bind_failure_then_recovery_starts_exactly_one_fork(self):
        # review-backend reproduction: bind failed and the next pump started a
        # second fork for the same lease with a rotated capability.
        for mode in ("bind-throw", "bind-lost"):
            with self.subTest(mode):
                result = self.run_scenario("parallel-rework-launch", mode)
                self.assertEqual(["work-rework-R2-a1"], result["spawned"], result)
                self.assertEqual(1, result["calls"].count("rework_dispatch"), result)
                self.assertEqual("agent-rework-1", result["hostId"], result)
                self.assertIn("consumed", result["tracked"])

    def test_restart_with_recorded_launch_and_agent_row_binds_without_spawning(self):
        result = self.run_scenario("parallel-rework-launch", "restart-row")
        self.assertEqual([], result["spawned"], result)
        self.assertNotIn("rework_dispatch", result["calls"])
        self.assertEqual("agent-earlier", result["hostId"])
        self.assertIn("consumed", result["tracked"])

    def test_later_process_resolves_an_unlisted_launch_as_absent_then_starts_one_fork(self):
        result = self.run_scenario("parallel-rework-launch", "restart-no-row")
        self.assertEqual(["work-rework-R2-a2"], result["spawned"], result)
        self.assertEqual(1, result["calls"].count("rework_launch_absent"), result)
        self.assertLess(result["calls"].index("rework_launch_absent"), result["calls"].index("rework_dispatch"))
        self.assertEqual(1, result["calls"].count("rework_dispatch"), result)
        # The established nonce reached the router on every launch fact that carries one.
        self.assertTrue(result["nonceSent"])
        self.assertEqual(["rework_launch_status", "rework_launch_absent", "rework_launch_status", "rework_launch_intent"],
                         result["routerNonceEvents"])

    def test_same_process_unlisted_launch_is_stuck_until_a_person_resolves_it(self):
        result = self.run_scenario("parallel-rework-launch", "same-no-row")
        self.assertEqual([], result["spawned"], result)
        for event in ("rework_launch_absent", "rework_launch_manual_resolve", "rework_dispatch", "bind"):
            self.assertNotIn(event, result["calls"])
        self.assertIsNone(result["hostId"])
        self.assertIn("Rework launch stuck", result["shown"])
        self.assertIn("work-rework-R2-a1", result["shown"])

    def test_rework_dispatch_without_a_process_nonce_records_no_intent_and_starts_no_fork(self):
        result = self.run_scenario("parallel-rework-launch", "nonce-set-throws")
        self.assertEqual([], result["spawned"], result)
        self.assertNotIn("rework_launch_intent", result["calls"], result)
        self.assertNotIn("rework_launch_absent", result["calls"], result)
        self.assertIsNone(result["hostId"])

    def test_unlisted_launch_without_a_process_nonce_is_never_resolved_automatically(self):
        # Backend says another process recorded the intent (same_process false),
        # but this host never obtains its own nonce: only manual resolution remains.
        result = self.run_scenario("parallel-rework-launch", "restart-no-row-nonce-never")
        self.assertEqual([], result["spawned"], result)
        for event in ("rework_launch_absent", "rework_launch_intent", "rework_dispatch", "bind"):
            self.assertNotIn(event, result["calls"], result)
        self.assertFalse(result["nonceSent"])
        self.assertIn("rework_launch_status", result["calls"])
        # No router call carried a launch_nonce field at all.
        self.assertEqual([], result["routerNonceEvents"])

    def test_function_level_concurrent_first_nonce_callers_share_one_read_back_value(self):
        # Function-level: reworkLaunchNonce's exact source called directly by two
        # and three concurrent first callers, with the fake env forcing get x N
        # before any set. Not a product call path.
        for callers in (2, 3):
            with self.subTest(callers=callers):
                result = self.run_scenario("nonce-concurrent-function", str(callers))
                # Before the first set, either every caller read the env unset
                # (each first read is its own) or one shared read did.
                self.assertIn(result["concurrentGets"], (1, callers), result)
                self.assertEqual(["get"] * result["concurrentGets"] + ["set"],
                                 result["log"][:result["concurrentGets"] + 1], result)
                self.assertEqual((1, 0, True, True), (result["distinct"], result["nulls"], result["allEqualEnv"],
                                                      result["envSet"]), result)
                self.assertEqual(1, result["sets"], result)
                self.assertTrue(result["afterEqualsEnv"], result)

    def test_function_level_concurrent_first_nonce_callers_all_fail_closed_when_the_set_fails(self):
        for callers in (2, 3):
            for fault in ("deny", "set-throws"):
                with self.subTest(callers=callers, fault=fault):
                    result = self.run_scenario("nonce-concurrent-function", str(callers), fault)
                    self.assertIn(result["concurrentGets"], (1, callers), result)
                    self.assertEqual((callers, False), (result["nulls"], result["envSet"]), result)
                    # One shared attempt for the concurrent callers, one for the later call.
                    self.assertEqual(2, result["sets"], result)
                    self.assertTrue(result["afterEqualsEnv"], result)

    def test_fixture_forced_concurrent_hooks_send_one_nonce_for_a_first_establishment(self):
        # The fixture host dispatches session.start (hydrate) and turn.complete
        # (pump) concurrently; both read the launch status before any nonce
        # exists. Whether Claude Code dispatches them concurrently is not measured.
        result = self.run_scenario("parallel-rework-nonce-race")
        self.assertIn(result["concurrentGets"], (1, 2), result)
        self.assertGreaterEqual(result["statusWithNonce"], 2, result)
        self.assertEqual((1, True, 1), (result["distinct"], result["allEqualEnv"], result["sets"]), result)
        denied = self.run_scenario("parallel-rework-nonce-race", "deny")
        self.assertIn(denied["concurrentGets"], (1, 2), denied)
        self.assertEqual((0, False), (denied["statusWithNonce"], denied["envSet"]), denied)

    def test_function_level_reordered_first_read_delivery_still_returns_one_nonce(self):
        # Function-level hypothesis (review-restart-terminal probe_nonce_reorder):
        # caller B's first read, issued before A's set, is delivered only after
        # A's set and read-back. Real host delivery order is not measured.
        fifo = self.run_scenario("nonce-reorder-function", "fifo")
        self.assertEqual((True, 1, 1), (fifo["single"], fifo["distinct"], fifo["sets"]), fifo)
        reordered = self.run_scenario("nonce-reorder-function", "reordered")
        self.assertEqual((True, 1, 1), (reordered["single"], reordered["distinct"], reordered["sets"]), reordered)
        self.assertEqual(["A:get", "A:set", "A:get"], reordered["log"][:3], reordered)

    def test_explicit_rework_spawn_denial_allows_one_retry_of_the_same_lease(self):
        result = self.run_scenario("parallel-rework-launch", "deny-once")
        self.assertEqual(["work-rework-R2-a1", "work-rework-R2-a2"], result["spawned"], result)
        self.assertEqual(2, result["calls"].count("rework_dispatch"), result)
        self.assertNotIn("release", result["calls"])
        self.assertEqual("agent-rework-2", result["hostId"])

    def test_parallel_fill_is_not_hard_limited_to_twenty(self):
        result = self.run_scenario("parallel-fill-beyond-twenty")
        self.assertEqual(25, result["spawned"], result)
        self.assertEqual(26, result["reserve"], result)

    def test_successful_worker_report_triggers_refill(self):
        result = self.run_scenario("parallel-report-refills-released-slot")
        self.assertEqual(1, result["spawned"], result)
        self.assertEqual(2, result["reserve"], result)

    def test_report_trigger_during_fill_sets_pending_again_latch(self):
        result = self.run_scenario("parallel-report-trigger-during-fill-latches")
        self.assertEqual(2, result["spawned"], result)
        self.assertGreaterEqual(result["reserve"], 3)

    def test_stale_stop_never_starts_a_fork(self):
        self.assertEqual(0, self.run_scenario("parallel-stale-stop-no-spawn")["spawned"])

    def test_stop_after_session_end_does_not_continue(self):
        self.assertFalse(self.run_scenario("stop-session-end")["block"])

    def test_stop_continuation_still_yields_to_a_new_turn(self):
        self.assertFalse(self.run_scenario("stop-superseded")["block"])
        self.assertTrue(self.run_scenario("stop-continues")["block"])


if __name__ == "__main__":
    unittest.main()
