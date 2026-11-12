"""The packaged ciel-work register.js against the real Work parallel backend.

tests/work_mod_real_backend_harness.mjs drives the real register.js hooks; its
router answers come from tests/work_mod_real_backend_server.py, which runs the
real work_parallel_mcp, ledger, coordination and finalization stores in a
temporary CIEL_RUNTIME_CONFIG_DIR with a temporary git repository. A restart
re-imports register.js, losing every in-memory mod map as a CLI process
restart does, while the backend's durable files remain.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support import work_mode_launch as w

NODE = shutil.which("node")
GIT = shutil.which("git")
HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent


@unittest.skipUnless(NODE and GIT, "node and git are required")
class WorkModRealBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory(prefix="work-mod-real-")
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
        for name in ("register.js", "side-intake.js"):
            shutil.copyfile(w.CLAUDE_MOD_DIR / "hooks" / name, cls.root / name)
        for name in ("work_mod_real_backend_harness.mjs", "work_mod_real_backend_server.py"):
            shutil.copyfile(HERE / name, cls.root / name)

    def run_scenario(self, name):
        with tempfile.TemporaryDirectory(prefix="work-real-" + name + "-") as scratch:
            env = {key: value for key, value in os.environ.items() if not key.startswith("CIEL_RUNTIME_")}
            env.update(CIEL_RUNTIME_CONFIG_DIR=str(Path(scratch) / "config"), WORK_REAL_ROOT=str(Path(scratch) / "work"),
                       WORK_SOURCE=str(SOURCE), PYTHON=sys.executable, PYTHONIOENCODING="utf-8")
            (Path(scratch) / "config").mkdir()
            done = subprocess.run([NODE, "work_mod_real_backend_harness.mjs", name], cwd=self.root, env=env,
                                  capture_output=True, text=True, encoding="utf-8", timeout=240, check=False)
        self.assertEqual(0, done.returncode, done.stderr)
        result = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertNotIn("error", result, result.get("trace") or result)
        return result

    def test_approve_runs_the_real_saga_to_finalized(self):
        result = self.run_scenario("approve")
        self.assertEqual(1, result["woken"])
        decided = json.loads(result["decision"]["result"])
        self.assertEqual(("approve", "finalized"), (decided["decision"], decided["state"]))
        # Main gets a bounded confirmation only.
        self.assertEqual({"decision", "lease_id", "item_id", "state", "finalization_version"}, set(decided))
        review = result["backend"]["reviews"][result["lease"]]
        self.assertEqual(("finalized", "acknowledged"), (review["state"], review["wake"]["state"]))
        self.assertEqual("done", result["backend"]["nodes"][result["item"]])
        self.assertEqual("done", result["backend"]["leases"][result["lease"]]["state"])

    def test_completions_killed_before_review_converge_after_restart(self):
        # run-20261111-105012: worker 1 reported, a native list still showed it
        # running, worker 2 reported, then the process died before any wake.
        result = self.run_scenario("completion-restart")
        first, second = result["approved"]["lease_id"], result["other"]["lease_id"]
        # Checkpoint A as recorded: one review pending with no wake attempt, the
        # other result has no review row, both leases submitted with receipts.
        checkpoint = result["checkpoint"]
        self.assertEqual([first], list(checkpoint["reviews"]))
        self.assertEqual(("pending", 0), (checkpoint["reviews"][first]["wake"]["state"],
                                          checkpoint["reviews"][first]["wake"]["attempts"]))
        self.assertEqual(("submitted", "submitted"), (checkpoint["leases"][first]["state"],
                                                      checkpoint["leases"][second]["state"]))
        self.assertEqual(("completion", "completion"), (checkpoint["runs"][first]["exit"]["kind"],
                                                        checkpoint["runs"][second]["exit"]["kind"]))
        # The receipt keeps the run terminal although a running row arrived after it.
        self.assertEqual(("terminal", "terminal"), (checkpoint["runs"][first]["state"],
                                                    checkpoint["runs"][second]["state"]))
        restarted = result["afterRestart"]
        self.assertEqual(("terminal", "terminal"), (restarted["runs"][first]["state"],
                                                    restarted["runs"][second]["state"]))
        # The fork absent after restart is observed terminal: its review exists.
        self.assertEqual(("pending", None), (restarted["reviews"][second]["state"],
                                             restarted["reviews"][second]["decision"]))
        # One idempotent observation per submitted lease per module load: process
        # 1's session.start sends n2 (already reviewed) and n4; process 2's,
        # after n2 is done, sends n4 only. Review rows and wakes are counted
        # separately below and do not grow.
        self.assertEqual(3, result["restartTerminals"])
        # A model-forged observation is refused by the mod and by the router.
        self.assertIn("recorded only by the Work host", result["forged"]["deny"])
        self.assertEqual(0, result["forgedRouterCalls"])
        self.assertIn("verified mod-internal restart host required", result["forgedBackend"].get("error", ""))
        # One durable wake per review, never repeated by a later restart.
        self.assertEqual(2, result["promptsAfterRestart"])
        self.assertEqual(2, result["wakePrepares"])
        self.assertEqual(2, result["promptsAfterSecondRestart"])
        for lease in (first, second):
            self.assertEqual(1, result["afterSecondRestart"]["reviews"][lease]["wake"]["attempts"])
        # One decision: approve reaches finalized and the run is archived.
        self.assertEqual(1, result["reviewDecisions"])
        decided = json.loads(result["decision"]["result"])
        self.assertEqual(("approve", "finalized"), (decided["decision"], decided["state"]), decided)
        final = result["afterDecision"]
        self.assertEqual("finalized", final["reviews"][first]["state"])
        self.assertTrue(all(final["reviews"][first]["stages"].values()))
        self.assertEqual("done", final["leases"][first]["state"])
        self.assertEqual("done", final["nodes"][result["approved"]["item_id"]])
        self.assertNotIn(first, final["runs"])
        self.assertIn(first, final["coordination_archive"])
        # The other result still waits for its own explicit main review.
        self.assertEqual(("pending", None), (final["reviews"][second]["state"], final["reviews"][second]["decision"]))
        self.assertEqual("submitted", final["leases"][second]["state"])

    def test_absent_running_worker_without_a_receipt_is_not_observed_terminal_after_restart(self):
        result = self.run_scenario("absent-no-receipt-restart")
        lease = result["lease"]
        self.assertEqual(0, result["restartTerminals"])
        self.assertEqual(result["before"]["leases"][lease], result["after"]["leases"][lease])
        self.assertEqual(("running", True), (result["after"]["leases"][lease]["state"],
                                             result["after"]["leases"][lease]["slot"]))
        self.assertIsNone(result["after"]["runs"][lease]["exit"])
        self.assertNotIn(result["after"]["runs"][lease]["state"], ("terminal", "handed_off"))
        self.assertEqual({}, result["after"]["reviews"])
        self.assertEqual(0, result["prompts"])

    def test_reviewed_terminal_result_with_mismatched_proofs_is_repaired_by_a_process_restart(self):
        result = self.run_scenario("proof-mismatch-restart")
        lease = result["lease"]
        self.assertNotEqual(result["seeded"]["ledger"], result["seeded"]["workspace"])
        before = result["before"]
        self.assertEqual(("pending", "terminal"), (before["reviews"][lease]["state"], before["runs"][lease]["state"]))
        # (a) session.start alone, in a new process: exactly one restart event,
        # and the proofs converge on the ledger's proof.
        self.assertEqual(1, result["restartAfterStart"])
        self.assertEqual(result["seeded"]["ledger"], result["proofsAfterStart"]["ledger"])
        self.assertEqual(result["proofsAfterStart"]["ledger"], result["proofsAfterStart"]["workspace"])
        self.assertEqual(1, result["promptsAfterStart"])
        calls = result["restartCalls"]
        self.assertEqual(result["restartTotal"], len(calls))
        # The repair rewrites this lease's workspace manifest only.
        repair = calls[0]
        self.assertIsNone(repair["error"])
        self.assertEqual(["workspace", "workspaces"],
                         sorted(k for k in repair["before"] if repair["before"][k] != repair["after"][k]))
        # (b) Each module load sends it once: process 1, process 2 and the reload.
        # With the proofs matched, those observations change no store's version
        # or on-disk bytes (measured right around the backend call).
        self.assertEqual(3, len(calls))
        for index, call in enumerate(calls[1:], 1):
            self.assertIsNone(call["error"], index)
            self.assertEqual(call["before"], call["after"], index)
        # No boundary changes the board, parallel, coordination, receipts,
        # finalization or workspace bytes; a turn end in the same module sends nothing.
        sent = {row["label"]: row["sent"] for row in result["boundaries"]}
        self.assertEqual({"turn.complete": 0, "turn.complete repeated": 0, "session.start process 2": 1,
                          "turn.complete process 2": 0, "turn.complete process 2 repeated": 0,
                          "session.start reload": 1, "turn.complete after reload": 0}, sent)
        for row in result["boundaries"]:
            for key in ("board", "parallel", "coordination", "receipts", "finalization", "workspace", "workspaces"):
                self.assertEqual(row["before"][key], row["after"][key], (row["label"], key))
            self.assertIsNotNone(row["after"]["workspace"], row["label"])
        # (c) Replays create no second review, no second wake, and no failure.
        self.assertEqual(1, result["promptsAfterReplay"])
        self.assertEqual(1, result["wakePreparesAfterReplay"])
        self.assertEqual([lease], list(result["afterReplay"]["reviews"]))
        self.assertEqual(result["afterStart"]["reviews"][lease]["wake"], result["afterReplay"]["reviews"][lease]["wake"])
        self.assertEqual([], result["restartErrors"])
        self.assertEqual(["process 1", "process 2", "reload", "after final"], [row["label"] for row in result["failures"]])
        for row in result["failures"]:
            self.assertEqual((False, [], []), (row["recent"], row["entries"], row["restart"]), row["label"])
        decided = json.loads(result["decision"]["result"])
        self.assertEqual(("approve", "finalized"), (decided["decision"], decided["state"]), decided)
        final = result["final"]
        self.assertEqual("done", final["leases"][lease]["state"])
        self.assertNotIn(lease, final["runs"])
        self.assertIn(lease, final["coordination_archive"])
        # (d) Finalized and archived: no later boundary sends it again.
        self.assertEqual(0, result["restartAfterFinal"])
        self.assertEqual(final["reviews"], result["afterFinal"]["reviews"])

    def test_rework_blocked_after_rebind_is_a_restart_no_op_until_the_stage_unblocks(self):
        result = self.run_scenario("rework-blocked-rebind")
        lease, replacement = result["lease"], result["replacement"]
        # The blocked state: decision recorded, workspace rebound, board stage failed,
        # predecessor still submitted.
        blocked = result["blocked"]["reviews"][lease]
        self.assertEqual(("rework", "deciding", "stage_failed"),
                         (blocked["decision"], blocked["state"], blocked["blocked"]["code"]))
        self.assertEqual({"accepted": True, "workspace": True, "board": False, "lease": False, "archive": False},
                         blocked["stages"])
        self.assertEqual("submitted", result["blocked"]["leases"][lease]["state"])
        self.assertEqual("rebound", result["blockedWorkspace"])
        # Every observation of it succeeds as a no-op: no store's version or bytes
        # change, including both workspace manifests.
        self.assertEqual([], result["restartErrors"])
        self.assertEqual(2, len(result["blockedCalls"]))
        for call in result["blockedCalls"]:
            self.assertIsNone(call["error"])
            self.assertEqual(call["before"], call["after"])
            self.assertEqual({lease, replacement}, set(call["after"]["workspaces"]))
        # Once per module load (two processes and one reload), never per turn end.
        self.assertEqual([("process 1 session.start", 1), ("process 1 turn.complete x2", 0),
                          ("process 2 session.start", 1), ("process 2 turn.complete x3", 0),
                          ("reload session.start", 1), ("reload turn.complete x2", 0),
                          ("unblocked turn.complete", 0), ("after ready process 3", 0)],
                         [(row["label"], row["sent"]) for row in result["segments"]])
        # The failure log stays empty in every module.
        self.assertEqual(["process 1", "process 2", "reload", "after ready"], [row["label"] for row in result["failures"]])
        for row in result["failures"]:
            self.assertEqual((False, [], []), (row["recent"], row["entries"], row["restart"]), row["label"])
        self.assertEqual(result["blocked"]["reviews"][lease]["stages"], result["stillBlocked"]["reviews"][lease]["stages"])
        self.assertEqual([], result["spawnsWhileBlocked"])
        # After the stage unblocks, the same decision reaches rework_ready.
        final = result["final"]
        self.assertEqual(("rework_ready", None), (final["reviews"][lease]["state"], final["reviews"][lease]["blocked"]))
        self.assertTrue(all(final["reviews"][lease]["stages"].values()))
        self.assertEqual("done", final["leases"][lease]["state"])
        self.assertEqual(["work-rework-" + replacement + "-a1"], result["spawns"])
        self.assertEqual("bound", result["afterReady"]["reviews"][lease]["launch"]["state"])

    def test_reload_keeps_the_process_nonce_so_only_the_manual_path_resolves(self):
        # Reload: same process, shared env (owned CC 2.1.322 probe 44e40778).
        result = self.run_scenario("nonce-reload")
        replacement = result["replacement"]
        first, second = ("work-rework-" + replacement + "-a1", "work-rework-" + replacement + "-a2")
        held = self.launch_of(result["afterReload"], replacement)
        self.assertEqual(("launch_intent", 1), (held["state"], held["attempt"]))
        self.assertEqual(([first], 0), (result["reloadSpawns"], result["reloadAbsent"]))
        final = self.launch_of(result["final"], replacement)
        self.assertEqual(("bound", 2), (final["state"], final["attempt"]))
        self.assertEqual(([first, second], 0, 1), (result["spawns"], result["absent"], result["manual"]))
        intents = [row["nonce"] for row in result["hostMarkers"] if row["event"] == "rework_launch_intent"]
        self.assertEqual(2, len(intents))
        self.assertEqual(1, len(set(intents)), "reload regenerated the process nonce")
        self.assertRegex(intents[0], r"^[0-9a-f]{64}$")

    def test_new_process_env_gets_a_new_nonce_and_absence_resolves(self):
        # Process restart: fresh env. Same scenario as the thrown-spawn test.
        result = self.run_scenario("intent-absent-restart")
        nonces = [row["nonce"] for row in result["hostMarkers"] if row["event"] == "rework_launch_intent"]
        self.assertEqual(2, len(nonces))
        self.assertNotEqual(nonces[0], nonces[1])
        self.assertEqual(1, result["absent"])
        statuses = [row["nonce"] for row in result["hostMarkers"] if row["event"] == "rework_launch_status"]
        self.assertTrue(statuses and None not in statuses)

    def test_env_failure_sends_no_intent_and_resolves_nothing_automatically(self):
        result = self.run_scenario("nonce-env-failure")
        replacement = result["replacement"]
        old = self.launch_of(result["final"], replacement)
        self.assertEqual(("launch_intent", 1), (old["state"], old["attempt"]))
        self.assertEqual((0, 0), (result["absent"], result["newIntents"]))
        self.assertEqual(["work-rework-" + replacement + "-a1"], result["spawns"])
        self.assertEqual("none", self.launch_of(result["final"], result["secondReplacement"])["state"])
        self.assertEqual("reserved", result["final"]["leases"][result["secondReplacement"]]["state"])
        # Status reads without a nonce stay unknown; none carries a value.
        late = [row for row in result["hostMarkers"] if row["event"] == "rework_launch_status"][-1]
        self.assertIsNone(late["nonce"])

    def test_legacy_intent_without_a_nonce_is_resolved_by_a_new_process_as_before(self):
        result = self.run_scenario("nonce-legacy")
        replacement = result["replacement"]
        legacy = self.launch_of(result["legacy"], replacement)
        self.assertEqual(("launch_intent", None), (legacy["state"], legacy["host_nonce"]))
        final = self.launch_of(result["final"], replacement)
        self.assertEqual(("bound", 2), (final["state"], final["attempt"]))
        self.assertEqual(1, result["absent"])
        self.assertEqual(["work-rework-" + replacement + "-a1", "work-rework-" + replacement + "-a2"],
                         result["spawns"])

    def assert_bind_race_reaches_review(self, result, recovery_label):
        predecessor, replacement = result["lease"], result["replacement"]
        steps = {row["label"]: row for row in result["steps"]}
        recovered = steps[recovery_label]
        # Recovered without any person action: launch and workspace are both bound.
        self.assertEqual(("bound", "agent-1"), (recovered["launch"]["state"], recovered["launch"]["agent"]))
        self.assertEqual(("ready", "agent-1"), (recovered["manifest"]["status"], recovered["manifest"]["host_id"]))
        # A second exact bind is a no-op for every store and the manifest.
        self.assertEqual([None], [row["error"] for row in result["idempotent"]])
        self.assertEqual([[]], [row["changed"] for row in result["idempotent"]])
        # The replacement then publishes its own review; the predecessor hide applies.
        self.assertEqual([[replacement, "pending"]], result["pendingBeforeApprove"])
        decided = json.loads(result["approve"]["result"])
        self.assertEqual(("approve", "finalized"), (decided["decision"], decided["state"]))
        self.assertEqual([], result["pendingAfterApprove"])
        final = result["steps"][-1]
        self.assertEqual({predecessor: "rework_ready", replacement: "finalized"}, final["reviews"])
        self.assertEqual(("done", "bound"), (final["lease"]["state"], final["launch"]["state"]))
        self.assertEqual([], result["reconcileErrors"])
        return steps

    def test_one_concurrent_finalization_write_during_rework_bind_is_retried_in_request(self):
        result = self.run_scenario("bind-launch-bound-race")
        self.assertEqual(1, len(result["raceAfterInitial"]["fired"]))
        # The first launch_bound CAS conflicted; the re-read retry succeeded in the same request.
        self.assertEqual(2, len(result["raceAfterInitial"]["calls"]))
        self.assertEqual([None], [row["error"] for row in result["bindResults"]])
        self.assertEqual(1, result["bindCalls"])
        self.assert_bind_race_reaches_review(result, "after rework dispatch and bind")

    def test_exhausted_rework_bind_is_replayed_at_the_next_boundary(self):
        result = self.run_scenario("bind-launch-bound-exhausted")
        # Every bounded attempt of every mod retry conflicted: ledger bound, launch and workspace not.
        initial = result["steps"][0]
        self.assertEqual(("running", "agent-1"), (initial["lease"]["state"], initial["lease"]["host_id"]))
        self.assertEqual("spawn_observed", initial["launch"]["state"])
        self.assertIsNone(initial["manifest"]["host_id"])
        self.assertEqual(3, result["bindsAfterInitial"])
        self.assertEqual(["Work finalization state changed; reread"] * 3, [row["error"] for row in result["bindResults"][:3]])
        # The next turn boundary replays the idempotent bind once and it succeeds.
        self.assertEqual([None], [row["error"] for row in result["bindResults"][3:]])
        self.assertEqual(4, result["bindCalls"])
        self.assertEqual(1, len(result["raceAfterRecovery"]["fired"]))
        self.assert_bind_race_reaches_review(result, "after boundary recovery")

    def test_exhausted_rework_bind_is_recovered_by_a_new_process(self):
        result = self.run_scenario("bind-launch-bound-restart")
        self.assertEqual("submitted", result["report"]["state"])
        steps = self.assert_bind_race_reaches_review(result, "after process restart (session.start)")
        # session.start's restart observation ran before the replayed bind and failed;
        # the next boundary's observation succeeded and published the review.
        self.assertEqual(["workspace host binding mismatch", None], [row["error"] for row in result["restartCalls"]])
        self.assertEqual("pending", steps["after turn.complete in the new process"]["reviews"][result["replacement"]])
        self.assertEqual(4, result["bindCalls"])

    def assert_person_request_settled(self, result, recovery_label):
        steps = {row["label"]: row for row in result["steps"]}
        # Exhausted: ledger bound, launch observed, workspace unbound; then the
        # web console's confirmed request for that exact launch.
        self.assertEqual({"state": "manual_requested"}, result["manual"])
        requested = steps["after the person requested resolution"]
        self.assertEqual(("manual_requested", "spawn_observed", ["manual_requested"]),
                         (requested["launch"]["state"], requested["launch"]["manual_from"],
                          requested["launch"]["audit"]))
        self.assertEqual(("running", "agent-1"), (requested["lease"]["state"], requested["lease"]["host_id"]))
        self.assertIsNone(requested["manifest"]["host_id"])
        errors = [row["error"] for row in result["bindResults"]]
        self.assertEqual(["Work finalization state changed; reread"] * 3, errors[:3])
        # One host bind replay settles the request; no later boundary sends another.
        self.assertEqual([None], errors[3:])
        self.assertEqual(4, result["bindCalls"])
        recovered = steps[recovery_label]
        self.assertEqual((None, ["manual_requested"]), (recovered["launch"]["manual_from"], recovered["launch"]["audit"]))
        self.assert_bind_race_reaches_review(result, recovery_label)
        return steps

    def test_person_request_on_an_exhausted_rework_bind_is_settled_at_the_next_boundary(self):
        result = self.run_scenario("bind-launch-bound-manual")
        steps = self.assert_person_request_settled(result, "after boundary recovery")
        self.assertEqual(4, steps["after report and idle turn.complete"]["binds"])

    def test_person_request_on_an_exhausted_rework_bind_is_settled_by_a_new_process(self):
        result = self.run_scenario("bind-launch-bound-manual-restart")
        self.assertEqual("submitted", result["report"]["state"])
        steps = self.assert_person_request_settled(result, "after process restart (session.start)")
        self.assertEqual(["workspace host binding mismatch", None], [row["error"] for row in result["restartCalls"]])
        self.assertEqual("pending", steps["after turn.complete in the new process"]["reviews"][result["replacement"]])

    def test_unresendable_launch_of_a_ledger_bound_lease_sends_nothing_at_four_boundaries(self):
        for variant, state in (("none", "none"), ("denied", "spawn_denied")):
            with self.subTest(state=state):
                result = self.run_scenario("bind-launch-bound-" + variant)
                self.assertEqual({"state": state}, result["forced"])
                unresendable = result["unresendable"]
                self.assertEqual((0, []), (unresendable["bindSends"], unresendable["newFailures"]),
                                 unresendable)
                final = result["steps"][-1]
                self.assertEqual((state, "running", "agent-1"),
                                 (final["launch"]["state"], final["lease"]["state"], final["lease"]["host_id"]))
                self.assertEqual(3, result["bindCalls"])

    def test_rework_predecessor_leaves_terminal_web_mod_and_model_review_views(self):
        result = self.run_scenario("rework-review-lifecycle")
        predecessor, replacement = result["lease"], result["replacement"]
        self.assertEqual("submitted", result["reported"])
        labels = [row["label"] for row in result["views"]]
        self.assertEqual(["rework bound and running", "replacement submitted, native still running",
                          "replacement review pending", "replacement finalized", "after a new process"], labels)
        # Before its own review exists, the replacement is still queued/running and
        # the predecessor remains the single Rework row in every consumer.
        for row in result["views"][:2]:
            self.assertEqual([[predecessor, "rework_ready"]], row["model"], row)
            self.assertEqual([[predecessor, "rework_ready"]], row["web"], row)
            self.assertIn("MAIN REVIEW (1)", row["head"], row)
            self.assertTrue(any(predecessor in line for line in row["text"]), row)
        # Once the replacement publishes a pending review, it is the only visible
        # row; the decided predecessor disappears from terminal, web and model.
        pending = result["views"][2]
        self.assertEqual([[replacement, "pending"]], pending["model"])
        self.assertEqual([[replacement, "pending"]], pending["web"])
        self.assertIn("MAIN REVIEW (1)", pending["head"])
        self.assertFalse(any(predecessor in line for line in pending["text"]), pending)
        # After approval, and again after a fresh process, none shows a main review.
        for row in result["views"][3:]:
            self.assertEqual(([], [], None), (row["model"], row["web"], row["head"]), row)
            self.assertFalse(any(predecessor in line or replacement in line for line in row["text"]), row)
        decided = json.loads(result["approve"]["result"])
        self.assertEqual(("approve", "finalized"), (decided["decision"], decided["state"]))
        # Stored history remains: both finalization rows still exist.
        self.assertEqual({predecessor, replacement}, set(result["final"]["reviews"]))
        self.assertEqual(("rework_ready", "rework", "bound"),
                         (result["final"]["reviews"][predecessor]["state"],
                          result["final"]["reviews"][predecessor]["decision"],
                          result["final"]["reviews"][predecessor]["launch"]["state"]))

    def test_harness_output_carries_nonce_digests_only(self):
        result = self.run_scenario("nonce-reload")
        launch = self.launch_of(result["final"], result["replacement"])
        values = [launch["host_nonce"]] + [row["observer_nonce"] for row in launch["audit"]]
        values += [row["nonce"] for row in result["hostMarkers"]]
        present = [value for value in values if value is not None]
        self.assertTrue(present)
        for value in present:
            self.assertRegex(value, r"^[0-9a-f]{64}$")

    @staticmethod
    def launch_of(inspected, replacement):
        return next(r for r in inspected["reviews"].values() if r["replacement_lease_id"] == replacement)["launch"]

    @staticmethod
    def resume_slots(inspected, replacement):
        """Slot-holding resume leases for this item: a leak would show two."""
        item = inspected["leases"][replacement]["item_id"]
        return [key for key, row in inspected["leases"].items()
                if row["role"] == "resume" and row["item_id"] == item and row["slot"]]

    def assert_internal_launch_markers(self, result):
        markers = result["hostMarkers"]
        self.assertTrue(markers)
        for row in markers:
            self.assertEqual((True, False), (row["internal"], row["fork"]), row)
        nonces = {row["nonce"] for row in markers if row["event"] == "rework_launch_intent"}
        self.assertTrue(nonces and None not in nonces, markers)

    def test_rework_launch_is_intent_observed_bound_and_restart_never_spawns_again(self):
        result = self.run_scenario("rework-restart")
        decided = json.loads(result["decision"]["result"])
        self.assertEqual(("rework", "rework_ready"), (decided["decision"], decided["state"]))
        self.assertNotIn("dispatch", decided)
        self.assertNotIn("worker_token", result["decision"]["result"])
        replacement = result["replacement"]
        name = "work-rework-" + replacement + "-a1"
        self.assertEqual([name], result["spawnsAfterDecision"])
        lease = result["afterDecision"]["leases"][replacement]
        self.assertEqual(("running", "agent-1", name), (lease["state"], lease["host_id"], lease["dispatch_name"]))
        self.assertEqual("bound", self.launch_of(result["afterDecision"], replacement)["state"])
        # Restart replayed: still exactly one spawn, one dispatch and one intent.
        self.assertEqual([name], result["spawns"])
        self.assertEqual((1, 1, 1), (result["dispatches"], result["intents"], result["observed"]))
        self.assertEqual("agent-1", result["afterRestart"]["leases"][replacement]["host_id"])
        self.assert_internal_launch_markers(result)

    def test_thrown_spawn_never_listed_is_held_in_process_then_resolved_by_a_later_process(self):
        result = self.run_scenario("intent-absent-restart")
        replacement = result["replacement"]
        first, second = ("work-rework-" + replacement + "-a1", "work-rework-" + replacement + "-a2")
        crashed = self.launch_of(result["afterCrash"], replacement)
        self.assertEqual(("launch_intent", 1, first), (crashed["state"], crashed["attempt"], crashed["dispatch_name"]))
        before = result["afterCrash"]["leases"][replacement]["report_hash"]
        # Same process, even after a session.start reset: held, not resolved.
        held = self.launch_of(result["sameProcess"], replacement)
        self.assertEqual(("launch_intent", 1), (held["state"], held["attempt"]))
        self.assertEqual(before, result["sameProcess"]["leases"][replacement]["report_hash"])
        self.assertEqual(([first], 0, 1), (result["sameProcessSpawns"], result["sameProcessAbsent"],
                                           result["sameProcessDispatches"]))
        # A later process proves the exact name absent: one fresh fork, one bind.
        final = self.launch_of(result["final"], replacement)
        self.assertEqual(("bound", 2, "agent-2"), (final["state"], final["attempt"], final["agent_id"]))
        self.assertIn("absent", [row["kind"] for row in final["audit"]])
        self.assertEqual(([first, second], 1, 2), (result["spawns"], result["absent"], result["dispatches"]))
        lease = result["final"]["leases"][replacement]
        self.assertEqual(("running", "agent-2", second), (lease["state"], lease["host_id"], lease["dispatch_name"]))
        self.assertEqual([replacement], self.resume_slots(result["final"], replacement))
        # The first attempt's fork arriving late is stopped and never bound.
        self.assertEqual(["agent-late"], result["stopped"])
        self.assertEqual("agent-2", result["afterLate"]["leases"][replacement]["host_id"])
        self.assertEqual("bound", self.launch_of(result["afterLate"], replacement)["state"])
        self.assertIn("consumed", result["lateNotice"])
        self.assert_internal_launch_markers(result)
        # The intent nonce of each process is constant, and the two processes differ.
        nonces = [row["nonce"] for row in result["hostMarkers"] if row["event"] == "rework_launch_intent"]
        self.assertEqual(2, len(set(nonces)), result["hostMarkers"])

    def test_observed_but_unbound_fork_gone_after_restart_is_resolved_by_its_exact_agent_id(self):
        result = self.run_scenario("observed-absent-restart")
        replacement = result["replacement"]
        observed = self.launch_of(result["afterObserved"], replacement)
        self.assertEqual(("spawn_observed", "agent-1"), (observed["state"], observed["agent_id"]))
        self.assertIsNone(result["afterObserved"]["leases"][replacement]["host_id"])
        final = self.launch_of(result["final"], replacement)
        self.assertEqual(("bound", 2, "agent-2"), (final["state"], final["attempt"], final["agent_id"]))
        self.assertIn("absent", [row["kind"] for row in final["audit"]])
        self.assertEqual(1, result["absent"])
        self.assertEqual(["work-rework-" + replacement + "-a1", "work-rework-" + replacement + "-a2"], result["spawns"])
        self.assertEqual([replacement], self.resume_slots(result["final"], replacement))
        self.assert_internal_launch_markers(result)

    def test_web_request_with_the_fork_listed_late_is_refused_as_agent_present_and_bound(self):
        result = self.run_scenario("manual-late-present")
        replacement = result["replacement"]
        review_lease = next(key for key, row in result["final"]["reviews"].items()
                            if row["replacement_lease_id"] == replacement)
        before = result["webBefore"][review_lease]
        self.assertEqual({"state": "launch_intent", "attempt": 1}, {k: before[k] for k in ("state", "attempt")})
        self.assertRegex(before["launch_ref"], r"^[0-9a-f]{32}$")
        self.assertNotIn("work-rework-", json.dumps(result["webBefore"]))
        self.assertEqual(("manual_requested", before["launch_ref"], review_lease),
                         (result["request"]["state"], result["request"]["launch_ref"], result["request"]["lease_id"]))
        # Idempotent retry of the same ref.
        self.assertEqual(("manual_requested", before["launch_ref"]),
                         (result["retry"]["state"], result["retry"]["launch_ref"]))
        # The old ref is refused once the launch is bound (and the present restore minted another ref).
        self.assertIn("error", result["stale"], result["stale"])
        self.assertRegex(result["stale"]["error"], "stale rework launch reference|not unresolved")
        self.assertNotIn("launch_ref", result["webAfter"][review_lease])
        final = self.launch_of(result["final"], replacement)
        self.assertEqual(("bound", "agent-late"), (final["state"], final["agent_id"]))
        self.assertEqual(["manual_requested", "present"], [row["kind"] for row in final["audit"]][-2:])
        self.assertEqual((["work-rework-" + replacement + "-a1"], 1), (result["spawns"], result["present"]))
        self.assertEqual("agent-late", result["final"]["leases"][replacement]["host_id"])
        self.assertTrue(result["webAfter"][review_lease]["agent_present"])
        self.assertEqual([replacement], self.resume_slots(result["final"], replacement))

    def test_pane_resolution_needs_confirmation_then_resolves_a_same_process_launch(self):
        result = self.run_scenario("manual-pane-absent")
        replacement = result["replacement"]
        self.assertEqual("launch_intent", self.launch_of(result["stuck"], replacement)["state"])
        self.assertEqual(0, result["requestsAfterDecline"])
        self.assertEqual(2, len(result["asks"]))
        self.assertIn("Resolve stuck rework launch?", result["asks"][0]["question"])
        self.assertEqual(["Request resolution", "Keep waiting"], result["asks"][0]["choices"])
        self.assertEqual((1, 1), (result["requests"], result["manual"]))
        final = self.launch_of(result["final"], replacement)
        self.assertEqual(("bound", 2, "agent-2"), (final["state"], final["attempt"], final["agent_id"]))
        self.assertIn("manual", [row["kind"] for row in final["audit"]])
        self.assertEqual(["work-rework-" + replacement + "-a1", "work-rework-" + replacement + "-a2"], result["spawns"])
        self.assertEqual([replacement], self.resume_slots(result["final"], replacement))
        old = result["beforeRequest"][next(iter(result["beforeRequest"]))]["launch_ref"]
        self.assertRegex(old, r"^[0-9a-f]{32}$")
        # Re-dispatched after the request: the first attempt's ref is refused.
        self.assertIn("error", result["stale"], result["stale"])
        self.assertRegex(result["stale"]["error"], "stale rework launch reference|not unresolved")
        self.assertIn("Request resolution", result["asks"][1]["choices"])

    def test_requested_resolution_with_a_listed_fork_is_resolved_by_session_start_alone(self):
        result = self.run_scenario("manual-present-restart")
        replacement = result["replacement"]
        self.assertEqual("manual_requested", result["request"]["state"])
        final = self.launch_of(result["final"], replacement)
        self.assertEqual(("bound", "agent-late"), (final["state"], final["agent_id"]), result["sessionStartEvents"])
        self.assertEqual("present", final["audit"][-1]["kind"])
        self.assertEqual((["work-rework-" + replacement + "-a1"], 1), (result["spawns"], result["present"]))
        self.assertEqual("agent-late", result["final"]["leases"][replacement]["host_id"])
        # The session.start pump still ran after hydration (it fills: reserve_next).
        self.assertIn("reserve_next", result["sessionStartEvents"], result["sessionStartEvents"])

    def test_listed_fork_after_a_thrown_spawn_is_reconciled_by_exact_name_after_restart(self):
        result = self.run_scenario("intent-listed-restart")
        replacement = result["replacement"]
        name = "work-rework-" + replacement + "-a1"
        self.assertIn(self.launch_of(result["afterCrash"], replacement)["state"], ("launch_intent", "spawn_observed"))
        self.assertIsNone(result["afterCrash"]["leases"][replacement]["host_id"])
        # A model-facing status (no host token) never shows the unbound name.
        self.assertNotIn(name, json.dumps(result["modelStatus"]))
        final = self.launch_of(result["final"], replacement)
        self.assertEqual(("bound", 1, "agent-started"), (final["state"], final["attempt"], final["agent_id"]))
        self.assertEqual(([name], 1), (result["spawns"], result["dispatches"]))
        self.assertEqual(("running", "agent-started"), (result["final"]["leases"][replacement]["state"],
                                                         result["final"]["leases"][replacement]["host_id"]))

    def test_model_supplied_host_marker_never_reaches_a_launch_event(self):
        result = self.run_scenario("forged-host")
        self.assertIn("deny", result["result"])
        self.assertEqual(0, result["after"])
        # rework_dispatch from the model: router saw agent_id only, so it refused.
        self.assertIn("deny", result["dispatch"])
        self.assertIn("verified coordinator fork capability required", result["dispatch"]["deny"])
        # Main forging review_wake / review_roll_forward is refused before the router.
        self.assertIn("recorded only by the Work host", result["forgedWake"]["deny"])
        self.assertIn("recorded only by the Work host", result["forgedRoll"]["deny"])
        self.assertEqual(0, result["forgedRouterCalls"])
        # And the router refuses the shape a forwarded model call would carry.
        self.assertIn("verified mod-internal review host required", result["backendWake"].get("error", ""), result)
        self.assertIn("error", result["backendRoll"], result)
        self.assertIn("verified mod-internal launch host required", result["backendForkLaunch"].get("error", ""), result)

    def test_explicit_spawn_denial_allows_exactly_one_retry(self):
        result = self.run_scenario("deny-once")
        replacement = result["replacement"]
        review = next(r for r in result["afterDeny"]["reviews"].values() if r["replacement_lease_id"] == replacement)
        self.assertEqual("spawn_denied", review["launch"]["state"])
        self.assertEqual(1, result["denied"])
        self.assertEqual(["work-rework-" + replacement + "-a1", "work-rework-" + replacement + "-a2"], result["spawns"])
        final = next(r for r in result["final"]["reviews"].values() if r["replacement_lease_id"] == replacement)
        self.assertEqual(("bound", 2), (final["launch"]["state"], final["launch"]["attempt"]))
        self.assertEqual("agent-2", result["final"]["leases"][replacement]["host_id"])

    def test_awaiting_capacity_rolls_forward_at_an_event_boundary_then_dispatches(self):
        result = self.run_scenario("capacity")
        decided = json.loads(result["decision"]["result"])
        self.assertEqual("awaiting_capacity", decided["state"])
        waiting = result["waiting"]["reviews"][result["lease"]]
        self.assertEqual(("awaiting_capacity", "paused"), (waiting["state"], waiting["blocked"]["code"]))
        self.assertGreaterEqual(result["rollForwards"], 1)
        final = result["final"]["reviews"][result["lease"]]
        self.assertEqual(("rework_ready", "bound", None), (final["state"], final["launch"]["state"], final["blocked"]))
        self.assertEqual(1, len(result["spawns"]))

    def test_review_wake_is_prepared_and_recorded_once_across_restart(self):
        result = self.run_scenario("wake")
        self.assertEqual((1, 0), (result["first"]["prompts"], result["first"]["appended"]))
        wake = result["first"]["backend"]["reviews"][result["lease"]]["wake"]
        self.assertEqual(("queued", 1), (wake["state"], wake["attempts"]))
        self.assertEqual((1, 0), (result["second"]["prompts"], result["second"]["appended"]))
        self.assertEqual((1, 1), (result["prepares"], result["records"]))
        self.assertEqual(1, result["second"]["backend"]["reviews"][result["lease"]]["wake"]["attempts"])
        # Delivery is not a decision: Stop stays gated.
        self.assertIn("work_parallel_item_review", result["stop"]["block"])

    def test_busy_main_wake_is_appended_and_not_resent_after_restart(self):
        result = self.run_scenario("wake-busy")
        self.assertEqual((0, 1), (result["prompts"], result["appended"]))
        self.assertEqual("appended", result["backend"]["reviews"][result["lease"]]["wake"]["state"])
        self.assertEqual({"prompts": 0, "appended": 1}, result["afterRestart"])


if __name__ == "__main__":
    unittest.main()
