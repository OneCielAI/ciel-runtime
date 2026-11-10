"""Behavior of the packaged ciel-work hook's Edit/Write gate under concurrent agents.

Runs the real register.js under Node with a scripted host (tests/work_mod_harness.mjs);
no Claude Code, router or network is involved.
"""
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

    def run_scenario(self, name):
        done = subprocess.run([NODE, HARNESS.name, name], cwd=self.root, capture_output=True, text=True,
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
        self.assertEqual(result["event"]["event"], "reconcile")
        self.assertEqual(result["event"]["_host"]["state"], "unknown")

    def test_parallel_ui_maps_each_item_to_its_fork_agent(self):
        result = self.run_scenario("parallel-assignment-labels")
        self.assertEqual(result["dispatch"]["description"], "Work n229 [ PAY-229 ] Add retry backoff")
        self.assertEqual(result["dispatch"]["dispatch"], "started")
        self.assertEqual(result["spawnedEvent"]["description"], result["dispatch"]["description"])
        self.assertEqual(result["spawned"]["agentId"], "host-9")
        self.assertIn("Assignments (item → fork agent): 3 lease(s), 3 holding a slot", result["shown"])
        self.assertIn("n227 [ PAY-227 ] Fix payment retry → agent work-alpha [host host-1]", result["shown"])
        self.assertIn("path: Product goal → Checkout phase → Fix payment retry", result["shown"])
        self.assertIn("n228 → agent (not launched yet)", result["shown"])
        self.assertIn(result["dispatch"]["name"] + " [host host-9]", result["shown"])

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

    def test_unreported_fork_is_stopped_and_free_slot_auto_dispatches_exact_assignment(self):
        result = self.run_scenario("parallel-unreported-stops-and-fills")
        self.assertEqual(["unreported", "reserve_next", "bind", "reserve_next"], result["calls"])
        self.assertEqual({"tool": "TaskStop", "task_id": "agent-old"}, result["tools"][0])
        self.assertEqual("/wt/n3", result["spawned"]["cwd"])
        self.assertIn('"note":"exact note"', result["spawned"]["prompt"])
        self.assertIn('"worker_token":"' + "b" * 64 + '"', result["spawned"]["prompt"])
        self.assertIn("Owned Work notification", result["consumed"]["consumed"])

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
        self.assertEqual(result["afterWait"], {"slot": False, "calls": ["wait"]})
        self.assertTrue(result["resumed"])
        self.assertTrue(result["slot"])
        self.assertEqual(result["calls"], ["reacquire"])

    def test_pause_after_no_progress_still_instructs_discovery(self):
        result = self.run_scenario("stop-pause-discovery")
        self.assertIn("auto continue paused", result["third"])
        self.assertIn("Work 미결 아이템 탐색", result["third"])
        self.assertIn("이 세션의 검색 수단", result["third"])

    def test_restart_recovery_resumes_same_worker_and_blocks_duplicates(self):
        result = self.run_scenario("parallel-recovery-context")
        self.assertIn('SendMessage({ to: \\"agent-old\\"', result["context"])
        self.assertNotIn("Work recovery", result["live"])
        self.assertTrue(result["duplicate"].startswith("deny: Work item n2 already has lease L1"), result)

    def test_restart_sendmessage_authorizes_deferred_exact_spawn_once(self):
        # Prody 2026-11-09: SendMessage returned, then agent.spawn arrived after
        # the old finally block had already deleted the resume authorization.
        result = self.run_scenario("parallel-restart-sendmessage-deferred-spawn")
        self.assertTrue(result["sent"], result)
        self.assertEqual("agent-new", result["spawned"], result)
        self.assertIn("no pending work_parallel_run dispatch", result["duplicate"])

    def test_terminal_worker_starts_independent_readonly_review(self):
        result = self.run_scenario("parallel-auto-review")
        self.assertTrue(result["released"])
        self.assertEqual(result["spawned"]["subagentType"], "fork")
        self.assertIn("independent read-only reviewer", result["spawned"]["prompt"])

    def test_stop_after_session_end_does_not_continue(self):
        self.assertFalse(self.run_scenario("stop-session-end")["block"])

    def test_stop_continuation_still_yields_to_a_new_turn(self):
        self.assertFalse(self.run_scenario("stop-superseded")["block"])
        self.assertTrue(self.run_scenario("stop-continues")["block"])


if __name__ == "__main__":
    unittest.main()
