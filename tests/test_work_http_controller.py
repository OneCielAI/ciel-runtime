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
        self.saved_config = []
        self.controller = WorkHttpController(WorkHttpServices(
            self.state, lambda: self.config, lambda: "claude:one",
            lambda identity: {"session_id": identity, "ciel_runtime": {"version": "test"}, "last_observed": None},
            HttpResponseAdapter.write_json, "test-instance", save_config=self._save_config))
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

    def _save_config(self, value):
        saved = dict(value)
        self.config.clear()
        self.config.update(saved)
        self.saved_config.append(saved)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.temp.cleanup()

    def request(self, path="/ca/work", body=None, headers=None):
        data = None if body is None else json.dumps(body).encode()
        request_headers = {"Content-Type": "application/json"}
        request_headers.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, headers=request_headers)
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
        status, error = self.request("/ca/work?session_id=codex:two")
        self.assertEqual(422, status)
        self.assertEqual("invalid_request", error["error"])
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
        status, error = self.request("/ca/work?session_id=codex:two")
        self.assertEqual(422, status)
        self.assertEqual("invalid_request", error["error"])

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

    def test_current_identity_is_required_and_legacy_id_must_match(self):
        from dataclasses import replace
        original = self.controller.services
        self.controller.services = replace(original, current_identity=lambda: None)
        self.assertEqual(422, self.request()[0])
        self.assertEqual(422, self.request("/ca/work/api/v1/openapi")[0])
        self.assertEqual(422, self.request("/ca/work/nodes/add", {
            "session_id": "claude:one", "expected_version": 0, "title": "x"})[0])
        self.controller.services = original
        self.assertFalse(self.state.exists())

    def test_v1_reads_use_current_session_and_legacy_board_matches(self):
        _, legacy = self.change("add", title="root", children=[{"title": "phase"}])
        status, board = self.request("/ca/work/api/v1/board")
        self.assertEqual(200, status)
        self.assertEqual(legacy["nodes"], board["nodes"])
        status, state = self.request("/ca/work/api/v1/state?session_id=claude:one")
        self.assertEqual((200, legacy["version"]), (status, state["version"]))
        status, queue = self.request("/ca/work/api/v1/queue")
        self.assertEqual(200, status)
        self.assertEqual("n1", queue["active"]["id"])
        self.assertEqual([], queue["items"])
        self.assertEqual(422, self.request("/ca/work/api/v1/board?session_id=codex:two")[0])
        openapi = self.request("/ca/work/api/v1/openapi")[1]
        self.assertEqual("3.1.0", openapi["openapi"])
        parallel_help = openapi["paths"]["/ca/work/api/v1/parallel"]["get"]["description"]
        self.assertIn("main session is the coordinator", parallel_help)
        self.assertIn("Other sessions expose counts only", parallel_help)
        events_help = openapi["paths"]["/ca/work/api/v1/events"]["get"]["description"]
        self.assertIn("parallel.changed", events_help)
        self.assertIn("no event id", events_help)

    def test_v1_operator_projections_are_redacted_and_current_session_scoped(self):
        self.config.update({"work_mode": True, "work_auto_continue": False})
        store = session_work_plan_store(self.state, "claude:one")
        def seed(doc):
            doc["side_reviews"] = {"a" * 64: {"state": "pending", "kind": "info",
                "action": "answer", "summary": "review this", "reply": "private reply",
                "source": {"authorization": "secret"}, "recorded_at": 1}}
            doc["side_handoffs"] = {"b" * 64: {"state": "requested", "kind": "request",
                "summary": "act on this", "question": "safe question", "reply_token": "secret"}}
        store.transaction(seed)

        status, settings = self.request("/ca/work/api/v1/settings")
        self.assertEqual(200, status)
        self.assertTrue(any(row["key"] == "work_mode" for row in settings["settings"]))
        status, parallel = self.request("/ca/work/api/v1/parallel")
        self.assertEqual(200, status)
        self.assertEqual("claude:one", parallel["session_id"])
        status, side = self.request("/ca/work/api/v1/side")
        self.assertEqual(200, status)
        serialized = json.dumps(side)
        self.assertIn("review this", serialized)
        # No inbox receipt exists for this seeded review, so delivery is not claimed.
        self.assertEqual("unknown", side["reviews"][0]["delivery"])
        self.assertNotIn("private reply", serialized)
        self.assertNotIn("secret", serialized)
        status, audit = self.request("/ca/work/api/v1/audit")
        self.assertEqual((200, []), (status, audit["events"]))
        self.assertEqual(422, self.request("/ca/work/api/v1/side?session_id=codex:two")[0])

    def test_v1_operator_commands_update_settings_and_side_records(self):
        status, result = self.request("/ca/work/api/v1/commands/settings-update", {
            "key": "work_auto_continue", "value": "off"})
        self.assertEqual(200, status)
        self.assertFalse(self.config["work_auto_continue"])
        self.assertTrue(self.saved_config)

        store = session_work_plan_store(self.state, "claude:one")
        def seed(doc):
            doc["side_reviews"] = {"a" * 64: {"state": "pending", "kind": "info",
                "action": "answer", "summary": "review", "reply": "private", "recorded_at": 1}}
            doc["side_handoffs"] = {"b" * 64: {"state": "requested", "item_id": "n1",
                "kind": "request", "summary": "handoff", "gates": []}}
        store.transaction(lambda doc: tree.add_subtree(doc, None, {"title": "root"}))
        store.transaction(seed)
        version = store.read_snapshot()["version"]
        status, reviewed = self.request("/ca/work/api/v1/commands/side-review", {
            "receipt_ids": ["a" * 64], "verdict": "ok", "note": "", "expected_version": version})
        self.assertEqual(200, status)
        version = reviewed["result"]["version"]
        status, handed = self.request("/ca/work/api/v1/commands/side-handoff", {
            "event_id": "b" * 64, "answer": "handled", "expected_version": version})
        self.assertEqual(200, status)
        self.assertEqual("answered", handed["result"]["state"])

    def test_v1_idempotency_cas_and_changed_request_conflict(self):
        request = {"expected_version": 0, "goal": "goal", "children": [
            {"title": "phase", "children": [{"title": "task"}]}]}
        headers = {"Idempotency-Key": "create-goal"}
        status, first = self.request("/ca/work/api/v1/commands/plan-set", request, headers)
        self.assertEqual(200, status)
        self.assertEqual(1, first["version"])
        self.assertEqual(first, self.request("/ca/work/api/v1/commands/plan-set", request, headers)[1])
        self.assertEqual(1, self.request()[1]["version"])
        status, conflict = self.request("/ca/work/api/v1/commands/plan-set",
            {**request, "goal": "different"}, headers)
        self.assertEqual((409, "idempotency_conflict"), (status, conflict["error"]))
        status, conflict = self.request("/ca/work/api/v1/commands/node-add",
            {"expected_version": 0, "title": "stale", "operation_id": "stale-add"})
        self.assertEqual((409, "version_conflict"), (status, conflict["error"]))

    def test_v1_human_commands_queue_and_sibling_semantics(self):
        def command(name, key, **body):
            return self.request("/ca/work/api/v1/commands/" + name,
                {"expected_version": self.request()[1]["version"], **body},
                {"Idempotency-Key": key})
        self.assertEqual(200, command("plan-set", "one", goal="one", children=[
            {"title": "phase", "children": [{"title": "a"}, {"title": "b"}]}])[0])
        self.assertEqual(200, command("plan-set", "two", goal="two")[0])
        self.assertEqual(200, command("plan-set", "three", goal="three")[0])
        board = self.request()[1]
        ids = {node["title"]: node["id"] for node in board["nodes"]}
        status, error = command("queue-position", "active-no-queue", root_id=ids["one"], queue_position=1)
        self.assertEqual(422, status)
        self.assertIn("not a waiting goal", error["message"])
        self.assertEqual(200, command("queue-position", "queue-three", root_id=ids["three"], queue_position=1)[0])
        self.assertEqual([ids["three"], ids["two"]], [row["id"] for row in self.request("/ca/work/api/v1/queue")[1]["items"]])
        self.assertEqual(200, command("sibling-reorder", "move-b", id=ids["b"], position=1)[0])
        board = self.request()[1]
        self.assertEqual([ids["b"], ids["a"]], [n["id"] for n in board["nodes"] if n["parent_id"] == ids["phase"]])
        self.assertEqual(404, command("node-remove", "missing", id="n999")[0])
        self.assertEqual(200, command("node-update", "priority-two", id=ids["two"], priority="P1")[0])
        self.assertEqual("P1", next(n for n in self.request()[1]["nodes"] if n["id"] == ids["two"])["priority"])
        self.assertEqual(200, command("node-update", "progress-a", id=ids["a"], progress="tests passed")[0])
        self.assertEqual("tests passed", next(n for n in self.request()[1]["nodes"] if n["id"] == ids["a"])["progress"])

    def test_v1_commands_match_mcp_tree_results(self):
        from ciel_runtime_support import work_mcp_tools
        mcp_state = Path(self.temp.name) / "mcp-state"
        def mcp(name, **args):
            result = work_mcp_tools.dispatch_work_tool(
                1, name, dict(args, session_id="claude:one"), load_config=lambda: {}, state_dir=mcp_state,
                respond=lambda _id, text, error=False: {"text": text, "error": error})
            self.assertFalse(result["error"], result)
        def rest(name, key, **body):
            status, payload = self.request("/ca/work/api/v1/commands/" + name,
                {"expected_version": self.request()[1]["version"], **body}, {"Idempotency-Key": key})
            self.assertEqual(200, status, payload)
        plan = {"children": [{"title": "phase", "children": [{"title": "a"}, {"title": "b"}]}]}
        mcp("work_plan_set", goal="one", **plan)
        rest("plan-set", "one", goal="one", **plan)
        mcp("work_plan_set", goal="two")
        rest("plan-set", "two", goal="two")
        mcp("work_node_update", id="n1", note="scoped")
        rest("node-update", "note", id="n1", note="scoped")
        mcp("work_node_update", id="n5", priority="P2")
        rest("node-update", "priority", id="n5", priority="P2")
        mcp("work_select", root_id="n5", user_requested=True)
        rest("active-select", "select", root_id="n5", user_requested=True)
        def comparable(doc):
            return {"active_root": doc["active_root"], "phase": doc["phase"], "nodes": [
                {key: value for key, value in node.items() if key not in ("created_at", "updated_at")}
                for node in doc["nodes"]]}
        expected = comparable(session_work_plan_store(mcp_state, "claude:one").read_snapshot())
        self.assertEqual(expected, comparable(session_work_plan_store(self.state, "claude:one").read_snapshot()))

    def test_v1_execute_replan_select_focus_and_urgent(self):
        def command(name, key, **body):
            return self.request("/ca/work/api/v1/commands/" + name,
                {"expected_version": self.request()[1]["version"], **body},
                {"Idempotency-Key": key})
        command("plan-set", "plan", goal="goal", children=[
            {"title": "phase", "children": [{"title": "task"}]}])
        self.assertEqual("executing", command("execute", "execute")[1]["result"]["phase"])
        self.assertEqual("planning", command("replan", "replan", reason="revise")[1]["result"]["phase"])
        command("node-add", "queued", title="queued")
        board = self.request()[1]
        queued = board["queue_ids"][0]
        self.assertEqual(200, command("active-select", "select", root_id=queued, user_requested=True)[0])
        self.assertEqual(200, command("focus", "focus", node_ids=[queued], user_requested=True)[0])
        status, urgent = command("urgent", "urgent", goal="urgent", user_requested=True)
        self.assertEqual(200, status)
        self.assertTrue(urgent["result"]["urgent"])

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
        v1 = urllib.request.urlopen(self.base + "/ca/work/api/v1/events", timeout=4)
        v1_initial = frame(v1)
        self.assertIn("event: work.snapshot", v1_initial)
        self.assertIn('"refetch": "/ca/work/api/v1/board"', str(v1_initial))
        v1.close()
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

    def request(self, path="/ca/work", body=None, headers=None, token="owned-admin"):
        data = None if body is None else json.dumps(body).encode()
        request_headers = {"Content-Type": "application/json", "Authorization": "Bearer " + token}
        request_headers.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, headers=request_headers)
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
