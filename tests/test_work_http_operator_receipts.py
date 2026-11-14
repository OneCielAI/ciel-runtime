"""Operator audit receipts hold no text, path or secret, and never repeat an effect.

The receipt in board ``rest_operations`` is a bounded summary of short id-like
words and numbers; a replay returns that summary. Because no transaction spans
the effect's store and the board, the receipt is written as an intent first:
a refused effect drops it, a committed one replays, and an attempt whose
receipt could not be completed is refused instead of being run again.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ciel_runtime_support.http_response import HttpResponseAdapter
from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices
from ciel_runtime_support.work_parallel import WorkParallelConflict
from ciel_runtime_support.work_plan_store import session_work_plan_store

from test_work_http_parallel_gaps import serve

SCOPE = "claude:operator-receipts"
TEXT = "EXTERNAL ORIGINAL TEXT with a reply body"
TOKEN = "host-token-" + "f00d" * 8
REPLY_TOKEN = "reply-token-" + "beef" * 8
PATH = "C:/secret/repo/.claude/worktrees/w1"


class OperatorReceiptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name) / "state"
        self.ledger = mock.Mock()
        self.ledger.path = str(Path(temporary.name) / "work-parallel.json")
        self.controller = WorkHttpController(WorkHttpServices(
            self.state, lambda: {"work_mode": True}, lambda: SCOPE,
            lambda identity: {"session_id": identity, "last_observed": None},
            HttpResponseAdapter.write_json, "test-instance", parallel_store=lambda: self.ledger))
        self.request = serve(self, self.controller)
        self.board = session_work_plan_store(self.state, SCOPE)

    def board_text(self):
        return self.board.path.read_text(encoding="utf-8") if self.board.path.exists() else ""

    def assert_no_secret(self, text):
        for secret in (TEXT, TOKEN, REPLY_TOKEN, PATH, "reply body"):
            self.assertNotIn(secret, text)

    def test_side_reply_retry_receipt_and_replay_hold_no_text_or_token(self):
        raw = {"receipt_id": "a" * 64, "accepted": True, "stage": "processed", "original": TEXT,
               "recap": TEXT, "result": {"reply": TEXT, "final_text": TEXT}, "context": {"reply_token": REPLY_TOKEN},
               "processing": {"owner": TOKEN, "generation": 3}, "main_fallback": {"reason": TEXT}}
        body = {"receipt_id": "a" * 64, "expected_state": "uncertain", "confirmed": True}
        with mock.patch("ciel_runtime_support.side_reply_outbox.retry", return_value=raw) as retry:
            status, first = self.request("/ca/work/api/v1/commands/side-reply-retry", body, {"Idempotency-Key": "r-1"})
            self.assertEqual(200, status, first)
            status, again = self.request("/ca/work/api/v1/commands/side-reply-retry", body, {"Idempotency-Key": "r-1"})
        self.assertEqual(1, retry.call_count)
        self.assertEqual((200, True), (status, again["replayed"]))
        self.assertEqual({"receipt_id": "a" * 64}, again["result"])
        self.assert_no_secret(self.board_text())
        self.assert_no_secret(json.dumps(again))

    def test_item_review_receipt_drops_the_dispatch_workspace_path(self):
        decided = {"decision": "rework", "lease_id": "L1", "item_id": "n2", "state": "rework_ready",
                   "finalization_version": 5, "dispatch": {"workspace": PATH, "lease_id": "R2"},
                   "reason": "blocked at " + PATH}
        with mock.patch("ciel_runtime_support.work_parallel_mcp._main_review", return_value=decided):
            status, body = self.request("/ca/work/api/v1/commands/parallel-item-review", {
                "lease_id": "L1", "decision": "rework", "evidence": "fix", "expected_finalization_version": 4},
                {"Idempotency-Key": "rev-1"})
        self.assertEqual(200, status, body)
        stored = json.loads(self.board_text())["rest_operations"]["rev-1"]
        self.assertEqual({"decision": "rework", "lease_id": "L1", "item_id": "n2", "state": "rework_ready",
                          "finalization_version": 5}, stored["response"]["result"])
        self.assert_no_secret(self.board_text())

    def test_list_results_store_only_a_count(self):
        rows = [{"receipt_id": "b" * 64, "reply": TEXT, "summary": TEXT}] * 3
        with mock.patch("ciel_runtime_support.side_review.review", return_value=rows):
            status, _ = self.request("/ca/work/api/v1/commands/side-review", {
                "receipt_ids": ["b" * 64], "verdict": "ok", "expected_version": 0}, {"Idempotency-Key": "sr-1"})
        self.assertEqual(200, status)
        self.assertEqual({"rows": 3}, json.loads(self.board_text())["rest_operations"]["sr-1"]["response"]["result"])
        self.assert_no_secret(self.board_text())

    def test_refused_effect_leaves_no_receipt_and_the_key_can_be_retried(self):
        body = {"lease_id": "L1", "decision": "approve", "expected_finalization_version": 4}
        conflict = WorkParallelConflict("Work finalization changed; reread")
        with mock.patch("ciel_runtime_support.work_parallel_mcp._main_review", side_effect=conflict):
            status, _ = self.request("/ca/work/api/v1/commands/parallel-item-review", body, {"Idempotency-Key": "k-1"})
        self.assertEqual(409, status)
        self.assertNotIn("k-1", (json.loads(self.board_text() or "{}").get("rest_operations") or {}))
        with mock.patch("ciel_runtime_support.work_parallel_mcp._main_review",
                        return_value={"decision": "approve", "lease_id": "L1", "state": "finalized"}) as review:
            status, ok = self.request("/ca/work/api/v1/commands/parallel-item-review", body, {"Idempotency-Key": "k-1"})
        self.assertEqual((200, False, 1), (status, ok["replayed"], review.call_count))

    def test_lost_receipt_commit_is_never_followed_by_a_second_effect(self):
        body = {"lease_id": "L1", "decision": "approve", "expected_finalization_version": 4}
        effect = mock.Mock(return_value={"decision": "approve", "lease_id": "L1", "state": "finalized"})
        with mock.patch("ciel_runtime_support.work_parallel_mcp._main_review", effect), \
                mock.patch("ciel_runtime_support.work_application.WorkApplication.operator_commit",
                           side_effect=OSError("board busy")):
            status, _ = self.request("/ca/work/api/v1/commands/parallel-item-review", body, {"Idempotency-Key": "c-1"})
        self.assertEqual(503, status)
        with mock.patch("ciel_runtime_support.work_parallel_mcp._main_review", effect):
            status, unknown = self.request("/ca/work/api/v1/commands/parallel-item-review", body,
                                           {"Idempotency-Key": "c-1"})
        self.assertEqual((409, "operation_outcome_unknown"), (status, unknown["error"]))
        self.assertEqual(1, effect.call_count)
        status, audit = self.request("/ca/work/api/v1/audit")
        self.assertEqual([("c-1", "prepared")], [(row["operation_id"], row["state"]) for row in audit["events"]])

    def test_prepared_receipts_stay_readable_by_57dbfb3(self):
        import test_work_parallel_rework_worktree as rw
        body = {"lease_id": "L1", "decision": "approve", "expected_finalization_version": 4}
        with mock.patch("ciel_runtime_support.work_parallel_mcp._main_review", return_value={}), \
                mock.patch("ciel_runtime_support.work_application.WorkApplication.operator_commit",
                           side_effect=OSError("board busy")):
            self.request("/ca/work/api/v1/commands/parallel-item-review", body, {"Idempotency-Key": "p-1"})
        beta = rw.beta_module("work_plan_tree").normalize_document(json.loads(self.board_text()))
        self.assertIn("p-1", beta["rest_operations"])


if __name__ == "__main__":
    unittest.main()
