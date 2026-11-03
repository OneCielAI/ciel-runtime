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
        (stub / "index.js").write_text("export const atom = (key, init) => ({ key, init })\n"
                                       "export const read = async () => null\n"
                                       "export const update = async () => {}\n", encoding="utf-8")
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
