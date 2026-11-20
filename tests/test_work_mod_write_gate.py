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
        shutil.copyfile(w.CLAUDE_MOD_DIR / "hooks" / "texts.generated.js", cls.root / "texts.generated.js")
        shutil.copyfile(HARNESS, cls.root / HARNESS.name)

    @unittest.skip('retired 2026-11-18 (user): Stop never injects a message into main; the side checks at turn end instead')
    def test_slow_board_read_still_continues_the_turn(self):
        row = self.run_scenario('slow-board-read-still-continues-the-turn')
        self.assertFalse(row['unreadable'], row)
        self.assertIn('n2', row['block'], row)

    def test_periodic_pump_and_gate_host_round_run_while_a_fork_runs(self):
        # Stage 3 (2026-11-17): HEAD had no periodic pump and skipped the gate
        # check while any Work fork ran (and the router refused side intake off).
        row = self.run_scenario('stage3-periodic-pump-and-gate-host-round')
        self.assertGreaterEqual(row['periodicArmed'], 1, row)
        self.assertTrue(row['periodicPumped'], row)
        self.assertGreaterEqual(row['rearmed'], 1, row)
        self.assertEqual({'idle': True, 'forks_running': 1}, row['firstGate'])
        self.assertIn('diagnosis', row['claims'])
        self.assertTrue(row['round']['repumped'], row)
        self.assertTrue(row['round']['notified'], row)
        self.assertEqual(['P2'], row['round']['attention'])
        self.assertIn('Ciel Work dispatch check (host gate check, every 5 min; 1 Work fork(s) running)', row['notice'])
        self.assertIn('P2 free slot but no runnable item (goal in planning 1)', row['notice'])
        self.assertIn('Side intake is off, so 1 blocked/waiting item(s) were not re-checked', row['notice'])
        self.assertIn('grants no execution approval', row['notice'])
        self.assertIn('Ciel Work dispatch wake', row['wake'])
        # Same diagnosis next interval: one notice only, but the round is recorded again.
        self.assertEqual(1, row['notices'])
        self.assertGreaterEqual(row['rounds'], 2)
        self.assertTrue(any('side intake off' in line for line in row['logs']), row['logs'])

    def test_p0_outcomes_reach_main_once_with_the_exact_reason(self):
        row = self.run_scenario("parallel-p0-outcome-notices")
        self.assertEqual(1, len(row["notices"]), row)
        notice = row["notices"][0]
        self.assertIn("n1 (runs): P0 fork started: agent agent-1 on item n2.", notice)
        self.assertIn("n3 (refused): no P0 fork (workspace_git_untrusted)", notice)
        self.assertIn("detected dubious ownership", notice)
        self.assertNotIn("n5 (pending)", notice)      # a pending launch claims nothing
        self.assertNotIn("n6 (ran earlier)", notice)  # after "started" a block is not news
        self.assertTrue(all(call["internal"] for call in row["claims"]), row)
        self.assertEqual(1, len(row["wakes"]), row)   # a refusal wakes an idle main

    def test_a_started_p0_fork_is_told_without_waking_main(self):
        row = self.run_scenario("parallel-p0-outcome-notices", "started-only")
        self.assertEqual(1, len(row["notices"]), row)
        self.assertEqual([], row["wakes"])

    def test_an_undelivered_p0_outcome_is_released_for_the_next_pump(self):
        row = self.run_scenario("parallel-p0-outcome-notices", "append-denied")
        self.assertEqual([], row["notices"])
        self.assertIn({"root": "n3", "outcome": "blocked:workspace_git_untrusted", "release": True, "internal": True},
                      row["claims"])

    def test_main_steps_wake_the_pump_during_a_long_turn(self):
        row = self.run_scenario('main-steps-wake-the-pump-during-a-long-turn')
        self.assertTrue(row['firstStepPumped'], row)
        self.assertTrue(row['throttled'], row)
        self.assertTrue(row['nextWindowPumped'], row)
        self.assertFalse(row['childStepPumped'], row)

    def run_scenario(self, name, *args):
        done = subprocess.run([NODE, HARNESS.name, name, *args], cwd=self.root, capture_output=True, text=True,
                              encoding="utf-8", timeout=60, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def lifecycle(self, result):
        """The scenario's Work lifecycle events, without the G5 gate_presence report.

        ciel-work reports the fork gate's presence (display only) once per module
        start and on change; the start-time report is asynchronous, so where it
        lands among lifecycle events is not part of their order. It is asserted
        in its own test (test_first_spawn_reports_the_gate_presence_once).
        """
        def keep(call):
            return call != "gate_presence" and not (isinstance(call, dict) and call.get("event") == "gate_presence")
        for holder in [result, *[value for value in result.values() if isinstance(value, dict)]]:
            if isinstance(holder.get("calls"), list):
                holder["calls"] = [call for call in holder["calls"] if keep(call)]
        return result

    @unittest.skip('retired 2026-11-18 (user): Stop never injects a message into main; the side checks at turn end instead')
    def test_high_priority_advice_prompts_main_at_safe_stop_even_without_auto_continue(self):
        result=self.run_scenario('side-advice-stop')
        self.assertIn('work_side_handoff',result['block'])
        self.assertIn('holds no event data',result['block'])
        self.assertIn('grants no approval',result['block'])
        # Payload details are in the definite host append, never repeated in Stop.
        self.assertNotIn('Daniel approval 118269',result['block'])
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

    @unittest.skip('retired 2026-11-18 (user): Stop never injects a message into main; the side checks at turn end instead')
    def test_stop_presents_only_its_own_started_election_and_records_on_the_next_step(self):
        # Owned probe stopblock-838268bd/d9595ffa (Claude Code 2.1.321): the Stop
        # block continues the same main turn; its next turn.step precedes the
        # request carrying the block. That step is the proof that records queued.
        result = self.run_scenario('side-wake-stop-election')
        self.assertIn('grants no approval', result['first']['block'])
        self.assertIn('work_side_handoff', result['first']['block'])
        for secret in ('a' * 32, 'owner_nonce', 'attempt_id', hashlib.sha256(b'a' * 32).hexdigest()[:16]):
            self.assertNotIn(secret, result['first']['block'])
        self.assertEqual({'state': 'appended', 'phase': 'started', 'records': 0}, result['beforeStep'])
        self.assertEqual({'state': 'appended', 'records': 0}, result['afterStep'])
        # The continuation Stop emits one wake-only reminder; later Stop adds nothing.
        self.assertIn('holds no event data', result['second']['block'])
        self.assertEqual('', result['third'])
        # The payload was stored inside that Stop, so the continuation request
        # (next main step) carries it: read is recorded (append-views-54579a2e).
        self.assertEqual(['prepare', 'start', 'appended', 'read'], result['calls'])

    @unittest.skip('retired 2026-11-18 (user): Stop never injects a message into main; the side checks at turn end instead')
    def test_stop_proof_is_bound_to_the_blocked_turn_and_a_later_step(self):
        # classic.Stop has no turnId: the entry is bound to the last main turnId
        # and step index. Another turn's step, or a step that is not later,
        # never settles it; the next step of the same turn does.
        for proof in ('other-turn', 'same-index'):
            with self.subTest(proof=proof):
                result = self.run_scenario('side-wake-stop-election', proof)
                self.assertIn('block', result['first'])
                self.assertEqual(0, result['early'])
                self.assertEqual({'state': 'appended', 'records': 0}, result['afterStep'])

    @unittest.skip('retired 2026-11-18 (user): Stop never injects a message into main; the side checks at turn end instead')
    def test_turn_end_before_another_step_keeps_definite_append(self):
        result = self.run_scenario('side-wake-stop-election', 'interrupt')
        self.assertIn('block', result['first'])
        self.assertEqual(0, result['early'])
        self.assertEqual({'state': 'appended', 'records': 0}, result['afterStep'])
        self.assertNotIn('delivery to main is uncertain', result['third'])
        # The turn ended first: main is woken (wake-only lease ask), and a later
        # main step (built after that Stop) proves the read.
        self.assertEqual(['prepare', 'start', 'appended', 'prompt_prepare', 'read'], result['calls'])

    @unittest.skip('retired 2026-11-18 (user): Stop never injects a message into main; the side checks at turn end instead')
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

    @unittest.skip('retired 2026-11-18 (user): Stop never injects a message into main; the side checks at turn end instead')
    def test_stop_and_pump_in_one_process_present_a_wake_exactly_once(self):
        # Stop runs while main is still working, so a pump that wins appends
        # into the running turn; Stop then shows its one-time reminder for the
        # unacknowledged appended wake (no event data), never a second election.
        # No request had carried the row yet: the reminder continues the turn,
        # and that next step records the read.
        expected = {'stop-first': (True, 1, 'appended', ['prepare:stop', 'start:stop', 'appended:', 'read:']),
                    'pump-first': (False, 1, 'appended', ['prepare:append', 'start:append', 'appended:', 'read:']),
                    'interleaved': (True, 1, 'appended', ['prepare:stop', 'start:stop', 'appended:', 'read:'])}
        for order, (elected, appended, state, calls) in expected.items():
            with self.subTest(order=order):
                result = self.run_scenario('side-wake-stop-pump-same-owner', order)
                self.assertEqual(elected, result['elected'])
                self.assertEqual(0, result['submitted'])
                self.assertEqual(appended, result['appended'])
                self.assertEqual(1, result['submitted'] + result['appended'])
                self.assertTrue(result['block'])
                self.assertEqual(state, result['wake']['state'])
                self.assertEqual(calls, result['calls'])
                self.assertFalse(result['staleShown'])

    @unittest.skip('retired 2026-11-18 (user): Stop never injects a message into main; the side checks at turn end instead')
    def test_lost_stop_start_is_never_stranded_or_called_uncertain(self):
        for variant, calls in (('uncommitted', ['prepare:stop', 'start:stop', 'prepare:append', 'start:stop', 'appended:']),
                               ('committed', ['prepare:stop', 'start:stop', 'appended:'])):
            with self.subTest(variant=variant):
                result = self.run_scenario('side-wake-stop-start-lost', variant)
                self.assertFalse(result['stopBlock'])
                self.assertEqual('', result['againBlock'])
                self.assertFalse(result['paneUncertain'])
                self.assertEqual(0, result['beforePump']['submitted'])
                # The pump appends it once under the same started attempt; no
                # prompt holds the payload.
                self.assertEqual((1, 0), (result['appended'], result['submitted']))
                self.assertEqual({'state': 'appended', 'phase': 'started', 'channel': 'stop'}, result['wake'])
                # No main request followed the append yet: the next Stop reminds
                # once, naming the event and holding none of its data.
                self.assertIn('holds no event data', result['laterBlock'])
                self.assertIn('"event_id":"' + 's' * 64 + '"', result['laterBlock'])
                self.assertNotIn('Start lost', result['laterBlock'])
                self.assertNotIn('Advice data', result['laterBlock'])
                self.assertEqual(calls, result['calls'])
                self.assertFalse(result['staleShown'])

    # Adversarial schedules ported from review-side-wake's stop_adversarial.mjs.
    # Each of the defect tests fails on the reviewed draft (register.js 81949d1a,
    # side-intake.js 6da3de4c) and passes here.

    @unittest.skip('retired 2026-11-18 (user): Stop never injects a message into main; the side checks at turn end instead')
    def test_lost_stop_start_answer_is_delivered_under_the_same_started_attempt(self):
        for variant in ('committed', 'uncommitted'):
            with self.subTest(variant=variant):
                result = self.run_scenario('adv-start-lost', variant)
                self.assertFalse(result['stopBlock'])
                self.assertEqual((0, 1), (result['prompts'], result['appends']))
                # One router-side attempt; every start and the one record name it
                # and its owner; no second attempt is ever started.
                self.assertEqual(1, result['attempts'])
                self.assertEqual('appended/stop', result['wake'])
                for row in result['starts'] + result['records']:
                    self.assertEqual((result['routerAttempt'], result['routerOwner']),
                                     (row['attempt_id'], row['owner_nonce']))
                self.assertEqual(1, len(result['records']))
                self.assertEqual(['appended'], [row['state'] for row in result['records']])

    @unittest.skip('retired 2026-11-18 (user): Stop never injects a message into main; the side checks at turn end instead')
    def test_superseded_stop_presents_nothing_itself_and_its_attempt_is_never_left_uncertain(self):
        # Defect A: turn.start(T2) while the Stop's start was in flight. The
        # stale Stop returns no block and proves nothing (no queued record).
        # Item 4: the started attempt it could not present is appended once by
        # side-intake.js instead of staying delivery uncertain.
        result = self.run_scenario('adv-turn-start-during-election')
        self.assertFalse(result['staleStopReturnedBlock'])
        self.assertEqual(0, result['records'])
        self.assertEqual('appended/started', result['wake'])
        self.assertFalse(result['laterUncertain'])

    def test_one_side_event_is_presented_to_main_once_after_a_release_a_retry_or_a_clear(self):
        # Items 2 and 3. Real register.js and side-intake.js on one shared
        # conversation. Before the payload moved out of the prompt each variant
        # presented the event twice: the held payload prompt entered after an
        # uncertainty notice at Stop (anomaly, /clear then reload), or after the
        # retried attempt's prompt (retry). Owned probes prompt-registry-e2bed500
        # and 5fe2353d measured such a released prompt entering 3 s later.
        for variant in ('anomaly', 'retry', 'clear-skip', 'clear-fails'):
            with self.subTest(variant=variant):
                result = self.run_scenario('sse-reads', variant)
                self.assertEqual(1, result['presentations'], result['final'])
                # The payload is stored by the append, before any prompt enters.
                self.assertEqual((['append'], 'appended'), (result['issued']['payloadRows'], result['issued']['wake']))
                self.assertEqual(0, result['issued']['heldPayloadPrompts'])
                self.assertEqual('appended', result['afterRelease']['wake'])
                self.assertEqual((['append'], [], 'appended'), (result['final']['payloadRows'], result['final']['stopBlocks'],
                                                               result['final']['wake']))
                self.assertEqual(['E1:appended'], result['records'])
                if variant == 'retry':
                    # A delivered wake offers no may-duplicate retry.
                    self.assertFalse(result['retryOffered'])

    @unittest.skip('retired 2026-11-18 (user): Stop never injects a message into main; the side checks at turn end instead')
    def test_wake_in_the_last_request_of_a_turn_is_read_in_that_turn_once(self):
        # Item 7. Main is busy in the last request of a turn. The payload is
        # appended at once (no prompt, no wait for the turn's end); the Stop
        # reminder continues the turn so the next request carries the row. The
        # reminder names the event and holds none of its data: before, it
        # repeated the payload, so that request held the event twice.
        result = self.run_scenario('sse-busy-final-step')
        self.assertEqual(1, result['presentations'], result['final'])
        running = result['whileTurnRuns']
        self.assertEqual((['append'], 0, 'appended'), (running['payloadRows'], running['held'], running['wake']))
        final = result['final']
        self.assertEqual((['append'], [], 1, 0, 0), (final['payloadRows'], final['stopBlocks'], final['stopReminders'],
                                                    final['plainPrompts'], final['held']))
        # The continuation request's exact messageCount includes the appended
        # host row, so its first carrying request records read with no prompt.
        self.assertEqual(['E1:prepare', 'E1:start', 'E1:appended', 'E1:read'], result['sideCalls'])

    @unittest.skip('retired 2026-11-18 (user): Stop never injects a message into main; the side checks at turn end instead')
    def test_superseded_stop_election_is_delivered_once_by_the_pump_without_a_person(self):
        # Item 4. The Stop returned no block, so this module knows the started
        # attempt was never presented. The pump delivers that same attempt into
        # the running turn; it is never called uncertain. The turn's next request
        # carries the row (read); the unacknowledged event is still reminded once.
        result = self.run_scenario('sse-superseded-stop')
        self.assertFalse(result['staleStopReturnedBlock'])
        self.assertEqual({'delivered': 1, 'appends': 1, 'wake': 'appended/started'}, result['whileT2Runs'])
        self.assertEqual((1, 1, 'appended/started'), (result['delivered'], result['attempts'], result['wake']))
        self.assertFalse(result['laterUncertain'])
        # No later Stop holds the event's data. The fixture's main never
        # acknowledges, so the one-time reminder (no event data) is shown once.
        self.assertEqual((0, 1), (result['laterBlocks'], result['laterReminders']))

    @unittest.skip('retired 2026-11-18 (user): Stop never injects a message into main; the side checks at turn end instead')
    def test_record_that_lands_late_never_calls_the_wake_uncertain(self):
        # Defect B: recordFaults=1 and recordFaults=2.
        result = self.run_scenario('adv-record-late-success')
        self.assertFalse(result['first'])
        self.assertIn('holds no event data',result['secondBlock'])
        self.assertEqual(('', 'appended'), (result['thirdBlock'], result['wake']))
        self.assertEqual(2, result['calls'].count('work_side_wake:appended'))
        result = self.run_scenario('adv-record-fail-after-proof')
        self.assertFalse(result['first'])
        self.assertIn('holds no event data',result['second'])
        self.assertEqual(('', '', 'appended'), (result['third'], result['fourth'], result['wake']))
        self.assertEqual(3, result['calls'].count('work_side_wake:appended'))
        self.assertEqual(1, result['calls'].count('work_side_wake:start:stop'))

    @unittest.skip('retired 2026-11-18 (user): Stop never injects a message into main; the side checks at turn end instead')
    def test_stale_knowledge_of_another_attempt_never_hides_uncertainty(self):
        # Defect C: Stop's start lost a real CAS; another process started and died.
        result = self.run_scenario('adv-stale-unpresented')
        self.assertFalse(result['firstStopBlock'])
        self.assertTrue(result['laterStopUncertainBlock'])
        self.assertTrue(result['paneRetryOffered'])
        self.assertEqual(0, result['prompts'])
        control = self.run_scenario('adv-control-uncertain-visible')
        self.assertEqual((True, True), (control['stopUncertainBlock'], control['paneRetryOffered']))

    @unittest.skip('retired 2026-11-18 (user): Stop never injects a message into main; the side checks at turn end instead')
    def test_a_step_after_the_turn_ended_never_settles_a_stop_presentation(self):
        for reason in ('aborted', 'answer'):
            with self.subTest(reason=reason):
                result = self.run_scenario('adv-turn-end-before-step', reason)
                self.assertFalse(result['presented'])
                self.assertEqual(('appended/started', 0), (result['wake'], result['records']))

    @unittest.skip('retired 2026-11-18 (user): Stop never injects a message into main; the side checks at turn end instead')
    def test_continuation_stop_gates_only_notices_and_elects_a_new_event_once(self):
        result = self.run_scenario('adv-stop-hook-active-loop')
        self.assertEqual((False, True, False, 'appended'),
                         (result['first'], result['second'], result['third'], result['wake']))
        result = self.run_scenario('adv-uncertain-stop-hook-active')
        self.assertEqual([True, False, False, False], result['blocks'])
        self.assertFalse(result['leak'])
        # A new event at a stop_hook_active Stop is presented exactly once and
        # does not block again on the next Stops.
        result = self.run_scenario('adv-continuation-new-event')
        self.assertEqual([True, True, True, False], result['blocks'])
        self.assertEqual([1, 1], result['presented'])
        self.assertEqual(['appended', 'appended'], result['states'])

    @unittest.skip('retired 2026-11-18 (user): Stop never injects a message into main; the side checks at turn end instead')
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
        self.assertEqual((False, 0, 1, 'appended'), (result['stop'], result['prompts'], result['appends'], result['wake']))
        result = self.run_scenario('adv-two-handoffs')
        self.assertFalse(result['first'])
        self.assertFalse(result['firstIncludesSecond'])
        result = self.run_scenario('adv-pane-retry')
        self.assertTrue(result['button'])
        self.assertIsNone(result['afterCancel'])
        self.assertEqual({'event_id': 'e' * 64, 'state': 'retry', 'attempt_ref': result['ref'], 'revision': 6,
                          'confirmed': True}, result['retry'])
        # The router takes a person-confirmed retry only with this host's marker.
        self.assertTrue(result['retryInternal'])

    # Per-key idle prompts. Main-review E2E run-20261111-161947: a second plugin
    # prompt was issued at 22:22:47.650 before the first (22:22:46.501) started
    # its turn. Each prompt is issued at once under its own key; the engine
    # holds the second (owned probes prompt-gate-0dd78bf3,
    # prompt-registry-e2bed500). Stop must not also present a review whose
    # prompt is issued and unresolved (prompt-stop-28f9d226).

    def test_both_idle_prompts_are_issued_at_once_and_each_records_its_own_outcome(self):
        result = self.run_scenario('prompt-keys', 'reviews')
        # Both $.prompt.submit calls happen before either resolves.
        self.assertEqual(['L1', 'L2'], sorted(result['whileBothPending']['invocations']))
        self.assertEqual((2, 2), (result['whileBothPending']['pending'], result['maxActive']))
        # Both review payloads were appended and durably recorded before either
        # wake-only prompt resolves. Prompt outcome cannot change delivery state.
        # The two leases' deliveries are concurrent chains (register.js
        # finishParallelWake runs via `void`), so their record order is not
        # part of the contract: a slow off-thread digest for L1 records L2 first.
        self.assertEqual((['L1:appended', 'L2:appended'], ['parallel:L2']),
                         (sorted(result['afterFirst']['records']), result['afterFirst']['pending']))
        self.assertEqual(['L1:appended', 'L2:appended'], sorted(result['records']))
        result = self.run_scenario('prompt-keys', 'reviews:reverse')
        self.assertEqual((['L1:appended', 'L2:appended'], ['parallel:L1']),
                         (sorted(result['afterFirst']['records']), result['afterFirst']['pending']))
        self.assertEqual(['L1:appended', 'L2:appended'], sorted(result['records']))

    def test_side_wake_payload_never_waits_behind_a_prompt_and_its_own_prompt_holds_none(self):
        # Two side wakes: both payloads are appended and recorded while both
        # wake prompts are still unresolved in the engine.
        result = self.run_scenario('prompt-keys', 'sides')
        pending = result['whileBothPending']
        self.assertEqual((['wake:E1', 'wake:E2'], ['E1', 'E2'], ['E1:appended', 'E2:appended'], 2),
                         (sorted(pending['invocations']), sorted(pending['appended']), sorted(pending['records']),
                          pending['pending']))
        # A wake prompt that resolves records nothing about either row: queued is
        # not a read. Only a main step's exact messageCount records read_at.
        self.assertEqual(['E1:appended', 'E2:appended'], sorted(result['afterFirst']['records']))
        self.assertEqual({'E1': 'appended', 'E2': 'appended'}, result['sideWakes'])
        # Review and side payloads are both appended before their wake-only
        # prompts resolve, in either producer order.
        for mode in ('review-side', 'side-review'):
            with self.subTest(mode=mode):
                result = self.run_scenario('prompt-keys', mode)
                pending = result['whileBothPending']
                self.assertEqual((['L1', 'wake:E1'], sorted(['L1', 'E1']), sorted(['L1:appended', 'E1:appended']), 2),
                                 (sorted(pending['invocations']), sorted(pending['appended']), sorted(pending['records']), result['maxActive']))
                # No main turn ran in this scenario, so no row is read.
                self.assertEqual(['E1:appended', 'L1:appended'], sorted(result['records']))

    @unittest.skip('retired 2026-11-18 (user): Stop never injects a message into main; the side checks at turn end instead')
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
        for fault in ('drop', 'throw'):
            with self.subTest(fault=fault):
                result = self.run_scenario('prompt-keys', 'reviews:' + fault)
                # Record order between the two concurrent lease chains is not the contract.
                self.assertEqual(['L1:appended', 'L2:appended'], sorted(result['afterFirst']['records']))
                self.assertEqual(['parallel:L2'], result['afterFirst']['pending'])
                self.assertEqual(['L1:appended', 'L2:appended'], sorted(result['records']))
        # A side wake's own prompt holds no payload, so its drop or throw
        # records nothing about the wake (test_side_intake_mod:
        # test_wake_prompt_drop_or_throw_is_submitted_once_more...).
        result = self.run_scenario('prompt-keys', 'side-review:throw')
        self.assertEqual(['E1:appended', 'L1:appended'], result['records'])
        self.assertEqual({'E1': 'appended'}, result['sideWakes'])

    def test_an_issued_prompt_key_is_shared_and_submitted_once(self):
        result = self.run_scenario('prompt-key-dedup')
        self.assertEqual(['first'], result['calls'])
        self.assertTrue(result['sameEntry'])
        self.assertEqual((True, False), (result['pendingBefore'], result['pendingAfter']))
        self.assertEqual(['queued', 'queued'], result['results'])

    @unittest.skip('retired 2026-11-18 (user): Stop never injects a message into main; the side checks at turn end instead')
    def test_latent_prompt_never_blocks_other_keys_and_is_exposed_after_its_idle_window(self):
        result = self.run_scenario('prompt-key-latent')
        # Issue order between independent keys is not part of the contract.
        self.assertEqual(['L1', 'L2', 'wake'], sorted(result['issued']))
        self.assertEqual(['parallel:L1', 'parallel:L2', 'wake:E1'], sorted(result['registryWhileAll']))
        # The side wake's payload was appended while every prompt was pending.
        self.assertEqual(1, result['appendedWhileAll'])
        # The side wake and L2 settle while the review prompt L1 has not resolved.
        latent = result['whileLatent']
        self.assertEqual((['E1:appended', 'L1:appended', 'L2:appended'], ['parallel:L1'], True),
                         (sorted(latent['records']), latent['registry'], latent['pendingL1']))
        # L1 is held out of Stop's decision gate while its prompt is unresolved;
        # the side wake is delivered, so nothing is uncertain and no retry is offered.
        self.assertEqual({'review': False, 'uncertain': False, 'retry': False}, latent['view'])
        # No window during the main turn; turn.complete arms a full one.
        self.assertEqual((0, 1), (result['liveInTurn'], result['liveAfterTurn']))
        self.assertEqual(['E1:appended', 'L1:appended', 'L2:appended'], sorted(result['afterFireInTurn']['records']))
        self.assertTrue(result['afterFireInTurn']['pendingL1'])
        after = result['afterWindow']
        # L1's uncertain record comes only after the window, after both others.
        self.assertEqual((['E1:appended', 'L1:appended', 'L2:appended'], [], False),
                         (sorted(after['records']), after['registry'], after['pendingL1']))
        self.assertEqual({'review': True, 'uncertain': False, 'retry': False}, after['view'])

    def test_compat_review_without_durable_wake_stays_terminal_across_session_start(self):
        result = self.run_scenario("parallel-submitted-wakes-main-without-reviewer", "session-restart")
        self.assertEqual(1, result["beforeRestart"])
        # session.start clears noticedParallelReviews; the terminal prompt
        # record for the same key stops a second raw submit.
        self.assertEqual(1, len(result["submitted"]), result)

    def test_review_prompt_is_issued_again_after_state_loss_only_for_an_older_projection(self):
        # A review row without wake.state is an older router's projection. Its
        # only fences are module memory and $.state, so a fresh process (new
        # module copy, empty $.state) issues the review prompt again. The current
        # backend always projects wake.state (tests/test_side_compat_review.py);
        # with it the second process issues nothing and asks for no new attempt.
        old = self.run_scenario('compat-review-state-loss', 'old')
        self.assertEqual(([1, 1], [], None), (old['rawSubmits'], old['prepares'], old['wake']))
        current = self.run_scenario('compat-review-state-loss', 'current')
        self.assertEqual(([1, 0], ['granted'], ['L1:appended'], 'appended'),
                         (current['rawSubmits'], current['prepares'], current['records'], current['wake']))

    @unittest.skip('retired 2026-11-18 (user): Stop never injects a message into main; the side checks at turn end instead')
    def test_clear_reasserts_the_prompt_fence_and_a_reload_never_submits_again(self):
        # /clear resets $.state and raises classic.SessionStart in the same
        # module instance (measured: nonce-lifetime-probe-44e40778). The
        # re-claim there is harness-only evidence. A review prompt is the one
        # prompt that still holds a payload; a side wake's /clear cases are
        # test_one_side_event_is_presented_to_main_once_after_a_release_a_retry_or_a_clear.
        key = 'parallel:L1'
        result = self.run_scenario('prompt-key-clear', 'review:reassert')
        self.assertEqual({'registry': [key], 'sameGeneration': True, 'pendingInFirst': True}, result['afterClear'])
        self.assertEqual((True, 0), (result['reload']['hydrated'], result['reload']['rawSubmits']))
        # session.end before the boundary keeps the entry; re-claim still lands.
        ended = self.run_scenario('prompt-key-clear', 'review:end')
        self.assertEqual({'registry': [key], 'sameGeneration': True, 'pendingInFirst': True}, ended['afterClear'])
        self.assertEqual((True, 0), (ended['reload']['hydrated'], ended['reload']['rawSubmits']))
        self.assertEqual({'review': False, 'uncertain': False, 'retry': False}, result['reload']['view'])
        self.assertTrue(result['afterWindow']['review'])
        self.assertEqual(1, result['firstRawSubmits'])
        # No re-claim, or a failing $.state: the reload still raw-submits
        # nothing, because the durable submitting row fences it; the review is
        # presented at once instead (a possible duplicate presentation if the
        # first prompt later enters; the Work review path, not changed here).
        for variant in ('skip', 'fails'):
            control = self.run_scenario('prompt-key-clear', 'review:' + variant)
            self.assertEqual({'registry': [], 'sameGeneration': False, 'pendingInFirst': True}, control['afterClear'])
            self.assertEqual((False, 0), (control['reload']['hydrated'], control['reload']['rawSubmits']))
            self.assertTrue(control['reload']['view']['review'])

    @unittest.skip('retired 2026-11-18 (user): Stop never injects a message into main; the side checks at turn end instead')
    def test_prompt_liveness_table_for_review_side_wake_and_retry(self):
        # Review, side and retry payloads are appended and recorded before any
        # prompt; each prompt is a wake that holds none. Prompt anomalies never
        # change the durable delivery outcome.
        hidden = {'review': False, 'uncertain': False, 'retry': False}
        for condition in ('finite', 'anomaly', 'restart', 'reload'):
            with self.subTest(path='review', condition=condition):
                result = self.run_scenario('prompt-key-cell', 'review:' + condition)
                self.assertEqual((1, ['parallel:L1'], 1), (result['issued']['submits'], result['issued']['registry'],
                                                           result['issued']['live']))
                # Issued and unresolved: hidden from Stop.
                self.assertEqual(hidden, result['before'])
                records = result['records']
                self.assertEqual(['L1:appended'], records)
                if condition in ('finite', 'anomaly'):
                    self.assertEqual([], result['registry'])
                    self.assertTrue(result['after']['review'])
                    if condition == 'anomaly':
                        self.assertEqual(['L1:appended'], result['second']['lateRecords'])
                else:
                    second = result['second']
                    self.assertEqual(0, second['submits'])
                    if condition == 'restart':
                        self.assertEqual({'pending': False, 'live': 0}, second['hydrated'])
                        self.assertTrue(second['before']['review'])
                    else:
                        self.assertEqual({'pending': True, 'live': 1}, second['hydrated'])
                        self.assertEqual(hidden, second['before'])
                        self.assertTrue(second['after']['review'])
                        self.assertEqual([], second['registryAfterWindow'])
        for path in ('side', 'retry'):
            for condition in ('finite', 'anomaly', 'restart', 'reload'):
                with self.subTest(path=path, condition=condition):
                    result = self.run_scenario('prompt-key-cell', path + ':' + condition)
                    issued = result['issued']
                    # One append holds the payload; the one prompt holds none.
                    self.assertEqual((1, 0, 1, ['wake:E1'], 1), (issued['appended'], issued['payloadPrompts'],
                                                                 issued['submits'], issued['registry'], issued['live']))
                    self.assertEqual(hidden, result['before'])
                    # The wake is recorded appended once, whatever its prompt does.
                    self.assertEqual(['E1:appended'], [r for r in result['records'] if not r.endswith(':retry')])
                    self.assertEqual(('appended', False), (result['side']['state'], result['side']['delivery_uncertain']))
                    if path == 'retry':
                        self.assertEqual('E1:retry', result['records'][0])
                    if condition == 'finite':
                        # The prompt entered and started main's turn. Its queued
                        # outcome is recorded first; the turn's first step then
                        # records the read. The key is clear.
                        calls = result['sideCalls']
                        self.assertEqual('E1:read', calls[-1])
                        self.assertLess(calls.index('E1:prompt_queued'), calls.index('E1:read'))
                        self.assertEqual(([], hidden), (result['registry'], result['after']))
                    elif condition == 'anomaly':
                        # The idle window releases the wake prompt, not the wake.
                        self.assertEqual(([], hidden), (result['registry'], result['after']))
                        self.assertNotIn('E1:read', result['sideCalls'])
                    else:
                        second = result['second']
                        self.assertEqual((hidden, hidden, 0), (second['before'], second['after'], second['payloadPrompts']))
                        # The backend prompt lease survives both a new process
                        # and a hot reload, so neither can add an outstanding
                        # prompt while the first is unresolved.
                        self.assertEqual(0, second['submits'])

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
        self.assertIs(True, retry['_host'].get('internal'))

    def test_review_payload_reaches_a_changed_conversation_once_through_the_sidecar(self):
        result = self.run_scenario('review-conversation-change')
        # S1: appended once, one wake prompt, journal attempt recorded appended.
        self.assertEqual({'appends': 1, 'prompts': 1, 'wake': 'appended'}, result['inS1'])
        # S2 (conversation changed): the payload is re-appended exactly once and
        # one wake-only prompt holds none of it, however often reviews project.
        self.assertEqual({'appends': 1, 'prompts': 1, 'promptHasPayload': False}, result['inS2'])
        # A second process already in S2 adds nothing.
        self.assertEqual({'appends': 0, 'prompts': 0}, result['thirdProcess'])
        calls = result['reviewCalls']
        self.assertEqual(1, calls.count('L1:review_appended'))
        self.assertEqual(1, calls.count('L1:review_reappend_appended'))
        self.assertEqual(1, calls.count('L1:review_reappend_prepare'))
        self.assertEqual('L1:review_read', calls[-1])
        # The frozen journal saw one prepare/record only; its attempt is unchanged.
        self.assertEqual((1, 'appended', 1), (result['prepares'], result['journal']['state'],
                                              result['journal']['attempts']))

    def test_every_review_wake_text_carries_the_projected_review_pin(self):
        # Option 1 (team-lead): main decides with expected_review_pin, a
        # read-time pin the router projects on each pending row and which the
        # wake's own prepare/record writes do not stale. The first append and
        # the changed-conversation re-append each carry exactly that pin.
        result = self.run_scenario('review-wake-text-carries-review-pin')
        self.assertEqual([result['pin']], result['first'])
        self.assertEqual([result['pin']], result['moved'])
        self.assertEqual([True], result['approvalChanged'])
        self.assertEqual([True], result['movedApprovalChanged'])
        self.assertEqual(('appended', result['pin']), (result['wakeAfter'], result['pinAfter']))

    def test_a_new_review_gets_one_wake_and_the_reminder_only_after_it_still_waits(self):
        result = self.run_scenario('review-reminder-waits-after-the-first-wake')
        result.pop('scenario', None)
        self.assertEqual({'settled': True, 'wakes': 1, 'immediate': 0, 'afterWaiting': 1, 'repeated': 1}, result)

    def test_sendmessage_goes_on_when_the_host_already_resumed_the_same_worker(self):
        # Celly 2026-11-20 n1041: HEAD denied with "could not return to running:
        # Parallel read failed: transition" although the worker was running.
        same = self.run_scenario('sendmessage-after-the-host-already-resumed-the-worker', 'same')
        self.assertEqual((True, None), (same['sent'], same['deny']), same)
        other = self.run_scenario('sendmessage-after-the-host-already-resumed-the-worker', 'other-worker')
        self.assertFalse(other['sent'], other)
        self.assertIn('could not return to running', other['deny'])

    def test_a_review_only_the_pane_lists_is_reminded_after_it_still_waits(self):
        # The draft that recorded first-seen only at projection never reminded
        # this row (0 after 21 minutes); HEAD d216883 reminded at once.
        result = self.run_scenario('review-reminder-pane-only-row')
        result.pop('scenario', None)
        self.assertEqual({'wakes': 0, 'immediate': 0, 'afterWaiting': 1}, result)

    def test_a_pane_snapshot_older_than_the_review_queue_is_not_reminded(self):
        # Andy 2026-11-20: 20 finalized results kept being named "awaiting main
        # review" because the pane's snapshot is read only when the pane opens.
        result = self.run_scenario('review-reminder-ignores-a-stale-pane-snapshot')
        self.assertEqual(0, result['afterWaiting'], result)

    def test_rows_stored_during_a_main_hook_are_fenced_when_it_returns_in_the_probe_order(self):
        # Owned probe append-views-54579a2e (Claude Code 2.1.323): S, stored
        # inside step 0's dispatch, was absent from request 1 and present in
        # request 2 (L6/L7, result.json requests); B, stored inside a Stop
        # dispatch, was in the continuation request (L9/L10). Plain messages()
        # lists no append and messageCount (7, 12) matches no count (L5, L11),
        # so the harness models both. A row stored before a main hook returns
        # is in the next main request: read at the next main step, never later.
        probe = self.run_scenario('causal-probe-order', 'probe')
        self.assertEqual({'T': 1, 'wakePrompts': 1}, probe['issued'])
        self.assertEqual((['T'], 1), (probe['afterStep0']['reads'], probe['afterStep0']['S']))
        self.assertEqual((True, ['T'], 1), (probe['afterStop']['block'], probe['afterStop']['reads'],
                                            probe['afterStop']['B']))
        self.assertEqual(['T', 'S', 'B'], probe['afterStep1']['reads'])
        self.assertEqual((0, 1), (probe['afterTurn']['wakePromptsAfterTurn'], probe['afterTurn']['promptPrepares']))
        # Interrupted after step 1 (a tool-result continuation, no Stop between
        # the steps or before the turn ends): S was stored before step 0
        # returned, so step 1's request carried it and it needs no wake; B was
        # stored during step 1's own dispatch, after its request was pinned, so
        # no request carried it and exactly one wake prompt follows for it.
        cut = self.run_scenario('causal-probe-order', 'interrupted')
        self.assertEqual(['T', 'S'], cut['afterStep1']['reads'])
        self.assertEqual((['T', 'S'], 1), (cut['afterTurn']['reads'], cut['afterTurn']['wakePromptsAfterTurn']))

    def test_host_only_side_tools_are_refused_to_main_and_workers_and_reach_nothing(self):
        # Measured before this gate (evidence/before/model-side-tool-probe.json):
        # these calls reached the engine and the router, which refused them only
        # for the missing host token. Now the mod refuses them before the engine.
        result = self.run_scenario('host-only-side-tools')
        for row in result['main'] + result['worker']:
            with self.subTest(tool=row['tool']):
                self.assertFalse(row['engineRan'])
                self.assertIn('called only by the Ciel Work host itself', row['deny'])
        # This module's own calls (pane retries, claims, records) still pass.
        self.assertEqual({'tool': 'work_side_wake', 'deny': None, 'engineRan': True}, result['own'])
        # Main's own acknowledgment is not a host-only tool.
        self.assertEqual((['work_side_handoff'], None), (result['handoff']['routed'], result['handoff']['deny']))

    def test_side_status_is_the_hosts_own_read(self):
        # Celly 2026-11-20: main's own work_side_status call reached the router
        # without a conversation identity ("conversation identity is unavailable").
        result = self.run_scenario('side-status-is-host-only')
        self.assertIn('called only by the Ciel Work host itself', result['deny'])
        self.assertFalse(result['engineRan'])
        self.assertEqual([], result['routed'])
        self.assertEqual({'result': 'own-ran'}, result['own'])

    def test_fork_spawn_without_connected_work_tools_says_to_restart(self):
        # Celly 2026-11-20: work_mode_set(false) then (true) in one session left
        # the Work tools unloaded; four P0 forks were denied with raw tool text.
        result = self.run_scenario('fork-spawn-when-work-tools-are-not-connected')
        self.assertFalse(result['engineRan'])
        self.assertIn('Work mode was turned on after this session launched', result['deny'])
        self.assertIn('restart_session', result['deny'])

    def test_pane_reappend_retry_is_shown_only_for_an_uncertain_reappend_and_needs_confirmation(self):
        result = self.run_scenario('side-reappend-pane-retry')
        # Only the uncertain re-append is offered; a live submitting one is not.
        self.assertEqual(1, result['buttons'])
        self.assertEqual(1, len(result['labels']))
        self.assertIn('q' * 11 + '…', result['labels'][0])
        self.assertEqual(0, result['afterCancel'])
        self.assertEqual(1, len(result['retries']))
        retry = result['retries'][0]
        self.assertEqual({'event_id': 'q' * 64, 'state': 'reappend_retry', 'reappend_ref': 'r' * 16, 'revision': 7,
                          'confirmed': True},
                         {key: retry[key] for key in ('event_id', 'state', 'reappend_ref', 'revision', 'confirmed')})
        self.assertIs(True, retry['_host']['internal'])
        self.assertFalse({'attempt_id', 'owner_nonce', 'conversation_id', 'receipt'} & set(retry))

    def test_a_review_reappend_released_elsewhere_is_reappended_once_by_the_running_host(self):
        result = self.run_scenario('review-reappend-released-elsewhere')
        # The append threw: nothing stored, the lease is uncertain, nothing retried.
        self.assertEqual({'appends': 0, 'lease': 'uncertain'}, result['stuck'])
        self.assertEqual({'prepares': 1}, result['beforeRelease'])
        # Released by a person elsewhere: the host asks again only after its
        # bounded re-check, then re-appends exactly once.
        self.assertEqual({'appends': 0, 'prepares': 1}, result['releasedBeforeRecheck'])
        self.assertEqual({'appends': 1, 'prepares': 2, 'reappended': 1}, result['after'])

    def test_pane_review_reappend_retry_is_shown_only_for_an_uncertain_one_and_needs_confirmation(self):
        result = self.run_scenario('review-reappend-pane-retry')
        self.assertEqual((1, ['Review re-append uncertain · L-stuck']), (result['buttons'], result['labels']))
        self.assertEqual(0, result['afterCancel'])
        self.assertEqual([{'event_id': 'L-stuck', 'state': 'review_reappend_retry', 'reappend_ref': 's' * 16,
                           'confirmed': True, '_host': {'internal': True}}],
                         [{key: retry[key] for key in ('event_id', 'state', 'reappend_ref', 'confirmed', '_host')}
                          for retry in result['retries']])

    def test_a_pane_retry_refused_by_the_host_gate_names_the_dashboard_and_rest(self):
        # team-lead ruling (keep the router's internal-host gate): a refused pane
        # retry must tell the person where else to retry.
        result = self.run_scenario('pane-retry-refused-by-host-gate')
        self.assertEqual(3, len(result['gate']), result)
        for toast in result['gate']:
            self.assertIn('Use the Work web dashboard Side tab or REST side-*-retry.', toast)
        self.assertEqual(['Main wake', 'Release notice', 'Reply (retained state preserved)'],
                         [toast.split(' retry refused')[0] for toast in result['gate']])
        # Any other refusal keeps the plain message.
        self.assertEqual(3, len(result['other']), result)
        for toast in result['other']:
            self.assertIn('retry not accepted; refresh and inspect.', toast)
            self.assertNotIn('dashboard', toast)

    def test_pane_notice_retry_is_shown_only_for_an_uncertain_notice_and_needs_confirmation(self):
        result = self.run_scenario('side-notice-pane-retry')
        self.assertEqual(1, result['buttons'])
        self.assertEqual(1, len(result['labels']))
        self.assertIn('u' * 11 + '…', result['labels'][0])
        self.assertEqual(0, result['afterCancel'])
        # With the exact attempt the pane showed (retry pins, team-lead defect 2).
        self.assertEqual([{'event_id': 'u' * 64, 'state': 'notice_retry', 'confirmed': True,
                           'attempt_ref': 'u' * 16, 'revision': 2}],
                         [{k: retry.get(k) for k in ('event_id', 'state', 'confirmed', 'attempt_ref', 'revision')}
                          for retry in result['retries']])
        for retry in result['retries']:
            self.assertFalse({'attempt_id', 'owner_nonce', 'receipt', 'text'} & set(retry))
            self.assertIs(True, retry['_host'].get('internal'))

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
        # Since session fork|fresh (db23560) the proof names the pending row's
        # own agent type: fork, or the plugin's registered fresh type.
        self.assertIn('not the side agent type', observed['exactGeneral']['deny'])
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
                self.assertIn('not the side agent type', attempts['generalPurpose']['deny'])
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

    @unittest.skip('retired 2026-11-18 (user): Stop never injects a message into main; the side checks at turn end instead')
    def test_time_limit_handoff_keeps_bounded_deadline_and_evidence(self):
        result = self.run_scenario('side-time-limit-handoff-bounded')
        text=result['context']
        self.assertIn('"kind":"time_limit"', text)
        self.assertIn('Complete before maintenance closes', text)
        self.assertIn('2026-11-11T03:00:00Z', text)
        self.assertIn('change ticket CHG-42', text)
        self.assertIn('n9', text)
        self.assertNotIn('SECRET-THREAD', text)
        self.assertNotIn('SECRET-SSE', text)
        self.assertIn('holds no event data',result['block'])
        self.assertNotIn('change ticket CHG-42',result['block'])

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

    def test_side_retry_after_side_model_failure_is_spawned_for_main_model(self):
        row = self.run_scenario('side-parent-model-retry-is-marked-for-the-gate')
        self.assertEqual(['Side SSE ' + 'e' * 12, 'Side SSE ' + 'd' * 12 + ' main-model'], row['spawned'])

    def test_side_status_delivers_failed_original_without_a_free_actor_slot(self):
        row = self.run_scenario('side-status-delivers-failed-original-without-free-actor-slot')
        self.assertEqual(1, row['appended'], row)
        self.assertTrue(row['carriesOriginal'], row)
        self.assertIn('work_side_wake:notice_appended', row['calls'])

    def test_failed_side_event_original_is_appended_to_main_once(self):
        row = self.run_scenario('side-failed-event-original-reaches-main')
        self.assertEqual(1, row['appended'], row)
        self.assertTrue(row['carriesOriginal'], row)
        self.assertTrue(row['recorded'], row)
        self.assertEqual(1, row['calls'].count('work_side_wake:notice_prepare'), row)

    def test_side_sends_immediate_ack_before_source_prefetch_and_fork(self):
        row = self.run_scenario('side-immediate-ack-before-prefetch-and-fork')
        self.assertTrue(row['ackBeforePrefetch'], row)
        self.assertIn('work_side_ack:prepare', row['order'])
        self.assertIn('walkie_send_message', row['order'])
        self.assertIn('work_side_ack:submitted', row['order'])

    def test_side_receipt_list_shows_message_then_metadata_on_two_lines(self):
        row = self.run_scenario('side-receipt-list-uses-message-then-metadata-two-lines')
        self.assertTrue(row['rows'], row)
        self.assertIn('GitHub', row['button'])
        self.assertIn('PR #94', row['button'])
        self.assertNotIn('com.ciel', row['button'])
        self.assertNotIn('message.created', row['button'])
        self.assertIn('com.ciel.walkie.support.message', row['meta'])
        self.assertIn('message.created', row['meta'])

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
        # The exact attempt the pane showed (retry pins, team-lead defect 3).
        self.assertEqual(('b' * 16, 2), (result['retries'][0].get('attempt_ref'), result['retries'][0].get('revision')))
        self.assertIs(True, result['retries'][0]['_host'].get('internal'))

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

    # Blocker 5 mod half (lead ruling G6): M1 the host records the person's
    # approval itself; M3 no credential in the result; M4 a model _host is never
    # forwarded; M5 the dialog names who asked.
    def test_approved_plan_is_recorded_by_the_host_with_an_attestation(self):
        result = self.run_scenario("execute-approval-attested")
        self.assertEqual([], result["engine"], "the model's own call must not go on")
        self.assertEqual({"result": "Plan approved; Work is in the executing phase."}, result["result"])
        self.assertEqual(1, len(result["executes"]))
        sent = result["executes"][0]
        self.assertEqual({"token": "host-token", "internal": True, "user_confirmed": True}, sent["_host"])
        self.assertEqual((7, True, "claude:S1"), (sent["expected_version"], sent["user_confirmed"], sent["session_id"]))
        self.assertFalse(result["resultHasToken"])
        self.assertEqual(["Approve this Work plan?", "", "Requested by: main"], result["asked"][0])

    def test_dialog_names_the_subagent_that_asked(self):
        result = self.run_scenario("execute-approval-attested", "Approve", "a1b2c3")
        self.assertEqual("Requested by: subagent a1b2c3", result["asked"][0][2])

    def test_declined_approval_records_nothing(self):
        result = self.run_scenario("execute-approval-attested", "Keep planning")
        self.assertEqual(([], []), (result["engine"], result["executes"]))
        self.assertEqual({"deny": "Work plan not approved; keep planning."}, result["result"])

    def test_auto_approval_forwards_without_model_flags(self):
        result = self.run_scenario("execute-auto-forwards-clean")
        self.assertEqual(1, len(result["engine"]))
        forwarded = result["engine"][0]
        self.assertNotIn("_host", forwarded)
        self.assertNotIn("user_confirmed", forwarded)
        self.assertEqual("claude:S1", forwarded["session_id"])

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

    @unittest.skip('retired 2026-11-18 (user): Stop never injects a message into main; the side checks at turn end instead')
    def test_display_refresh_does_not_drop_runnable_queued_work_at_stop(self):
        result = self.run_scenario("stop-display-refresh")
        self.assertTrue(result["block"], result)
        self.assertIn("call work_select with root_id n2", result["text"])

    @unittest.skip('retired 2026-11-18 (user): Stop never injects a message into main; the side checks at turn end instead')
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

    @unittest.skip('retired 2026-11-18 (user): Stop never injects a message into main; the side checks at turn end instead')
    def test_blocked_wait_includes_pickup_guidance(self):
        result = self.run_scenario("stop-discovery-blocked")
        self.assertIn("와키 미결 아이템", result["text"])
        self.assertIn("기존 블록 유지", result["text"])

    @unittest.skip('retired 2026-11-18 (user): Stop never injects a message into main; the side checks at turn end instead')
    def test_focused_item_without_a_runnable_step_continues_other_work(self):
        result = self.run_scenario("stop-focus-unrunnable")
        self.assertIn("The next item is n5", result["text"])
        self.assertIn("call work_select with root_id n3", result["text"])
        self.assertNotIn("stays held", result["text"])

    @unittest.skip('retired 2026-11-18 (user): Stop never injects a message into main; the side checks at turn end instead')
    def test_focus_no_longer_suppresses_discovery_when_nothing_runs(self):
        result = self.run_scenario("stop-discovery-focus")
        self.assertIn("Work 미결 아이템 탐색", result["text"])
        self.assertNotIn("focus only", result["text"])

    def test_b2_router_owner_process_gates_recovery_adoption_and_restart(self):
        # R2 B2 (backend mcp 9aeeb289; lead rulings: paths 2/4 act on own or gone
        # only; ruling D: path 3 holds only other_live). The fake router models
        # the B2 router; the restart-recovery tests above model a pre-B2 router.
        # (recovery sends to L1, turn-end reconciles of adopted leases L1+L2, restart_submitted for L2)
        expected = {"own": (1, 2, 1), "gone": (1, 2, 1), "unknown": (0, 0, 1), "other_live": (0, 0, 0)}
        for owner, (recovery, reconciles, restart) in expected.items():
            with self.subTest(owner=owner):
                result = self.run_scenario("parallel-owner-process-b2", owner)
                self.assertEqual((recovery, reconciles, restart),
                                 (result["recovery"], result["turnReconciles"], result["restart"]), result)
                self.assertEqual(1, len(result["hosts"]), result)
                self.assertTrue(result["hosts"][0].startswith("HOSTS: live "), result)

    def test_pre_b2_router_status_says_cross_process_protection_is_inactive(self):
        # Lead ruling (1): a status without session_attachments is a pre-B2 router.
        result = self.run_scenario("parallel-rework-launch", "same-no-row")
        self.assertIn("HOSTS: router predates B2 ownership; cross-process protection inactive", result["shown"])

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

    def test_native_observation_that_publishes_a_review_wakes_main_without_a_turn_end(self):
        # Andy n2505 2026-11-20: completion 08:54:18, listed idle 08:54:55,
        # review wake only at 09:05:23 after a main turn end.
        result = self.run_scenario("native-observation-publishes-review")
        self.assertTrue(result["ticked"], result)
        self.assertIn(["L1:idle"], result["observations"], result)
        self.assertEqual(["L1"], result["prepared"], result)
        self.assertEqual(1, sum("Ciel Work result is awaiting main review" in row and '"lease_id":"L1"' in row
                                for row in result["appended"]), result)

    def test_own_fork_absent_after_its_receipt_is_observed_terminal_without_a_restart(self):
        # Celly n1070 2026-11-20: receipt 10:42:48, last listed running 10:40:44,
        # absent from 10:47:14; the review opened only after Celly restarted.
        result = self.run_scenario("own-fork-absent-after-receipt")
        self.assertTrue(result["ticked"], result)
        self.assertEqual([{"event": "restart_submitted_terminal", "lease_id": "L1", "generation": 3,
                           "receipt_digest": "d" * 64, "absent": "fork-a", "complete": True}],
                         result["restart"], result)

    def test_work_parallel_run_starts_fork_without_model_agent_call(self):
        result = self.lifecycle(self.run_scenario("parallel-direct-spawn-started"))
        self.assertEqual(result["run"]["dispatch"], "started")
        self.assertEqual(result["run"]["agent_id"], "host-9")
        self.assertEqual(result["run"]["lease_id"], "L9")
        self.assertEqual(result["spawned"]["subagentType"], "fork")
        self.assertEqual(result["spawned"]["description"], "Work n227")
        self.assertIn('"lease_id":"L9"', result["spawned"]["prompt"])
        self.assertIn("work_parallel_report", result["spawned"]["prompt"])
        self.assertEqual([call["event"] for call in result["calls"]], ["reserve", "bind"])

    def test_gate_presence_probe_reaches_the_gate_while_work_is_planning_or_executing(self):
        # Real CC 2.1.323 runs this query through ciel-work's own tool.check
        # before the separate gate. The board phase must not change presence.
        for phase in ("planning", "executing"):
            with self.subTest(phase=phase):
                result = self.run_scenario("fork-gate-probe-through-own-check", phase)
                self.assertGreaterEqual(len(result["reachedGate"]), 1, result)
                self.assertGreaterEqual(result["reports"], 1, result)

    def test_first_spawn_reports_the_gate_presence_once(self):
        # G5 (backend 9a852b08): display only. The live probe answered, so the
        # report says loaded, version 1, with the single-use probe nonce, as a
        # host-internal event; one report per module, not one per probe.
        calls = self.run_scenario("parallel-direct-spawn-started")["calls"]
        reports = [call for call in calls if call.get("event") == "gate_presence"]
        self.assertEqual(1, len(reports), calls)
        self.assertEqual(("loaded", "1", True), (reports[0]["state"], reports[0]["gate_version"],
                                                 reports[0]["_host"]["internal"]))
        self.assertRegex(reports[0]["probe_ref"], r"^[0-9a-f]{32}$")

    def test_direct_spawn_through_own_hook_reserves_once_and_starts(self):
        result = self.lifecycle(self.run_scenario("parallel-direct-spawn-through-own-hook"))
        # Owned run 2026-11-09: the hook reserved the same item again, the router
        # refused it, and the fork never started at any cap.
        self.assertEqual(["reserve", "bind"], result["calls"])
        self.assertEqual("started", result["run"]["dispatch"])
        self.assertEqual("host-9", result["run"]["agent_id"])
        self.assertIn('"lease_id":"L9"', result["spawned"]["prompt"])

    def test_work_parallel_run_spawn_denial_releases_and_keeps_item(self):
        result = self.lifecycle(self.run_scenario("parallel-direct-spawn-denied-releases"))
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
        result = self.lifecycle(self.run_scenario("parallel-session-start-releases-orphan-reserved"))
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

    @unittest.skip('retired 2026-11-18 (user): Stop never injects a message into main; the side checks at turn end instead')
    def test_side_answer_is_reviewed_once_by_main_with_host_identity(self):
        result = self.run_scenario("side-review-stop-once")
        self.assertIn("Do not answer, ACK or repeat them", result["first"]["block"])
        self.assertIn("message:c:119649", result["first"]["block"])
        self.assertNotIn("block", result["second"])
        self.assertEqual("host-token", result["sent"][0]["_host"]["token"])
        self.assertEqual("S1", result["sent"][0]["session_id"].split(":")[-1])

    @unittest.skip('retired 2026-11-18 (user): Stop never injects a message into main; the side checks at turn end instead')
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

    @unittest.skip('retired 2026-11-18 (user): Stop never injects a message into main; the side checks at turn end instead')
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
        result = self.lifecycle(self.run_scenario("parallel-second-missing-exit-salvages-before-stop"))
        self.assertEqual(["host_salvage", "unreported"],
                         [event for event in result["calls"] if event != "native_observation"], result)
        self.assertEqual({"tool": "TaskStop", "task_id": "agent-old"}, result["tools"][0])
        self.assertEqual("unreported", result["lease"]["state"])

    def test_spawn_denial_releases_unbound_reservation(self):
        result = self.lifecycle(self.run_scenario("parallel-spawn-denied-releases-unbound"))
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
        result = self.lifecycle(self.run_scenario("parallel-waiting-releases-slot-and-sendmessage-reacquires"))
        self.assertFalse(result["afterWait"]["slot"])
        self.assertEqual("wait", result["afterWait"]["calls"][0])
        self.assertIn("native_observation", result["afterWait"]["calls"])
        self.assertTrue(result["resumed"])
        self.assertTrue(result["slot"])
        # Stage 3: a waiting worker reports only from running, so the coordinator
        # resume (waiting_input -> running) follows the reacquire before the send.
        self.assertEqual(result["calls"], ["reacquire", "resume"])
        self.assertEqual("running", result["state"])
        self.assertEqual(7, result["resume"]["plan_version"])

    def test_p0_sendmessage_records_the_native_delivery_on_the_exact_reacquire(self):
        result = self.run_scenario("p0-sendmessage-settles-reacquire")
        self.assertEqual(1, result["sends"], result)
        self.assertEqual([{"lease": "L1", "outcome": "sent", "internal": True, "exact": True}],
                         result["completions"], result)

    def test_p0_sendmessage_does_not_send_using_an_unsettled_prior_reacquire(self):
        result = self.run_scenario("p0-sendmessage-settles-reacquire", "old-open")
        self.assertEqual(0, result["sends"], result)
        self.assertIn("recovery", result["deny"], result)
        self.assertNotIn("no active slot", result["deny"], result)

    def test_pump_records_the_delivery_of_a_p0_parked_resume(self):
        result = self.run_scenario("parallel-pump-resumes-cleared-parked-worker", "p0")
        self.assertEqual(["agent-a"], result["sent"])
        self.assertEqual([{"lease": "L1", "outcome": "sent", "internal": True}], result["completions"])
        self.assertEqual({"state": "running", "slot": True}, result["l1"])

    def test_pump_rolls_back_a_refused_p0_parked_resume_once(self):
        result = self.run_scenario("parallel-pump-resumes-cleared-parked-worker", "p0-refused")
        self.assertEqual([{"lease": "L1", "outcome": "failed", "internal": True}], result["completions"])
        # The completion rolled the exact P0 slot back; no second release follows.
        self.assertNotIn("wait:L1", result["events"])
        self.assertEqual({"state": "waiting_input", "slot": False}, result["l1"])

    def test_pump_resumes_a_parked_worker_whose_wait_cleared(self):
        result = self.run_scenario("parallel-pump-resumes-cleared-parked-worker")
        self.assertIn("reacquire:L1", result["events"])
        self.assertIn("resume:L1", result["events"])
        self.assertLess(result["events"].index("reacquire:L1"), result["events"].index("resume:L1"))
        self.assertNotIn("reacquire:L2", result["events"])
        # A question-parked worker (no recorded wait was cleared) waits for main.
        self.assertNotIn("reacquire:L3", result["events"])
        self.assertEqual({"state": "waiting_input", "slot": False}, result["l3"])
        self.assertEqual(["agent-a"], result["sent"])
        self.assertIn("lease L1", result["text"])
        self.assertIn("not new execution approval", result["text"])
        self.assertEqual({"state": "running", "slot": True}, result["l1"])
        self.assertEqual({"state": "waiting_input", "slot": False}, result["l2"])

    def test_a_refused_parked_resume_is_recorded_for_main(self):
        result = self.run_scenario("parallel-pump-resumes-cleared-parked-worker", "refused")
        self.assertEqual(["agent-a"], result["sent"])
        self.assertTrue(any("main may resume it with SendMessage" in line for line in result["logs"]), result["logs"])
        # Not lost: parked again without a slot, and main is told once which worker to resume.
        self.assertEqual({"state": "waiting_input", "slot": False}, result["l1"])
        self.assertIn("wait:L1", result["events"])
        self.assertEqual(1, len(result["notices"]), result["notices"])
        self.assertIn("worker agent-a (lease L1, item n2)", result["notices"][0])
        self.assertIn("Resume it with SendMessage", result["notices"][0])

    def test_a_parked_worker_the_native_list_dropped_is_still_resumed(self):
        result = self.run_scenario("parallel-pump-resumes-cleared-parked-worker", "unlisted")
        self.assertEqual(["agent-a"], result["sent"])
        self.assertEqual({"state": "running", "slot": True}, result["l1"])
        self.assertEqual([], result["notices"])

    @unittest.skip('retired 2026-11-18 (user): Stop never injects a message into main; the side checks at turn end instead')
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
        # Idle main: payload appended once; the one prompt is wake-only and never
        # repeated for the same review.
        self.assertEqual(1, len(result["appended"]))
        self.assertEqual(1, len(result["submitted"]), result)
        wake = result["appended"][0]["message"]["content"][0]["text"]
        self.assertNotIn('"lease_id"', result["submitted"][0])
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
        self.assertEqual((1, 1, "appended"), (len(idle["prompts"]), len(idle["appended"]), idle["wakeState"]))
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
        self.assertEqual(1, len(result["appended"]), result)
        self.assertEqual("appended", result["wakeState"])
        self.assertEqual(["prepare", "record"], [row["operation"] for row in result["calls"] if row.get("event") == "review_wake"])

    def test_pump_rolls_forward_only_stored_blocked_decisions(self):
        result = self.run_scenario("parallel-roll-forward-selection")
        # awaiting_capacity and a blocked deciding saga; never undecided, ready or running rows.
        self.assertEqual(["LA", "LB"], result["rolled"])
        self.assertIn("Rework (waiting for slot)", result["shown"])
        self.assertIn("blocked paused: dispatch paused", result["shown"])
        self.assertIn("blocked stage_failed", result["shown"])

    def test_idle_refresh_rolls_forward_review_added_without_board_change(self):
        result = self.run_scenario("parallel-idle-roll-forward")
        self.assertEqual(1000, result["idleDelay"], result)
        self.assertEqual(5, result["boardVersion"], result)
        self.assertEqual(["LA"], result["rolled"], result)

    @unittest.skip('retired 2026-11-18 (user): Stop never injects a message into main; the side checks at turn end instead')
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
        result = self.lifecycle(self.run_scenario("parallel-rework-dispatch-started"))
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
                         [event for event in result["routerNonceEvents"] if str(event).startswith("rework_")])
        # R2 B2 contract (backend mcp 9aeeb289; lead ruling: targeted hostProcess
        # on backend's list): the host status read (no event), reserve_next and
        # native_observation carry the same nonce too.
        self.assertIn(None, result["routerNonceEvents"])
        self.assertIn("reserve_next", result["routerNonceEvents"])
        self.assertIn("native_observation", result["routerNonceEvents"])

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

    @unittest.skip('retired 2026-11-18 (user): Stop never injects a message into main; the side checks at turn end instead')
    def test_stop_continuation_still_yields_to_a_new_turn(self):
        self.assertFalse(self.run_scenario("stop-superseded")["block"])
        self.assertTrue(self.run_scenario("stop-continues")["block"])


if __name__ == "__main__":
    unittest.main()
