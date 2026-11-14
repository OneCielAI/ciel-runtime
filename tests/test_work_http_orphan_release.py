"""Orphan release over REST shares the menu/ctl function (pinned preview, one lease).

A dead owner is released on its observation. A lease from a build without
owner records (``legacy_no_owner``) is released only with the operator's
explicit confirmation. Both are pinned to the exact preview digest and the
ledger version it was built from, audited, and replayed by Idempotency-Key.
The audit record names the operator surface from X-Ciel-Operator-Source.
"""
import json
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support import work_parallel_mcp as m
from ciel_runtime_support.http_response import HttpResponseAdapter
from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices
from ciel_runtime_support.work_parallel import WorkParallelStore
from ciel_runtime_support.work_plan_store import session_work_plan_store

from test_work_http_parallel_gaps import serve

SCOPE = "claude:orphan-release"


class OrphanReleaseRestTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.state = self.tmp / "state"
        self.store = WorkParallelStore(self.tmp / "config" / "work-parallel.json")
        self.store.configure({"enabled": True}, capability_ready=True, expected_version=0)
        controller = WorkHttpController(WorkHttpServices(
            self.state, lambda: {"work_mode": True}, lambda: SCOPE,
            lambda identity: {"session_id": identity, "last_observed": None},
            HttpResponseAdapter.write_json, "test-instance",
            parallel_status=lambda identity, board: m.project(self.store, identity, board),
            parallel_store=lambda: self.store))
        self.request = serve(self, controller)
        self.board = session_work_plan_store(self.state, SCOPE)

    def seed(self, ident, owner):
        """One slot-holding running lease, as an older or crashed session left it."""
        doc = self.store.read()
        doc["leases"][ident] = {
            "id": ident, "scope": SCOPE, "item_id": "item-" + ident, "priority": "P2", "created_at": 1,
            "role": "worker", "mode": "fork", "plan_version": 1, "state": "running", "host_id": "agent-" + ident,
            "slot": True, "usage": 0.05, "artifact": None, "rework": 0, "evidence": None, "idle_proof": None,
            "cleanup_proof": None, "report_hash": None, **({"owner": owner} if owner is not None else {})}
        doc["version"] += 1
        self.store.path.write_text(json.dumps(doc), encoding="utf-8")

    def preview(self):
        status, body = self.request("/ca/work/api/v1/commands/parallel-orphans-preview", {})
        self.assertEqual(200, status, body)
        return body["result"]

    def apply(self, body, headers=None):
        return self.request("/ca/work/api/v1/commands/parallel-orphans-apply", body, headers)

    def ledger_bytes(self):
        return self.store.path.read_bytes()

    def test_legacy_no_owner_lease_is_released_only_with_confirmation(self):
        self.seed("legacy-lease-0001", None)
        preview = self.preview()
        row = next(row for row in preview["leases"] if row["lease_id"] == "legacy-lease-0001")
        self.assertEqual("legacy_no_owner", row["owner_state"])
        body = {"lease_id": "legacy-lease-0001", "preview_digest": preview["digest"],
                "expected_version": preview["version"]}
        before = self.ledger_bytes()
        status, refused = self.apply(body)
        self.assertEqual(422, status, refused)
        self.assertIn("confirmation that its session is shut down", refused["message"])
        self.assertEqual(before, self.ledger_bytes())
        status, released = self.apply({**body, "legacy_confirmed": True}, {"Idempotency-Key": "orphan-1"})
        self.assertEqual(200, status, released)
        self.assertEqual({"lease_id": "legacy-lease-0001", "item_id": "item-legacy-lease-0001", "priority": "P2",
                          "owner_state": "legacy_no_owner", "version": preview["version"] + 1, "state": "orphaned"},
                         released["result"])
        lease = self.store.read()["leases"]["legacy-lease-0001"]
        self.assertEqual(("orphaned", False), (lease["state"], lease["slot"]))
        status, replay = self.apply({**body, "legacy_confirmed": True}, {"Idempotency-Key": "orphan-1"})
        self.assertEqual((200, True), (status, replay["replayed"]))
        self.assertEqual(released["result"], replay["result"])

    def test_stale_digest_or_version_and_live_owner_release_nothing(self):
        self.seed("legacy-lease-0002", None)
        preview = self.preview()
        good = {"lease_id": "legacy-lease-0002", "preview_digest": preview["digest"],
                "expected_version": preview["version"], "legacy_confirmed": True}
        before = self.ledger_bytes()
        status, stale = self.apply({**good, "preview_digest": "0" * 64})
        self.assertEqual(422, status, stale)
        status, version = self.apply({**good, "expected_version": preview["version"] - 1})
        self.assertEqual((409, "version_conflict"), (status, version["error"]))
        status, missing = self.apply({k: v for k, v in good.items() if k != "expected_version"})
        self.assertEqual(422, status, missing)
        self.assertEqual(before, self.ledger_bytes())
        # Any ledger change after the preview invalidates its digest.
        self.store.configure({"launches_per_minute": 5}, capability_ready=True,
                             expected_version=self.store.read()["version"])
        before = self.ledger_bytes()
        status, moved = self.apply({**good, "expected_version": self.store.read()["version"]})
        self.assertIn(status, (409, 422), moved)
        self.assertEqual(before, self.ledger_bytes())

    def audit(self):
        status, audit = self.request("/ca/work/api/v1/audit")
        self.assertEqual(200, status, audit)
        return audit["events"]

    def release_body(self, ident):
        self.seed(ident, None)
        preview = self.preview()
        return {"lease_id": ident, "preview_digest": preview["digest"], "expected_version": preview["version"],
                "legacy_confirmed": True}

    def test_audit_source_is_server_derived_and_a_forged_header_changes_nothing(self):
        # Loopback without credentials: rest, whatever the client claims.
        status, _ = self.apply(self.release_body("legacy-lease-0003"),
                               {"Idempotency-Key": "forged-1", "X-Ciel-Operator-Source": "ciel-runtimectl"})
        self.assertEqual(200, status)
        self.assertEqual("authenticated REST operator",
                         self.store.read()["leases"]["legacy-lease-0003"]["terminal"]["proof"])
        # Administrator token: rest. Session cookie only: web.
        status, _ = self.apply(self.release_body("legacy-lease-0004"),
                               {"Idempotency-Key": "bearer-1", "Authorization": "Bearer secret-admin-token",
                                "X-Ciel-Operator-Source": "web"})
        self.assertEqual(200, status)
        status, _ = self.apply(self.release_body("legacy-lease-0005"),
                               {"Idempotency-Key": "cookie-1", "Cookie": "ciel_session=secret-session-value",
                                "X-Ciel-Operator-Source": "ciel-runtimectl"})
        self.assertEqual(200, status)
        self.assertEqual("authenticated web operator",
                         self.store.read()["leases"]["legacy-lease-0005"]["terminal"]["proof"])
        events = {row["operation_id"]: row for row in self.audit()}
        self.assertEqual(("rest", {"auth": "loopback"}), (events["forged-1"]["source"], events["forged-1"]["origin"]))
        self.assertEqual(("rest", {"auth": "bearer"}), (events["bearer-1"]["source"], events["bearer-1"]["origin"]))
        self.assertEqual(("web", {"auth": "session"}), (events["cookie-1"]["source"], events["cookie-1"]["origin"]))
        stored = self.board.path.read_text(encoding="utf-8")
        for secret in ("secret-admin-token", "secret-session-value"):
            self.assertNotIn(secret, stored)

    def test_in_process_ctl_origin_is_trusted_and_recorded_without_secrets(self):
        controller = WorkHttpController(WorkHttpServices(
            self.state, lambda: {"work_mode": True}, lambda: SCOPE,
            lambda identity: {"session_id": identity, "last_observed": None},
            HttpResponseAdapter.write_json, "test-instance",
            parallel_status=lambda identity, board: m.project(self.store, identity, board),
            parallel_store=lambda: self.store,
            operator_origin={"source": "ctl", "os_user": "operator-user", "pid": 4242}))
        ctl = serve(self, controller)
        body = self.release_body("legacy-lease-0006")
        status, released = ctl("/ca/work/api/v1/commands/parallel-orphans-apply", body,
                               {"Idempotency-Key": "ctl-1", "X-Ciel-Operator-Source": "web"})
        self.assertEqual(200, status, released)
        self.assertEqual("ciel-runtimectl operator",
                         self.store.read()["leases"]["legacy-lease-0006"]["terminal"]["proof"])
        event = next(row for row in self.audit() if row["operation_id"] == "ctl-1")
        self.assertEqual(("ctl", {"os_user": "operator-user", "pid": 4242}), (event["source"], event["origin"]))
        # A network caller cannot pass an origin: the REST runtime never sets it.
        bad = WorkHttpController(WorkHttpServices(
            self.state, lambda: {"work_mode": True}, lambda: SCOPE,
            lambda identity: {"session_id": identity, "last_observed": None},
            HttpResponseAdapter.write_json, "test-instance", parallel_store=lambda: self.store,
            operator_origin={"source": "web"}))
        status, refused = serve(self, bad)("/ca/work/api/v1/commands/parallel-orphans-apply",
                                           self.release_body("legacy-lease-0007"))
        self.assertEqual(422, status, refused)

    def test_dead_owner_release_still_works_through_the_shared_function(self):
        self.seed("dead-lease-00001", {"session_pid": 2_147_483_000, "identity": "gone-process"})
        preview = self.preview()
        row = next(row for row in preview["leases"] if row["lease_id"] == "dead-lease-00001")
        if row["owner_state"] != "dead":
            self.skipTest("owner observation on this host is " + row["owner_state"])
        status, released = self.apply({"lease_id": "dead-lease-00001", "preview_digest": preview["digest"],
                                       "expected_version": preview["version"]}, {"Cookie": "ciel_session=s"})
        self.assertEqual(200, status, released)
        self.assertEqual("dead", released["result"]["owner_state"])
        self.assertEqual("authenticated web operator",
                         self.store.read()["leases"]["dead-lease-00001"]["terminal"]["proof"])


if __name__ == "__main__":
    unittest.main()
