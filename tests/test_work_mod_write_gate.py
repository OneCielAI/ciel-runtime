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
        shutil.copyfile(HARNESS, cls.root / HARNESS.name)

    def run_scenario(self, name):
        done = subprocess.run([NODE, HARNESS.name, name], cwd=self.root, capture_output=True, text=True,
                              encoding="utf-8", timeout=60, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

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
            "router-throws": "router-call",
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

    def test_stopped_with_only_blocked_work_demands_recheck(self):
        result = self.run_scenario("stop-blocked-recheck")
        self.assertIn("re-verify before reporting no work", result["text"])
        self.assertIn("n92", result["text"])
        self.assertIn("Exact production freeze logs not identified", result["text"])
        self.assertIn("work_node_update", result["text"])
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
        self.assertIn("Execution lease: L9", result["spawned"]["prompt"])
        self.assertEqual([call["event"] for call in result["calls"]], ["reserve", "bind"])

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

    def test_session_start_releases_orphan_reserved_but_keeps_bound_worker(self):
        result = self.run_scenario("parallel-session-start-releases-orphan-reserved")
        self.assertEqual([call["event"] for call in result["calls"]], ["release"])
        self.assertEqual(result["calls"][0]["lease_id"], "L1")
        self.assertNotIn("L1", result["leases"])
        self.assertIn("L2", result["leases"])
        self.assertEqual(result["leases"]["L2"]["host_id"], "agent-live")

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
