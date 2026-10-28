"""Work HTTP contract against real owned loopback HTTP sockets."""
import json
import tempfile
import threading
import unittest
import urllib.error
from ciel_runtime_support.router_access import RouterAccessPolicy, RouterAccessHttpController
from ciel_runtime_support.request_body_policy import RouterRequestBodyPolicy
from ciel_runtime_support.router_http import RouterHttpCore, RouterHttpErrors, RouterHttpGetEndpoints, RouterHttpHandler, RouterHttpPostEndpoints, RouterHttpPresentation, RouterHttpServices
from contextlib import nullcontext
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ciel_runtime_support.http_response import HttpResponseAdapter
from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices
from ciel_runtime_support.work_plan_store import session_work_plan_store
from ciel_runtime_support import work_plan_tree as tree


class WorkHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name) / "state"
        self.config = {}
        self.controller = WorkHttpController(WorkHttpServices(
            self.state, lambda: self.config, lambda: "claude:one",
            lambda identity: {"session_id": identity, "ciel_runtime": {"version": "test"}, "last_observed": None},
            HttpResponseAdapter.write_json, "test-instance"))
        controller = self.controller

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

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:" + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.temp.cleanup()

    def request(self, path="/ca/work", body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        try:
            response = urllib.request.urlopen(req, timeout=3)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def change(self, operation, **fields):
        return self.request("/ca/work/nodes/" + operation, dict(
            session_id="claude:one", expected_version=self.request()[1]["version"], **fields))

    def test_read_absent_is_nonmutating_and_isolated(self):
        status, board = self.request()
        self.assertEqual(200, status)
        self.assertEqual([], board["nodes"])
        self.assertFalse(self.state.exists())
        self.assertEqual("claude:one", board["runtime"]["session_id"])
        self.assertIsNone(board["runtime"]["last_observed"])
        self.assertEqual(200, self.request("/ca/work?session_id=codex:two")[0])
        self.assertFalse(self.state.exists())

    def test_full_tree_mutations_and_conflict(self):
        status, board = self.change("add", title="root", children=[{"title": "phase", "children": [
            {"title": "first"}, {"title": "second"}]}])
        self.assertEqual(200, status)
        ids = {n["title"]: n["id"] for n in board["nodes"]}
        self.assertEqual(4, len(ids))
        status, reordered = self.change("reorder", id=ids["second"], position=1)
        self.assertEqual(200, status)
        self.assertEqual([ids["second"], ids["first"]], [n["id"] for n in reordered["nodes"] if n["parent_id"] == ids["phase"]])
        status, edited = self.change("update", id=ids["second"], title="renamed")
        self.assertEqual(200, status)
        status, noop = self.change("update", id=ids["second"], title="renamed")
        self.assertFalse(noop["changed"])
        self.assertEqual(edited["version"], noop["version"])
        status, error = self.request("/ca/work/nodes/remove", {"session_id": "claude:one", "expected_version": 0, "id": ids["phase"]})
        self.assertEqual(409, status)
        self.assertEqual("version_conflict", error["error"])
        status, removed = self.change("remove", id=ids["phase"])
        self.assertEqual(200, status)
        self.assertEqual([ids["root"]], [n["id"] for n in removed["nodes"]])

    def test_all_roots_and_session_isolation(self):
        self.change("add", title="root-one")
        status, board = self.change("add", title="root-two", children=[{"title": "queued-child"}])
        self.assertEqual(200, status)
        self.assertEqual(3, len(board["nodes"]))
        self.assertEqual(2, len(board["root_ids"]))
        self.assertEqual(1, len(board["queue_ids"]))
        self.assertEqual([], self.request("/ca/work?session_id=codex:two")[1]["nodes"])

    def test_runtime_error_invalidation_has_no_error_body(self):
        errors = []
        from dataclasses import replace
        self.controller.services = replace(self.controller.services, recent_errors=lambda _identity: list(errors))
        response = urllib.request.urlopen(urllib.request.Request(self.base + "/ca/work/events",
            headers={"Authorization": "Bearer owned-admin"}), timeout=4)
        def frame():
            lines = []
            while True:
                line = response.readline().decode().strip()
                if not line:
                    return lines
                lines.append(line)
        self.assertIn("event: work.snapshot", frame())
        errors.append({"request_id": "proof", "status": 504, "category": "timeout"})
        changed = frame()
        self.assertIn("event: runtime.error", changed)
        self.assertNotIn("504", str(changed))
        response.close()
        self.assertEqual(504, self.request("/ca/work/runtime")[1]["runtime"]["errors"][0]["status"])

    def test_validation_and_corruption(self):
        for version in (True, 1.0, "0", -1, None):
            self.assertEqual(422, self.request("/ca/work/nodes/add", {"expected_version": version, "title": "x"})[0])
        self.assertEqual(422, self.request("/ca/work?session_id=../../bad")[0])
        self.assertEqual(422, self.request("/ca/work?session_id=claude:one&session_id=codex:two")[0])
        store = session_work_plan_store(self.state, "claude:one")
        store.path.parent.mkdir(parents=True)
        store.path.write_text("invalid")
        before = sorted(str(p.relative_to(self.state)) for p in self.state.rglob("*"))
        self.assertEqual(503, self.request()[0])
        self.assertEqual(before, sorted(str(p.relative_to(self.state)) for p in self.state.rglob("*")))
        self.assertEqual("invalid", store.path.read_text())

    def test_sse_snapshot_then_mcp_store_commit_and_reconnect(self):
        response = urllib.request.urlopen(self.base + "/ca/work/events", timeout=4)
        def frame(stream):
            lines = []
            while True:
                line = stream.readline().decode().strip()
                if not line:
                    return lines
                lines.append(line)
        initial = frame(response)
        self.assertIn("event: work.snapshot", initial)
        store = session_work_plan_store(self.state, "claude:one")
        store.transaction(lambda d: tree.add_node(d, None, "MCP-equivalent commit"))
        changed = frame(response)
        self.assertIn("event: work.changed", changed)
        self.assertIn("id: test-instance:1", changed)
        response.close()
        req = urllib.request.Request(self.base + "/ca/work/events", headers={"Last-Event-ID": "old-instance:0"})
        with urllib.request.urlopen(req, timeout=4) as stream:
            self.assertIn("event: work.resync_required", frame(stream))




class WorkRouterAuthTests(WorkHttpTests):
    def setUp(self):
        super().setUp()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.config.update(router_debug_external_access=True, router_debug_external_access_confirmed=True)
        policy = RouterAccessPolicy({}, lambda v, default: default if v is None else bool(v),
                                    lambda v, default: default, lambda: self.config)
        rejection = RouterAccessHttpController(
            lambda handler, cfg: policy.request_allowed(handler, cfg, lambda: "owned-admin", lambda: "owned-bridge"),
            policy.external_access_enabled)
        reject = rejection.reject_external_request
        def false(*_args, **_kwargs):
            return False
        writer = HttpResponseAdapter.write_json
        services = RouterHttpServices(
            core=RouterHttpCore(lambda: self.config, reject,
                lambda _cfg: ("fixture", {}), json.loads, lambda _e: False, lambda *_a: None,
                lambda *_a: nullcontext(), RouterRequestBodyPolicy(environment={})),
            get=RouterHttpGetEndpoints(false, false, false, false, false, false, false, self.controller.get, false),
            post=RouterHttpPostEndpoints(false, false, false, false, self.controller.post, false),
            presentation=RouterHttpPresentation(false, false, false, writer, false, false, false),
            errors=RouterHttpErrors(false, writer))

        class Handler(RouterHttpHandler):
            def _services(self):
                return services

            def setup(self):
                super().setup()
                self.client_address = ("192.0.2.10", self.client_address[1])

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:" + str(self.server.server_port)

    def request(self, path="/ca/work", body=None, token="owned-admin"):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + token})
        try:
            response = urllib.request.urlopen(req, timeout=3)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def test_auth_rejection_before_mutation(self):
        self.assertEqual(401, self.request(token="wrong")[0])
        self.assertEqual(401, self.request("/ca/work/nodes/add", {"title": "x", "expected_version": 0}, token="wrong")[0])
        self.assertFalse(self.state.exists())
        self.assertEqual(401, self.request(token="owned-bridge")[0])
        self.config["router_debug_external_access"] = False
        self.assertEqual(403, self.request()[0])

    def test_sse_snapshot_then_mcp_store_commit_and_reconnect(self):
        # The inherited stream test exercises direct controller sockets. Here
        # authentication itself is proved on production GET/POST dispatch.
        self.assertEqual(200, self.request()[0])
