"""Focused tests for the host-owned Work coordination sidecar."""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ciel_runtime_support.work_parallel import WorkParallelConflict, WorkParallelError, WorkParallelStore
from ciel_runtime_support.work_parallel_receipts import WorkCoordinationStore, coordination_store

SCOPE = "claude:test"
FP = "a" * 64
SCOPE_DIGEST = "b" * 64
EVIDENCE = "c" * 64


def manifest(generation=1, role="worker"):
    return {"item_id": "i1", "root_id": "r1", "priority": "P2", "role": role,
            "generation": generation, "approval_generation": 1, "assigned_todos": ["i1"],
            "fingerprint": FP, "workspace": None, "created_at": 10 + generation}


def handoff():
    return {"done": ["implemented parser"], "remaining": ["run E2E"], "blockers": [],
            "workspace": {"path": "C:/repo", "branch": "work/l1", "head": "abc",
                          "dirty": True, "changed_files": ["a.py"], "diff_stat": "1 file changed"},
            "next_step": "run focused E2E"}


class CoordinationStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = WorkCoordinationStore(Path(self.tmp.name) / "work-parallel-coordination.json")

    def tearDown(self):
        self.tmp.cleanup()

    def approve(self):
        state = self.store.read()
        return self.store.reconcile_roots(SCOPE, [{"root_id": "r1", "phase": "executing",
                                                   "scope_digest": SCOPE_DIGEST}],
                                          expected_version=state["version"])[0]

    def assign(self, lease="l1", generation=1, role="worker"):
        state = self.store.read()
        return self.store.record_assignment(SCOPE, lease, manifest(generation, role),
                                            expected_version=state["version"])[0]

    def test_helper_path_read_noncreating_and_independent_cas(self):
        ledger = WorkParallelStore(Path(self.tmp.name) / "work-parallel.json")
        sidecar = coordination_store(ledger)
        self.assertEqual("work-parallel-coordination.json", sidecar.path.name)
        self.assertEqual(0, sidecar.read()["version"])
        self.assertFalse(sidecar.path.exists())
        state = sidecar.reconcile_roots(SCOPE, [{"root_id": "r1", "phase": "planning",
                                                "scope_digest": SCOPE_DIGEST}], expected_version=0)[0]
        self.assertEqual(1, state["version"])
        with self.assertRaises(WorkParallelConflict):
            sidecar.reconcile_roots(SCOPE, [], expected_version=0)

    def test_root_generation_only_on_entering_or_changed_executing_scope(self):
        state, generations = self.store.reconcile_roots(SCOPE, [{"root_id": "r1", "phase": "planning",
                                                                 "scope_digest": SCOPE_DIGEST}], expected_version=0)
        self.assertEqual(0, generations["r1"])
        state, generations = self.store.reconcile_roots(SCOPE, [{"root_id": "r1", "phase": "executing",
                                                                 "scope_digest": SCOPE_DIGEST}],
                                                        expected_version=state["version"])
        self.assertEqual(1, generations["r1"])
        state, generations = self.store.reconcile_roots(SCOPE, [{"root_id": "r1", "phase": "executing",
                                                                 "scope_digest": SCOPE_DIGEST}],
                                                        expected_version=state["version"])
        self.assertEqual(1, generations["r1"])
        state, generations = self.store.reconcile_roots(SCOPE, [{"root_id": "r1", "phase": "executing",
                                                                 "scope_digest": "d" * 64}],
                                                        expected_version=state["version"])
        self.assertEqual(2, generations["r1"])

    def test_workspace_attaches_before_native_bind(self):
        self.approve()
        state = self.assign()
        workspace = {"path": "C:/repo/wt", "branch": "work/l1", "head": "abc"}
        state, run = self.store.attach_workspace("l1", workspace, expected_version=state["version"])
        self.assertEqual(workspace, run["workspace"])
        state, same = self.store.attach_workspace("l1", workspace, expected_version=state["version"])
        self.assertEqual(workspace, same["workspace"])
        state, _ = self.store.bind_run("l1", "agent-1", "work-i1", expected_version=state["version"])
        with self.assertRaises(WorkParallelError):
            self.store.attach_workspace("l1", workspace, expected_version=state["version"])

    def test_unbound_reservation_cancel_archives_and_removes_run(self):
        self.approve()
        state = self.assign()
        state, _ = self.store.cancel_reserved("l1", outcome="spawn denied", expected_version=state["version"])
        self.assertNotIn("l1", state["runs"])
        self.assertEqual("spawn denied", state["archive"][-1]["summary"]["outcome"])

    def test_exact_run_contract_native_exit_and_recovery(self):
        self.approve()
        state = self.assign()
        run = state["runs"]["l1"]
        self.assertEqual({"scope", "lease_id", "item_id", "root_id", "priority", "role", "generation",
                          "approval_generation", "fingerprint", "agent_id", "agent_name", "state", "workspace",
                          "reserved_at", "bound_at", "state_changed_at", "native_status", "native_seen_at",
                          "exit", "exit_attempts", "recovery", "replaced_by", "assigned_todos"}, set(run))
        state, run = self.store.bind_run("l1", "agent-1", "work-i1", expected_version=state["version"])
        self.assertEqual("bound", run["state"])
        state, run = self.store.update_native("l1", "running", expected_version=state["version"])
        self.assertEqual("active", run["state"])
        state, attempts = self.store.exit_attempt("l1", expected_version=state["version"])
        self.assertEqual(1, attempts)
        state, recovery = self.store.mark_recovery("l1", "resuming", error_code="native_uncertain",
                                                   expected_version=state["version"])
        self.assertEqual(1, recovery["attempts"])

    def completion(self):
        return {"summary": "done", "coverage": [{"item_id": "i1", "verified": True}],
                "verification": "focused test passed"}

    def test_late_native_observation_never_reopens_a_completed_run(self):
        # run-20261111-105012: the completion receipt landed while the fork was
        # still listed running; the first native observation came after it.
        self.approve()
        state = self.assign()
        state, _ = self.store.bind_run("l1", "agent-1", "work-i1", expected_version=state["version"])
        state, _ = self.store.persist_receipt("l1", "completion", self.completion(),
                                              expected_version=state["version"])
        self.assertEqual("terminal", state["runs"]["l1"]["state"])
        for status in ("running", "idle", "waiting", "pending"):
            state, run = self.store.update_native("l1", status, expected_version=state["version"])
            self.assertEqual(("terminal", status), (run["state"], run["native_status"]))
        state, _ = self.store.archive_terminal("l1", {"state": "done", "outcome": "main accepted"},
                                               expected_version=state["version"])
        self.assertNotIn("l1", state["runs"])

    def test_receipt_terminal_survives_every_native_order(self):
        import itertools
        statuses = ("pending", "running", "waiting", "idle", "missing")
        self.approve()
        state = self.store.read()
        cases = 0
        for before in ((), ("running",), ("running", "idle")):
            for after in itertools.permutations(statuses, 2):
                lease = "p%d" % cases
                cases += 1
                state, _ = self.store.record_assignment(SCOPE, lease, manifest(), expected_version=state["version"])
                state, _ = self.store.bind_run(lease, "agent-" + lease, "work-" + lease, expected_version=state["version"])
                for status in before:
                    state, _ = self.store.update_native(lease, status, expected_version=state["version"])
                state, _ = self.store.persist_receipt(lease, "completion", dict(self.completion(), summary=lease),
                                                      expected_version=state["version"])
                for status in after:
                    state, run = self.store.update_native(lease, status, expected_version=state["version"])
                    self.assertEqual("terminal", run["state"], (before, after, status))
                state, _ = self.store.archive_terminal(lease, {"state": "done", "outcome": "main accepted"},
                                                       expected_version=state["version"])
        self.assertEqual(60, cases)

    def test_completion_receipt_is_final_against_later_receipts(self):
        self.approve()
        state = self.assign()
        state, _ = self.store.bind_run("l1", "agent-1", "work-i1", expected_version=state["version"])
        state, first = self.store.persist_receipt("l1", "completion", self.completion(),
                                                  expected_version=state["version"])
        for kind, body in (("host_salvage", handoff()), ("handoff", handoff()),
                           ("question", {"question": "q", "blocking_todos": [], "decision_needed": "d"})):
            with self.subTest(kind=kind), self.assertRaisesRegex(WorkParallelError, "exit receipt is final"):
                self.store.persist_receipt("l1", kind, body, expected_version=state["version"])
        # The identical completion replay stays idempotent.
        state, same = self.store.persist_receipt("l1", "completion", self.completion(),
                                                 expected_version=state["version"])
        self.assertEqual((first, "terminal", {"kind": "completion", "digest": first}),
                         (same, state["runs"]["l1"]["state"], state["runs"]["l1"]["exit"]))

    def test_handoff_stays_handed_off_and_question_still_resumes(self):
        self.approve()
        state = self.assign("h1")
        state = self.assign("q1", 2)
        for lease in ("h1", "q1"):
            state, _ = self.store.bind_run(lease, "agent-" + lease, "work-" + lease,
                                           expected_version=state["version"])
        state, _ = self.store.persist_receipt("h1", "handoff", handoff(), expected_version=state["version"])
        state, run = self.store.update_native("h1", "running", expected_version=state["version"])
        self.assertEqual("handed_off", run["state"])
        question = {"question": "which API?", "blocking_todos": ["i1"], "decision_needed": "pick one"}
        state, _ = self.store.persist_receipt("q1", "question", question, expected_version=state["version"])
        self.assertEqual("waiting", state["runs"]["q1"]["state"])
        # A question is not an exit: the same worker resumes after its answer.
        state, run = self.store.update_native("q1", "running", expected_version=state["version"])
        self.assertEqual("active", run["state"])

    def test_run_reopened_by_an_older_build_is_restored_to_its_receipt_state(self):
        self.approve()
        state = self.assign()
        state, _ = self.store.bind_run("l1", "agent-1", "work-i1", expected_version=state["version"])
        state, _ = self.store.persist_receipt("l1", "completion", self.completion(),
                                              expected_version=state["version"])
        raw = json.loads(self.store.path.read_text(encoding="utf-8"))
        raw["runs"]["l1"].update(state="active", native_status="idle")
        self.store.path.write_text(json.dumps(raw), encoding="utf-8")
        state = self.store.read()
        state, run = self.store.update_native("l1", "missing", expected_version=state["version"])
        self.assertEqual("terminal", run["state"])

    def test_structured_handoff_is_stored_fetchable_and_absent_from_projection(self):
        self.approve()
        state = self.assign()
        state, receipt_digest = self.store.persist_receipt("l1", "handoff", handoff(),
                                                           expected_version=state["version"])
        self.assertEqual(handoff(), self.store.detail(receipt_digest))
        state, same_digest = self.store.persist_receipt("l1", "handoff", detail=handoff(),
                                                        detail_digest=receipt_digest,
                                                        expected_version=state["version"])
        self.assertEqual(receipt_digest, same_digest)
        projection = self.store.projection(SCOPE)
        self.assertEqual(receipt_digest, projection["receipts"][0]["digest"])
        rendered = json.dumps(projection)
        self.assertNotIn("implemented parser", rendered)
        self.assertNotIn("next_step", rendered)
        before = state["version"]
        state, repeated = self.store.persist_receipt("l1", "handoff", handoff(), expected_version=before)
        self.assertEqual(receipt_digest, repeated)
        self.assertEqual(before, state["version"])

    def test_strict_receipt_schema_and_forbidden_run_payload(self):
        self.approve()
        state = self.assign()
        bad = handoff()
        bad["transcript"] = "forbidden"
        with self.assertRaises(WorkParallelError):
            self.store.persist_receipt("l1", "handoff", bad, expected_version=state["version"])
        raw = json.loads(self.store.path.read_text(encoding="utf-8"))
        raw["runs"]["l1"]["reasoning"] = "forbidden"
        self.store.path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaisesRegex(WorkParallelError, "corrupt Work coordination"):
            self.store.read()

    def test_supersede_archives_old_generation_and_terminal_archive_is_bounded_shape(self):
        self.approve()
        state = self.assign("old", 1)
        state = self.assign("new", 2, role="resume")
        state, replacement = self.store.supersede("old", "new", expected_version=state["version"])
        self.assertEqual("new", replacement["lease_id"])
        self.assertNotIn("old", state["runs"])
        self.assertEqual("new", state["archive"][-1]["run"]["replaced_by"])
        completion = {"summary": "done", "coverage": [{"item_id": "i1", "verified": True,
                                                          "evidence_digest": EVIDENCE}],
                      "verification": "focused test passed"}
        state, receipt_digest = self.store.persist_receipt("new", "completion", completion,
                                                           expected_version=state["version"])
        state, row = self.store.archive_terminal("new", {"state": "done", "outcome": "accepted",
                                                          "receipt_digest": receipt_digest, "published": True},
                                                  expected_version=state["version"])
        self.assertNotIn("new", state["runs"])
        self.assertEqual("accepted", row["summary"]["outcome"])

    def test_fixed_diagnostic_codes_and_bounded_projection(self):
        self.approve()
        state = self.assign()
        state, row = self.store.diagnostic(SCOPE, "P2", "capacity", expected_version=state["version"])
        self.assertEqual(1, row["count"])
        state, row = self.store.diagnostic(SCOPE, "P2", "capacity", expected_version=state["version"])
        self.assertEqual(2, row["count"])
        with self.assertRaises(WorkParallelError):
            self.store.diagnostic(SCOPE, "P2", "free text", expected_version=state["version"])
        self.assertEqual(1, len(self.store.projection(SCOPE, limit=1)["runs"]))


if __name__ == "__main__":
    unittest.main()
