"""REST side-drain: the console drain (admit=False) through the shared operator path.

admit=False never admits anything to the board; it lists the newest side
receipts. Over REST each row is the redacted receipt detail that
GET /ca/work/api/v1/side/receipt serves, never the raw inbox record.
"""
import json
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.http_response import HttpResponseAdapter
from ciel_runtime_support.side_intake_service import receive
from ciel_runtime_support.work_application import openapi_document
from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices
from ciel_runtime_support.work_plan_store import session_work_plan_store

from test_work_http_parallel_gaps import serve

SCOPE = "claude:side-drain"
SECRET = "sk-" + "A1b2C3d4E5f6G7h8"


class SideDrainRestTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name) / "state"
        controller = WorkHttpController(WorkHttpServices(
            self.state, lambda: {"work_mode": True}, lambda: SCOPE,
            lambda identity: {"session_id": identity, "last_observed": None},
            HttpResponseAdapter.write_json, "test-instance"))
        self.request = serve(self, controller)
        self.board = session_work_plan_store(self.state, SCOPE)
        self.receipts = [receive(self.state, SCOPE, f"side request {index} with key {SECRET}", f"event-{index}",
                                 admit=False)["receipt_id"] for index in range(3)]

    def test_drain_lists_redacted_receipts_and_admits_nothing(self):
        board_before = self.board.read_snapshot()
        status, body = self.request("/ca/work/api/v1/commands/side-drain", {"limit": 2})
        self.assertEqual(200, status, body)
        rows = body["result"]["rows"]
        self.assertEqual(2, len(rows))
        self.assertTrue(set(row["receipt_id"] for row in rows) <= set(self.receipts))
        self.assertFalse(body["result"]["admitted"])
        self.assertNotIn(SECRET, json.dumps(body))
        board_after = self.board.read_snapshot()
        self.assertEqual(board_before["nodes"], board_after["nodes"])
        self.assertEqual(board_before["version"], board_after["version"])
        # A read: no audit receipt, nothing stored on the board.
        self.assertEqual({}, board_after.get("rest_operations", {}))

    def test_limit_is_bounded(self):
        for limit in (0, 101, "5", None):
            with self.subTest(limit=limit):
                status, body = self.request("/ca/work/api/v1/commands/side-drain", {"limit": limit})
                self.assertEqual(422, status, body)
        status, body = self.request("/ca/work/api/v1/commands/side-drain", {})
        self.assertEqual(200, status, body)
        self.assertEqual(3, len(body["result"]["rows"]))

    def test_openapi_documents_side_drain(self):
        post = openapi_document()["paths"]["/ca/work/api/v1/commands/side-drain"]["post"]
        schema = post["requestBody"]["content"]["application/json"]["schema"]
        self.assertEqual({"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                         schema["properties"]["limit"])
        self.assertEqual([], schema["required"])


if __name__ == "__main__":
    unittest.main()
