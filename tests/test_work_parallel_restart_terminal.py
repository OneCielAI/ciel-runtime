"""A submitted Work result whose fork ended with its process; temp config only.

Reproduces run-20261111-105012 against the real ledger, coordination, native
evidence and finalization stores: two completion receipts, a native running
row after the first receipt, one review published, then a process restart in
which neither fork is listed.
"""
import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ciel_runtime_support import work_parallel_mcp as m
from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.work_parallel import WorkParallelError, WorkParallelStore
from ciel_runtime_support.work_plan_store import WorkPlanStore

SECRET = "restart-host-secret-" * 3
SCOPE = "claude:restart-terminal"
HOST = {"internal": True}


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


class Board:
    def __init__(self, path):
        self.store = WorkPlanStore(path)
        doc = self.store.read()
        doc, root = self.store.transaction(lambda d: tree.set_plan(d, {
            "title": "Goal", "note": "", "priority": "P1", "children": [
                {"title": "Pass", "note": "", "ref": "", "waiting_on": "", "children": []},
                {"title": "Fail", "note": "", "ref": "", "waiting_on": "", "children": []},
            ]}, None), expected_version=doc["version"])
        self.items = [n["id"] for n in doc["nodes"] if n["parent_id"] == root]
        self.store.transaction(lambda d: tree.set_phase(d, "executing", root),
                               expected_version=doc["version"])

    def read(self):
        return self.store.read()

    def transaction(self, *args, **kwargs):
        return self.store.transaction(*args, **kwargs)


class RestartSubmittedTerminalTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.repo = self.tmp / "repo"
        git(self.tmp, "init", str(self.repo))
        git(self.repo, "config", "user.name", "fixture")
        git(self.repo, "config", "user.email", "fixture@example.invalid")
        (self.repo / "f").write_text("base")
        git(self.repo, "add", "f")
        git(self.repo, "commit", "-m", "base")
        self.board = Board(self.tmp / "work-plan.json")
        self.store = WorkParallelStore(self.tmp / "work-parallel.json")
        self.config = {"work_parallel_host": {"token": SECRET, "capability_ready": True}}
        runtime_patch = patch("ciel_runtime_support.runtime_paths.CONFIG_DIR", self.tmp / "config")
        runtime_patch.start()
        self.addCleanup(runtime_patch.stop)
        self.leases = [self.bound(item, "worker-" + str(index)) for index, item in enumerate(self.board.items)]

    def call(self, name, host=None, **args):
        if name != "item_review":
            args.setdefault("expected_version", self.store.read()["version"])
        args["_host"] = {"token": SECRET, **(host or {})}
        with patch.object(m, "ledger", return_value=self.store):
            return json.loads(m.run("work_parallel_" + name, args, self.board, SCOPE, self.config, None))

    def bound(self, item, worker):
        reserve = self.call("event", {"fork": True, "workspace_repo": str(self.repo)},
                            event="reserve", item_id=item, priority="P1")
        self.call("event", {"host_id": worker, "agent_name": worker}, event="bind", lease_id=reserve["lease_id"])
        (Path(reserve["workspace"]["path"]) / (worker + ".txt")).write_text("result")
        return {**reserve, "worker": worker, "item": item}

    def complete(self, lease):
        with patch.object(m, "ledger", return_value=self.store):
            m.report({"lease_id": lease["lease_id"], "worker_token": lease["worker_token"],
                      "event": "completion", "plan_version": self.board.read()["version"],
                      "artifact": "a" * 64, "evidence": "tests pass",
                      "detail": {"summary": "done", "coverage": [{"item_id": lease["item"], "verified": True}],
                                 "verification": "tests pass"}}, self.board, self.config)

    def native(self, status):
        rows = [{"lease_id": lease["lease_id"], "agent_id": lease["worker"], "agent_name": lease["worker"],
                 "status": status} for lease in self.leases]
        return self.call("event", {}, event="native_observation", rows=rows, complete=True,
                         expected_evidence_version=m.native_evidence(self.store).read()["version"])

    def checkpoint_a(self):
        """The recorded order up to the kill before the first review wake."""
        first, second = self.leases
        self.complete(first)
        self.native("running")
        self.complete(second)
        self.call("event", {"state": "idle", "proof": "Native agent.list status: idle"},
                  event="reconcile", lease_id=first["lease_id"])
        self.native("idle")
        return first, second

    def restart(self, lease, host=None, agent=None, **override):
        run = self.runs().get(lease["lease_id"]) or {}
        args = {"generation": run.get("generation", 1),
                "receipt_digest": (run.get("exit") or {}).get("digest") or "0" * 64, **override}
        return self.call("event", {**HOST, "absent_agent_id": agent or lease["worker"],
                                   "absent_from_complete_list": True, **(host or {})},
                         event="restart_submitted_terminal", lease_id=lease["lease_id"], **args)

    def stores(self):
        return (self.store.read(), m.coordination(self.store).read(), m.finalization(self.store).read(),
                m.native_evidence(self.store).read())

    def runs(self):
        return m.coordination(self.store).read()["runs"]

    def reviews(self):
        return m.finalization(self.store).read()["reviews"]

    def approve(self, lease):
        return self.call("item_review", {}, lease_id=lease["lease_id"], decision="approve",
                         decision_id="approve-1", evidence="main verified",
                         expected_finalization_version=self.call("status", {})["review"]["version"])

    def test_receipt_before_running_row_keeps_the_run_archivable(self):
        first, second = self.checkpoint_a()
        runs = self.runs()
        self.assertEqual(("terminal", "idle"), (runs[first["lease_id"]]["state"], runs[first["lease_id"]]["native_status"]))
        self.assertEqual("terminal", runs[second["lease_id"]]["state"])
        result = self.approve(first)
        self.assertEqual(("approve", "finalized"), (result["decision"], result["state"]), result)
        self.assertNotIn(first["lease_id"], self.runs())

    def test_absent_submitted_fork_after_restart_gets_its_review_once(self):
        first, second = self.checkpoint_a()
        self.assertNotIn(second["lease_id"], self.reviews())
        # Restart: neither fork is listed; a complete scan marks both missing.
        self.call("event", {}, event="native_observation", rows=[], complete=True,
                  expected_evidence_version=m.native_evidence(self.store).read()["version"])
        result = self.restart(second)
        run = self.runs()[second["lease_id"]]
        self.assertEqual({"lease_id": second["lease_id"], "generation": run["generation"],
                          "completion_receipt": run["exit"]["digest"], "observed_terminal": True,
                          "review_pending": True}, result)
        row = self.reviews()[second["lease_id"]]
        self.assertEqual(("pending", None, "pending", 0),
                         (row["state"], row["decision"], row["wake"]["state"], row["wake"]["attempts"]))
        lease = self.store.read()["leases"][second["lease_id"]]
        self.assertEqual(("submitted", False), (lease["state"], lease["slot"]))
        # Replays change nothing in any store.
        self.restart(second)
        versions = (self.store.read()["version"], m.coordination(self.store).read()["version"],
                    m.finalization(self.store).read()["version"])
        self.restart(second)
        self.restart(first)
        self.assertEqual(versions, (self.store.read()["version"], m.coordination(self.store).read()["version"],
                                    m.finalization(self.store).read()["version"]))
        # The already published first review keeps its original idle proof.
        self.assertEqual("Native agent.list status: idle", self.store.read()["leases"][first["lease_id"]]["idle_proof"])
        self.assertEqual("finalized", self.approve(first)["state"])
        self.assertEqual("finalized", self.approve(second)["state"])

    def test_restart_observation_is_host_only_and_exact(self):
        first, second = self.checkpoint_a()
        before = self.stores()
        receipt = self.runs()[second["lease_id"]]["exit"]["digest"]
        refusals = [
            ("mod-internal restart host", {"host": {"internal": False}}),
            # The shape the mod forwards for a model call: _host {agent_id} only.
            ("mod-internal restart host", {"host": {"internal": None, "agent_id": None}}),
            ("mod-internal restart host", {"host": {"agent_id": second["worker"]}}),
            ("complete current agent list", {"host": {"absent_from_complete_list": False}}),
            ("exact submitted fork absent", {"agent": first["worker"]}),
            ("exact durable completion receipt", {"generation": 2}),
            ("exact durable completion receipt", {"receipt_digest": "f" * 64}),
            ("exact durable completion receipt", {"receipt_digest": self.runs()[first["lease_id"]]["exit"]["digest"]}),
            ("exact generation and completion receipt", {"receipt_digest": "not-a-digest"}),
        ]
        for message, override in refusals:
            with self.subTest(override=override), self.assertRaisesRegex(WorkParallelError, message):
                self.restart(second, **override)
        self.assertNotEqual("f" * 64, receipt)
        self.assertEqual(before, self.stores())
        self.assertNotIn(second["lease_id"], self.reviews())

    def test_running_lease_without_a_receipt_is_never_observed_terminal(self):
        lease = self.leases[0]
        before = self.stores()
        with self.assertRaisesRegex(WorkParallelError, "submitted restart lease required"):
            self.restart(lease)
        self.assertEqual(before, self.stores())
        self.assertEqual("running", self.store.read()["leases"][lease["lease_id"]]["state"])

    def test_submitted_lease_without_a_completion_receipt_is_never_observed_terminal(self):
        # A legacy submitted lease has no coordination run, so no durable receipt.
        lease = self.leases[1]
        self.complete(lease)
        coord = m.coordination(self.store)
        raw = json.loads(coord.path.read_text(encoding="utf-8"))
        run = raw["runs"].pop(lease["lease_id"])
        coord.path.write_text(json.dumps(raw), encoding="utf-8")
        before = self.stores()
        with self.assertRaisesRegex(WorkParallelError, "exact durable completion receipt"):
            self.restart(lease, generation=run["generation"], receipt_digest=run["exit"]["digest"])
        self.assertEqual(before, self.stores())
        self.assertNotIn(lease["lease_id"], self.reviews())
        self.assertIsNone(self.store.read()["leases"][lease["lease_id"]].get("terminal"))

    def proofs(self, lease):
        from ciel_runtime_support import work_parallel_workspace as ws
        current = self.store.read()["leases"][lease["lease_id"]]
        repo, key, manifest = ws._identity(current["workspace"]["repo"], lease["lease_id"], None)
        workspace = json.loads(manifest.read_text(encoding="utf-8"))
        return current["idle_proof"], workspace["idle_proof"]

    def force_review_proof_divergence(self, lease):
        """review-restart-terminal probe_idle_restart_race.py order, deterministic.

        Restart starts publishing with its proof and pauses just before its
        workspace write. An ordinary idle reconcile completes in that window.
        """
        import threading
        from ciel_runtime_support import work_parallel_workspace as ws
        lease_id = lease["lease_id"]
        restart_at_workspace, normal_done = threading.Event(), threading.Event()
        original = ws.set_activity
        errors = []

        def gated(repo, ident, host_id, *, idle, proof=None, config_dir=None):
            if ident == lease_id and proof == m.RESTART_TERMINAL_PROOF and not restart_at_workspace.is_set():
                restart_at_workspace.set()
                if not normal_done.wait(10):
                    raise RuntimeError("normal reconcile did not finish")
            return original(repo, ident, host_id, idle=idle, proof=proof, config_dir=config_dir)

        def restart_call():
            try:
                self.restart(lease)
            except Exception as error:  # Reported below; the race is the subject.
                errors.append(("restart", type(error).__name__, str(error)))
        with patch.object(ws, "set_activity", side_effect=gated):
            thread = threading.Thread(target=restart_call)
            thread.start()
            self.assertTrue(restart_at_workspace.wait(10), "restart did not reach its workspace write")
            try:
                self.call("event", {"state": "idle", "proof": "Native agent.list status: idle"},
                          event="reconcile", lease_id=lease_id)
            finally:
                normal_done.set()
            thread.join(10)
            self.assertFalse(thread.is_alive())
        return errors

    def test_forced_restart_and_idle_reconcile_order_converges_without_a_new_idle_event(self):
        _, second = self.checkpoint_a()
        errors = self.force_review_proof_divergence(second)
        self.assertEqual([], errors)
        # One review, and its ledger and workspace proofs agree.
        self.assertIn(second["lease_id"], self.reviews())
        ledger_proof, workspace_proof = self.proofs(second)
        self.assertEqual(ledger_proof, workspace_proof)
        # Approve succeeds first time; nothing else is sent between.
        self.assertEqual("finalized", self.approve(second)["state"])

    def test_existing_review_with_mismatched_proofs_is_repaired_once_idempotently(self):
        _, second = self.checkpoint_a()
        self.call("event", {}, event="native_observation", rows=[], complete=True,
                  expected_evidence_version=m.native_evidence(self.store).read()["version"])
        self.restart(second)
        review_before = self.reviews()[second["lease_id"]]
        # The exact divergent end state of the reviewer's probe: ledger has the
        # ordinary proof, workspace kept the restart proof, review exists.
        from ciel_runtime_support import work_parallel_workspace as ws
        self.call("event", {"state": "idle", "proof": "Native agent.list status: idle"},
                  event="reconcile", lease_id=second["lease_id"])
        lease = self.store.read()["leases"][second["lease_id"]]
        ws.set_activity(lease["workspace"]["repo"], second["lease_id"], lease["host_id"], idle=True,
                        proof=m.RESTART_TERMINAL_PROOF)
        self.assertEqual(("Native agent.list status: idle", m.RESTART_TERMINAL_PROOF), self.proofs(second))
        with self.assertRaisesRegex(ValueError, "positive matching idle workspace proof"):
            self.approve(second)
        # One automatic repair through the existing host restart replay.
        self.restart(second)
        self.assertEqual(("Native agent.list status: idle", "Native agent.list status: idle"), self.proofs(second))
        review_after = self.reviews()[second["lease_id"]]
        self.assertEqual((review_before["wake"], review_before["workspace"]["receipt_digest"]),
                         (review_after["wake"], review_after["workspace"]["receipt_digest"]))
        stores = self.stores()
        self.restart(second)
        self.assertEqual(stores, self.stores())
        self.assertEqual("finalized", self.approve(second)["state"])

    def reopen_run(self, lease):
        coord = m.coordination(self.store)
        raw = json.loads(coord.path.read_text(encoding="utf-8"))
        raw["runs"][lease["lease_id"]]["state"] = "active"
        coord.path.write_text(json.dumps(raw), encoding="utf-8")

    def test_approve_blocked_at_archive_by_an_old_build_rolls_forward(self):
        # review-restart-terminal probe_blocked_archive.py: old build reopened
        # the run, approve was recorded, lease done, archive blocked.
        first, _ = self.checkpoint_a()
        self.reopen_run(first)
        result = self.approve(first)
        self.assertIn(result["state"], ("deciding", "finalized"))
        if result["state"] == "deciding":
            self.assertEqual("done", self.store.read()["leases"][first["lease_id"]]["state"])
            rolled = self.call("event", {"internal": True}, event="review_roll_forward", lease_id=first["lease_id"])
            result = rolled
        self.assertEqual("finalized", result["state"])
        self.assertEqual("finalized", self.reviews()[first["lease_id"]]["state"])
        self.assertNotIn(first["lease_id"], self.runs())
        archived = m.coordination(self.store).read()["archive"][-1]
        self.assertEqual((first["lease_id"], "main accepted"), (archived["run"]["lease_id"], archived["summary"]["outcome"]))

    def test_same_status_idle_then_approve_reaches_finalized(self):
        # probe_same_status.py: a same-status idle row is a native no-op.
        first, _ = self.checkpoint_a()
        self.reopen_run(first)
        self.native("idle")
        self.assertEqual("finalized", self.approve(first)["state"])

    def test_archive_repair_refuses_a_different_receipt_and_stays_blocked(self):
        first, _ = self.checkpoint_a()
        self.reopen_run(first)
        coord = m.coordination(self.store)
        raw = json.loads(coord.path.read_text(encoding="utf-8"))
        second_receipt = next(row["digest"] for row in raw["receipts"].values() if row["lease_id"] != first["lease_id"])
        reviewed = raw["runs"][first["lease_id"]]["exit"]["digest"]
        with self.assertRaisesRegex(WorkParallelError, "does not match the reviewed receipt"):
            coord.restore_completion_terminal(first["lease_id"], second_receipt,
                                              expected_version=coord.read()["version"])
        self.assertEqual("active", self.runs()[first["lease_id"]]["state"])
        self.assertNotEqual(reviewed, second_receipt)
        # After the decision is durable, the run's exit no longer equals the
        # reviewed receipt: the archive stage stays blocked with an explicit reason.
        original = m._board_accept

        def board_then_diverge(*args, **kwargs):
            board = original(*args, **kwargs)
            changed = json.loads(coord.path.read_text(encoding="utf-8"))
            changed["runs"][first["lease_id"]]["exit"] = {"kind": "completion", "digest": second_receipt}
            coord.path.write_text(json.dumps(changed), encoding="utf-8")
            return board
        with patch.object(m, "_board_accept", side_effect=board_then_diverge):
            result = self.approve(first)
        self.assertEqual("deciding", result["state"])
        self.assertIn("does not match the reviewed receipt", result["reason"])
        self.assertEqual("stage_failed", self.reviews()[first["lease_id"]]["blocked"]["code"])
        rolled = self.call("event", {"internal": True}, event="review_roll_forward", lease_id=first["lease_id"])
        self.assertEqual("deciding", rolled["state"])
        self.assertIn("does not match the reviewed receipt", rolled["reason"])
        self.assertEqual("active", self.runs()[first["lease_id"]]["state"])

    def test_run_reopened_by_an_older_build_is_restored_and_approve_finalizes(self):
        first, _ = self.checkpoint_a()
        # The exact checkpoint A state of the recorded run: review pending, run active.
        coord = m.coordination(self.store)
        raw = json.loads(coord.path.read_text(encoding="utf-8"))
        raw["runs"][first["lease_id"]]["state"] = "active"
        coord.path.write_text(json.dumps(raw), encoding="utf-8")
        self.restart(first)
        self.assertEqual("terminal", self.runs()[first["lease_id"]]["state"])
        self.assertEqual("finalized", self.approve(first)["state"])


if __name__ == "__main__":
    unittest.main()
