"""A person resolves an operator command whose outcome is unknown, without re-running it.

operation-reconcile reads the store the command writes and records what it
observed (applied, not_applied or not_observable) on the receipt;
operation-release gives up on a not_observable outcome, pinned to the receipt
version the person saw. Neither runs the effect, both work when all 128
receipts are outcome-unknown, and /audit shows outcome, last_reconcile and
receipt_version. REST and ciel-runtimectl use the same path.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.http_response import HttpResponseAdapter
from ciel_runtime_support.work_application import MAX_OPERATION_RECEIPTS, WorkApplication
from ciel_runtime_support.work_ctl import CtlError, WorkCtl
from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices
from ciel_runtime_support.work_plan_store import session_work_plan_store

from test_work_http_parallel_gaps import serve

SCOPE = "claude:11111111-2222-3333-4444-888888888888"
EVENT = "e" * 64
LEASE = "a" * 32
REVIEW = "/ca/work/api/v1/commands/parallel-item-review"
RECONCILE = "/ca/work/api/v1/commands/operation-reconcile"
RELEASE = "/ca/work/api/v1/commands/operation-release"


class FakeJournal:
    def __init__(self, rows):
        self.rows = rows

    def read(self):
        return {"version": 1, "reviews": self.rows, "rows": {}}


class OperationReconcileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name) / "state"
        self.board = session_work_plan_store(self.state, SCOPE)
        doc = self.board.read()
        self.board.transaction(lambda d: tree.set_plan(d, {
            "title": "Goal", "note": "", "priority": "P1", "children": [
                {"title": "Item", "note": "", "ref": "", "waiting_on": "", "children": []}]}, None),
            expected_version=doc["version"])
        self.board.transaction(lambda d: d.setdefault("side_handoffs", {}).update({EVENT: {
            "state": "requested", "kind": "request", "action": "update", "summary": "s", "gates": [],
            "requested_at": 1.0}}))
        self.ledger = mock.Mock()
        self.ledger.path = str(Path(temporary.name) / "work-parallel.json")
        self.reviews = {}
        self.controller = WorkHttpController(WorkHttpServices(
            self.state, lambda: {"work_mode": True}, lambda: SCOPE,
            lambda identity: {"session_id": identity, "last_observed": None},
            HttpResponseAdapter.write_json, "test-instance", parallel_store=lambda: self.ledger))
        self.request = serve(self, self.controller)
        journal = mock.patch("ciel_runtime_support.work_parallel_mcp.finalization",
                             lambda store=None: FakeJournal(self.reviews))
        side = mock.patch("ciel_runtime_support.work_parallel_mcp.stranded",
                          lambda store=None: FakeJournal({}))
        journal.start(), side.start()
        self.addCleanup(journal.stop), self.addCleanup(side.stop)

    def receipt(self, key):
        return json.loads(self.board.path.read_text(encoding="utf-8"))["rest_operations"][key]

    def audit_row(self, key):
        status, audit = self.request("/ca/work/api/v1/audit")
        return next(row for row in audit["events"] if row["operation_id"] == key)

    def unknown_review(self, key, decision="approve"):
        """A decision whose receipt commit was lost: the receipt stays prepared."""
        effect = mock.Mock(return_value={"decision": decision, "lease_id": LEASE, "state": "finalized"})
        with mock.patch("ciel_runtime_support.work_parallel_mcp._main_review", effect), \
                mock.patch("ciel_runtime_support.work_application.WorkApplication.operator_commit",
                           side_effect=OSError("board busy")):
            status, _ = self.request(REVIEW, {"lease_id": LEASE, "decision": decision, "evidence": "ok",
                                              "expected_finalization_version": 4}, {"Idempotency-Key": key})
        self.assertEqual(503, status)
        self.assertEqual("prepared", self.receipt(key)["state"])
        return effect

    def test_audit_shows_outcome_receipt_version_and_no_reconcile_yet(self):
        self.unknown_review("u-1")
        row = self.audit_row("u-1")
        self.assertEqual(("unknown", 1, None), (row["outcome"], row["receipt_version"], row["last_reconcile"]))

    def test_reconcile_applied_commits_the_receipt_without_rerunning(self):
        effect = self.unknown_review("u-1")
        self.reviews[LEASE] = {"scope": SCOPE, "decision": "approve", "launch": {}}
        status, body = self.request(RECONCILE, {"operation_id": "u-1", "confirmed": True}, {"Idempotency-Key": "x"})
        self.assertEqual(200, status, body)
        self.assertEqual({"operation_id": "u-1", "command": "parallel_item_review", "outcome": "applied",
                          "observed": "decision_recorded", "receipt_version": 2}, body["result"])
        row = self.audit_row("u-1")
        self.assertEqual(("applied", "committed", "applied"),
                         (row["outcome"], row["state"], row["last_reconcile"]["result"]))
        # A replay of the original key returns the reconciled result; the effect never runs again.
        with mock.patch("ciel_runtime_support.work_parallel_mcp._main_review", effect):
            status, replay = self.request(REVIEW, {"lease_id": LEASE, "decision": "approve", "evidence": "ok",
                                                   "expected_finalization_version": 4}, {"Idempotency-Key": "u-1"})
        self.assertEqual((200, True), (status, replay["replayed"]))
        self.assertEqual(1, effect.call_count)

    def test_reconcile_not_applied_finishes_the_key(self):
        effect = self.unknown_review("u-1")
        self.reviews[LEASE] = {"scope": SCOPE, "decision": None, "launch": {}}
        status, body = self.request(RECONCILE, {"operation_id": "u-1", "confirmed": True})
        self.assertEqual((200, "not_applied", "decision_absent"),
                         (status, body["result"]["outcome"], body["result"]["observed"]))
        with mock.patch("ciel_runtime_support.work_parallel_mcp._main_review", effect):
            status, again = self.request(REVIEW, {"lease_id": LEASE, "decision": "approve", "evidence": "ok",
                                                  "expected_finalization_version": 4}, {"Idempotency-Key": "u-1"})
        self.assertEqual((409, "idempotency_conflict"), (status, again["error"]))
        self.assertIn("new key", again["message"])
        self.assertEqual(1, effect.call_count)

    def test_not_observable_then_release_pinned_to_the_receipt_version(self):
        self.unknown_review("u-1")
        status, body = self.request(RECONCILE, {"operation_id": "u-1", "confirmed": True})
        self.assertEqual((200, "unknown", "review_absent", 2),
                         (status, body["result"]["outcome"], body["result"]["observed"],
                          body["result"]["receipt_version"]))
        row = self.audit_row("u-1")
        self.assertEqual(("unknown", "not_observable", 2),
                         (row["outcome"], row["last_reconcile"]["result"], row["receipt_version"]))
        status, stale = self.request(RELEASE, {"operation_id": "u-1", "confirmed": True,
                                               "expected_receipt_version": 1})
        self.assertEqual((409, "receipt_changed"), (status, stale["error"]))
        status, body = self.request(RELEASE, {"operation_id": "u-1", "confirmed": True,
                                              "expected_receipt_version": 2})
        self.assertEqual((200, "released_unverified", 3),
                         (status, body["result"]["outcome"], body["result"]["receipt_version"]))
        row = self.audit_row("u-1")
        self.assertEqual(("released_unverified", None), (row["outcome"], row["last_reconcile"]))

    def test_release_requires_a_not_observable_reconcile_first(self):
        self.unknown_review("u-1")
        status, body = self.request(RELEASE, {"operation_id": "u-1", "confirmed": True,
                                              "expected_receipt_version": 1})
        self.assertEqual((409, "reconcile_required"), (status, body["error"]))

    def test_refusals_unknown_known_and_unconfirmed(self):
        status, body = self.request(RECONCILE, {"operation_id": "nope", "confirmed": True})
        self.assertEqual((404, "unknown_operation"), (status, body["error"]))
        self.unknown_review("u-1")
        status, body = self.request(RECONCILE, {"operation_id": "u-1"})
        self.assertEqual(422, status)
        self.reviews[LEASE] = {"scope": SCOPE, "decision": "approve", "launch": {}}
        self.request(RECONCILE, {"operation_id": "u-1", "confirmed": True})
        status, body = self.request(RECONCILE, {"operation_id": "u-1", "confirmed": True})
        self.assertEqual((409, "operation_not_unknown"), (status, body["error"]))
        status, body = self.request(RELEASE, {"operation_id": "u-1", "confirmed": True,
                                              "expected_receipt_version": 2})
        self.assertEqual((409, "operation_not_unknown"), (status, body["error"]))

    def test_side_handoff_is_observed_from_the_board(self):
        # The intent was written and the process stopped before the effect.
        WorkApplication(self.board).operator_prepare(
            "side_handoff", {"event_id": EVENT, "answer": "", "expected_version": 3}, "h-1")
        status, body = self.request(RECONCILE, {"operation_id": "h-1", "confirmed": True})
        self.assertEqual(("not_applied", "handoff_requested"), (body["result"]["outcome"], body["result"]["observed"]))

    def test_resolutions_work_when_every_receipt_is_outcome_unknown(self):
        def fill(doc):
            receipts = doc.setdefault("rest_operations", {})
            for index in range(MAX_OPERATION_RECEIPTS):
                receipts[f"u-{index:03d}"] = {
                    "digest": f"{index:064x}", "command": "settings_update", "source": "rest", "origin": {},
                    "state": "prepared", "created_at": 1.0 + index, "request": {"key": "k"},
                    "response": {"ok": False, "state": "prepared"}}
        self.board.transaction(fill, audit_only=True)
        status, body = self.request(RECONCILE, {"operation_id": "u-000", "confirmed": True}, {"Idempotency-Key": "n"})
        self.assertEqual((200, "unknown", "no_stored_proof"),
                         (status, body["result"]["outcome"], body["result"]["observed"]))
        status, body = self.request(RELEASE, {"operation_id": "u-000", "confirmed": True,
                                              "expected_receipt_version": 2})
        self.assertEqual((200, "released_unverified"), (status, body["result"]["outcome"]))
        self.assertEqual(MAX_OPERATION_RECEIPTS, len(json.loads(self.board.path.read_text())["rest_operations"]))

    def test_ctl_reconcile_and_release_use_the_same_path(self):
        self.unknown_review("u-1")
        ctl = WorkCtl(lambda: {"work_mode": True}, lambda value: None, self.state)
        with mock.patch("ciel_runtime_support.work_parallel_mcp.ledger", return_value=self.ledger):
            result = ctl.post(SCOPE, "operation-reconcile", {"operation_id": "u-1", "confirmed": True}, None)
            self.assertEqual(("unknown", "review_absent", 2), (result["result"]["outcome"], result["result"]["observed"],
                                                                result["result"]["receipt_version"]))
            released = ctl.post(SCOPE, "operation-release", {"operation_id": "u-1", "confirmed": True,
                                                             "expected_receipt_version": 2}, None)
            self.assertEqual("released_unverified", released["result"]["outcome"])
            with self.assertRaises(CtlError) as raised:
                ctl.post(SCOPE, "operation-release", {"operation_id": "u-1", "confirmed": True,
                                                      "expected_receipt_version": 3}, None)
        self.assertEqual("operation_not_unknown", raised.exception.code)
        self.assertEqual("ctl", self.receipt("u-1")["resolution"]["source"])

    def test_late_commit_after_a_release_records_the_effect(self):
        app = WorkApplication(self.board)
        body = {"lease_id": LEASE, "decision": "approve", "evidence": "ok", "expected_finalization_version": 4}
        app.operator_prepare("parallel_item_review", body, "late-1")
        self.request(RECONCILE, {"operation_id": "late-1", "confirmed": True})
        self.request(RELEASE, {"operation_id": "late-1", "confirmed": True, "expected_receipt_version": 2})
        app.operator_commit("parallel_item_review", body, "late-1", {"lease_id": LEASE, "state": "finalized"})
        self.assertEqual("applied", self.audit_row("late-1")["outcome"])

    def test_57dbfb3_reads_resolved_receipts(self):
        import test_work_parallel_rework_worktree as rw
        self.unknown_review("u-1")
        self.request(RECONCILE, {"operation_id": "u-1", "confirmed": True})
        self.unknown_review("u-2")
        self.request(RECONCILE, {"operation_id": "u-2", "confirmed": True})
        self.request(RELEASE, {"operation_id": "u-2", "confirmed": True, "expected_receipt_version": 2})
        raw = json.loads(self.board.path.read_text(encoding="utf-8"))
        self.assertEqual({"prepared", "released_unverified"}, {row["state"] for row in raw["rest_operations"].values()})
        beta = rw.beta_module("work_plan_tree").normalize_document(raw)
        self.assertEqual({"u-1", "u-2"}, set(beta["rest_operations"]))

    def test_openapi_documents_both_resolutions(self):
        from ciel_runtime_support.work_application import openapi_document
        paths = openapi_document()["paths"]
        for name, required in (("operation-reconcile", {"operation_id", "confirmed"}),
                               ("operation-release", {"operation_id", "confirmed", "expected_receipt_version"})):
            schema = paths["/ca/work/api/v1/commands/" + name]["post"]["requestBody"]["content"][
                "application/json"]["schema"]
            self.assertEqual(required, set(schema["required"]))
        text = json.dumps(paths["/ca/work/api/v1/commands/operation-release"])
        self.assertIn("may already have happened", text)
        self.assertIn("reconcile_required", json.dumps(openapi_document()))


if __name__ == "__main__":
    unittest.main()
