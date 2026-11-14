"""Operator and REST errors are classified by exception type, not by message words.

Unreadable stored Work state (board, parallel ledger and its journals, failure
log) answers 503 ``state_unavailable`` with the fixed message "Work state
unreadable". A refused request answers 422 with its message, even when that
message happens to contain "snapshot" or "stored". A stale version is 409.
"""
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.http_response import HttpResponseAdapter
from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices
from ciel_runtime_support.work_parallel import WorkParallelError, WorkParallelStore
from ciel_runtime_support.work_parallel_failures import WorkParallelFailureLog
from ciel_runtime_support.work_parallel_finalization import WorkParallelFinalizationStore
from ciel_runtime_support.work_parallel_native import NativeForkEvidence
from ciel_runtime_support.work_parallel_receipts import WorkCoordinationStore
from ciel_runtime_support.work_parallel_stranded import WorkParallelStrandedStore
from ciel_runtime_support.work_plan_store import WorkPlanStore, session_work_plan_store

from test_work_http_parallel_gaps import serve

SCOPE = "claude:11111111-2222-3333-4444-666666666666"
UNREADABLE = {"ok": False, "error": "state_unavailable", "message": "Work state unreadable"}


class Captured:
    def __init__(self):
        self.status = self.payload = None


def classify(state, error):
    def write(target, payload, status=200):
        target.status, target.payload = status, payload
    controller = WorkHttpController(WorkHttpServices(
        state, lambda: {"work_mode": True}, lambda: SCOPE, lambda identity: {"session_id": identity},
        write, "test-instance"))
    handler = Captured()
    controller._error(handler, error)
    return handler.status, handler.payload


class TypedClassificationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / "state"
        self.ledger_path = self.root / "parallel" / "work-parallel.json"
        self.ledger_path.parent.mkdir(parents=True)
        self.controller = WorkHttpController(WorkHttpServices(
            self.state, lambda: {"work_mode": True}, lambda: SCOPE,
            lambda identity: {"session_id": identity, "last_observed": None},
            HttpResponseAdapter.write_json, "test-instance",
            parallel_store=lambda: WorkParallelStore(self.ledger_path)))
        self.request = serve(self, self.controller)

    def corrupt(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"{not json")
        return path

    def raised(self, read):
        with self.assertRaises(Exception) as caught:
            read()
        return caught.exception

    # Real request paths ------------------------------------------------------

    def test_corrupt_finalization_journal_on_item_review_is_503(self):
        self.corrupt(self.ledger_path.with_name("work-parallel-finalization.json"))
        status, body = self.request("/ca/work/api/v1/commands/parallel-item-review", {
            "lease_id": "L1", "decision": "approve", "evidence": "ok", "expected_finalization_version": 0},
            {"Idempotency-Key": "fin-1"})
        self.assertEqual((503, UNREADABLE), (status, body))

    def test_corrupt_ledger_on_parallel_configure_is_503(self):
        self.corrupt(self.ledger_path)
        status, body = self.request("/ca/work/api/v1/commands/parallel-configure", {
            "changes": {"paused": True}, "expected_version": 0}, {"Idempotency-Key": "cfg-1"})
        self.assertEqual((503, UNREADABLE), (status, body))

    def test_unreadable_board_on_an_operator_write_is_503(self):
        board = session_work_plan_store(self.state, SCOPE)
        board.path.mkdir(parents=True)  # a directory where the board file belongs: open() fails
        status, body = self.request("/ca/work/api/v1/commands/side-review", {
            "receipt_ids": ["c" * 64], "verdict": "ok", "note": "", "expected_version": 0},
            {"Idempotency-Key": "board-1"})
        self.assertEqual((503, UNREADABLE), (status, body))

    # Every journal's corrupt read, through the one classifier ---------------

    def test_every_journal_corrupt_read_is_unavailable(self):
        reads = {
            "ledger": lambda: WorkParallelStore(self.corrupt(self.root / "a" / "work-parallel.json")).read(),
            "finalization": lambda: WorkParallelFinalizationStore(
                self.corrupt(self.root / "b" / "work-parallel-finalization.json")).read(),
            "stranded": lambda: WorkParallelStrandedStore(
                self.corrupt(self.root / "c" / "work-parallel-stranded-v1.json")).read(),
            "coordination": lambda: WorkCoordinationStore(
                self.corrupt(self.root / "d" / "work-parallel-coordination.json")).read(),
            "native": lambda: NativeForkEvidence(self.corrupt(self.root / "e" / "native.json")).read(),
            "failures": lambda: WorkParallelFailureLog(
                self.corrupt(self.root / "f" / "work-parallel-failures-v1.json")).read(),
            "board": lambda: WorkPlanStore(self.corrupt(self.root / "g" / "work-plan.json")).read_snapshot(),
        }
        for name, read in reads.items():
            with self.subTest(journal=name):
                self.assertEqual((503, UNREADABLE), classify(self.state, self.raised(read)))

    def test_refusals_naming_snapshot_or_stored_are_422_with_their_message(self):
        for error in (WorkParallelError("stale completion workspace snapshot; result retained"),
                      tree.WorkPlanError("the stored receipt id is not a side review")):
            with self.subTest(message=str(error)):
                status, body = classify(self.state, error)
                self.assertEqual((422, "invalid_request", str(error)),
                                 (status, body["error"], body["message"]))

    def test_os_error_stays_503(self):
        self.assertEqual((503, UNREADABLE), classify(self.state, OSError("disk")))


if __name__ == "__main__":
    unittest.main()
