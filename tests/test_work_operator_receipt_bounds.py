"""The 128-receipt idempotency bound of board ``rest_operations``.

Board commands and operator commands share one map of at most 128 receipts
(57dbfb3 rejects a larger map). Eviction removes the oldest finished receipt
and never a prepared one, whose outcome is unknown. A refused effect gives back
the receipt it evicted, or, when that receipt's key was reused meanwhile, stays
recorded as refused. Each receipt carries a text-free request summary. A key
whose receipt was evicted is still refused by the command's own pin.
"""
import json
import tempfile
import threading
import unittest
from pathlib import Path

from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.http_response import HttpResponseAdapter
from ciel_runtime_support.work_application import (MAX_OPERATION_RECEIPTS, WorkApplication,
                                                   WorkOperationConflict)
from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices
from ciel_runtime_support.work_plan_store import session_work_plan_store

from test_work_http_parallel_gaps import serve

SCOPE = "claude:11111111-2222-3333-4444-777777777777"
RECEIPT = "c" * 64
SECRET_TEXT = "PRIVATE ANSWER TEXT"


class ReceiptBoundTests(unittest.TestCase):
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
        self.app = WorkApplication(self.board)
        self.controller = WorkHttpController(WorkHttpServices(
            self.state, lambda: {"work_mode": True}, lambda: SCOPE,
            lambda identity: {"session_id": identity, "last_observed": None},
            HttpResponseAdapter.write_json, "test-instance"))
        self.request = serve(self, self.controller)

    def receipts(self):
        return json.loads(self.board.path.read_text(encoding="utf-8")).get("rest_operations", {})

    def fill(self, count, state="committed", prefix="old-", board_keys=()):
        """Write ``count`` finished receipts, oldest first (created_at 1.0, 2.0, ...).

        A key in ``board_keys`` is written as a board-command receipt (no
        operator state) instead of an operator receipt.
        """
        def mutate(doc):
            receipts = doc.setdefault("rest_operations", {})
            for index in range(count):
                key = f"{prefix}{index:03d}"
                if key in board_keys:
                    receipts[key] = {"digest": f"{index:064x}", "command": "focus", "source": "rest",
                                     "origin": {}, "created_at": 1.0 + index, "response": {"ok": True}}
                    continue
                receipts[key] = {
                    "digest": f"{index:064x}", "command": "side_review", "source": "rest", "origin": {},
                    "state": state, "created_at": 1.0 + index,
                    "response": {"ok": state != "prepared", "state": state}}
        self.board.transaction(mutate, audit_only=True)

    # (a) never evict a prepared receipt -------------------------------------

    def test_prepare_evicts_the_oldest_finished_receipt_never_a_prepared_one(self):
        self.fill(1, state="prepared", prefix="unknown-")
        self.fill(MAX_OPERATION_RECEIPTS - 1)
        self.app.operator_prepare("parallel_configure", {"changes": {}, "expected_version": 0}, "new-1")
        receipts = self.receipts()
        self.assertEqual(MAX_OPERATION_RECEIPTS, len(receipts))
        self.assertIn("unknown-000", receipts)          # oldest overall, but its outcome is unknown
        self.assertNotIn("old-000", receipts)           # oldest finished receipt
        self.assertEqual("prepared", receipts["new-1"]["state"])

    def test_board_commands_never_evict_a_prepared_receipt_either(self):
        self.fill(1, state="prepared", prefix="unknown-")
        self.fill(MAX_OPERATION_RECEIPTS - 1)
        version = self.board.read()["version"]
        self.app.command("focus", {"expected_version": version, "node_ids": [], "user_requested": True}, "board-1")
        receipts = self.receipts()
        self.assertIn("unknown-000", receipts)
        self.assertNotIn("old-000", receipts)
        self.assertIn("board-1", receipts)

    def test_all_prepared_refuses_a_new_receipt_and_writes_nothing(self):
        self.fill(MAX_OPERATION_RECEIPTS, state="prepared", prefix="unknown-")
        before = self.board.path.read_bytes()
        status, body = self.request("/ca/work/api/v1/commands/parallel-configure", {
            "changes": {"paused": True}, "expected_version": 0}, {"Idempotency-Key": "full-1"})
        self.assertEqual((409, "operator_receipts_full"), (status, body["error"]))
        self.assertIn("resolve outcome-unknown operations first", body["message"])
        self.assertEqual(before, self.board.path.read_bytes())
        version = self.board.read()["version"]
        with self.assertRaises(WorkOperationConflict):
            self.app.command("focus", {"expected_version": version, "node_ids": [], "user_requested": True}, "board-2")
        self.assertEqual(before, self.board.path.read_bytes())

    # A refused effect gives back what its receipt evicted -------------------

    def test_refused_effect_restores_the_receipt_its_intent_evicted(self):
        self.fill(MAX_OPERATION_RECEIPTS)
        evicted = self.receipts()["old-000"]
        status, _ = self.request("/ca/work/api/v1/commands/side-review", {
            "receipt_ids": [RECEIPT], "verdict": "ok", "note": "", "expected_version": 0},
            {"Idempotency-Key": "refused-1"})
        self.assertEqual(409, status)  # stale board version: refused before any write
        receipts = self.receipts()
        self.assertNotIn("refused-1", receipts)
        self.assertEqual(evicted, receipts["old-000"])
        self.assertEqual(MAX_OPERATION_RECEIPTS, len(receipts))

    def test_refused_effect_stays_recorded_refused_when_the_evicted_key_was_reused(self):
        # old-000 is a board-command receipt: one key space (lead-rulings-work-lane-review-20261113.md
        # F2) forbids a board command from reusing a key an operator intent still holds.
        self.fill(MAX_OPERATION_RECEIPTS, board_keys={"old-000"})
        body = {"changes": {}, "expected_version": 0}
        self.app.operator_prepare("parallel_configure", body, "k-1")          # evicts old-000
        version = self.board.read()["version"]
        self.app.command("focus", {"expected_version": version, "node_ids": [], "user_requested": True}, "old-000")  # key reused
        reused = self.receipts()["old-000"]
        self.app.operator_abandon("parallel_configure", body, "k-1")
        receipts = self.receipts()
        self.assertEqual(reused, receipts["old-000"])
        self.assertEqual("refused", receipts["k-1"]["state"])
        self.assertLessEqual(len(receipts), MAX_OPERATION_RECEIPTS)
        # A refused key runs again for the same request; another request is a conflict.
        self.assertIsNone(self.app.operator_prepare("parallel_configure", body, "k-1"))
        self.assertEqual("prepared", self.receipts()["k-1"]["state"])

    def test_concurrent_abandon_and_prepare_only_touch_their_own_keys(self):
        body = {"changes": {}, "expected_version": 0}
        errors = []

        def abandon_loop():
            try:
                for index in range(40):
                    key = f"a-{index}"
                    self.app.operator_prepare("parallel_configure", body, key)
                    self.app.operator_abandon("parallel_configure", body, key)
            except Exception as error:  # surfaced below
                errors.append(error)

        def commit_loop():
            try:
                for index in range(40):
                    key = f"c-{index}"
                    self.app.operator_prepare("parallel_legacy_accept", body, key)
                    self.app.operator_commit("parallel_legacy_accept", body, key, {"lease_id": "L" + str(index)})
            except Exception as error:
                errors.append(error)
        threads = [threading.Thread(target=abandon_loop), threading.Thread(target=commit_loop)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual([], errors)
        receipts = self.receipts()
        self.assertEqual([], [key for key in receipts if key.startswith("a-")])
        self.assertEqual(40, len([key for key, row in receipts.items()
                                  if key.startswith("c-") and row["state"] == "committed"]))

    # Text-free request summary ---------------------------------------------

    def test_prepare_records_a_text_free_request_summary(self):
        # A started operation whose outcome is unknown: the summary is what the
        # person reconciles against, so it is written with the intent.
        self.app.operator_prepare("side_handoff", {
            "event_id": "e" * 64, "answer": SECRET_TEXT, "clear_wait": ["n2", "n3"], "activate": True,
            "expected_version": 3, "session_id": SCOPE}, "h-1")
        status, audit = self.request("/ca/work/api/v1/audit")
        self.assertEqual(200, status)
        row = next(row for row in audit["events"] if row["operation_id"] == "h-1")
        self.assertEqual({"event_id": "e" * 64, "expected_version": 3, "activate": True, "clear_wait_count": 2},
                         row["request"])
        self.assertNotIn(SECRET_TEXT, self.board.path.read_text(encoding="utf-8"))
        self.assertNotIn(SECRET_TEXT, json.dumps(audit))

    def test_the_summary_keeps_the_pins_the_request_was_decided_on(self):
        # The review pin (68 chars) and the settings fingerprint are pins, not
        # text: reconciling an outcome-unknown operation compares against them.
        from ciel_runtime_support.work_application import request_summary
        pin = "rp1:" + "a" * 64
        summary = request_summary({"lease_id": "l1", "decision": "approve", "expected_review_pin": pin,
                                   "expected_settings_fingerprint": "f" * 64, "expected_receipt_version": 2,
                                   "evidence": SECRET_TEXT})
        self.assertEqual({"lease_id": "l1", "decision": "approve", "expected_review_pin": pin,
                          "expected_settings_fingerprint": "f" * 64, "expected_receipt_version": 2}, summary)
        for bad in ("rp1:" + "A" * 64, "rp1:" + "a" * 63, "rp2:" + "a" * 64, pin + " " + SECRET_TEXT):
            self.assertNotIn("expected_review_pin", request_summary({"expected_review_pin": bad}))
        for bad in (SECRET_TEXT, "PRIVATE_WORD", "F" * 64, "f" * 63):
            self.assertNotIn("expected_settings_fingerprint",
                             request_summary({"expected_settings_fingerprint": bad}))

    # (b) replay safety after eviction ---------------------------------------

    def test_a_key_replayed_after_its_receipt_was_evicted_is_refused_by_the_command_pin(self):
        self.board.transaction(lambda d: d.setdefault("side_reviews", {}).update({RECEIPT: {
            "state": "pending", "kind": "info", "action": "answer", "summary": "s", "recorded_at": 1.0}}))
        version = self.board.read()["version"]
        body = {"receipt_ids": [RECEIPT], "verdict": "corrected", "note": "wrong", "expected_version": version}
        status, first = self.request("/ca/work/api/v1/commands/side-review", body, {"Idempotency-Key": "r-1"})
        self.assertEqual(200, status, first)
        # The receipt ages out of both bounds: the board mirror and the sidecar's resolved window.
        self.board.transaction(lambda d: d["rest_operations"].pop("r-1"), audit_only=True)
        from ciel_runtime_support.work_operator_intents import WorkOperatorIntents
        WorkOperatorIntents.beside(self.board.path).transaction(lambda intents: intents.pop("r-1", None))
        status, again = self.request("/ca/work/api/v1/commands/side-review", body, {"Idempotency-Key": "r-1"})
        self.assertEqual((409, "version_conflict"), (status, again["error"]))
        self.assertEqual("corrected", self.board.read()["side_reviews"][RECEIPT]["state"])

    def test_57dbfb3_reads_prepared_refused_and_evicted_receipts_at_the_bound(self):
        import test_work_parallel_rework_worktree as rw
        # old-000 is a board-command receipt: one key space (lead-rulings-work-lane-review-20261113.md
        # F2) forbids a board command from reusing a key an operator intent still holds.
        self.fill(MAX_OPERATION_RECEIPTS, board_keys={"old-000"})
        body = {"changes": {}, "expected_version": 0, "note": SECRET_TEXT}
        self.app.operator_prepare("parallel_configure", body, "k-1")            # holds an evicted copy
        version = self.board.read()["version"]
        self.app.command("focus", {"expected_version": version, "node_ids": [], "user_requested": True}, "old-000")
        self.app.operator_prepare("parallel_legacy_accept", body, "k-2")        # evicts old-001
        self.app.operator_abandon("parallel_configure", body, "k-1")           # refused: old-000 was reused
        raw = json.loads(self.board.path.read_text(encoding="utf-8"))
        states = {row.get("state") for row in raw["rest_operations"].values()}
        self.assertTrue({"prepared", "refused"} <= states, states)
        self.assertEqual(MAX_OPERATION_RECEIPTS, len(raw["rest_operations"]))
        beta = rw.beta_module("work_plan_tree").normalize_document(raw)
        self.assertEqual(set(raw["rest_operations"]), set(beta["rest_operations"]))
        self.assertNotIn(SECRET_TEXT, self.board.path.read_text(encoding="utf-8"))

    # (c) the bound is documented --------------------------------------------

    def test_openapi_documents_the_bound(self):
        from ciel_runtime_support.work_application import openapi_document
        text = json.dumps(openapi_document())
        self.assertIn("128", text)
        self.assertIn("operator_receipts_full", text)


if __name__ == "__main__":
    unittest.main()
