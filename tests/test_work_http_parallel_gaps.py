"""Web/REST exposure of backend Work parallel state (release rule: dashboard + REST).

1. A stranded recovery launch is listed and resolved through REST in the
   stranded-v1 journal that owns it, pinned by that journal's version.
2. A superseded stranded row is an ended audit row, never a decidable review.
3. The live refresh watches every file /parallel reads, sidecars included.
4. The durable host failure log is readable from /parallel.
5. OpenAPI declares real request bodies, responses and the security schemes.
6. Every operator command is audited and honors Idempotency-Key.
Temp config only; nothing here starts a runtime session.
"""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import test_work_parallel_rework_worktree as rw
from ciel_runtime_support import work_parallel_mcp as m
from ciel_runtime_support.http_response import HttpResponseAdapter
from ciel_runtime_support.work_application import openapi_document
from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices
from ciel_runtime_support.work_parallel import WorkParallelStore
from ciel_runtime_support.work_plan_store import session_work_plan_store

SCOPE = rw.SCOPE


def serve(case, controller):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if not controller.get(self, self.path.split("?")[0]):
                self.send_error(404)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            if not controller.post(self, self.path, body):
                self.send_error(404)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    case.addCleanup(thread.join, 3)
    case.addCleanup(server.server_close)
    case.addCleanup(server.shutdown)
    base = "http://127.0.0.1:" + str(server.server_port)

    def request(path, body=None, headers=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(base + path, data=data,
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            response = urllib.request.urlopen(req, timeout=30)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)
    return request


class StrandedLaunchRestTests(rw.ReworkFixture):
    """Gap 1 on real stores: a recovery launch of a stranded row, resolved over REST."""

    strand = rw.BrokenRetainedWorktreeTests.strand
    assert_stranded_before_review = rw.BrokenRetainedWorktreeTests.assert_stranded_before_review
    recover_stranded = rw.BrokenRetainedWorktreeTests.recover_stranded

    def controller(self):
        config = {"work_mode": True, **self.config}
        controller = WorkHttpController(WorkHttpServices(
            self.tmp / "state", lambda: config, lambda: SCOPE,
            lambda identity: {"session_id": identity, "last_observed": None},
            HttpResponseAdapter.write_json, "test-instance",
            parallel_status=lambda identity, board: m.project(self.store, identity, board),
            parallel_store=lambda: self.store))
        # The fixture board is the conversation board the controller reads.
        controller._store = lambda identity: self.board.store
        return controller, serve(self, controller)

    def unresolved_recovery_launch(self):
        message = "owned path unavailable; retained"
        first, replacement, token = self.strand(rw.delete_worktree, message)
        self.assert_stranded_before_review(first, replacement, token, message)
        recovery, result = self.recover_stranded(replacement)
        self.call("event", {"fork": True}, event="rework_dispatch", lease_id=recovery)
        self.call("event", {"internal": True, "launch_nonce": rw.FIRST_PROCESS}, event="rework_launch_intent",
                  lease_id=recovery, dispatch_name="work-rework-" + recovery + "-a1",
                  generation=result["dispatch"]["generation"])
        return replacement, recovery

    def test_recovery_launch_is_listed_and_resolved_in_the_stranded_journal(self):
        replacement, recovery = self.unresolved_recovery_launch()
        _, request = self.controller()
        status, view = request("/ca/work/api/v1/parallel")
        self.assertEqual(200, status, view)
        row = next(row for row in view["parallel_reviews"] if row["lease_id"] == replacement)
        self.assertEqual("stranded", row["review_journal"])
        launch = row["launch"]
        self.assertEqual("launch_intent", launch["state"])
        self.assertRegex(launch["launch_ref"], "^[0-9a-f]{32}$")
        stranded_version = view["stranded_version"]
        self.assertIsInstance(stranded_version, int)
        # The unbound recovery launch name stays on the host (bound forks keep
        # their existing evidence-group display).
        self.assertNotIn("work-rework-" + recovery, json.dumps(view))

        # The schema-5 version does not pin a stranded-v1 launch: refused, nothing written.
        before = self.durable_bytes()
        status, refused = request("/ca/work/api/v1/commands/parallel-launch-resolve", {
            "lease_id": replacement, "launch_ref": launch["launch_ref"], "confirmed": True,
            "expected_finalization_version": view["review_version"]})
        self.assertEqual(422, status, refused)
        self.assertIn("expected_stranded_version", refused["message"])
        self.assertEqual(before, self.durable_bytes())

        finalization_bytes = (self.tmp / "work-parallel-finalization.json").read_bytes()
        status, resolved = request("/ca/work/api/v1/commands/parallel-launch-resolve", {
            "lease_id": replacement, "launch_ref": launch["launch_ref"], "confirmed": True,
            "expected_stranded_version": stranded_version})
        self.assertEqual(200, status, resolved)
        self.assertEqual({"lease_id": replacement, "state": "manual_requested", "attempt": 1,
                          "launch_ref": launch["launch_ref"], "review_journal": "stranded",
                          "stranded_version": stranded_version + 1}, resolved["result"])
        self.assertEqual("manual_requested", self.stranded_rows()[replacement]["launch"]["state"])
        self.assertEqual(finalization_bytes, (self.tmp / "work-parallel-finalization.json").read_bytes())
        _, after = request("/ca/work/api/v1/parallel")
        row = next(row for row in after["parallel_reviews"] if row["lease_id"] == replacement)
        self.assertEqual("manual_requested", row["launch"]["state"])
        self.assertEqual(stranded_version + 1, after["stranded_version"])
        self.assert_beta_reads_every_store()


class ParallelViewTests(unittest.TestCase):
    """Gaps 2-4 at the browser projection, with exact bounded shapes."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.store = WorkParallelStore(self.tmp / "work-parallel.json")
        self.review = {"version": 3, "stranded_version": 7, "pending": [], "truncated": False}
        self.controller = WorkHttpController(WorkHttpServices(
            self.tmp / "state", lambda: {"work_mode": True}, lambda: SCOPE,
            lambda identity: {"session_id": identity, "last_observed": None},
            HttpResponseAdapter.write_json, "test-instance",
            parallel_status=lambda identity, board: self.projection(), parallel_store=lambda: self.store))

    def projection(self):
        empty = {name: [] for name in ("confirmed", "waiting", "reservation", "uncertain", "validating", "ended")}
        return {"version": 2, "policy": {"enabled": True, "paused": False, "priority_limits": {"P1": 1},
                                         "launches_per_minute": 4, "max_rework": 2},
                "forks": {"schema": "native-fork-evidence/1", "available": True, "confirmed_count": 0, **empty},
                "device": {"active_slots": 0}, "runtime": "claude", "limitations": [], "leases": {},
                "review": self.review, "legacy_reviews": []}

    def superseded_source(self):
        source = {"lease_id": "S1", "scope": SCOPE, "item_id": "n2", "root_id": "n1", "priority": "P1",
                  "kind": "no_report", "state": "superseded", "updated_at": 1_800_000_000.0,
                  "reason": "C:/secret/repo/.wt/n2 ownership failed",
                  "superseded": {"lease_id": "W2", "generation": 1, "observed_at": 1_800_000_100.0,
                                 "digest": "d" * 64}}
        row = m._stranded_view(source)
        row["item_title"] = "Fix retry"
        return row

    def test_superseded_row_is_an_ended_audit_row_with_fixed_fields_only(self):
        self.review["pending"] = [self.superseded_source()]
        view = self.controller._public_parallel(SCOPE, {"nodes": [], "version": 1})
        self.assertEqual([], view["parallel_reviews"])
        self.assertEqual([{"lease_id": "S1", "item_id": "n2", "priority": "P1", "item_title": "Fix retry",
                           "kind": "no_report", "phase": "superseded", "superseding_lease_id": "W2",
                           "observed_at": 1_800_000_100.0}], view["parallel_reviews_ended"])
        self.assertNotIn("C:/secret", json.dumps(view))
        self.assertNotIn("ownership failed", json.dumps(view))

    def test_stranded_version_is_exported(self):
        view = self.controller._public_parallel(SCOPE, {"nodes": [], "version": 1})
        self.assertEqual((3, 7), (view["review_version"], view["stranded_version"]))

    def test_live_refresh_watches_every_file_the_view_reads(self):
        names = [path.name for path in self.controller._parallel_paths()]
        for name in ("work-parallel.json", "work-parallel-native.json", "work-parallel-coordination.json",
                     "work-parallel-finalization.json", "work-parallel-stranded-v1.json",
                     "work-parallel-review-delivery.json", "work-parallel-failures-v1.json"):
            self.assertIn(name, names)

    def test_fingerprint_changes_with_each_sidecar_fact(self):
        board = {"nodes": [], "version": 1}
        prints = [self.controller._parallel_fingerprint(SCOPE, board)[0]]
        self.review["stranded_version"] = 8
        prints.append(self.controller._parallel_fingerprint(SCOPE, board)[0])
        self.review["pending"] = [self.superseded_source()]
        prints.append(self.controller._parallel_fingerprint(SCOPE, board)[0])
        from ciel_runtime_support.work_parallel_failures import WorkParallelFailureLog
        WorkParallelFailureLog.beside(self.store.path).record(SCOPE, "revival_refused", lease_id="L1",
                                                              dedup_key="one")
        prints.append(self.controller._parallel_fingerprint(SCOPE, board)[0])
        self.assertEqual(len(prints), len(set(prints)), prints)

    def test_failure_log_is_read_for_this_session_only(self):
        from ciel_runtime_support.work_parallel_failures import WorkParallelFailureLog
        log = WorkParallelFailureLog.beside(self.store.path)
        log.record(SCOPE, "decision_refused_stop", lease_id="L1", dedup_key="a", now=1_800_000_000)
        log.record("claude:someone-else", "revival_refused", lease_id="L9", dedup_key="b", now=1_800_000_001)
        view = self.controller._public_parallel(SCOPE, {"nodes": [], "version": 1})
        self.assertEqual(["decision_refused_stop"], [row["code"] for row in view["parallel_failures"]])
        self.assertEqual({"id", "code", "lease_id", "first_at", "at", "count"},
                         set(view["parallel_failures"][0]))
        self.assertEqual(2, view["failures_version"])
        self.assertNotIn("someone-else", json.dumps(view))
        self.assertNotIn("L9", json.dumps(view))


class OpenApiTests(unittest.TestCase):
    """Gap 5: real bodies, responses and security, not a generic expected_version."""

    def test_operator_bodies_name_their_real_fields(self):
        paths = openapi_document()["paths"]

        def body(name):
            return paths["/ca/work/api/v1/commands/" + name]["post"]["requestBody"]["content"][
                "application/json"]["schema"]
        expected = {
            "parallel-item-review": {"lease_id", "decision", "expected_finalization_version"},
            "parallel-launch-resolve": {"lease_id", "launch_ref", "confirmed"},
            "parallel-legacy-accept": {"lease_id", "confirmed", "expected_version"},
            "parallel-configure": {"changes", "expected_version"},
            "side-notice-retry": {"receipt_id", "attempt_ref", "revision", "confirmed"},
            "side-wake-retry": {"event_id", "attempt_ref", "revision", "confirmed"},
            "side-reply-retry": {"receipt_id", "expected_state", "revision"},
        }
        for name, required in expected.items():
            with self.subTest(command=name):
                self.assertEqual(required, set(body(name)["required"]))
                self.assertIn("operation_id", body(name)["properties"])
        launch = body("parallel-launch-resolve")["properties"]
        self.assertIn("expected_finalization_version", launch)
        self.assertIn("expected_stranded_version", launch)
        self.assertEqual(["approve", "rework"], body("parallel-item-review")["properties"]["decision"]["enum"])
        self.assertEqual({"type": "boolean", "const": True}, body("side-notice-retry")["properties"]["confirmed"])
        # Replay pins of the side retries (continue-sse contract): the exact
        # attempt and revision the person saw; the reply attempt ref is null
        # when the receipt has no outbox attempt (route_unavailable).
        notice, reply = body("side-notice-retry")["properties"], body("side-reply-retry")["properties"]
        self.assertEqual("^[0-9a-f]{16}$", notice["attempt_ref"]["pattern"])
        self.assertIn("notice_attempt_ref", notice["attempt_ref"]["description"])
        self.assertIn("notice_revision", notice["revision"]["description"])
        self.assertEqual(["string", "null"], reply["attempt_ref"]["type"])
        self.assertIn("reply_attempt_ref", reply["attempt_ref"]["description"])
        self.assertIn("reply_revision", reply["revision"]["description"])
        self.assertEqual(0, reply["revision"]["minimum"])
        self.assertIn("refresh before retry", body("side-notice-retry")["description"])
        self.assertIn("refresh before retry", body("side-reply-retry")["description"])
        self.assertIn("Parallel ledger version",
                      body("parallel-configure")["properties"]["expected_version"]["description"])

    def test_every_controller_command_is_documented_with_responses(self):
        document = openapi_document()
        routes = {path.rsplit("/", 1)[1] for path in document["paths"] if "/commands/" in path}
        for name in ("plan-set", "parallel-legacy-accept", "side-reappend-retry", "review-reappend-retry",
                     "parallel-launch-resolve", "side-notice-retry"):
            self.assertIn(name, routes)
        for path, item in document["paths"].items():
            if "/commands/" in path:
                with self.subTest(path=path):
                    self.assertEqual({"200", "409", "422", "503"}, set(item["post"]["responses"]))

    def test_security_schemes_and_parallel_response_fields(self):
        document = openapi_document()
        schemes = document["components"]["securitySchemes"]
        self.assertEqual({"type": "http", "scheme": "bearer"},
                         {k: schemes["adminBearer"][k] for k in ("type", "scheme")})
        self.assertEqual(("header", "x-ciel-runtime-token"),
                         (schemes["adminToken"]["in"], schemes["adminToken"]["name"]))
        self.assertEqual(("cookie", "ciel_session"),
                         (schemes["webSession"]["in"], schemes["webSession"]["name"]))
        self.assertEqual([{"adminBearer": []}, {"adminToken": []}, {"webSession": []}], document["security"])
        fields = document["components"]["schemas"]["ParallelView"]["properties"]
        for name in ("stranded_version", "parallel_reviews_ended", "parallel_legacy_reviews",
                     "parallel_failures", "failures_version"):
            self.assertIn(name, fields)


class OperatorAuditTests(unittest.TestCase):
    """Gap 6: Idempotency-Key and the audit trail on the shared operator path."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.state = self.tmp / "state"
        self.store = WorkParallelStore(self.tmp / "config" / "work-parallel.json")
        self.store.configure({"enabled": True}, capability_ready=True, expected_version=0)
        self.saved = []
        self.config = {"work_mode": True}
        self.controller = WorkHttpController(WorkHttpServices(
            self.state, lambda: self.config, lambda: SCOPE,
            lambda identity: {"session_id": identity, "last_observed": None},
            HttpResponseAdapter.write_json, "test-instance",
            parallel_status=lambda identity, board: m.project(self.store, identity, board),
            save_config=self.saved.append, parallel_store=lambda: self.store))
        self.request = serve(self, self.controller)
        self.board = session_work_plan_store(self.state, SCOPE)

    def configure(self, key, changes, version, *, header=True, body_key=None):
        body = {"changes": changes, "expected_version": version}
        if body_key is not None:
            body["operation_id"] = body_key
        return self.request("/ca/work/api/v1/commands/parallel-configure", body,
                            {"Idempotency-Key": key} if header and key else None)

    def test_keyed_operator_command_replays_without_repeating_its_effect(self):
        board_version = self.board.read_snapshot()["version"]
        version = self.store.read()["version"]
        status, first = self.configure("op-1", {"launches_per_minute": 5}, version)
        self.assertEqual(200, status, first)
        self.assertEqual(("op-1", False), (first["operation_id"], first["replayed"]))
        after = self.store.read()["version"]
        self.assertEqual(version + 1, after)
        status, again = self.configure("op-1", {"launches_per_minute": 5}, version)
        self.assertEqual(200, status, again)
        self.assertEqual(("op-1", True), (again["operation_id"], again["replayed"]))
        self.assertEqual(after, self.store.read()["version"])
        self.assertEqual(1, len(self.saved))
        # The receipt is an audit row, not a plan change: the board version is kept.
        self.assertEqual(board_version, self.board.read_snapshot()["version"])
        status, audit = self.request("/ca/work/api/v1/audit")
        self.assertEqual(200, status, audit)
        self.assertEqual([("op-1", "parallel_configure")],
                         [(row["operation_id"], row["command"]) for row in audit["events"]])

    def test_reused_key_with_another_request_conflicts_and_mismatched_keys_are_refused(self):
        version = self.store.read()["version"]
        self.assertEqual(200, self.configure("op-2", {"launches_per_minute": 5}, version)[0])
        before = (self.store.path.read_bytes(), self.board.path.read_bytes())
        status, conflict = self.configure("op-2", {"launches_per_minute": 6}, version + 1)
        self.assertEqual((409, "idempotency_conflict"), (status, conflict["error"]))
        status, mismatch = self.configure("op-3", {"launches_per_minute": 6}, version + 1, body_key="op-4")
        self.assertEqual(422, status, mismatch)
        self.assertIn("must match", mismatch["message"])
        self.assertEqual(before, (self.store.path.read_bytes(), self.board.path.read_bytes()))

    def test_unkeyed_command_is_audited_with_a_server_key_and_large_results_are_bounded(self):
        version = self.store.read()["version"]
        status, result = self.configure(None, {"paused": True}, version, header=False)
        self.assertEqual(200, status, result)
        self.assertTrue(result["operation_id"].startswith("server-"))
        receipts = self.board.read_snapshot()["rest_operations"]
        stored = receipts[result["operation_id"]]["response"]["result"]
        self.assertLessEqual(len(json.dumps(stored)), WorkHttpController.MAX_RECEIPT_RESULT_BYTES)

    def test_refused_effect_writes_no_receipt_and_reads_are_not_audited(self):
        status, _ = self.configure("op-5", {"launches_per_minute": 5}, 99)
        self.assertEqual(409, status)
        status, _ = self.request("/ca/work/api/v1/commands/parallel-orphans-preview", {})
        self.assertEqual(200, status)
        self.assertEqual({}, self.board.read_snapshot().get("rest_operations", {}))

    def test_operator_receipts_stay_readable_by_57dbfb3(self):
        version = self.store.read()["version"]
        self.assertEqual(200, self.configure("op-6", {"launches_per_minute": 5}, version)[0])
        raw = json.loads(self.board.path.read_text(encoding="utf-8"))
        beta = rw.beta_module("work_plan_tree").normalize_document(raw)
        self.assertIn("op-6", beta["rest_operations"])

    def test_ctl_and_rest_share_one_operator_path(self):
        version = self.store.read()["version"]
        with patch.object(self.controller, "_operator_effect", wraps=self.controller._operator_effect) as effect:
            first = self.controller.operator(SCOPE, "parallel_configure",
                                             {"changes": {"launches_per_minute": 7}, "expected_version": version},
                                             "ctl-1")
            replay = self.controller.operator(SCOPE, "parallel_configure",
                                              {"changes": {"launches_per_minute": 7}, "expected_version": version},
                                              "ctl-1")
        self.assertEqual(1, effect.call_count)
        self.assertEqual((False, True), (first["replayed"], replay["replayed"]))


if __name__ == "__main__":
    unittest.main()
