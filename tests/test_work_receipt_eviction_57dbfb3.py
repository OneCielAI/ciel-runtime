"""R3: operator intents survive a real 57dbfb3 board-mirror eviction.

The sidecar is authoritative and written first. The board ``rest_operations``
row remains a schema-compatible mirror that 57dbfb3 can read and evict. These
tests run a real 57dbfb3 board command in a separate process and prove that the
operator outcome, replay and reconcile facts remain in this build's sidecar.
"""
import json
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.work_application import MAX_OPERATION_RECEIPTS, WorkApplication
from ciel_runtime_support.work_plan_store import WorkPlanStore

SOURCE_ROOT = Path(__file__).resolve().parents[1]
BETA = "57dbfb3"


def beta(name):
    try:
        source = subprocess.run(["git", "-C", str(SOURCE_ROOT), "show", BETA + ":ciel_runtime_support/" + name + ".py"],
                                check=True, capture_output=True, text=True, encoding="utf-8").stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise unittest.SkipTest("beta source unavailable") from error
    qualified = "ciel_runtime_support._r3_beta_" + name
    module = types.ModuleType(qualified)
    module.__package__ = "ciel_runtime_support"
    sys.modules[qualified] = module
    exec(compile(source, qualified, "exec"), module.__dict__)
    return module


BETA_COMMAND = r"""
import json, sys
root, path, key = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, root)
import ciel_runtime_support.work_application as a
from ciel_runtime_support.work_plan_store import WorkPlanStore
assert a.__file__.startswith(root), a.__file__
store = WorkPlanStore(path)
version = store.read()["version"]
a.WorkApplication(store).command("focus", {"expected_version": version, "node_ids": [], "user_requested": True}, key)
print(json.dumps({"beta_module": a.__file__}))
"""


class BetaEvictionMeasurement(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "work-plan.json"
        self.store = WorkPlanStore(self.path)
        doc = self.store.read()
        self.store.transaction(lambda d: tree.set_plan(d, {
            "title": "Goal", "note": "", "priority": "P1", "children": [
                {"title": "Item", "note": "", "ref": "", "waiting_on": "", "children": []}]}, None),
            expected_version=doc["version"])
        self.app = WorkApplication(self.store)

    def fill(self, prepared_keys=()):
        """127 committed receipts (created_at 10.0, 11.0, ...) plus prepared ones at created_at 1.0, 2.0, ..."""
        def mutate(doc):
            receipts = doc.setdefault("rest_operations", {})
            for index, key in enumerate(prepared_keys):
                receipts[key] = {"digest": f"{index + 1:064x}", "command": "parallel_configure", "source": "rest",
                                 "origin": {}, "state": "prepared", "created_at": 1.0 + index,
                                 "request": {"expected_version": 0}, "receipt_version": 1,
                                 "last_reconcile": None, "response": {"ok": False, "state": "prepared"}}
            for index in range(MAX_OPERATION_RECEIPTS - len(receipts)):
                receipts[f"old-{index:03d}"] = {"digest": f"{index + 900:064x}", "command": "focus",
                                                "created_at": 10.0 + index, "response": {"ok": True}}
        self.store.transaction(mutate, audit_only=True)

    def beta_command(self, key):
        """One 57dbfb3 board command in a separate 57dbfb3 process (its own application, tree and store)."""
        import test_work_parallel_rework_worktree as rw
        done = subprocess.run([sys.executable, "-I", "-B", "-c", BETA_COMMAND, str(rw.beta_tree()),
                               str(self.path), key],
                              capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(0, done.returncode, done.stderr[-1500:])
        return json.loads(done.stdout.strip().splitlines()[-1])

    def receipts(self):
        return json.loads(self.path.read_text(encoding="utf-8")).get("rest_operations", {})

    def intents(self):
        from ciel_runtime_support.work_operator_intents import WorkOperatorIntents
        return WorkOperatorIntents.beside(self.path).read()["intents"]

    def sidecar_prepared(self, key="unknown-1", command="parallel_configure",
                         body=None):
        body = body or {"changes": {}, "expected_version": 0}
        self.assertIsNone(self.app.operator_prepare(command, body, key))
        self.assertEqual("prepared", self.intents().get(key, {}).get("state"))
        return body

    def test_57dbfb3_evicts_the_board_mirror_of_a_prepared_intent_and_its_effect_is_not_run(self):
        body = self.sidecar_prepared()
        self.fill()
        def oldest(doc):
            doc["rest_operations"]["unknown-1"]["created_at"] = 0.5
        self.store.transaction(oldest, audit_only=True)
        self.beta_command("beta-1")
        self.assertNotIn("unknown-1", self.receipts())
        self.assertEqual("prepared", self.intents().get("unknown-1", {}).get("state"))
        from ciel_runtime_support.work_application import WorkOperationOutcomeUnknown
        with self.assertRaises(WorkOperationOutcomeUnknown):
            self.app.operator_prepare("parallel_configure", body, "unknown-1")
        rows = {row["operation_id"]: row for row in self.audit()["events"]}
        self.assertEqual(("prepared", "unknown"), (rows["unknown-1"]["state"], rows["unknown-1"]["outcome"]))

    def test_57dbfb3_evicts_a_committed_mirror_and_replay_returns_the_sidecar_summary(self):
        body = self.sidecar_prepared("done-1", "parallel_legacy_accept")
        committed = self.app.operator_commit("parallel_legacy_accept", body, "done-1", {"lease_id": "L1"})
        self.fill()
        def oldest(doc):
            doc["rest_operations"]["done-1"]["created_at"] = 0.5
        self.store.transaction(oldest, audit_only=True)
        self.beta_command("beta-1")
        self.assertNotIn("done-1", self.receipts())
        self.assertEqual(committed, self.app.operator_prepare("parallel_legacy_accept", body, "done-1"))

    def test_57dbfb3_normalize_still_reads_the_board_with_sidecar_mirrors(self):
        import test_work_parallel_rework_worktree as rw
        self.sidecar_prepared()
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        beta = rw.beta_module("work_plan_tree").normalize_document(raw)
        self.assertIn("unknown-1", beta["rest_operations"])

    def audit(self):
        from ciel_runtime_support.work_http_controller import WorkHttpController
        return WorkHttpController._audit_projection("claude:test", self.app.document(), self.app.audit_receipts())

    def test_a_replay_after_a_real_57dbfb3_eviction_is_refused_by_the_command_pin(self):
        """R3 step 3: losing the receipt shortens the replay window; the pin still refuses a repeat."""
        import time
        from ciel_runtime_support.http_response import HttpResponseAdapter
        from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices
        from ciel_runtime_support.work_plan_store import session_work_plan_store
        from test_work_http_parallel_gaps import serve
        scope = "claude:11111111-2222-3333-4444-cccccccccccc"
        state = self.path.parent / "state"
        board = session_work_plan_store(state, scope)
        doc = board.read()
        board.transaction(lambda d: tree.set_plan(d, {"title": "Goal", "note": "", "priority": "P1",
                                                      "children": []}, None), expected_version=doc["version"])
        receipt = "c" * 64
        board.transaction(lambda d: d.setdefault("side_reviews", {}).update({receipt: {
            "state": "pending", "kind": "info", "action": "answer", "summary": "s", "recorded_at": 1.0}}))
        request = serve(self, WorkHttpController(WorkHttpServices(
            state, lambda: {"work_mode": True}, lambda: scope, lambda identity: {"session_id": identity},
            HttpResponseAdapter.write_json, "test-instance")))
        body = {"receipt_ids": [receipt], "verdict": "corrected", "note": "wrong",
                "expected_version": board.read()["version"]}
        status, first = request("/ca/work/api/v1/commands/side-review", body, {"Idempotency-Key": "op-1"})
        self.assertEqual(200, status, first)
        later = time.time() + 1000

        def fill(doc):
            receipts = doc["rest_operations"]
            for index in range(MAX_OPERATION_RECEIPTS - 1):
                receipts[f"later-{index:03d}"] = {"digest": f"{index + 900:064x}", "command": "focus",
                                                  "created_at": later + index, "response": {"ok": True}}
        board.transaction(fill, audit_only=True)
        self.path = board.path
        self.beta_command("beta-evicts")
        self.assertNotIn("op-1", self.receipts())  # the real 57dbfb3 process evicted our receipt
        # Also aged out of the sidecar's resolved window: only the command pin is left.
        from ciel_runtime_support.work_operator_intents import WorkOperatorIntents
        WorkOperatorIntents.beside(board.path).transaction(lambda intents: intents.pop("op-1", None))
        reviewed_at = board.read()["side_reviews"][receipt]["reviewed_at"]
        status, again = request("/ca/work/api/v1/commands/side-review", body, {"Idempotency-Key": "op-1"})
        print(chr(10) + "[R3-measure] replay after 57dbfb3 eviction:", status, again.get("error"))
        self.assertEqual((409, "version_conflict"), (status, again["error"]))
        self.assertEqual(reviewed_at, board.read()["side_reviews"][receipt]["reviewed_at"])  # not repeated


class SidecarAuthorityHttpTests(unittest.TestCase):
    """R3 required tests on the shared operator HTTP path, with a real 57dbfb3 board writer."""

    SCOPE = "claude:11111111-2222-3333-4444-dddddddddddd"
    REVIEW = "/ca/work/api/v1/commands/parallel-item-review"
    BODY = {"lease_id": "L1", "decision": "approve", "evidence": "ok", "expected_finalization_version": 4}

    def setUp(self):
        from unittest import mock
        from ciel_runtime_support.http_response import HttpResponseAdapter
        from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices
        from ciel_runtime_support.work_plan_store import session_work_plan_store
        from test_work_http_parallel_gaps import serve
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name) / "state"
        ledger = mock.Mock()
        ledger.path = str(Path(temporary.name) / "work-parallel.json")
        self.request = serve(self, WorkHttpController(WorkHttpServices(
            self.state, lambda: {"work_mode": True}, lambda: self.SCOPE,
            lambda identity: {"session_id": identity, "last_observed": None},
            HttpResponseAdapter.write_json, "test-instance", parallel_store=lambda: ledger)))
        self.board = session_work_plan_store(self.state, self.SCOPE)
        doc = self.board.read()
        self.board.transaction(lambda d: tree.set_plan(d, {"title": "Goal", "note": "", "priority": "P1",
                                                           "children": []}, None), expected_version=doc["version"])
        self.effect = mock.Mock(return_value={"decision": "approve", "lease_id": "L1", "state": "finalized"})
        patcher = mock.patch("ciel_runtime_support.work_parallel_mcp._main_review", self.effect)
        patcher.start()
        self.addCleanup(patcher.stop)

    def app(self):
        return WorkApplication(self.board)

    def review(self, key):
        return self.request(self.REVIEW, dict(self.BODY), {"Idempotency-Key": key})

    def mirror(self):
        return json.loads(self.board.path.read_text(encoding="utf-8")).get("rest_operations", {})

    def intents(self):
        from ciel_runtime_support.work_operator_intents import WorkOperatorIntents
        return WorkOperatorIntents.beside(self.board.path).read()["intents"]

    def audit_row(self, key):
        status, audit = self.request("/ca/work/api/v1/audit")
        self.assertEqual(200, status, audit)
        return next((row for row in audit["events"] if row["operation_id"] == key), None)

    def fill_then_57dbfb3_evicts(self, key):
        """Fill the board mirror to the bound with newer rows; a real 57dbfb3 command evicts ``key``."""
        later = time.time() + 1000

        def fill(doc):
            receipts = doc.setdefault("rest_operations", {})
            for index in range(MAX_OPERATION_RECEIPTS - len(receipts)):
                receipts[f"later-{index:03d}"] = {"digest": f"{index + 900:064x}", "command": "focus",
                                                  "created_at": later + index, "response": {"ok": True}}
        self.board.transaction(fill, audit_only=True)
        self.path = self.board.path
        BetaEvictionMeasurement.beta_command(self, "beta-evicts")
        self.assertNotIn(key, self.mirror())  # the real 57dbfb3 process evicted the mirror

    def unknown(self, key):
        """An operator command whose commit was lost: outcome unknown."""
        from unittest import mock
        with mock.patch("ciel_runtime_support.work_application.WorkApplication.operator_commit",
                        side_effect=OSError("board busy")):
            status, _ = self.review(key)
        self.assertEqual(503, status)

    # A real 57dbfb3 eviction of the board mirror ----------------------------

    def test_57dbfb3_evicts_a_prepared_mirror_then_409_outcome_unknown_and_no_second_effect(self):
        self.unknown("u-1")
        self.fill_then_57dbfb3_evicts("u-1")
        status, body = self.review("u-1")
        self.assertEqual((409, "operation_outcome_unknown", 1), (status, body.get("error"), self.effect.call_count))
        self.assertEqual("prepared", self.intents().get("u-1", {}).get("state"))
        row = self.audit_row("u-1")
        self.assertEqual(("prepared", "unknown"), (row["state"], row["outcome"]))
        # The lookup rewrote the mirror; 57dbfb3 still reads the bounded board.
        self.assertEqual("prepared", self.mirror()["u-1"]["state"])
        self.assertLessEqual(len(self.mirror()), MAX_OPERATION_RECEIPTS)
        import test_work_parallel_rework_worktree as rw
        rw.beta_module("work_plan_tree").normalize_document(json.loads(self.board.path.read_text(encoding="utf-8")))

    def test_57dbfb3_evicts_a_committed_mirror_then_replay_returns_the_stored_summary(self):
        status, first = self.review("c-1")
        self.assertEqual(200, status, first)
        self.fill_then_57dbfb3_evicts("c-1")
        status, again = self.review("c-1")
        self.assertEqual((200, True), (status, again.get("replayed")), again)
        self.assertEqual({"decision": "approve", "lease_id": "L1", "state": "finalized"}, again["result"])
        self.assertEqual(1, self.effect.call_count)
        # A finished row past the newest-100 audit window is not listed; the sidecar still holds it.
        self.assertEqual("committed", self.intents().get("c-1", {}).get("state"))

    # A crash between the two writes, in both orders -------------------------

    def test_sidecar_prepare_written_mirror_not_converges_at_the_next_lookup(self):
        from unittest import mock
        with mock.patch.object(WorkApplication, "_mirror_row", create=True):
            self.assertIsNone(self.app().operator_prepare("parallel_item_review", dict(self.BODY), "s-1"))
        self.assertNotIn("s-1", self.mirror())
        self.assertEqual("prepared", self.intents().get("s-1", {}).get("state"))
        self.assertEqual("unknown", self.audit_row("s-1")["outcome"])
        status, body = self.review("s-1")
        self.assertEqual((409, "operation_outcome_unknown"), (status, body["error"]))
        self.assertEqual(0, self.effect.call_count)
        self.assertEqual("prepared", self.mirror()["s-1"]["state"])

    def test_sidecar_commit_written_mirror_not_converges_at_the_next_lookup(self):
        from unittest import mock
        app = self.app()
        app.operator_prepare("parallel_item_review", dict(self.BODY), "s-2")
        with mock.patch.object(WorkApplication, "_mirror_row", create=True):
            app.operator_commit("parallel_item_review", dict(self.BODY), "s-2",
                                {"lease_id": "L1", "state": "finalized"})
        self.assertEqual(("prepared", "committed"), (self.mirror()["s-2"]["state"], self.intents().get("s-2", {}).get("state")))
        self.assertEqual("applied", self.audit_row("s-2")["outcome"])
        status, again = self.review("s-2")
        self.assertEqual((200, True), (status, again.get("replayed")), again)
        self.assertEqual({"lease_id": "L1", "state": "finalized"}, again["result"])
        self.assertEqual(0, self.effect.call_count)
        self.assertEqual("committed", self.mirror()["s-2"]["state"])

    def mirror_only(self, key, state, receipt_version, response):
        """A board row the sidecar does not hold (written before it existed, or ahead of it)."""
        digest = self.app()._operator_digest("parallel_item_review", dict(self.BODY))

        def write(doc):
            doc.setdefault("rest_operations", {})[key] = {
                "digest": digest, "command": "parallel_item_review", "source": "rest", "origin": {},
                "state": state, "created_at": time.time(), "request": {"lease_id": "L1"},
                "receipt_version": receipt_version, "last_reconcile": None, "response": response}
        self.board.transaction(write, audit_only=True)

    def test_mirror_prepare_written_sidecar_not_converges_to_outcome_unknown(self):
        self.mirror_only("m-1", "prepared", 1, {"ok": False, "state": "prepared"})
        self.assertNotIn("m-1", self.intents())
        self.assertEqual("unknown", self.audit_row("m-1")["outcome"])
        status, body = self.review("m-1")
        self.assertEqual((409, "operation_outcome_unknown"), (status, body["error"]))
        self.assertEqual(0, self.effect.call_count)
        self.assertEqual("prepared", self.intents().get("m-1", {}).get("state"))

    def test_mirror_commit_ahead_of_the_sidecar_converges_to_committed(self):
        app = self.app()
        app.operator_prepare("parallel_item_review", dict(self.BODY), "m-2")
        committed = {"ok": True, "schema_version": 1, "operation": "parallel_item_review", "operation_id": "m-2",
                     "version": self.board.read()["version"], "changed": True,
                     "result": {"lease_id": "L1", "state": "finalized"}}
        self.mirror_only("m-2", "committed", 2, committed)
        self.assertEqual("prepared", self.intents().get("m-2", {}).get("state"))
        status, again = self.review("m-2")
        self.assertEqual((200, True), (status, again.get("replayed")), again)
        self.assertEqual({"lease_id": "L1", "state": "finalized"}, again["result"])
        self.assertEqual(0, self.effect.call_count)
        self.assertEqual(("committed", 2), (self.intents().get("m-2", {}).get("state"), self.intents().get("m-2", {}).get("receipt_version")))


if __name__ == "__main__":
    unittest.main()
