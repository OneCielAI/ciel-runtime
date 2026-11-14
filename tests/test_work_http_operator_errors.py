"""Operator command errors are classified by type, on REST and ciel-runtimectl alike.

A stale board version on an operator write is a version conflict (409) with the
current version, never "Work state unreadable" (503). The refused write leaves
no receipt, so the same key can be retried after a reread.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.http_response import HttpResponseAdapter
from ciel_runtime_support.work_ctl import CtlError, WorkCtl
from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices
from ciel_runtime_support.work_plan_store import session_work_plan_store

from test_work_http_parallel_gaps import serve

SCOPE = "claude:11111111-2222-3333-4444-555555555555"
RECEIPT = "c" * 64
EVENT = "e" * 64


class OperatorVersionConflictTests(unittest.TestCase):
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
        self.board.transaction(lambda d: d.setdefault("side_reviews", {}).update({RECEIPT: {
            "state": "pending", "kind": "info", "action": "answer", "summary": "s", "recorded_at": 1.0}}))
        self.board.transaction(lambda d: d.setdefault("side_handoffs", {}).update({EVENT: {
            "state": "requested", "kind": "request", "action": "update", "summary": "s", "gates": [],
            "requested_at": 1.0}}))
        self.current = self.board.read()["version"]
        self.controller = WorkHttpController(WorkHttpServices(
            self.state, lambda: {"work_mode": True}, lambda: SCOPE,
            lambda identity: {"session_id": identity, "last_observed": None},
            HttpResponseAdapter.write_json, "test-instance"))
        self.request = serve(self, self.controller)

    def receipts(self):
        return json.loads(self.board.path.read_text(encoding="utf-8")).get("rest_operations", {})

    def stale_review(self, version):
        return {"receipt_ids": [RECEIPT], "verdict": "ok", "note": "", "expected_version": version}

    def test_rest_stale_side_review_is_a_409_with_the_current_version(self):
        status, body = self.request("/ca/work/api/v1/commands/side-review", self.stale_review(self.current - 1),
                                    {"Idempotency-Key": "stale-1"})
        self.assertEqual(409, status, body)
        self.assertEqual({"ok": False, "error": "version_conflict", "version": self.current},
                         {key: body.get(key) for key in ("ok", "error", "version")})
        self.assertNotIn("stale-1", self.receipts())
        # The same key works after a reread.
        status, body = self.request("/ca/work/api/v1/commands/side-review", self.stale_review(self.current),
                                    {"Idempotency-Key": "stale-1"})
        self.assertEqual(200, status, body)

    def test_rest_never_issued_version_is_a_409_too(self):
        status, body = self.request("/ca/work/api/v1/commands/side-review", self.stale_review(self.current + 5),
                                    {"Idempotency-Key": "ahead-1"})
        self.assertEqual((409, "version_conflict", self.current), (status, body.get("error"), body.get("version")))

    def test_rest_stale_side_handoff_is_a_409(self):
        status, body = self.request("/ca/work/api/v1/commands/side-handoff", {
            "event_id": EVENT, "answer": "", "expected_version": self.current - 1}, {"Idempotency-Key": "h-1"})
        self.assertEqual((409, "version_conflict", self.current), (status, body.get("error"), body.get("version")))

    def test_ctl_stale_side_review_is_a_version_conflict(self):
        ctl = WorkCtl(lambda: {"work_mode": True}, lambda value: None, self.state)
        with mock.patch("ciel_runtime_support.work_parallel_mcp.ledger", side_effect=OSError("no ledger")):
            with self.assertRaises(CtlError) as raised:
                ctl.post(SCOPE, "side-review", self.stale_review(self.current - 1), "ctl-stale-1")
        self.assertEqual("version_conflict", raised.exception.code)
        self.assertEqual(self.current, raised.exception.payload.get("version"))
        self.assertNotIn("ctl-stale-1", self.receipts())


if __name__ == "__main__":
    unittest.main()
