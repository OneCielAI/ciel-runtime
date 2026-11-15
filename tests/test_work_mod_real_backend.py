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
BETA = "57dbfb3"


def beta_root(scratch):
    """A private extract of 57dbfb3's ciel_runtime_support for a real beta process.

    WORK_BETA_ROOT names a prepared extract (owned test trees outside git);
    otherwise it is archived from this checkout's repository.
    """
    prepared = os.environ.get("WORK_BETA_ROOT")
    if prepared:
        return prepared
    import io
    import tarfile
    try:
        top = subprocess.run([GIT, "-C", str(SOURCE), "rev-parse", "--show-toplevel"], check=True,
                             capture_output=True, text=True).stdout.strip()
        archive = subprocess.run([GIT, "-C", top, "archive", "--format=tar", BETA, "ciel_runtime_support"],
                                 check=True, capture_output=True).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise unittest.SkipTest("beta source unavailable: " + BETA) from error
    root = Path(scratch) / ("beta-" + BETA)
    with tarfile.open(fileobj=io.BytesIO(archive)) as handle:
        handle.extractall(root, filter="data")
    return str(root)


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

    def run_scenario(self, name, beta=False):
        with tempfile.TemporaryDirectory(prefix="work-real-" + name + "-") as scratch:
            env = {key: value for key, value in os.environ.items() if not key.startswith("CIEL_RUNTIME_")}
            env.update(CIEL_RUNTIME_CONFIG_DIR=str(Path(scratch) / "config"), WORK_REAL_ROOT=str(Path(scratch) / "work"),
                       WORK_SOURCE=str(SOURCE), PYTHON=sys.executable, PYTHONIOENCODING="utf-8")
            if beta:
                env["WORK_BETA_ROOT"] = beta_root(scratch)
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

    def test_missing_fork_gate_starts_no_work_fork_and_changes_nothing_durable(self):
        result = self.run_scenario("fork-gate-missing")
        decided = json.loads(result["decision"]["result"])
        self.assertEqual("rework", decided["decision"], decided)
        self.assertEqual(([], 0, 0), (result["spawns"], result["dispatches"], result["intents"]), result)
        self.assertIn("FORK GATE: Work fork gate not loaded: parallel execution disabled", result["statusText"])
        self.assertIn("fork-gate · Work fork gate not loaded: parallel execution disabled", result["statusText"])
        # G5: reported to the router on change only (start plus no change after).
        self.assertEqual(1, result["presenceEvents"], result)
        gate = result["hostForkGate"]
        self.assertEqual(("not_loaded", "missing", None, "Work fork gate not loaded: parallel execution disabled"),
                         (gate["state"], gate["reason"], gate["gate_version"], gate["message"]), gate)
        self.assertFalse(result["modelHasForkGate"])

    def test_fork_gate_of_another_version_is_a_reported_mismatch(self):
        result = self.run_scenario("fork-gate-mismatch")
        self.assertEqual(([], 0, 0), (result["spawns"], result["dispatches"], result["intents"]), result)
        self.assertIn("gate answered version 2, expected 1", result["statusText"])
        gate = result["hostForkGate"]
        self.assertEqual(("not_loaded", "mismatch", "2", "Work fork gate not loaded: parallel execution disabled"),
                         (gate["state"], gate["reason"], gate["gate_version"], gate["message"]), gate)
        self.assertEqual(1, result["presenceEvents"], result)

    def test_present_fork_gate_lets_the_rework_fork_start(self):
        result = self.run_scenario("fork-gate-present")
        self.assertEqual(1, len(result["spawns"]), result)
        self.assertEqual((1, 1), (result["dispatches"], result["intents"]), result)
        self.assertIn("FORK GATE: ciel-work-gate 1 answered at ", result["statusText"])
        self.assertNotIn("Work fork gate not loaded", result["statusText"])
        # G3 (backend 0ed166ea): what the fork gate reads for the bound replacement.
        row = result["hostRow"]
        self.assertEqual(("running", True), (row["state"], row["has_key"]), row)
        self.assertEqual(result["replacementWorkspace"]["path"], row["workspace_path"], result)
        self.assertTrue(result["replacementWorkspace"]["exists"], result["replacementWorkspace"])
        self.assertGreaterEqual(result["modelRowCount"], 1, result)
        self.assertFalse(result["modelRowHasKey"], "the model view never carries workspace_path")
        gate = result["hostForkGate"]
        self.assertEqual(("loaded", None, "1", None), (gate["state"], gate["reason"], gate["gate_version"],
                                                       gate["message"]), gate)
        self.assertEqual(1, result["presenceEvents"], result)
        self.assertFalse(result["modelHasForkGate"])

    def test_a_model_supplied_host_field_is_never_forwarded(self):
        result = self.run_scenario("model-host-field-dropped")
        self.assertEqual([{"tool": "engine:work_status", "host": None},
                          {"tool": "engine:work_parallel_report", "host": None}], result["routed"])

    # Blocker 1 (lead ruling): a wake-driven decision citing exactly what the
    # wake text showed lands on its first attempt, with no "reread" refusal,
    # although the wake's own prepare/record writes moved the version.
    def assert_wake_pin_shown(self, result):
        self.assertEqual(1, result["wakeCount"], result)
        self.assertIsInstance(result["shownVersion"], int, result)
        self.assertRegex(result["shownPin"] or "", r"^rp1:[0-9a-f]{64}$")
        self.assertTrue(result["reviewCall"], result)
        # The stale version the blocker is about: the wake moved it on.
        self.assertGreater(result["statusVersion"], result["shownVersion"], result)
        self.assertEqual(result["shownPin"], result["statusPin"], result)

    def assert_landed_first(self, result, pin):
        self.assertNotIn("deny", result["decision"], result["decision"])
        decided = json.loads(result["decision"]["result"])
        self.assertEqual(("approve", "finalized"), (decided["decision"], decided["state"]), decided)
        self.assertEqual([{"lease_id": result["lease"], "expected_finalization_version": result["shownVersion"],
                           "expected_review_pin": pin}], result["sent"])
        self.assertEqual("finalized", result["review"]["state"], result["review"])

    def test_wake_decision_with_the_shown_pin_lands_first_time(self):
        result = self.run_scenario("wake-pin-model-pin")
        self.assert_wake_pin_shown(result)
        self.assert_landed_first(result, result["shownPin"])

    def test_wake_decision_without_a_pin_gets_the_pin_the_host_projected(self):
        result = self.run_scenario("wake-pin-host-attached")
        self.assert_wake_pin_shown(result)
        self.assert_landed_first(result, result["shownPin"])

    def test_malformed_model_pin_is_replaced_by_the_projected_pin(self):
        result = self.run_scenario("wake-pin-bad-format")
        self.assert_wake_pin_shown(result)
        self.assert_landed_first(result, result["shownPin"])

    def test_a_pin_the_router_never_projected_is_refused_and_writes_nothing(self):
        result = self.run_scenario("wake-pin-stale-pin")
        self.assert_wake_pin_shown(result)
        self.assertIn("review changed; reread", result["decision"]["deny"], result["decision"])
        self.assertEqual("pending", result["review"]["state"], result["review"])

    def test_after_restart_the_shown_pin_still_lands_first_time(self):
        result = self.run_scenario("wake-pin-model-pin-after-restart")
        self.assert_wake_pin_shown(result)
        self.assert_landed_first(result, result["shownPin"])

    def test_after_restart_without_a_pin_the_version_rule_applies(self):
        # Lead ruling: a restarted host has no projected (lease, version) pair to
        # attach, so the plain version rule decides; the wake's version is stale.
        result = self.run_scenario("wake-pin-host-attached-after-restart")
        self.assert_wake_pin_shown(result)
        self.assertEqual([{"lease_id": result["lease"], "expected_finalization_version": result["shownVersion"],
                           "expected_review_pin": None}], result["sent"])
        self.assertIn("reread", result["decision"]["deny"], result["decision"])
        self.assertEqual("pending", result["review"]["state"], result["review"])

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

    def test_legacy_intent_without_a_nonce_is_refused_automatically_and_resolved_by_the_person(self):
        # Contract changed by backend's R2 B2 ruling (mcp 9aeeb289; lead ruling
        # 2026-11-13, "pre-nonce intent refused as owner_unknown on the automatic
        # path; manual resolve still allowed"). Before B2 a new process resolved a
        # pre-nonce intent automatically.
        result = self.run_scenario("nonce-legacy")
        replacement = result["replacement"]
        legacy = self.launch_of(result["legacy"], replacement)
        self.assertEqual(("launch_intent", None), (legacy["state"], legacy["host_nonce"]))
        automatic = self.launch_of(result["automatic"], replacement)
        self.assertEqual(("launch_intent", 1), (automatic["state"], automatic["attempt"]))
        self.assertGreaterEqual(result["absent"], 1)
        self.assertEqual(1, result["absentRefusals"])
        self.assertEqual(["work-rework-" + replacement + "-a1"], result["spawnsBeforeManual"])
        # The person's pane request, then the host's manual resolution: exactly one attempt 2.
        self.assertEqual((1, 1), (result["requests"], result["manual"]))
        final = self.launch_of(result["final"], replacement)
        self.assertEqual(("bound", 2), (final["state"], final["attempt"]))
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
        # The current backend can settle the unresolved launch while observing the
        # submitted fork at session.start. Older compatible bytes needed the mod's
        # following bind replay, so both measured sequences remain valid here.
        errors = [row["error"] for row in result["restartCalls"]]
        self.assertIn(errors, (["workspace host binding mismatch", None], [None]))
        self.assertEqual("pending", steps["after turn.complete in the new process"]["reviews"][result["replacement"]])
        self.assertIn(result["bindCalls"], (3, 4))

    def test_exhausted_rework_bind_after_a_noninteractive_restart_is_reviewed_at_the_first_boundary(self):
        result = self.run_scenario("bind-launch-bound-restart-noninteractive")
        predecessor, replacement = result["lease"], result["replacement"]
        steps = {row["label"]: row for row in result["steps"]}
        self.assertEqual({"state": "submitted"}, result["report"])
        # A non-interactive session.start runs no pump: the mod sends no bind there.
        started = steps["after process restart (session.start)"]
        self.assertEqual(3, started["binds"])
        # By the end of the first turn.complete the launch and workspace are bound,
        # the absent, submitted fork is observed and its review is published, and
        # main is woken for it once. The restart observation succeeds in that
        # boundary at the latest: the only one that may fail before it is
        # session.start's, which ran before any bind replay. (A backend whose
        # observation repairs the bind itself succeeds at session.start already.)
        first = steps["after turn.complete in the new process"]
        mismatch = "workspace host binding mismatch"
        errors = [row["error"] for row in result["restartCalls"]]
        # Woken once: one appended review payload and one wake-only prompt.
        woken = {key: result["wakesAfterFirst"][key] - result["wakesAfterStart"][key] for key in ("appended", "prompts")}
        self.assertEqual(("bound", "pending", {"appended": 1, "prompts": 1}),
                         (first["launch"]["state"], first["reviews"].get(replacement), woken),
                         {"restartCalls": errors, "step": first})
        self.assertIn(errors, ([mismatch, None], [None]))
        self.assertEqual(("ready", "agent-1"), (first["manifest"]["status"], first["manifest"]["host_id"]))
        self.assertLessEqual(first["binds"], 4)
        # A second boundary sends nothing more and observes nothing again.
        second = steps["after a second turn.complete in the new process"]
        self.assertEqual((first["binds"], "pending"), (second["binds"], second["reviews"].get(replacement)))
        self.assertEqual(result["restartCalls"], result["restartCallsAfterSecond"])
        self.assertEqual([[replacement, "pending"]], result["pendingBeforeApprove"])
        decided = json.loads(result["approve"]["result"])
        self.assertEqual(("approve", "finalized"), (decided["decision"], decided["state"]))
        self.assertEqual({predecessor: "rework_ready", replacement: "finalized"}, result["steps"][-1]["reviews"])
        self.assertEqual([], result["reconcileErrors"])

    def test_exhausted_rework_bind_of_an_already_idle_fork_is_reviewed_at_the_first_boundary(self):
        result = self.run_scenario("bind-launch-bound-reported-first")
        replacement = result["replacement"]
        initial = result["steps"][0]
        self.assertEqual(("running", "agent-1", "spawn_observed", 3),
                         (initial["lease"]["state"], initial["lease"]["host_id"], initial["launch"]["state"],
                          result["bindsAfterInitial"]))
        self.assertEqual({"state": "submitted"}, result["report"])
        # One boundary: the bind is replayed first, then the idle fork is reconciled.
        first = result["steps"][1]
        self.assertEqual("after report and idle turn.complete", first["label"])
        self.assertEqual(([None], "bound", "pending"),
                         ([row["error"] for row in result["bindResults"]][3:], first["launch"]["state"],
                          first["reviews"].get(replacement)), first)
        self.assertEqual(("ready", "agent-1"), (first["manifest"]["status"], first["manifest"]["host_id"]))
        self.assertEqual([], result["reconcileErrors"])
        self.assertEqual([[replacement, "pending"]], result["pendingBeforeApprove"])
        decided = json.loads(result["approve"]["result"])
        self.assertEqual(("approve", "finalized"), (decided["decision"], decided["state"]))
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
        # The current backend's submitted-terminal observation can settle the
        # launch at restart without a mod bind replay; compatible older bytes send
        # one successful replay. No later boundary sends another.
        self.assertIn(errors[3:], ([None], []))
        self.assertIn(result["bindCalls"], (3, 4))
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
        self.assertIn([row["error"] for row in result["restartCalls"]],
                      (["workspace host binding mismatch", None], [None]))
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

    def assert_stopped_policy_still_replays_the_bind(self, variant, policy):
        result = self.run_scenario("bind-launch-bound-" + variant)
        replacement = result["replacement"]
        steps = {row["label"]: row for row in result["steps"]}
        # Exhausted before the person stopped dispatch: ledger bound, launch and workspace not.
        initial = result["steps"][0]
        self.assertEqual(("running", "agent-1", "spawn_observed"),
                         (initial["lease"]["state"], initial["lease"]["host_id"], initial["launch"]["state"]))
        self.assertIsNone(initial["manifest"]["host_id"])
        self.assertEqual(3, result["bindsAfterInitial"])
        self.assertEqual(policy, result["policy"])
        recovered = steps["after boundary recovery"]
        reviewed = steps["after report and idle turn.complete"]
        errors = [row["error"] for row in result["bindResults"]]
        # Mechanism and outcome in one line: the bind sent at the boundary, the
        # launch after it, and the replacement's review after its report and idle.
        self.assertEqual(([None], "bound", "pending"),
                         (errors[3:], recovered["launch"]["state"], reviewed["reviews"].get(replacement)),
                         {"reconcileErrors": result["reconcileErrors"], "failures": reviewed["failures"]})
        # One boundary under the stopped policy replays the bind once: launch and workspace are bound.
        self.assertEqual(["Work finalization state changed; reread"] * 3 + [None], errors, recovered)
        self.assertEqual(("bound", "agent-1"), (recovered["launch"]["state"], recovered["launch"]["agent"]))
        self.assertEqual(("ready", "agent-1"), (recovered["manifest"]["status"], recovered["manifest"]["host_id"]))
        # The fork's report and idle then publish its review without waiting for resume.
        self.assertEqual({"state": "submitted"}, result["report"])
        self.assertEqual("pending", reviewed["reviews"].get(replacement), reviewed)
        self.assertEqual([], result["reconcileErrors"])
        self.assertEqual([[replacement, "pending"]], result["pendingBeforeApprove"])
        # The policy is still stopped, and nothing was dispatched under it.
        self.assertEqual(policy, result["policyAtReview"])
        self.assertEqual(["work-rework-" + replacement + "-a1"], result["spawnsAtReview"])
        self.assertEqual(4, result["bindCalls"])
        return result

    def test_paused_policy_still_replays_an_exhausted_rework_bind(self):
        result = self.assert_stopped_policy_still_replays_the_bind("paused", {"enabled": True, "paused": True})
        decided = json.loads(result["approve"]["result"])
        self.assertEqual(("approve", "finalized"), (decided["decision"], decided["state"]))

    def test_disabled_policy_still_replays_an_exhausted_rework_bind(self):
        self.assert_stopped_policy_still_replays_the_bind("disabled", {"enabled": False, "paused": False})

    def assert_refused_report_binds_the_fork_at_once(self, variant):
        result = self.run_scenario("report-refused-" + variant)
        replacement = result["replacement"]
        name = "work-rework-" + replacement + "-a1"
        steps = {row["label"]: row for row in result["steps"]}
        # The dispatch's one bind never reached the backend: the fork runs unbound.
        lost = steps["after the rework dispatch lost its bind"]
        self.assertEqual(("reserved", None, "spawn_observed", "agent-1", 1),
                         (lost["lease"]["state"], lost["lease"]["host_id"], lost["launch"]["state"],
                          lost["launch"]["agent"], lost["bindSends"]))
        # Its first report is refused, and that refusal alone binds it: no boundary passes.
        first, second = result["reports"]
        self.assertEqual((True, "worker lease is not active"), (first["refused"], first["answer"]))
        bound = steps["after the first report"]
        self.assertEqual((("running", "agent-1"), ("bound", "agent-1"), 2, (False, "submitted")),
                         ((bound["lease"]["state"], bound["lease"]["host_id"]),
                          (bound["launch"]["state"], bound["launch"]["agent"]), bound["bindSends"],
                          (second["refused"], second["answer"])), result["steps"])
        # The fork's next report succeeded, so its idle publishes the review; no second fork.
        final = steps["after the idle turn.complete"]
        self.assertEqual("pending", final["reviews"].get(replacement), final)
        self.assertEqual(([name], 2), (final["spawns"], final["bindSends"]))
        return result

    def test_refused_report_of_an_unbound_rework_fork_binds_it_at_once(self):
        result = self.assert_refused_report_binds_the_fork_at_once("unbound")
        self.assertEqual({"enabled": True, "paused": False}, result["policyAtEnd"])

    def test_refused_report_of_an_unbound_rework_fork_binds_it_under_a_paused_policy(self):
        result = self.assert_refused_report_binds_the_fork_at_once("unbound-paused")
        self.assertEqual(({"enabled": True, "paused": True}, {"enabled": True, "paused": True}),
                         (result["policy"], result["policyAtEnd"]))

    def assert_stopped_policy_binds_a_started_fork(self, variant, policy):
        result = self.run_scenario("stopped-policy-bind-lost-" + variant)
        replacement = result["replacement"]
        name = "work-rework-" + replacement + "-a1"
        steps = {row["label"]: row for row in result["steps"]}
        lost = steps["after the rework dispatch lost its bind"]
        self.assertEqual(("reserved", None, "spawn_observed", "agent-1", 1),
                         (lost["lease"]["state"], lost["lease"]["host_id"], lost["launch"]["state"],
                          lost["launch"]["agent"], lost["bindSends"]))
        self.assertEqual(policy, result["policy"])
        # One boundary under the stopped policy binds the fork that already runs.
        bound = steps["after one boundary under the stopped policy"]
        self.assertEqual((("running", "agent-1"), ("bound", "agent-1"), 2),
                         ((bound["lease"]["state"], bound["lease"]["host_id"]),
                          (bound["launch"]["state"], bound["launch"]["agent"]), bound["bindSends"]), bound)
        # Its report and idle then publish the review; nothing was dispatched.
        self.assertEqual({"state": "submitted"}, result["report"])
        final = steps["after the report and the idle turn.complete"]
        self.assertEqual("pending", final["reviews"].get(replacement), final)
        self.assertEqual(([name], 1, policy), (final["spawns"], final["dispatches"], result["policyAtEnd"]))

    def test_paused_policy_still_binds_a_started_rework_fork_whose_bind_was_lost(self):
        self.assert_stopped_policy_binds_a_started_fork("paused", {"enabled": True, "paused": True})

    def test_disabled_policy_still_binds_a_started_rework_fork_whose_bind_was_lost(self):
        self.assert_stopped_policy_binds_a_started_fork("disabled", {"enabled": False, "paused": False})

    def test_paused_policy_records_an_absent_launch_but_starts_no_new_attempt(self):
        result = self.run_scenario("stopped-policy-absent-paused")
        replacement = result["replacement"]
        first, second = ("work-rework-" + replacement + "-a1", "work-rework-" + replacement + "-a2")
        steps = {row["label"]: row for row in result["steps"]}
        thrown = steps["after the thrown spawn"]
        self.assertEqual(("launch_intent", 1, [first], 1),
                         (thrown["launch"]["state"], thrown["launch"]["attempt"], thrown["spawns"], thrown["dispatches"]))
        self.assertEqual({"enabled": True, "paused": True}, result["policy"])
        # The later process records the absence; under the paused policy neither it
        # nor a further boundary dispatches attempt 2.
        for label in ("after a new process under the stopped policy", "after one boundary under the stopped policy"):
            row = steps[label]
            # (An absent launch is stored as spawn_denied: the one state that allows a retry.)
            self.assertEqual(("spawn_denied", 1, "reserved", None, [first], 1),
                             (row["launch"]["state"], row["absent"], row["lease"]["state"], row["lease"]["host_id"],
                              row["spawns"], row["dispatches"]), row)
        # Resume: the next boundary starts exactly one new attempt and binds it.
        resumed = steps["after the policy resumed"]
        self.assertEqual((("bound", 2, "agent-2"), ("running", "agent-2"), [first, second], 2, 1),
                         ((resumed["launch"]["state"], resumed["launch"]["attempt"], resumed["launch"]["agent"]),
                          (resumed["lease"]["state"], resumed["lease"]["host_id"]), resumed["spawns"],
                          resumed["dispatches"], resumed["absent"]), resumed)

    def test_a_coalesced_pump_pass_does_not_repeat_the_resend_turn_complete_tried(self):
        result = self.run_scenario("coalesced-resend")
        self.assertTrue(result["pumpHeld"])
        # Every bind conflicts, so each resend is three bounded sends and none succeeds.
        self.assertEqual((["Work finalization state changed; reread"], "spawn_observed"),
                         (result["bindErrors"], result["launch"]))
        self.assertEqual(3, result["bindsAfterDispatch"])
        # turn.complete tries the resend once while the worker report's pump is held.
        self.assertEqual(6, result["bindsAfterTurnComplete"])
        # The held pump's own pass resends once (the report's boundary). The pass
        # that turn.complete's coalesced call adds does not resend a second time.
        self.assertEqual(9, result["bindsAfterPump"])

    def test_no_boundary_lists_native_agents_more_often_than_before_the_stale_fork_check(self):
        result = self.run_scenario("list-calls")
        self.assertEqual(("submitted", "finalized"), (result["reported"], result["approve"]))
        # $.agent.list() calls per boundary, measured on register.js ea7768f5
        # (before the pump checked for stale rework forks at every pass).
        before = [("session.start, one result awaiting review", 3),
                  ("rework decision: dispatch and bind", 1),
                  ("turn.complete, replacement running", 3),
                  ("worker report (pump only), replacement running", 1),
                  ("turn.complete, replacement submitted and idle", 2),
                  ("turn.complete, replacement awaiting review", 2),
                  ("approve decision", 0),
                  ("turn.complete, every lease ended", 2),
                  ("worker report (pump only), every lease ended", 0)]
        self.assertEqual([label for label, _ in before], [label for label, _ in result["listCalls"]])
        for (label, limit), (_, calls) in zip(before, result["listCalls"]):
            with self.subTest(boundary=label):
                self.assertLessEqual(calls, limit)

    def test_one_lease_whose_reconcile_is_refused_does_not_stop_the_others_or_the_pump(self):
        # A deleted worktree no longer makes the backend refuse an idle reconcile
        # (Item 2 publishes a stranded row instead), so the refusal here is the
        # router's own, injected for exactly one lease.
        result = self.run_scenario("turn-complete-refused-lease")
        self.assertIsNone(result["brokenPath"])
        # The mod reaches the refused lease first, so the healthy one comes after the refusal.
        self.assertEqual(["stranded", "healthy"], result["order"])
        steps = {row["label"]: row for row in result["steps"]}
        started = steps["after session.start"]
        self.assertEqual(({"stranded": "submitted", "healthy": "submitted"},
                          {"stranded": None, "healthy": None}),
                         (started["leases"], started["reviews"]))
        first = steps["after the first turn.complete"]
        # The stranded lease's idle reconcile is refused by the real backend ...
        self.assertEqual(["stranded"], [row[0] for row in first["errors"]], first)
        self.assertTrue(first["errors"][0][1])
        # ... and the healthy lease is still reconciled, reviewed and stopped, the
        # pump still runs (its auto-fill attempt) and main is woken for the review.
        self.assertEqual(({"stranded": None, "healthy": "pending"}, 2, ["healthy"], ["healthy"]),
                         (first["reviews"], first["reconciles"], first["stopped"], first["appendedReviews"]), first)
        # One idle wake-only prompt per review (continue-sse's delivery design).
        self.assertEqual(1, first["prompts"], first)
        self.assertGreaterEqual(first["fills"], 1, first)
        self.assertEqual("submitted", first["leases"]["stranded"])
        # The refusal is shown once, with the lease it belongs to.
        self.assertEqual(1, len(first["ring"]), first)
        self.assertIn(result["strandedLease"], first["ring"][0])
        self.assertIn(first["errors"][0][1], first["ring"][0])
        # The next boundary tries the stranded lease again, pumps again, and
        # neither repeats the same failure line nor wakes main a second time.
        second = steps["after the second turn.complete"]
        self.assertEqual(["stranded"], [row[0] for row in second["errors"]], second)
        self.assertGreaterEqual(second["fills"], 1, second)
        self.assertEqual((first["ring"], 0, ["healthy"]), (second["ring"], second["wakes"], second["stopped"]), second)

    STRANDED_WORKSPACE_KEYS = {"path", "branch", "head", "unavailable", "reason"}

    def assert_stranded_row(self, row, kind):
        stranded = row["stranded"]
        self.assertEqual({"kind": kind, "approve": "refused", "rework": "fresh_workspace",
                          "counts_against_rework_limit": False},
                         {key: stranded[key] for key in ("kind", "approve", "rework", "counts_against_rework_limit")}, row)
        self.assertTrue(isinstance(stranded["reason"], str) and 0 < len(stranded["reason"]) <= 300, row)
        # stranded-v1 contract: an undecided stranded row is phase pending, state pending.
        self.assertEqual(("pending", "pending"), (stranded.get("phase"), row["state"]), row)
        self.assertEqual({"code": kind, "message": stranded["reason"]}, row["blocked"], row)
        # A placeholder is never a fact: no dirty flag, changed files or diff.
        self.assertEqual(self.STRANDED_WORKSPACE_KEYS, set(row["workspace"]), row)
        self.assertIs(True, row["workspace"]["unavailable"], row)

    def assert_sidecar_storage(self, result, kind, recovered):
        # stranded-v1 (interim pre-supersede checkpoint): the row exists in the
        # sidecar and no schema-5 finalization row exists for that lease.
        storage = result["strandedStorage"]
        self.assertTrue(storage["sidecarAvailable"], storage)
        self.assertIsNone(storage["schema5"], storage)
        row = storage["sidecar"]
        self.assertIsNotNone(row, storage)
        self.assertEqual(kind, row["kind"], row)
        if recovered:
            self.assertEqual(result["replacement"], row["replacement_lease_id"], row)
            self.assertEqual(row["generation"] + 1, row["replacement_generation"], row)
            self.assertIn(row["state"], ("deciding", "recovered"), row)

    def assert_stranded_views(self, result, lease, kind, broken_path):
        # /work-mode parallel: refused approve and a fresh-workspace rework, no placeholder facts.
        text = result["statusText"]
        self.assertIn("approve refused", text)
        self.assertIn("rework starts in a fresh workspace", text)
        self.assertIn("does not count against the rework limit", text)
        # Main's review message carries the same, and the stranded facts it may rely on.
        self.assertEqual(1, len(result["appended"]), result["appended"])
        appended = result["appended"][0]
        self.assertIn('"stranded":{"kind":"' + kind + '"', appended)
        self.assertIn("approve is refused", appended)
        self.assertIn("rework starts in a fresh workspace", appended)
        for absent in ('"dirty"', '"changed_files"', '"diff_stat"'):
            self.assertNotIn(absent, appended)
        # The web card: the derived state without the reason text or any path.
        web = [row for row in result["web"] if row["lease_id"] == lease]
        self.assertEqual(1, len(web), result["web"])
        # The sidecar's pending phase is exported as a fixed word (stranded-v1).
        self.assertEqual({"kind": kind, "approve": "refused", "rework": "fresh_workspace",
                          "counts_against_rework_limit": False, "phase": "pending"}, web[0]["stranded"], web[0])
        self.assertEqual({"code": kind}, web[0]["blocked"], web[0])
        self.assertFalse({"dirty", "changed_files", "changed_count", "diff_stat"} & set(web[0].get("workspace", {})), web[0])
        self.assertNotIn(broken_path, json.dumps(result["web"]))

    def test_broken_worktree_publishes_one_stranded_row_beside_the_healthy_review(self):
        result = self.run_scenario("turn-complete-broken-lease")
        self.assertEqual(["stranded", "healthy"], result["order"])
        steps = {row["label"]: row for row in result["steps"]}
        first = steps["after the first turn.complete"]
        # No reconcile is refused and no failure is shown: both results are published.
        self.assertEqual(([], []), (first["errors"], first["ring"]), first)
        # The healthy result is a schema-5 review; the stranded one is stored in
        # the stranded-v1 sidecar only (see assert_sidecar_storage below).
        self.assertEqual({"stranded": None, "healthy": "pending"}, first["reviews"], first)
        self.assertEqual(["healthy", "stranded"], sorted(first["stopped"]), first)
        # Each review is appended to main once, with one wake-only prompt each.
        self.assertEqual(["healthy", "stranded"], first["appendedReviews"], first)
        self.assertEqual(2, first["prompts"], first)
        rows = {row["role"]: row for row in first["pending"]}
        self.assertEqual({"stranded", "healthy"}, set(rows), first["pending"])
        self.assert_stranded_row(rows["stranded"], "workspace_unavailable")
        self.assertIsNone(rows["healthy"]["stranded"], rows["healthy"])
        # Repeated boundaries: the same single row, no second wake, no failure.
        second = steps["after the second turn.complete"]
        self.assertEqual(([], [], 0), (second["errors"], second["ring"], second["wakes"]), second)
        self.assertEqual(first["pending"], second["pending"])
        self.assert_stranded_views(result, result["strandedLease"], "workspace_unavailable", result["brokenPath"])
        # Approve is refused and writes nothing; rework recovers in a fresh worktree.
        self.assertIn("approve refused; rework creates a fresh workspace", result["approveStranded"]["deny"])
        self.assertEqual(0, result["finalAfterApprove"])
        reworked = json.loads(result["reworkStranded"]["result"])
        self.assertEqual(("rework", "workspace_unavailable", True), (reworked["decision"], reworked.get("recovery"),
                                                                    reworked.get("fresh_workspace")), reworked)
        self.assertIsNotNone(result["replacement"])
        self.assertNotEqual(result["brokenPath"], result["replacementWorkspace"]["path"])
        self.assertTrue(result["replacementWorkspace"]["exists"], result["replacementWorkspace"])
        self.assertEqual("finalized", json.loads(result["approveHealthy"]["result"])["state"])
        self.assert_sidecar_storage(result, "workspace_unavailable", recovered=True)

    def test_presented_review_lists_never_carry_ended_superseded_rows(self):
        # Static (module-internal lists the harness cannot render): the pane list,
        # the observatory rows and the card's MAIN REVIEW all go through one filter;
        # ended rows appear only in the card's ended section (asserted behaviorally
        # in the superseded scenario below).
        source = (w.CLAUDE_MOD_DIR / "hooks" / "register.js").read_text(encoding="utf-8")
        for marker in ("function parallelPresentedReviews(rows) {",
                       "reviews: parallelPresentedReviews(parallelReviews(observatoryParallel)).slice(0, 8)",
                       "observatoryReviews = parallelPresentedReviews(queue.rows).slice(0, 24)",
                       "const reviews = parallelPresentedReviews(parallelReviews(state))"):
            self.assertIn(marker, source)
        self.assertEqual(0, source.count("queue.rows.slice("), "an unfiltered review list remains")

    def test_superseded_stranded_row_is_an_ended_audit_row_after_a_real_beta_redispatch(self):
        # checkpoint3: a real 57dbfb3 process (which never reads the sidecar)
        # redispatches the item of a no-report stranded row.
        result = self.run_scenario("stranded-superseded", beta=True)
        lease, beta = result["lease"], result["betaLease"]
        before = result["before"]
        self.assertEqual([{"state": "pending", "phase": "pending"}], before["rows"], before)
        self.assertGreaterEqual(before["appended"], 1, before)
        self.assertTrue(before["stopShows"], before)
        self.assertIn("ok", result["beta"][0], result["beta"])
        self.assertIsInstance(beta, str)
        after = result["after"]
        # Host read: nothing decidable left in review.pending; one ended row in review.ended.
        self.assertEqual([], after["pending"], after)
        self.assertEqual(1, len(after["rows"]), after)
        row = after["rows"][0]
        self.assertEqual("reserved", row["stranded"].get("superseding_state"), row)
        # The model's own status (no host authentication) carries no superseded row and no ended list.
        self.assertEqual((0, False, False), (after["model"]["pendingSuperseded"], after["model"]["hasEnded"],
                                             after["model"]["mentionsLease"]), after["model"])
        self.assertEqual(("superseded", "superseded", beta), (row["state"], row["stranded"]["phase"],
                                                              row["stranded"]["superseding_lease_id"]), row)
        self.assertNotIn("reason", row["stranded"], row)
        self.assertIsNone(row["workspace"], row)
        self.assertIsNone(row["blocked"], row)
        # Never a review for main: no wake, no append, no Stop gate.
        self.assertEqual((0, 0, False), (after["appendedForLease"], after["promptsForLease"], after["stopShows"]), after)
        # The person sees it only in the ended area, as the superseding lease and a time.
        text = result["statusText"]
        lines = [line for line in text.split("\n") if lease in line]
        self.assertEqual(1, len(lines), text)
        self.assertRegex(lines[0], r"^  Stranded result superseded by Work lease " + beta[:8]
                         + r" · superseding lease reserved · observed \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ · ")
        self.assertIn("ENDED · SUPERSEDED STRANDED RESULTS (1)", text)
        self.assertNotIn(lease, text.split("ENDED · SUPERSEDED", 1)[0])
        self.assertEqual([], result["web"]["reviews"], result["web"])
        ended = result["web"]["superseded"]
        self.assertEqual(1, len(ended), ended)
        self.assertEqual({"lease_id", "item_id", "priority", "kind", "phase", "superseding_lease_id", "observed_at",
                          "superseding_state"}, set(ended[0]) - {"item_title"}, ended)
        self.assertEqual("reserved", ended[0]["superseding_state"], ended)
        self.assertEqual(("no_report", "superseded", beta), (ended[0]["kind"], ended[0]["phase"],
                                                            ended[0]["superseding_lease_id"]), ended)
        # Main's decisions are refused with a reread and write nothing.
        for key in ("approve", "rework"):
            self.assertIn("stranded result superseded by lease " + beta + "; reread", result[key]["deny"], result[key])
        self.assertEqual(0, result["finalDelta"])
        self.assertFalse(result["stopAfterRefusal"])
        # The superseding execution ends (its unbound reservation is released for
        # real): the result stays an ended row, visible with the winner's state,
        # and never becomes a decidable review or a wake again.
        self.assertIn("ok", result["betaRelease"][0], result["betaRelease"])
        gone = result["afterWinnerGone"]
        self.assertIsNone(gone["winner"], gone)
        self.assertEqual([{"state": "superseded", "winner": "absent"}], gone["ended"], gone)
        self.assertEqual((0, False, False), (gone["model"]["pendingSuperseded"], gone["model"]["hasEnded"],
                                             gone["model"]["mentionsLease"]), gone["model"])
        self.assertEqual([], [row for row in gone["rows"] if row["state"] == "pending"], gone)
        self.assertEqual((0, 0, False, []), (gone["appendedForLease"], gone["promptsForLease"], gone["stopShows"],
                                             gone["webReviews"]), gone)
        self.assertEqual(1, len(gone["webEnded"]), gone)
        self.assertEqual(("superseded", beta, "absent"), (gone["webEnded"][0]["phase"],
                                                          gone["webEnded"][0]["superseding_lease_id"],
                                                          gone["webEnded"][0].get("superseding_state")), gone)
        # The console card lists it too, under the ended heading, with the winner's state as a fixed word.
        card = gone["statusText"]
        self.assertIn("ENDED · SUPERSEDED STRANDED RESULTS (1)", card)
        lines = [line for line in card.split(chr(10)) if lease in line]
        self.assertEqual(1, len(lines), card)
        self.assertTrue(lines[0].startswith("  Stranded result superseded by Work lease " + beta[:8]
                                            + " · superseding lease absent · observed "), lines[0])
        self.assertNotIn(lease, card.split("ENDED · SUPERSEDED", 1)[0])

    def test_review_broken_after_publication_is_moved_and_decided_on_the_stranded_row(self):
        # checkpoint3 legacy tombstone: main decides on the earlier healthy projection.
        result = self.run_scenario("legacy-tombstone-decision")
        self.assertGreaterEqual(result["publishedAppends"], 1, result)
        self.assertIsInstance(result["earlierVersion"], int, result)
        self.assertEqual(("pending", None), (result["beforeBreak"]["schema5"], result["beforeBreak"]["sidecar"]))
        # The decision completes the move, then decides on the stranded row; never a reread.
        self.assertIn("workspace unavailable: ", result["approve"]["deny"], result["approve"])
        self.assertIn("; approve refused; rework creates a fresh workspace", result["approve"]["deny"])
        self.assertNotIn("reread", result["approve"]["deny"])
        moved = result["afterApprove"]
        self.assertIsNone(moved["schema5"], moved)
        self.assertEqual(("workspace_unavailable", "removed"), (moved["sidecar"]["kind"],
                                                               moved["sidecar"]["legacy_review_removed"]), moved)
        reworked = json.loads(result["rework"]["result"])
        self.assertEqual(("rework", "workspace_unavailable", True), (reworked["decision"], reworked.get("recovery"),
                                                                    reworked.get("fresh_workspace")), reworked)
        self.assertIsNotNone(result["replacement"])
        self.assertNotEqual(result["brokenPath"], result["replacementWorkspace"]["path"])
        self.assertTrue(result["replacementWorkspace"]["exists"], result["replacementWorkspace"])

    # Lead ruling X1 (lead-rulings-work-lane-review-20261113.md, section X1): not a
    # product defect. A plain turn end is not an idle observation (register.js
    # parallelTurnComplete); after a healthy publication the fork has left
    # parallelBound. The broken-after-publication row moves at the next positive
    # idle/terminal observation of that fork (including a new host's restart
    # observation, parallelRestartSubmitted) or at main's review decision. Until
    # then every real classic.Stop holds main and re-presents the undecided row
    # (parallelReviewGate). The checkpoint3 wording "next idle observation" was
    # corrected by that ruling; this test keeps its name and asserts the ruled
    # contract.
    def test_review_broken_after_publication_is_moved_at_the_next_boundary(self):
        for scenario in ("legacy-tombstone-boundary", "legacy-tombstone-boundary-decision"):
            with self.subTest(scenario=scenario):
                result = self.run_scenario(scenario)
                pending = ("pending", None)
                self.assertEqual(pending, (result["beforeBreak"]["schema5"], result["beforeBreak"]["sidecar"]))
                # turn.complete alone leaves the published ordinary row pending.
                self.assertEqual(pending, (result["afterBoundary"]["schema5"], result["afterBoundary"]["sidecar"]),
                                 result["afterBoundary"])
                self.assertEqual(["pending"], [row["state"] for row in result["rows"]], result["rows"])
                # Every real Stop, and its Stop-block continuation, holds main and re-presents the row.
                self.assertEqual([(False, True, True), (True, True, True)],
                                 [(stop["continuing"], stop["block"], stop["presents"]) for stop in result["stops"]],
                                 result["stops"])
                self.assertEqual(pending, (result["afterStop"]["schema5"], result["afterStop"]["sidecar"]),
                                 result["afterStop"])
                moved = result["afterRestart"] if scenario == "legacy-tombstone-boundary" else result["afterDecision"]
                if scenario == "legacy-tombstone-boundary-decision":
                    # Main's decision moves it: approve refused, rework recovers in a fresh workspace.
                    self.assertIn("workspace unavailable: ", result["decision"]["deny"], result["decision"])
                    self.assertIn("; approve refused; rework creates a fresh workspace", result["decision"]["deny"])
                self.assertIsNone(moved["schema5"], moved)
                self.assertEqual(("workspace_unavailable", "removed", "pending"), (
                    moved["sidecar"]["kind"], moved["sidecar"]["legacy_review_removed"], moved["sidecar"]["state"]),
                    moved)

    def test_failure_log_records_each_event_once_from_the_host_only(self):
        result = self.run_scenario("failure-log")
        worker, refused = result["workerLease"], result["refusedLease"]
        # Three refused sends to one done worker's fork, three refused decisions at one version.
        self.assertEqual(3, sum(1 for row in result["sends"] if row["deny"]), result["sends"])
        self.assertEqual(3, sum(1 for text in result["refusals"] if text), result["refusals"])
        records = result["failureRecords"]
        self.assertEqual([("revival_refused", worker, "rr:" + worker, True)],
                         [(r["code"], r["lease_id"], r["dedup_key"], r["internal"]) for r in records
                          if r["code"] == "revival_refused"], records)
        drs = [r for r in records if r["code"] == "decision_refused_stop"]
        self.assertEqual(1, len(drs), records)
        self.assertEqual((refused, True), (drs[0]["lease_id"], drs[0]["internal"]))
        self.assertRegex(drs[0]["dedup_key"], r"^drs:" + refused + r":\d+$")
        # The durable log: one row per event, count 1.
        log = result["failureLog"]
        rows = sorted((row["code"], row["lease_id"], row["count"]) for row in log["failures"])
        self.assertEqual([("decision_refused_stop", refused, 1), ("revival_refused", worker, 1)], rows, log)
        # Nothing recorded reaches a model-facing payload.
        for text in [row["deny"] for row in result["sends"]] + result["refusals"] + [result["appendedText"]]:
            self.assertNotIn("rr:", text or "")
            self.assertNotIn("drs:", text or "")
            self.assertNotIn("failure_record", text or "")
        # The refused row is not held again this turn.
        self.assertFalse(result["stopBlockedOnRefused"])

    def test_a_failed_failure_log_write_changes_no_decision_send_or_stop(self):
        logged = self.run_scenario("failure-log")
        failed = self.run_scenario("failure-log-write-fails")
        self.assertTrue(all(row["failed"] for row in failed["failureRecords"]), failed["failureRecords"])
        self.assertEqual(2, len(failed["failureRecords"]), failed["failureRecords"])
        self.assertEqual([], failed["failureLog"]["failures"])
        # The same denials, the same refusals and the same Stop outcome as with a working log.
        # Ids differ between the two runs: the worker's agent id and both lease ids.
        def strip(text):
            return ((text or "").replace(logged["workerId"], "W").replace(logged["workerLease"], "L")
                    .replace(logged["refusedLease"], "R"))

        def strip_failed(text):
            return ((text or "").replace(failed["workerId"], "W").replace(failed["workerLease"], "L")
                    .replace(failed["refusedLease"], "R"))
        self.assertEqual([strip(row["deny"]) for row in logged["sends"]], [strip_failed(row["deny"]) for row in failed["sends"]])
        self.assertEqual([strip(text) for text in logged["refusals"]], [strip_failed(text) for text in failed["refusals"]])
        self.assertEqual(logged["stopBlockedOnRefused"], failed["stopBlockedOnRefused"])

    def run_no_report(self, name):
        result = self.run_scenario(name)
        steps = {row["label"]: row for row in result["steps"]}
        lease = result["lease"]
        started = steps["after session.start"]
        self.assertEqual(("running", True), (started["lease"]["state"], started["lease"]["slot"]), started)
        self.assertEqual(([], None, None), (started["rows"], started["review"], started["sidecar"]), started)
        # Two exit-contract sends, one per native turn end that left no receipt.
        first, second = steps["after the first native turn end"], steps["after the second native turn end"]
        self.assertEqual((1, 0, 0), (first["sends"], first["salvages"], first["unreported"]), first)
        self.assertEqual("running", first["lease"]["state"], first)
        # The second send is followed by a durable host salvage receipt, unreported
        # (slot released) and the native stop.
        self.assertEqual((2, 1, 1, 1), (second["sends"], second["salvages"], second["unreported"], second["stopped"]), second)
        self.assertEqual(("unreported", False), (second["lease"]["state"], second["lease"]["slot"]), second)
        self.assertEqual("host_salvage", (second["run"]["exit"] or {}).get("kind"), second["run"])
        # Exactly one stranded main row.
        self.assertEqual(1, len(second["rows"]), second["rows"])
        row = second["rows"][0]
        self.assert_stranded_row(row, "no_report")
        self.assertEqual(("pending", None, None, None, []), (row["state"], row["decision"], row["artifact"],
                                                          row["completion_receipt"], row["coverage"]), row)
        self.assertEqual("No worker report: the native fork ended without a durable exit receipt.", row["summary"])
        self.assertEqual("Not verified: no worker report and the retained workspace is unavailable.", row["verification"])
        # The capability is revoked: the fork's own late report is refused.
        self.assertIn("error", result["lateReport"], result["lateReport"])
        boundary = steps["after a main boundary"]
        self.assertEqual((2, 1, 1, 1, second["wakes"]), (boundary["sends"], boundary["salvages"], boundary["unreported"],
                                                         boundary["stopped"], boundary["wakes"]), boundary)
        self.assertEqual(second["rows"], boundary["rows"])
        self.assert_stranded_views(result, lease, "no_report", result["brokenPath"])
        # Approve is refused and writes nothing; rework recovers in a fresh worktree.
        self.assertIn("no worker report: approve refused; rework creates a fresh workspace", result["approve"]["deny"])
        self.assertEqual(0, result["finalAfterApprove"])
        reworked = json.loads(result["rework"]["result"])
        self.assertEqual(("rework", "no_report", True), (reworked["decision"], reworked.get("recovery"),
                                                        reworked.get("fresh_workspace")), reworked)
        self.assertIsNotNone(result["replacement"])
        self.assertNotEqual(result["brokenPath"], result["replacementWorkspace"]["path"])
        self.assertTrue(result["replacementWorkspace"]["exists"], result["replacementWorkspace"])
        # A fresh worktree with no handoff: no retained-worktree warning, no broken path.
        self.assertFalse(result["spawnPromptHasWarning"])
        self.assertFalse(result["spawnPromptMentionsBrokenPath"])
        decided = steps["after the rework decision"]
        self.assertEqual(1, len(decided["spawns"]), decided)
        # Repeated boundaries change nothing: no new send, salvage, stop, spawn or row.
        for label in ("after a repeated boundary", "after another repeated boundary"):
            later = steps[label]
            self.assertEqual((decided["sends"], decided["salvages"], decided["unreported"], decided["stopped"],
                              decided["spawns"], decided["sidecar"]["replacement"]),
                             (later["sends"], later["salvages"], later["unreported"], later["stopped"],
                              later["spawns"], later["sidecar"]["replacement"]), (label, later))
            self.assertIsNone(later["review"], (label, later))
        self.assert_sidecar_storage(result, "no_report", recovered=True)
        return result

    def test_running_fork_that_never_reports_into_a_broken_worktree_becomes_one_stranded_row(self):
        self.run_no_report("stranded-no-report")

    def test_stranded_no_report_rework_works_at_the_rework_limit(self):
        self.run_no_report("stranded-no-report-at-limit")

    def test_rework_predecessor_leaves_terminal_web_mod_and_model_review_views(self):
        result = self.run_scenario("rework-review-lifecycle")
        predecessor, replacement = result["lease"], result["replacement"]
        self.assertEqual("submitted", result["reported"])
        labels = [row["label"] for row in result["views"]]
        self.assertEqual(["rework bound and running", "replacement submitted, native still running",
                          "replacement review pending", "replacement finalized", "after a new process"], labels)
        # Before its own review exists, the replacement is still queued/running and
        # the predecessor remains the single Rework row in every consumer. The
        # predecessor is generation 1: no bind followed a handoff, so the backend
        # reports changed_before_bind as not_applicable (Item 2 contract, 2026-11-13).
        for row in result["views"][:2]:
            self.assertEqual([[predecessor, "rework_ready", "not_applicable"]], row["model"], row)
            self.assertEqual([[predecessor, "rework_ready", "not_applicable"]], row["web"], row)
            self.assertIn("MAIN REVIEW (1)", row["head"], row)
            self.assertTrue(any(predecessor in line for line in row["text"]), row)
            self.assertTrue(any("not applicable (first generation)" in line for line in row["text"]), row)
        # Once the replacement publishes a pending review, it is the only visible
        # row; the decided predecessor disappears from terminal, web and model.
        pending = result["views"][2]
        self.assertEqual([[replacement, "pending", False]], pending["model"], result)
        self.assertEqual([[replacement, "pending", False]], pending["web"])
        self.assertIn("MAIN REVIEW (1)", pending["head"])
        self.assertFalse(any(predecessor in line for line in pending["text"]), pending)
        self.assertTrue(any("baseline at bind" in line for line in pending["text"]), pending)
        self.assertIn('"changed_before_bind":false', "\n".join(result["reviewMessages"]), result)
        self.assertIn("baseline at bind", "\n".join(result["reviewMessages"]), result)
        # The first replacement launch after a generation-1 result has no earlier
        # interrupted attempt to warn about (a second launch does:
        # test_second_rework_launch_warns_to_inspect_interrupted_attempt_changes).
        self.assertIn("first replacement launch for the retained worktree", result["replacementPrompt"], result)
        self.assertNotIn("Retained worktree warning", result["replacementPrompt"], result)
        self.assertNotIn("handoff_drift=", result["replacementPrompt"], result)
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

    def test_late_fork_is_stopped_at_a_boundary_whose_bind_resend_failed(self):
        result = self.run_scenario("late-fork-failed-resend")
        replacement = result["replacement"]
        first, second = ("work-rework-" + replacement + "-a1", "work-rework-" + replacement + "-a2")
        steps = {row["label"]: row for row in result["steps"]}
        # Attempt 2: the ledger is bound to its fork, the launch is still unresolved.
        dispatched = steps["after the new process dispatched attempt 2"]
        self.assertEqual([first, second], dispatched["spawns"], dispatched)
        self.assertEqual(("spawn_observed", 2), (dispatched["launch"]["state"], dispatched["launch"]["attempt"]))
        self.assertEqual(("running", "agent-2", second), (dispatched["lease"]["state"], dispatched["lease"]["host_id"],
                                                          dispatched["lease"]["dispatch_name"]))
        self.assertEqual(([], False), (dispatched["stopped"], dispatched["lateNoticeConsumed"]))
        # The boundary's resend failed again and repaired nothing ...
        failed = steps["after a boundary whose bind resend failed"]
        self.assertEqual("spawn_observed", failed["launch"]["state"])
        self.assertNotIn(None, failed["binds"])
        # One resend per boundary (three bounded sends), not one per caller of it.
        self.assertEqual(3, len(failed["binds"]) - len(dispatched["binds"]), failed)
        # ... yet attempt 1's late fork is owned and stopped at that same boundary.
        self.assertEqual((["agent-late"], 1, True),
                         (failed["stopped"], failed["lateRing"], failed["lateNoticeConsumed"]), failed)
        # The next boundary repairs the bind; the late fork is not stopped twice.
        recovered = steps["after a boundary whose bind resend succeeded"]
        self.assertEqual(("bound", "agent-2"), (recovered["launch"]["state"], recovered["launch"]["agent"]))
        self.assertEqual([None], recovered["binds"][len(failed["binds"]):])
        self.assertEqual((["agent-late"], 1, True),
                         (recovered["stopped"], recovered["lateRing"], recovered["lateNoticeConsumed"]), recovered)
        self.assertEqual([first, second], recovered["spawns"])

    def assert_late_fork_after_replacement_review(self, result, before_label):
        predecessor, replacement = result["lease"], result["replacement"]
        first, second = ("work-rework-" + replacement + "-a1", "work-rework-" + replacement + "-a2")
        steps = {row["label"]: row for row in result["steps"]}
        self.assertEqual("submitted", result["reported"])
        # The replacement's own review row replaced the predecessor's rework_ready row.
        self.assertEqual([[replacement, "pending"]], result["projectedAfterReview"])
        before = steps[before_label]
        self.assertEqual("rework_ready", before["reviews"][predecessor])
        self.assertEqual(("bound", 2, "agent-2"), (before["launch"]["state"], before["launch"]["attempt"],
                                                    before["launch"]["agent"]))
        self.assertEqual(([first, second], False), (before["spawns"], before["lateNoticeConsumed"]))
        # The bound fork of attempt 2 was listed at every boundary so far and is never rejected.
        self.assertEqual(0, before["lateRing"], before)
        self.assertNotIn("agent-late", before["stopped"])
        # One boundary with attempt 1's fork listed: it is owned and stopped once.
        for label in ("after one boundary with the late fork listed", "after a second boundary"):
            row = steps[label]
            self.assertEqual((1, 1, True), (row["stopped"].count("agent-late"), row["lateRing"],
                                            row["lateNoticeConsumed"]), row)
            # Never bound, and no further fork is started.
            self.assertEqual(("agent-2", second), (row["lease"]["host_id"], row["lease"]["dispatch_name"]))
            self.assertEqual(("bound", "agent-2"), (row["launch"]["state"], row["launch"]["agent"]))
            self.assertEqual([first, second], row["spawns"])
        return steps

    def test_late_fork_first_listed_after_the_replacement_review_is_stopped(self):
        result = self.run_scenario("late-fork-after-review")
        steps = self.assert_late_fork_after_replacement_review(result, "after the replacement published its review")
        self.assertEqual("pending", steps["after a second boundary"]["reviews"][result["replacement"]])

    def test_late_fork_first_listed_after_the_replacement_approval_is_stopped(self):
        result = self.run_scenario("late-fork-after-approve")
        decided = json.loads(result["approve"]["result"])
        self.assertEqual(("approve", "finalized"), (decided["decision"], decided["state"]))
        self.assertEqual([], result["projectedAfterApprove"])
        steps = self.assert_late_fork_after_replacement_review(result, "after the replacement was approved")
        final = steps["after a second boundary"]
        self.assertEqual(("finalized", "done"), (final["reviews"][result["replacement"]], final["lease"]["state"]))

    def test_late_fork_of_a_lease_that_left_the_status_read_is_stopped(self):
        result = self.run_scenario("late-fork-ended-window")
        replacement = result["replacement"]
        self.assertEqual(["finalized"] * 9, result["laterStates"])
        # The status read no longer carries the replacement lease in any row:
        # it is not open, and the ended group keeps the last eight ended leases.
        rows = result["statusRows"]
        self.assertEqual([], rows["open"])
        self.assertEqual(8, len(rows["groups"]["ended"]), rows)
        listed = {lease for group in rows["groups"].values() for lease in group}
        self.assertNotIn(replacement, listed)
        # The module read the bound fork's name while the lease was listed, and
        # that name never changes: attempt 1's fork is still stopped once.
        steps = self.assert_late_fork_after_replacement_review(result, "after nine later leases ended")
        final = steps["after a second boundary"]
        self.assertEqual(("finalized", "done"), (final["reviews"][replacement], final["lease"]["state"]))

    def test_normal_ended_window_needs_no_attribution_query(self):
        result = self.run_scenario("late-fork-ended-window")
        self.assertEqual([], result["attributions"], result)
        steps = {row["label"]: row for row in result["steps"]}
        final = steps["after a second boundary"]
        self.assertEqual((1, 1, True), (final["stopped"].count("agent-late"), final["lateRing"],
                                       final["lateNoticeConsumed"]), final)

    def test_reload_asks_the_backend_before_stopping_a_fork_that_left_the_status_read(self):
        result = self.run_scenario("late-fork-ended-window-reload")
        replacement = result["replacement"]
        self.assertEqual(["finalized"] * 9, result["laterStates"])
        rows = result["statusRows"]
        self.assertEqual([], rows["open"])
        self.assertNotIn(replacement, {lease for group in rows["groups"].values() for lease in group})
        first = result["spawns"][0]
        self.assertEqual((1, False),
                         (len(result["attributions"]),
                          any(not row["internal"] for row in result["attributions"])), result)
        self.assertIn(first, result["attributions"][0]["names"], result)
        steps = self.assert_late_fork_after_replacement_review(result, "after nine later leases ended")
        final = steps["after a second boundary"]
        self.assertEqual(("finalized", "done"), (final["reviews"][replacement], final["lease"]["state"]))

    def test_failed_reload_attribution_stops_nothing_and_the_next_pump_retries(self):
        result = self.run_scenario("late-fork-ended-window-reload-query-failed")
        first, second = result["attributions"]
        self.assertEqual((True, False), (first["lost"], second["lost"]), result)
        steps = {row["label"]: row for row in result["steps"]}
        failed = steps["after one boundary with the late fork listed"]
        self.assertEqual((0, 0, False), (failed["stopped"].count("agent-late"), failed["lateRing"],
                                         failed["lateNoticeConsumed"]), failed)
        retried = steps["after a second boundary"]
        self.assertEqual((1, 1, True), (retried["stopped"].count("agent-late"), retried["lateRing"],
                                       retried["lateNoticeConsumed"]), retried)

    def test_second_rework_launch_warns_to_inspect_interrupted_attempt_changes(self):
        result = self.run_scenario("rework-retry-warning")
        self.assertEqual(["work-rework-" + result["replacement"] + "-a1",
                          "work-rework-" + result["replacement"] + "-a2"], result["spawns"])
        self.assertIn("Retained worktree warning", result["prompt"], result)
        self.assertIn("interrupted rework attempt", result["prompt"], result)
        self.assertNotIn("handoff_drift=", result["prompt"], result)

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

    def test_p0_existing_root_launches_immediately_while_disabled_and_paused(self):
        result = self.run_scenario("p0-immediate-paused")
        self.assertFalse(result["priority"].get("deny"), result)
        self.assertIn("effective_priority", result["priority"].get("result", ""))
        self.assertEqual((False, True),
                         (result["p0"]["policy"]["enabled"], result["p0"]["policy"]["paused"]))
        self.assertEqual(1, len(result["spawns"]), result)
        self.assertRegex(result["spawns"][0], r"^work-p0-[0-9a-f]{32}$")
        self.assertIn("p0_launch_assignment", result["events"])
        self.assertIn("p0_launch_spawning", result["events"])
        self.assertNotIn("reserve_next", result["events"])
        lease = next(iter(result["backend"]["leases"].values()))
        self.assertEqual(("running", result["spawns"][0]), (lease["state"], lease["dispatch_name"]))
        self.assertIn("Execute exactly this P0 Work assignment immediately", result["prompt"])

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

    def test_unknown_fork_attribution_stops_and_owns_nothing(self):
        result = self.run_scenario("fork-attribution-unknown")
        names = ["work-rework-unknown-0-a1"]
        self.assertEqual((1, names, False),
                         (len(result["attributions"]), result["attributions"][0]["names"],
                          result["attributions"][0]["lost"]), result)
        # This is recorded by the Python server immediately before the real
        # work_parallel_mcp.run dispatch, not by the JS bridge shim.
        self.assertEqual([{"names": names, "internal": True}], result["backendAttributions"], result)
        self.assertEqual([], result["stopped"], result)
        self.assertNotIn("consumed", result["notice"], result)

    def test_fork_attribution_queries_at_most_64_names_and_defers_the_rest(self):
        result = self.run_scenario("fork-attribution-batch")
        self.assertEqual([64, 64], [len(row["names"]) for row in result["attributions"]], result)
        queried = [set(row["names"]) for row in result["attributions"]]
        self.assertEqual(65, len(queried[0] | queried[1]), result)
        self.assertEqual([], result["stopped"], result)

    def test_fork_attribution_parses_hyphenated_lease_ids_and_skips_malformed_and_side_rows(self):
        result = self.run_scenario("fork-attribution-shape")
        self.assertEqual([["work-rework-hyphenated-lease-id-a12", "work-rework-side-owned-looking-a1"]],
                         [row["names"] for row in result["attributions"]], result)
        self.assertEqual([], result["stopped"], result)

    def test_fork_attribution_pairs_replies_by_index_and_mismatch_or_short_reply_is_inert(self):
        result = self.run_scenario("fork-attribution-reply-shapes")
        self.assertEqual([["work-rework-dup-a1", "work-rework-dup-a1", "work-rework-other-a1"]],
                         [row["names"] for row in result["attributions"]], result)
        self.assertEqual(["unattributed-0"], result["stopped"], result)

    def assert_revival_refused(self, probe, label):
        # Refused before delivery: either tool.call denies, or session.send answers undelivered.
        self.assertTrue(probe["deny"] or probe["sendReason"], (label, probe))
        self.assertFalse(probe["delivered"], (label, probe))
        self.assertIsNone(probe["revived"], (label, probe))
        self.assertEqual(probe["before"], probe["after"], (label, probe))

    def test_nested_own_recovery_send_passes_but_another_plugin_is_still_refused(self):
        result = self.run_scenario("revival-nested-own-send")
        self.assertEqual(1, result["recoverySends"], result)
        self.assertTrue(result["own"]["delivered"], result["own"])
        self.assertIsNone(result["own"]["reason"], result["own"])
        self.assert_revival_refused(result["foreign"], "other plugin")

    def test_stale_and_done_rework_forks_cannot_be_revived_after_reload(self):
        # CC 2.1.323 revives a killed listed fork in place on SendMessage, with no
        # agent.spawn hook (owned preflight run-p0-row3t-20261112-172101).
        result = self.run_scenario("late-fork-ended-window-reload-revival")
        statuses = {row["id"]: row["status"] for row in result["revivalStatuses"]}
        self.assertEqual("killed", statuses["agent-late"], result["revivalStatuses"])
        for label, probe in result["revival"].items():
            self.assert_revival_refused(probe, label)

    def test_done_worker_and_side_fork_cannot_be_revived_but_others_can(self):
        result = self.run_scenario("revival-terminal")
        self.assertEqual("done", result["leaseState"]["state"], result["leaseState"])
        for label in ("doneById", "doneByName", "sideById", "otherPluginToDone"):
            self.assert_revival_refused(result["revival"][label], label)
        # Not a Work fork: the message is delivered unchanged, even to a teammate
        # whose name has the shape of a Work dispatch name.
        for label in ("teammate", "teammateWorkShapedName", "unlistedPeer"):
            probe = result["revival"][label]
            self.assertTrue(probe["delivered"], (label, probe))
            self.assertIsNone(probe["deny"], (label, probe))

    def test_stop_gate_holds_a_refused_row_once_per_turn_and_a_decidable_row_until_decided(self):
        # A refused review-tool call resets the engine's continuation cap, so an
        # unbounded gate repeats Stop without end (beta measurement).
        result = self.run_scenario("stop-gate-refused")
        first, second = result["turns"]
        for turn in (first, second):
            # Bounded: the turn's Stop passes before the harness's 5-stop cap.
            self.assertFalse(turn["rows"][-1]["blocked"], turn)
            # At most one block per (refused row, finalization version) per turn.
            versions = [row["refusedVersion"] for row in turn["rows"] if row["blocked"] and row["refusedShown"]]
            self.assertTrue(all(isinstance(version, int) for version in versions), turn)
            self.assertEqual(len(versions), len(set(versions)), turn)
        # The decidable row stays gated until decided: turn 1 approves it once.
        self.assertEqual(["finalized"], [row["decision"] for row in first["rows"] if row.get("decision") == "finalized"], first)
        self.assertTrue(any(row["blocked"] and row["normalShown"] and not row["refusedShown"] for row in first["rows"]), first)
        # Turn 2: the refused row (same version) is gated again exactly once.
        self.assertEqual(1, second["blocks"], second)
        self.assertTrue(second["rows"][0]["refusedShown"] and not second["rows"][0]["normalShown"], second)
        # Turn 1 saw two versions of the refused row (approving the other row
        # moved the finalization version), turn 2 one: three refusals in all.
        self.assertEqual(3, result["reviewRefusals"])

    def test_durable_lookup_refuses_a_done_worker_no_status_row_or_name_still_shows(self):
        result = self.run_scenario("revival-durable-only")
        self.assertEqual("done", result["oldLease"]["state"], result["oldLease"])
        # The bounded status read no longer mentions it: only fork_identity can.
        self.assertFalse(result["statusMentionsOld"])
        for label in ("byId", "byName", "otherPlugin"):
            self.assert_revival_refused(result["revival"][label], label)
            reason = result["revival"][label]["deny"] or result["revival"][label]["sendReason"]
            self.assertIn("(done)", reason, label)
        self.assertTrue(result["revival"]["unlistedPeer"]["delivered"], result["revival"]["unlistedPeer"])
        # Host-only, with both the listed id and the name of the target.
        calls = result["identityCalls"]
        self.assertTrue(calls and all(call["internal"] for call in calls), calls)
        self.assertIn(result["workerId"], calls[0]["ids"])
        self.assertIn("legacy-helper", calls[0]["names"])

    def test_failed_durable_lookup_falls_back_to_local_evidence_only(self):
        # The lead's rule for an unreadable store: only local evidence refuses.
        # Nothing local marks this fork, so it is delivered; recorded as the gap.
        result = self.run_scenario("revival-durable-only-lookup-failed")
        self.assertTrue(result["identityCalls"] and all(call["failed"] for call in result["identityCalls"]))
        for label in ("byId", "byName", "unlistedPeer"):
            self.assertTrue(result["revival"][label]["delivered"], (label, result["revival"][label]))

    def test_unreadable_work_store_refuses_only_locally_known_work_forks(self):
        result = self.run_scenario("revival-read-failure")
        for label in ("unlistedPeer", "unlistedPeerRef", "teammateWorkShapedName"):
            probe = result["revival"][label]
            self.assertTrue(probe["delivered"], (label, probe))
            self.assertIsNone(probe["deny"], (label, probe))
        for label in ("reservedNameForkById", "reservedNameForkByName", "cielWorkSpawnedFork", "sideFork",
                      "unlistedReservedName", "otherPluginToReservedFork"):
            self.assert_revival_refused(result["revival"][label], label)
            self.assertIn("could not be read", result["revival"][label]["deny"], label)
        # A deferred resume spawn with no authorization is denied when its own read fails.
        self.assertIn("deny", result["deferredWithoutAuthorization"], result["deferredWithoutAuthorization"])
        self.assertIn("router connection reset", result["deferredWithoutAuthorization"]["deny"])

    def test_in_place_resume_of_an_active_worker_leaves_no_spawn_authorization(self):
        result = self.run_scenario("revival-resume-auth")
        self.assertTrue(result["send"]["delivered"], result["send"])
        self.assertEqual("idle", result["send"]["after"], result["send"])
        # No deferred spawn follows an in-place revival, so none may use its authorization.
        self.assertIn("deny", result["laterSpawn"], result["laterSpawn"])

    def test_deferred_resume_authorization_is_consumed_once_and_expires(self):
        result = self.run_scenario("revival-resume-auth-unlisted")
        self.assertTrue(result["send"]["delivered"], result["send"])
        # The restart path still works: the deferred native resume spawn is accepted.
        self.assertEqual({"started": "unreported-worker"}, result["deferredSpawn"], result)
        self.assertTrue(result["secondSend"]["delivered"], result["secondSend"])
        self.assertIn("deny", result["expiredSpawn"], result["expiredSpawn"])

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
        self.assertIn("recorded only by the Work host", result["forgedAttribution"]["deny"])
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

    # Wait-deadlock audit (2026-11-13): main idle, no fork running, and the
    # state changes only through an operator surface or a late router connect.
    def test_idle_main_rolls_forward_after_operator_policy_resume(self):
        result = self.run_scenario("idle-policy-resume")
        self.assertEqual(("awaiting_capacity", "paused"), (result["before"]["state"], result["before"]["blocked"]), result)
        self.assertEqual({"paused": False}, result["change"], result)
        self.assertEqual("rework_ready", result["after"]["state"], result)
        self.assertEqual(result["before"]["spawns"] + 1, result["after"]["spawns"], result)

    def test_idle_main_dispatches_rework_decided_on_the_web(self):
        result = self.run_scenario("idle-web-rework")
        self.assertEqual("pending", result["before"]["state"], result)
        self.assertEqual({"state": "rework_ready"}, result["change"], result)
        self.assertEqual("rework_ready", result["after"]["state"], result)
        self.assertEqual(result["before"]["spawns"] + 1, result["after"]["spawns"], result)

    def test_idle_main_is_woken_for_review_after_late_router_connect(self):
        result = self.run_scenario("idle-start-unconnected")
        self.assertEqual((0, "pending"), (result["before"]["prompts"], result["before"]["wake"]), result)
        self.assertEqual(1, result["after"]["prompts"], result)
        self.assertEqual("appended", result["after"]["wake"], result)

    def test_idle_main_rolls_forward_after_owner_proof_arrives_without_a_turn(self):
        result = self.run_scenario("idle-owner-offline")
        self.assertEqual(("awaiting_capacity", "owner_offline"), (result["before"]["state"], result["before"]["blocked"]), result)
        self.assertEqual({"proven": True}, result["change"], result)
        self.assertEqual("rework_ready", result["after"]["state"], result)
        self.assertEqual(result["before"]["spawns"] + 1, result["after"]["spawns"], result)

    def test_idle_main_rolls_forward_when_owner_proof_arrives_after_idle_ticks(self):
        result = self.run_scenario("idle-owner-offline-late")
        self.assertEqual(("awaiting_capacity", "owner_offline"), (result["before"]["state"], result["before"]["blocked"]), result)
        self.assertEqual({"proven": True}, result["change"], result)
        self.assertEqual("rework_ready", result["after"]["state"], result)
        self.assertEqual(result["before"]["spawns"] + 1, result["after"]["spawns"], result)

    def test_idle_main_starts_attempt_two_after_a_web_launch_resolution(self):
        result = self.run_scenario("idle-web-launch-resolve")
        self.assertEqual(("launch_intent", 1), (result["launchBefore"]["state"], result["launchBefore"]["attempt"]), result)
        self.assertEqual({"state": "manual_requested"}, result["change"], result)
        self.assertEqual(2, result["launchAfter"]["attempt"], result)
        self.assertEqual(result["before"]["spawns"] + 1, result["after"]["spawns"], result)

    def test_idle_main_starts_attempt_two_after_the_other_live_owner_exits(self):
        result = self.run_scenario("idle-other-live-exits")
        self.assertEqual(("launch_intent", 1), (result["launchBefore"]["state"], result["launchBefore"]["attempt"]), result)
        self.assertGreaterEqual(result["waiting"], 1, result)
        self.assertEqual({"ended": 1}, result["change"], result)
        self.assertEqual(2, result["launchAfter"]["attempt"], result)
        self.assertEqual(result["before"]["spawns"] + 1, result["after"]["spawns"], result)

    def test_idle_refused_wake_prompt_stays_uncertain_and_is_never_resubmitted(self):
        # Measured on 52b4a77b and 306d9011 alike: the review append lands
        # (wake appended), the one wake prompt's $.prompt.submit throws, and the
        # prompt key settles uncertain (side-intake.js sidePromptSubmit). By the
        # accepted design an uncertain prompt is never resubmitted automatically
        # (sse-native-fork/duplicate-side-wake.md "Remaining uncertainty"); main
        # reaches the appended row at its next turn and the Stop gate. With main
        # idle that turn comes from the router fork-turn watcher (lead ruling
        # 1ee9591c), not from this module. This test pins the no-resubmit bound.
        result = self.run_scenario("idle-wake-submit-fails-twice")
        self.assertEqual(1, result["submitFailures"], result)
        self.assertEqual(("appended", 1, 0), (result["after"]["wake"], result["after"]["appended"],
                                              result["after"]["prompts"]), result)

    def test_idle_failed_wake_retry_never_prompts_and_is_paced_by_idle_ticks(self):
        result = self.run_scenario("idle-wake-failed-always")
        self.assertEqual("failed", result["after"]["wake"], result)
        self.assertEqual((0, 0), (result["after"]["prompts"], result["after"]["appended"]), result)
        idle_ticks = len([name for name in result["fired"] if name.startswith("scheduleIdleRefresh:")])
        # One attempt at session.start, then at most one per idle tick.
        self.assertLessEqual(result["after"]["prepares"], 1 + idle_ticks, result)

    def test_idle_main_releases_operator_changes_made_after_earlier_idle_ticks(self):
        # The change comes only after 3 rounds of idle ticks, so the first tick's
        # new host signature cannot be what releases it.
        for scenario in ("idle-policy-resume-late", "idle-web-rework-late", "idle-capacity-leaf-late",
                         "idle-web-launch-resolve-late", "idle-other-live-exits-late"):
            with self.subTest(scenario=scenario):
                result = self.run_scenario(scenario)
                self.assertTrue(result["early"], result)
                if "leaf" in scenario:
                    self.assertEqual((0, 1), (result["leafLeasesBefore"], result["leafLeasesAfter"]), result)
                elif "launch" in scenario or "other-live" in scenario:
                    self.assertEqual((1, 2), (result["launchBefore"]["attempt"], result["launchAfter"]["attempt"]), result)
                else:
                    self.assertEqual("rework_ready", result["after"]["state"], result)
                    self.assertEqual(result["before"]["spawns"] + 1, result["after"]["spawns"], result)

    def test_idle_review_wake_reaches_main_after_two_refused_appends(self):
        result = self.run_scenario("idle-wake-failed-twice")
        self.assertEqual("failed", result["before"]["wake"], result)
        self.assertEqual("appended", result["after"]["wake"], result)
        self.assertEqual((1, 1), (result["after"]["prompts"], result["after"]["appended"]), result)

    def test_idle_main_rolls_forward_a_launch_rate_wait_after_the_window(self):
        result = self.run_scenario("idle-launch-rate-review")
        self.assertEqual(("awaiting_capacity", "launch_rate"), (result["before"]["state"], result["before"]["blocked"]), result)
        self.assertEqual("rework_ready", result["after"]["state"], result)
        self.assertEqual(result["before"]["spawns"] + 1, result["after"]["spawns"], result)

    def test_idle_main_dispatches_a_runnable_leaf_after_the_launch_window(self):
        result = self.run_scenario("idle-launch-rate-leaf")
        self.assertEqual(0, result["leafLeasesBefore"], result)
        self.assertEqual(1, result["leafLeasesAfter"], result)

    def test_idle_main_dispatches_a_runnable_leaf_after_capacity_is_raised(self):
        result = self.run_scenario("idle-capacity-leaf")
        self.assertEqual(0, result["leafLeasesBefore"], result)
        self.assertEqual({"P1": 2}, result["change"], result)
        self.assertEqual(1, result["leafLeasesAfter"], result)

    def test_idle_awaiting_capacity_pump_writes_no_backend_file_when_owner_stays_offline(self):
        result = self.run_scenario("idle-nowrite-offline")
        self.assertEqual(("awaiting_capacity", "owner_offline"), (result["after"]["state"], result["after"]["blocked"]), result)
        self.assertEqual([], result["filesChanged"], result)

    def test_idle_awaiting_capacity_pump_writes_no_backend_file_while_paused(self):
        result = self.run_scenario("idle-nowrite-paused")
        self.assertEqual(("awaiting_capacity", "paused"), (result["after"]["state"], result["after"]["blocked"]), result)
        self.assertEqual([], result["filesChanged"], result)

    def test_idle_review_wake_is_never_prompted_twice_over_many_ticks(self):
        # Main idle throughout. A wake that was delivered, refused once (failed) or
        # lost once (pending) ends delivered exactly once after 8 rounds of every timer.
        for scenario, before in (("idle-wake-once", "appended"), ("idle-wake-failed", "failed"),
                                 ("idle-wake-pending", "pending")):
            with self.subTest(scenario=scenario):
                result = self.run_scenario(scenario)
                self.assertEqual(before, result["before"]["wake"], result)
                self.assertEqual("appended", result["after"]["wake"], result)
                self.assertEqual((1, 1), (result["after"]["prompts"], result["after"]["appended"]), result)

    def test_idle_main_recovers_an_unlisted_worker_after_late_router_connect(self):
        result = self.run_scenario("idle-start-unconnected-running")
        self.assertEqual(0, result["before"]["recoverySends"], result)
        self.assertEqual(1, result["after"]["recoverySends"], result)

    def test_router_connect_after_the_retry_window_recovers_and_wakes_once(self):
        # X2 (ctl F-A2-1, review/test_review_a2_row12_probe.py shape): the router
        # connects only after the six-step retry window has fully elapsed. Row 12
        # has no time window: the worker is recovered and main woken exactly once,
        # within one capped retry interval (RETRY_MS last step, 8 s).
        result = self.run_scenario("idle-start-unconnected-running-window")
        self.assertEqual((0, 0, "pending"), (result["before"]["recoverySends"], result["before"]["prompts"],
                                             result["before"]["wake"]), result)
        self.assertEqual((1, 1), (result["after"]["recoverySends"], result["after"]["prompts"]), result)
        self.assertEqual({"delay": 8000, "by": "retryStart", "round": 0}, result["releasedBy"], result)

    def test_host_phase_failure_with_a_working_board_recovers_once_later(self):
        # X2: the board read works but the session.start host phase fails until
        # after the original retry window; the idle refresh never hydrates or
        # recovers, so the capped retry must run the host phase exactly once.
        result = self.run_scenario("idle-start-unconnected-board-only")
        self.assertEqual(0, result["before"]["recoverySends"], result)
        self.assertEqual(1, result["after"]["recoverySends"], result)
        self.assertEqual(1, result["after"]["prompts"], result)

    def test_host_phase_retried_after_a_throw_past_the_recovery_send_sends_once(self):
        # X2 criterion 6 (lead): session.start's host phase sends recovery for
        # the unlisted running lease, then throws once later (its pump's status
        # read is refused). The retried host phase runs again (host_attach) but
        # skips that lease: one recovery send across every attempt.
        result = self.run_scenario("idle-start-recovery-then-pump-throws")
        self.assertEqual((1, 0), (result["before"]["recoverySends"], result["before"]["prompts"]), result)
        self.assertEqual(0, result["throwsLeft"], result)
        self.assertGreaterEqual(result["afterCalls"].get("host_attach", 0), 1, result)
        self.assertEqual((1, 1), (result["after"]["recoverySends"], result["after"]["prompts"]), result)

    def test_host_phase_retry_after_a_delivered_exit_contract_does_not_resend_it(self):
        # X2 criterion 6, lead/ctl exact shape: L1 unlisted recovery is sent;
        # L2 terminal exit contract is delivered; L3 terminal throws once before
        # its exit attempt. The retry may finish L3 but never resend L1 or L2.
        control = self.run_scenario("idle-start-recovery-exit-control")
        fault = self.run_scenario("idle-start-recovery-then-exit-throws")
        self.assertEqual(0, fault["exitThrowsLeft"], fault)
        self.assertGreaterEqual(fault["afterCalls"].get("host_attach", 0), 1, fault)
        self.assertEqual(1, fault["sendByAgent"]["check6-l1"], fault)
        self.assertEqual(control["sendByAgent"]["check6-l2"], fault["sendByAgent"]["check6-l2"], (control, fault))
        self.assertEqual(control["sendByAgent"]["check6-l3"], fault["sendByAgent"]["check6-l3"], (control, fault))
        self.assertEqual(control["after"]["salvages"], fault["after"]["salvages"], (control, fault))
        self.assertEqual(control["after"]["exitSends"], fault["after"]["exitSends"], (control, fault))

    def test_host_phase_retry_after_an_undelivered_exit_does_not_spend_attempt_two(self):
        control = self.run_scenario("idle-start-recovery-exit-undelivered-control")
        fault = self.run_scenario("idle-start-recovery-exit-undelivered-then-throws")
        self.assertEqual(0, fault["exitThrowsLeft"], fault)
        self.assertEqual(control["sendByAgent"], fault["sendByAgent"], (control, fault))
        self.assertEqual(control["after"]["salvages"], fault["after"]["salvages"], (control, fault))
        self.assertEqual(control["after"]["exitSends"], fault["after"]["exitSends"], (control, fault))

    def test_host_phase_retry_after_a_thrown_exit_send_does_not_spend_attempt_two(self):
        control = self.run_scenario("idle-start-recovery-exit-send-throws-control")
        fault = self.run_scenario("idle-start-recovery-exit-send-throws-then-throws")
        self.assertEqual(0, fault["exitThrowsLeft"], fault)
        self.assertEqual(control["sendByAgent"], fault["sendByAgent"], (control, fault))
        self.assertEqual(control["after"]["salvages"], fault["after"]["salvages"], (control, fault))
        self.assertEqual(control["after"]["exitSends"], fault["after"]["exitSends"], (control, fault))

    def test_retry_start_does_not_recover_an_unlisted_worker_twice(self):
        result = self.run_scenario("idle-retry-once")
        self.assertEqual(1, result["before"]["recoverySends"], result)
        self.assertEqual(1, result["after"]["recoverySends"], result)

    def test_review_wake_is_prepared_and_recorded_once_across_restart(self):
        result = self.run_scenario("wake")
        # The review payload is appended and an idle main gets one wake-only
        # prompt (continue-sse's accepted delivery design, measured 2026-11-13).
        self.assertEqual((1, 1), (result["first"]["prompts"], result["first"]["appended"]))
        wake = result["first"]["backend"]["reviews"][result["lease"]]["wake"]
        self.assertEqual(("appended", 1), (wake["state"], wake["attempts"]))
        # A process restart resends neither.
        self.assertEqual((1, 1), (result["second"]["prompts"], result["second"]["appended"]))
        self.assertEqual((1, 1), (result["prepares"], result["records"]))
        self.assertEqual(1, result["second"]["backend"]["reviews"][result["lease"]]["wake"]["attempts"])
        # Delivery is not a decision: Stop stays gated.
        self.assertIn("work_parallel_item_review", result["stop"]["block"])

    def test_busy_main_wake_is_appended_and_not_resent_after_restart(self):
        result = self.run_scenario("wake-busy")
        self.assertEqual((0, 1), (result["prompts"], result["appended"]))
        self.assertEqual("appended", result["backend"]["reviews"][result["lease"]]["wake"]["state"])
        self.assertEqual({"prompts": 0, "appended": 1}, result["afterRestart"])

    def test_host_attach_and_only_own_or_gone_leases_are_adopted_or_recovered(self):
        # R2 B2 (backend mcp 9f830f40): the host attaches once per process with the
        # launcher pid and its nonce; paths 2/4 act only on own or gone owners.
        result = self.run_scenario("owner-process-paths")
        attach = result["attach"]
        self.assertEqual(1, len(attach), attach)
        self.assertTrue(attach[0]["internal"] and attach[0]["nonce"] and isinstance(attach[0]["launcher_pid"], int))
        self.assertEqual({"own": "own", "other_live": "other_live", "unknown": "unknown", "gone": "gone"},
                         result["ownerProcess"])
        # Live: this process and the other_live stand-in; gone: the exited one.
        self.assertEqual((2, 1), (result["attachments"]["live"], result["attachments"]["gone"]))
        # Path 2: the recovery resume goes to own and gone only.
        self.assertEqual({"own": 1, "other_live": 0, "unknown": 0, "gone": 1}, result["recoverySends"])
        # Path 4: only adopted leases are reconciled at this process's turn end.
        self.assertEqual(["gone", "own"], result["turnReconciled"])
        self.assertTrue(any(line.startswith("HOSTS: live 2 · gone 1") for line in result["statusText"]), result["statusText"])
        self.assertTrue(any(" · owner other_live" in line for line in result["statusText"]), result["statusText"])
        # Every listed host call carries the one process nonce.
        for event in ("work_parallel_status", "host_attach", "native_observation"):
            row = result["nonceBy"][event]
            self.assertGreater(row["calls"], 0, event)
            self.assertEqual(row["calls"], row["withNonce"], event)
        self.assertEqual(1, result["nonceValues"])

    def test_unknown_launch_owner_offers_the_pane_resolve_control_and_sends_only_the_request(self):
        # R2 B2 (mcp 9aeeb289, lead ruling on backend's pane gap): the automatic
        # absence of an attempt with no owner record is refused with the ruled
        # text, and the pane offers "Resolve stuck launch…" for it.
        result = self.run_scenario("launch-owner-pane-unknown")
        # Each boundary (start, its pump, the turn end) asks once and is refused.
        self.assertGreaterEqual(result["absent"], 1, result)
        self.assertIn("    Rework launch stuck: Owner of this launch attempt is unknown; resolve it from the Work pane.",
                      result["statusLines"])
        self.assertTrue(result["control"], result["paneLabels"])
        # The press sends the person's request and nothing else.
        self.assertEqual(["rework_launch_manual_request"], result["pressCalls"])
        self.assertEqual([True], [row["user_confirmed"] for row in result["requestMarkers"]])
        # The host then checks its own list: the router allows a person's
        # resolution for an unknown owner, and exactly one new attempt starts.
        self.assertEqual(1, result["afterCalls"].count("rework_launch_manual_resolve"), result["afterCalls"])
        self.assertNotIn("rework_launch_absent", result["afterCalls"])
        self.assertEqual(2, len(result["spawns"]), result["spawns"])
        self.assertEqual(("bound", 2), (result["final"]["reviews"][result["lease"]]["launch"]["state"],
                                        result["final"]["reviews"][result["lease"]]["launch"]["attempt"]))

    def test_other_live_launch_owner_only_shows_the_waiting_text(self):
        result = self.run_scenario("launch-owner-pane-other-live")
        self.assertGreaterEqual(result["absent"], 1, result)
        self.assertEqual(["    Rework launch waiting: Another live Work host owns this launch attempt; waiting for that host."],
                         result["statusLines"])
        self.assertFalse(result["control"], result["paneLabels"])
        self.assertEqual([], result["pressCalls"])
        self.assertEqual(1, len(result["spawns"]), result["spawns"])
        self.assertEqual(("launch_intent", 1), (result["final"]["reviews"][result["lease"]]["launch"]["state"],
                                                result["final"]["reviews"][result["lease"]]["launch"]["attempt"]))

    def test_model_originated_work_calls_carry_no_launch_nonce(self):
        # Lead ruling: model-originated event/configure calls never get the nonce,
        # and a model-supplied _host is dropped.
        result = self.run_scenario("model-event-has-no-nonce")
        self.assertTrue(result["modelCalls"], result)
        for row in result["modelCalls"]:
            self.assertIsNone(row["nonce"], row)


if __name__ == "__main__":
    unittest.main()
