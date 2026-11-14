"""Board and operator commands share one idempotency key space (review F2, lead ruling 2026-11-13).

continue-ctl's probe (ctl-work-sse-parity/evidence/review/probe-r3.out) found
that R3 accepted an operator command under a board command's key and the
board mirror then replaced the board receipt; the board command's own replay
was refused and its /audit row was gone. Before R3 the operator command was
refused (probe-preR3-keyspace.out). The key space is one again: either
direction is an idempotency conflict, and a board receipt is never replaced.
"""
import json
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.http_response import HttpResponseAdapter
from ciel_runtime_support.work_application import WorkApplication, WorkOperationConflict
from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices
from ciel_runtime_support.work_operator_intents import WorkOperatorIntents
from ciel_runtime_support.work_plan_store import session_work_plan_store

from test_work_http_parallel_gaps import serve

SCOPE = "claude:11111111-2222-3333-4444-eeeeeeeeeeee"
KEY = "shared-key"
OPERATOR_BODY = {"changes": {}, "expected_version": 0}


class KeySpaceTests(unittest.TestCase):
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
        self.request = serve(self, WorkHttpController(WorkHttpServices(
            self.state, lambda: {"work_mode": True}, lambda: SCOPE,
            lambda identity: {"session_id": identity, "last_observed": None},
            HttpResponseAdapter.write_json, "test-instance")))

    def focus_body(self):
        return {"expected_version": self.board.read()["version"], "node_ids": [], "user_requested": True}

    def board_row(self, key=KEY):
        return json.loads(self.board.path.read_text(encoding="utf-8")).get("rest_operations", {}).get(key)

    def intents(self):
        return WorkOperatorIntents.beside(self.board.path).read()["intents"]

    def audit(self):
        status, body = self.request("/ca/work/api/v1/audit")
        self.assertEqual(200, status, body)
        return {row["operation_id"]: row for row in body["events"]}

    def test_reviewer_probe_operator_under_a_board_key_is_refused_and_the_board_replays(self):
        body = self.focus_body()
        first = self.app.command("focus", dict(body), KEY)
        with self.assertRaisesRegex(WorkOperationConflict, "already used for a different request"):
            self.app.operator_prepare("parallel_configure", dict(OPERATOR_BODY), KEY)
        self.assertEqual("focus", self.board_row()["command"])
        self.assertNotIn("state", self.board_row())
        self.assertNotIn(KEY, self.intents())
        self.assertEqual(first, self.app.command("focus", dict(body), KEY))  # stored response replays
        self.assertEqual("focus", self.audit()[KEY]["command"])

    def test_rest_operator_command_under_a_board_key_answers_409_idempotency_conflict(self):
        self.app.command("focus", self.focus_body(), KEY)
        status, body = self.request("/ca/work/api/v1/commands/parallel-configure", dict(OPERATOR_BODY),
                                    {"Idempotency-Key": KEY})
        self.assertEqual((409, "idempotency_conflict"), (status, body["error"]))
        self.assertEqual("focus", self.board_row()["command"])
        self.assertEqual("focus", self.audit()[KEY]["command"])

    def test_reverse_a_board_command_under_an_operator_key_is_refused(self):
        self.app.operator_prepare("parallel_configure", dict(OPERATOR_BODY), KEY)
        self.app.operator_commit("parallel_configure", dict(OPERATOR_BODY), KEY, {"version": 1})
        version = self.board.read()["version"]
        with self.assertRaisesRegex(WorkOperationConflict, "already used for a different request"):
            self.app.command("focus", self.focus_body(), KEY)
        self.assertEqual(version, self.board.read()["version"])  # no board effect ran
        self.assertEqual(("parallel_configure", "committed"),
                         (self.intents()[KEY]["command"], self.intents()[KEY]["state"]))
        # An older build may evict the operator mirror; the sidecar still holds the key.
        self.board.transaction(lambda d: d["rest_operations"].pop(KEY), audit_only=True)
        with self.assertRaisesRegex(WorkOperationConflict, "already used for a different request"):
            self.app.command("focus", self.focus_body(), KEY)
        self.assertEqual(version, self.board.read()["version"])

    def test_reverse_over_rest_answers_409_idempotency_conflict(self):
        self.app.operator_prepare("parallel_configure", dict(OPERATOR_BODY), KEY)
        status, body = self.request("/ca/work/api/v1/commands/focus", self.focus_body(), {"Idempotency-Key": KEY})
        self.assertEqual((409, "idempotency_conflict"), (status, body["error"]))
        self.assertEqual("prepared", self.intents()[KEY]["state"])

    def test_the_mirror_never_replaces_a_board_command_receipt(self):
        self.app.command("focus", self.focus_body(), KEY)
        board = self.board_row()
        receipts = {KEY: dict(board)}
        WorkApplication._mirror(receipts, KEY, {"digest": "0" * 64, "command": "parallel_configure",
                                                "state": "prepared", "created_at": 1.0,
                                                "response": {"ok": False, "state": "prepared"}})
        self.assertEqual({KEY: board}, receipts)

    def test_distinct_keys_are_unaffected(self):
        self.app.command("focus", self.focus_body(), "board-1")
        self.assertIsNone(self.app.operator_prepare("parallel_configure", dict(OPERATOR_BODY), "op-1"))
        self.assertEqual("prepared", self.board_row("op-1")["state"])
        self.assertEqual("focus", self.board_row("board-1")["command"])


if __name__ == "__main__":
    unittest.main()
