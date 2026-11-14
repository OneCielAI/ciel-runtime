"""Retired reviewer contract: legacy rows stay readable and drainable.

Reviewer forks are retired: no new reviewer lease is minted and the model
schemas no longer advertise the reviewer contract. A worker that an older
build (4eebfdc/5c91ea8) moved to ``reviewing`` can only leave that state by
consuming a submitted legacy reviewer result. The host selects that reviewer
itself, so neither main nor the web operator has to name it.
"""
import json
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from ciel_runtime_support import work_parallel_mcp as m
from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.http_response import HttpResponseAdapter
from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices
from ciel_runtime_support.work_parallel import WorkParallelError, WorkParallelStore
from ciel_runtime_support.work_plan_store import session_work_plan_store

from test_work_parallel_rework_worktree import BETA_DRIVER, beta_module, beta_tree

SECRET = "legacy-reviewer-host-secret-" * 2
SCOPE = "claude:legacy-reviewer"
NONE_ELIGIBLE = "independent reviewer result required before acceptance"
MULTIPLE = "multiple legacy reviewer results; resolve in the web dashboard or menu"


class LegacyReviewerFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.state = self.tmp / "state"
        self.board = session_work_plan_store(self.state, SCOPE)
        doc = self.board.read()
        doc, root = self.board.transaction(lambda d: tree.set_plan(d, {
            "title": "Goal", "note": "", "priority": "P1", "children": [
                {"title": "Item", "note": "", "ref": "", "waiting_on": "", "children": []},
            ]}, None), expected_version=doc["version"])
        self.item = next(n["id"] for n in doc["nodes"] if n["parent_id"] == root)
        self.board.transaction(lambda d: tree.set_phase(d, "executing", root),
                               expected_version=doc["version"])
        self.store = WorkParallelStore(self.tmp / "config" / "work-parallel.json")
        self.config = {"work_parallel_host": {"token": SECRET, "capability_ready": True}}
        runtime_patch = patch("ciel_runtime_support.runtime_paths.CONFIG_DIR", self.tmp / "config")
        runtime_patch.start()
        self.addCleanup(runtime_patch.stop)

    def call(self, name, host=None, **args):
        args.setdefault("expected_version", self.store.read()["version"])
        args["_host"] = {"token": SECRET, **(host or {})}
        with patch.object(m, "ledger", return_value=self.store):
            return json.loads(m.run("work_parallel_" + name, args, self.board, SCOPE, self.config, None))

    def reviewing_worker(self):
        """A worker an older build moved to ``reviewing``: result, verify, review."""
        self.call("configure", {}, changes={"enabled": True})
        reserved = self.call("event", {"fork": True}, event="reserve", item_id=self.item, priority="P1")
        lease = reserved["lease_id"]
        self.call("event", {"host_id": "agent-1"}, event="bind", lease_id=lease)
        plan = self.board.read()["version"]
        self.call("event", {"agent_id": "agent-1"}, event="result", lease_id=lease, plan_version=plan,
                  artifact="a" * 64, evidence="worker result")
        for event in ("verify", "review"):
            self.call("event", {}, event=event, lease_id=lease, plan_version=plan, artifact="a" * 64,
                      evidence="checked")
        self.assertEqual("reviewing", self.store.read()["leases"][lease]["state"])
        return lease, reserved["worker_token"]

    def legacy_reviewer(self, ident, *, host_id="reviewer-1", state="submitted", plan_version=None,
                        item_id=None, scope=SCOPE):
        """A submitted reviewer row exactly as 4eebfdc/5c91ea8 builds persisted it."""
        doc = self.store.read()
        doc["leases"][ident] = {
            "id": ident, "scope": scope, "item_id": item_id or self.item, "priority": "P1", "created_at": 1,
            "role": "reviewer", "mode": "fork",
            "plan_version": self.board.read()["version"] if plan_version is None else plan_version,
            "state": state, "host_id": host_id, "slot": False, "usage": 0, "artifact": "b" * 64, "rework": 0,
            "evidence": "independent findings", "idle_proof": "independent findings",
            "cleanup_proof": "reviewer workspace retained after read-only review" if state == "done" else None,
            "report_hash": None,
        }
        self.store.path.write_text(json.dumps(doc), encoding="utf-8")
        return ident

    def accept(self, lease, **extra):
        return self.call("event", {}, event="accept", lease_id=lease, plan_version=self.board.read()["version"],
                         artifact="a" * 64, evidence="main accepted the reviewed result", **extra)

    def durable_bytes(self):
        return {str(path.relative_to(self.tmp)): path.read_bytes()
                for path in sorted(self.tmp.rglob("*")) if path.is_file() and path.suffix != ".lock"}

    def assert_refused_without_writes(self, message, lease, **extra):
        before = self.durable_bytes()
        with self.assertRaises(WorkParallelError) as caught:
            self.accept(lease, **extra)
        self.assertEqual(message, str(caught.exception))
        self.assertEqual(before, self.durable_bytes())


class RetiredReviewerSchemaTests(LegacyReviewerFixture):
    def test_model_schemas_no_longer_advertise_the_reviewer_contract(self):
        tools = {tool["name"]: tool for tool in m.schemas()}
        self.assertNotIn("reviewer", json.dumps(m.schemas()))
        for name, tool in tools.items():
            with self.subTest(tool=name):
                props = tool["inputSchema"]["properties"]
                self.assertNotIn("reviewer_lease_id", props)
                self.assertEqual(["worker", "resume"], props["role"]["enum"])
        report = tools["work_parallel_report"]["description"]
        self.assertTrue(report.startswith("Worker-only question/completion/handoff report."), report)
        self.assertIn("workers and forks are denied", tools["work_parallel_item_review"]["description"])
        configure = tools["work_parallel_configure"]["description"]
        self.assertNotIn("reserved P1", configure)
        self.assertIn("concurrent fork sessions per priority", configure)

    def test_worker_refusal_names_only_question_completion_and_handoff(self):
        self.call("configure", {}, changes={"enabled": True})
        reserved = self.call("event", {"fork": True}, event="reserve", item_id=self.item, priority="P1")
        self.call("event", {"host_id": "agent-1"}, event="bind", lease_id=reserved["lease_id"])
        args = {"lease_id": reserved["lease_id"], "worker_token": reserved["worker_token"]}
        with patch.object(m, "ledger", return_value=self.store):
            with self.assertRaises(WorkParallelError) as caught:
                m.report({**args, "event": "verify"}, self.board, self.config)
            self.assertEqual("worker may only report question, completion or handoff", str(caught.exception))
            # The legacy reviewer refusal is kept for review events.
            with self.assertRaisesRegex(WorkParallelError, "reviewer reports are retired"):
                m.report({**args, "event": "review"}, self.board, self.config)


class LegacyReviewerDrainTests(LegacyReviewerFixture):
    def test_reviewing_worker_drains_with_lease_id_only(self):
        lease, _ = self.reviewing_worker()
        reviewer = self.legacy_reviewer("legacy-reviewer")
        self.accept(lease)
        leases = self.store.read()["leases"]
        self.assertEqual("accepted", leases[lease]["state"])
        self.assertEqual("done", leases[reviewer]["state"])
        self.assertEqual("reviewer workspace retained after read-only review", leases[reviewer]["cleanup_proof"])

    def test_matching_explicit_reviewer_id_is_still_accepted(self):
        lease, _ = self.reviewing_worker()
        reviewer = self.legacy_reviewer("legacy-reviewer")
        self.accept(lease, reviewer_lease_id=reviewer)
        self.assertEqual("accepted", self.store.read()["leases"][lease]["state"])

    def test_no_eligible_reviewer_is_refused_and_writes_nothing(self):
        lease, _ = self.reviewing_worker()
        # Each row differs from an eligible one in exactly one rule.
        self.legacy_reviewer("same-host", host_id="agent-1")
        self.legacy_reviewer("consumed", state="done")
        self.legacy_reviewer("stale-plan", plan_version=0)
        self.legacy_reviewer("other-item", item_id="other-item")
        self.legacy_reviewer("other-scope", scope="claude:someone-else")
        self.assert_refused_without_writes(NONE_ELIGIBLE, lease)

    def test_two_eligible_reviewers_are_refused_and_write_nothing(self):
        lease, _ = self.reviewing_worker()
        first = self.legacy_reviewer("reviewer-a", host_id="reviewer-a")
        self.legacy_reviewer("reviewer-b", host_id="reviewer-b")
        self.assert_refused_without_writes(MULTIPLE, lease)
        # Naming one of them does not choose between them.
        self.assert_refused_without_writes(MULTIPLE, lease, reviewer_lease_id=first)

    def test_mismatched_explicit_reviewer_id_is_refused_and_writes_nothing(self):
        lease, _ = self.reviewing_worker()
        self.legacy_reviewer("legacy-reviewer")
        ineligible = self.legacy_reviewer("same-host", host_id="agent-1")
        for named in (ineligible, "missing-reviewer"):
            with self.subTest(named=named):
                self.assert_refused_without_writes(
                    "reviewer_lease_id does not name the single eligible legacy reviewer result", lease,
                    reviewer_lease_id=named)

    def test_status_lists_the_reviewing_worker_for_main(self):
        lease, _ = self.reviewing_worker()
        self.legacy_reviewer("legacy-reviewer")
        status = self.call("status", None)
        self.assertEqual([{"lease_id": lease, "item_id": self.item, "priority": "P1", "reviewers": "one",
                           "item_title": "Item"}], status["legacy_reviews"])


class BetaWrittenLegacyLedgerTests(LegacyReviewerFixture):
    def beta_process(self, *calls):
        spec = {"root": str(beta_tree()), "config": str(self.tmp / "config"), "ledger": str(self.store.path),
                "board": str(self.board.path), "scope": SCOPE, "token": SECRET, "config_obj": self.config,
                "calls": [[name, args] for name, args in calls]}
        done = subprocess.run([sys.executable, "-I", "-B", "-c", BETA_DRIVER, json.dumps(spec)],
                              capture_output=True, text=True, encoding="utf-8", timeout=300)
        self.assertEqual(0, done.returncode, done.stderr[-2000:])
        return json.loads(done.stdout.strip().splitlines()[-1])

    def test_ledger_written_by_57dbfb3_stays_readable_and_drainable(self):
        plan = self.board.read()["version"]
        configured, reserved = self.beta_process(
            ("work_parallel_configure", {"host": {}, "changes": {"enabled": True}}),
            ("work_parallel_event", {"host": {"fork": True}, "event": "reserve", "item_id": self.item,
                                     "priority": "P1"}))
        self.assertIn("ok", configured)
        lease = reserved["ok"]["lease_id"]
        steps = self.beta_process(
            ("work_parallel_event", {"host": {"host_id": "agent-1"}, "event": "bind", "lease_id": lease}),
            ("work_parallel_event", {"host": {"agent_id": "agent-1"}, "event": "result", "lease_id": lease,
                                     "plan_version": plan, "artifact": "a" * 64, "evidence": "worker result"}),
            *[("work_parallel_event", {"host": {}, "event": event, "lease_id": lease, "plan_version": plan,
                                       "artifact": "a" * 64, "evidence": "checked"})
              for event in ("verify", "review")])
        self.assertEqual([True] * 4, ["ok" in step for step in steps], steps)
        reviewer = self.legacy_reviewer("legacy-reviewer")
        # 57dbfb3 rewrites the whole ledger, legacy reviewer row included.
        rewritten, = self.beta_process(("work_parallel_configure", {"host": {},
                                                                      "changes": {"launches_per_minute": 5}}))
        self.assertIn("ok", rewritten)
        self.assertEqual("submitted", self.store.read()["leases"][reviewer]["state"])

        self.accept(lease)
        leases = self.store.read()["leases"]
        self.assertEqual(("accepted", "done"), (leases[lease]["state"], leases[reviewer]["state"]))
        beta = beta_module("work_parallel").WorkParallelStore(self.store.path).read()["leases"]
        self.assertEqual(("accepted", "done"), (beta[lease]["state"], beta[reviewer]["state"]))


class LegacyAcceptOperatorTests(LegacyReviewerFixture):
    """The same host selection, reachable from the web dashboard and REST."""

    def setUp(self):
        super().setUp()
        work_config = {"work_mode": True, **self.config}
        controller = WorkHttpController(WorkHttpServices(
            self.state, lambda: work_config, lambda: SCOPE,
            lambda identity: {"session_id": identity, "last_observed": None},
            HttpResponseAdapter.write_json, "test-instance",
            parallel_status=lambda identity, board: m.project(self.store, identity, board),
            parallel_store=lambda: self.store))

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
        self.addCleanup(thread.join, 3)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.base = "http://127.0.0.1:" + str(server.server_port)

    def request(self, path, body=None, headers=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data,
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            response = urllib.request.urlopen(req, timeout=10)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def test_dashboard_lists_the_reviewing_worker_without_reviewer_identity(self):
        lease, _ = self.reviewing_worker()
        self.legacy_reviewer("legacy-reviewer", host_id="SECRET-REVIEWER-HOST")
        status, body = self.request("/ca/work/api/v1/parallel")
        self.assertEqual(200, status, body)
        self.assertEqual([{"lease_id": lease, "item_id": self.item, "priority": "P1", "reviewers": "one",
                           "item_title": "Item"}], body["parallel_legacy_reviews"])
        # The legacy list names no reviewer (the existing fork evidence groups
        # are a separate, unchanged contract).
        listed = json.dumps(body["parallel_legacy_reviews"])
        self.assertNotIn("SECRET-REVIEWER-HOST", listed)
        self.assertNotIn("legacy-reviewer", listed)

    def test_operator_accept_uses_host_selection(self):
        lease, _ = self.reviewing_worker()
        reviewer = self.legacy_reviewer("legacy-reviewer")
        _, view = self.request("/ca/work/api/v1/parallel")
        good = {"lease_id": lease, "confirmed": True, "expected_version": view["version"]}
        for bad in ({**good, "confirmed": False}, {k: v for k, v in good.items() if k != "confirmed"},
                    {**good, "expected_version": "1"}, {**good, "lease_id": ""}):
            with self.subTest(body=bad):
                before = self.durable_bytes()
                status, payload = self.request("/ca/work/api/v1/commands/parallel-legacy-accept", bad)
                self.assertEqual(422, status, payload)
                self.assertEqual(before, self.durable_bytes())
        status, payload = self.request("/ca/work/api/v1/commands/parallel-legacy-accept",
                                       {**good, "expected_version": view["version"] - 1})
        self.assertEqual((409, "version_conflict"), (status, payload["error"]))
        status, payload = self.request("/ca/work/api/v1/commands/parallel-legacy-accept", good)
        self.assertEqual(200, status, payload)
        self.assertEqual({"lease_id": lease, "state": "accepted", "version": self.store.read()["version"]},
                         payload["result"])
        leases = self.store.read()["leases"]
        self.assertEqual(("accepted", "done"), (leases[lease]["state"], leases[reviewer]["state"]))
        _, after = self.request("/ca/work/api/v1/parallel")
        self.assertEqual([], after["parallel_legacy_reviews"])

    def test_operator_accept_refuses_two_reviewers_and_writes_nothing(self):
        lease, _ = self.reviewing_worker()
        self.legacy_reviewer("reviewer-a", host_id="reviewer-a")
        self.legacy_reviewer("reviewer-b", host_id="reviewer-b")
        _, view = self.request("/ca/work/api/v1/parallel")
        self.assertEqual("multiple", view["parallel_legacy_reviews"][0]["reviewers"])
        before = self.durable_bytes()
        status, payload = self.request("/ca/work/api/v1/commands/parallel-legacy-accept",
                                       {"lease_id": lease, "confirmed": True, "expected_version": view["version"]})
        self.assertEqual((422, MULTIPLE), (status, payload["message"]))
        self.assertEqual(before, self.durable_bytes())


if __name__ == "__main__":
    unittest.main()
