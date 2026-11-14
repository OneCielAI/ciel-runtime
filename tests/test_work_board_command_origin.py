"""Every board command records the server-derived surface that issued it (lead ruling).

Board-command receipts once held only {digest, command, created_at, response},
so /audit showed source null for every board mutation, including execute
(plan approval). Now each receipt records source (web, rest or ctl) and origin
(auth, or os_user and pid) outside the digest, exactly like operator receipts.
A client header never sets it, and 57dbfb3 still reads the board.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.http_response import HttpResponseAdapter
from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices
from ciel_runtime_support.work_plan_store import session_work_plan_store

from test_work_http_parallel_gaps import serve

SCOPE = "claude:11111111-2222-3333-4444-bbbbbbbbbbbb"
ADD = "/ca/work/api/v1/commands/node-add"


class BoardCommandOriginTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name) / "state"
        self.board = session_work_plan_store(self.state, SCOPE)
        doc = self.board.read()
        self.board.transaction(lambda d: tree.set_plan(d, {
            "title": "Goal", "note": "", "priority": "P1", "children": []}, None), expected_version=doc["version"])
        self.request = serve(self, self.controller())

    def controller(self, origin=None):
        return WorkHttpController(WorkHttpServices(
            self.state, lambda: {"work_mode": True}, lambda: SCOPE,
            lambda identity: {"session_id": identity, "last_observed": None},
            HttpResponseAdapter.write_json, "test-instance", operator_origin=origin))

    def receipt(self, key):
        return json.loads(self.board.path.read_text(encoding="utf-8"))["rest_operations"][key]

    def add(self, key, headers=None, title="child"):
        version = self.board.read()["version"]
        root = self.board.read()["nodes"][0]["id"]
        return self.request(ADD, {"parent_id": root, "title": title, "expected_version": version},
                            {"Idempotency-Key": key, **(headers or {})})

    def audit_row(self, key):
        _, audit = self.request("/ca/work/api/v1/audit")
        return next(row for row in audit["events"] if row["operation_id"] == key)

    def test_loopback_board_command_is_rest(self):
        status, body = self.add("b-1")
        self.assertEqual(200, status, body)
        self.assertEqual(("rest", {"auth": "loopback"}), (self.receipt("b-1")["source"], self.receipt("b-1")["origin"]))
        self.assertEqual("rest", self.audit_row("b-1")["source"])

    def test_bearer_board_command_is_rest(self):
        self.assertEqual(200, self.add("b-2", {"Authorization": "Bearer owned-admin"})[0])
        self.assertEqual({"source": "rest", "auth": "bearer"},
                         {"source": self.receipt("b-2")["source"], **self.receipt("b-2")["origin"]})

    def test_session_cookie_board_command_is_web(self):
        self.assertEqual(200, self.add("b-3", {"Cookie": "ciel_session=signed-in-session"})[0])
        self.assertEqual(("web", {"auth": "session"}), (self.receipt("b-3")["source"], self.receipt("b-3")["origin"]))
        self.assertNotIn("signed-in-session", self.board.path.read_text(encoding="utf-8"))

    def test_a_forged_source_header_changes_nothing(self):
        self.assertEqual(200, self.add("b-4", {"X-Ciel-Operator-Source": "ctl"})[0])
        self.assertEqual("rest", self.receipt("b-4")["source"])

    def test_in_process_ctl_board_command_is_ctl_with_its_pid(self):
        ctl = self.controller({"source": "ctl", "os_user": "operator", "pid": os.getpid()})
        request = serve(self, ctl)
        version = self.board.read()["version"]
        root = self.board.read()["nodes"][0]["id"]
        status, body = request(ADD, {"parent_id": root, "title": "ctl child", "expected_version": version},
                               {"Idempotency-Key": "b-5"})
        self.assertEqual(200, status, body)
        self.assertEqual(("ctl", {"os_user": "operator", "pid": os.getpid()}),
                         (self.receipt("b-5")["source"], self.receipt("b-5")["origin"]))

    def test_origin_is_outside_the_digest_so_a_replay_from_another_surface_resolves(self):
        version = self.board.read()["version"]
        root = self.board.read()["nodes"][0]["id"]
        body = {"parent_id": root, "title": "same", "expected_version": version}
        self.assertEqual(200, self.request(ADD, body, {"Idempotency-Key": "b-6"})[0])
        status, again = self.request(ADD, body, {"Idempotency-Key": "b-6", "Authorization": "Bearer x"})
        self.assertEqual(200, status, again)
        self.assertEqual("rest", self.receipt("b-6")["source"])
        self.assertEqual({"auth": "loopback"}, self.receipt("b-6")["origin"])  # the first surface stays

    def test_57dbfb3_reads_board_receipts_with_origin(self):
        import test_work_parallel_rework_worktree as rw
        self.add("b-7", {"Cookie": "ciel_session=s"})
        raw = json.loads(self.board.path.read_text(encoding="utf-8"))
        beta = rw.beta_module("work_plan_tree").normalize_document(raw)
        self.assertIn("b-7", beta["rest_operations"])


if __name__ == "__main__":
    unittest.main()
