"""Safe session restart on every operator surface: REST, web console, prelaunch menu, ctl.

User requirement 2026-11-17: the safe restart (worktree snapshot, input hold,
--continue relaunch) is visible and callable from the menu, the web dashboard,
the REST API and ciel-runtimectl. Every surface queues through the one
RuntimeSessionRestartService. No test here touches a real process: clients are
records in temporary instance folders whose pids a fake is_running accepts.
"""
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ciel_runtime_support import restart_hold
from ciel_runtime_support import runtime_session_restart_menu as menu
from ciel_runtime_support.http_response import HttpResponseAdapter
from ciel_runtime_support.runtime_session_restart import (
    RESTART_CLIENTS_DIR_NAME,
    RESTART_REQUEST_FILE_NAME,
    RuntimeSessionRestartService,
    RuntimeSessionRestartServicePorts,
    SessionRestartCliPorts,
    session_restart_command,
)
from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices
from ciel_runtime_support.work_web_ui import render_work_web_ui

FAKE_PID = 424242  # never a real process: only the fake is_running accepts it
OTHER_PID = 434343


def make_service(root: Path, live=(FAKE_PID,)):
    instances = root / "router-instances"
    instance = instances / "inst-a"
    clients = instance / RESTART_CLIENTS_DIR_NAME
    clients.mkdir(parents=True)
    for pid in live:
        (clients / f"{pid}.json").write_text(json.dumps(
            {"pid": pid, "workspace": "W:/repo", "started_at": "2026-11-17T10:00:00", "router_port": 1}),
            encoding="utf-8")
    logs = []
    service = RuntimeSessionRestartService(RuntimeSessionRestartServicePorts(
        instance, instances, lambda pid: pid in live, lambda *parts: logs.append(" ".join(parts)),
        lambda _path: "digest", unisolated_test=lambda: False))
    return service, instance, logs


class RestApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service, self.instance, self.logs = make_service(self.root)
        self.queued = []

        def queue(**kwargs):
            self.queued.append(kwargs)
            return self.service.queue(**kwargs, instance_dir=self.instance)

        self.controller = self.build(lambda: menu.instance_status(self.service, self.instance, lambda _epoch: 3), queue)
        self.serve(self.controller)

    def build(self, status, queue, identity="claude:one"):
        return WorkHttpController(WorkHttpServices(
            self.root / "state", lambda: {}, lambda: identity,
            lambda ident: {"session_id": ident, "ciel_runtime": {"version": "test"}, "last_observed": None},
            HttpResponseAdapter.write_json, "test-instance", restart_status=status, restart_queue=queue))

    def serve(self, controller):
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
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(3)))
        self.base = "http://127.0.0.1:" + str(server.server_port)

    def call(self, path, body=None, headers=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base + path, data=data,
                                         headers={"Content-Type": "application/json", **(headers or {})})
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def test_runtime_get_projects_clients_queued_request_and_holds(self):
        status, body = self.call("/ca/work/api/v1/runtime")
        self.assertEqual(200, status)
        restart = body["runtime"]["restart"]
        self.assertTrue(restart["available"])
        self.assertEqual([FAKE_PID], [client["pid"] for client in restart["clients"]])
        self.assertIsNone(restart["pending_request"])
        self.assertEqual([], restart["holds"])
        restart_hold.begin(self.instance, FAKE_PID, "f" * 32, now=time.time() - 5)
        self.service.queue(source="mcp", reason="deploy", instance_dir=self.instance)
        restart = self.call("/ca/work/runtime")[1]["runtime"]["restart"]
        self.assertEqual("mcp", restart["pending_request"]["source"])
        self.assertEqual(FAKE_PID, restart["pending_request"]["target_pid"])
        hold, = restart["holds"]
        self.assertEqual((FAKE_PID, "f" * 12, 3), (hold["pid"], hold["request_id"], hold["inputs_since_hold"]))
        self.assertIsInstance(hold["started_at"], float)

    def test_unknown_input_count_is_null_not_zero(self):
        restart_hold.begin(self.instance, FAKE_PID, "e" * 32)

        def broken(_epoch):
            raise OSError("input log unreadable")

        self.assertIsNone(menu.holds(self.instance, broken)[0]["inputs_since_hold"])
        self.assertIsNone(menu.holds(self.instance)[0]["inputs_since_hold"])

    def test_post_requires_explicit_confirmation(self):
        status, body = self.call("/ca/work/api/v1/runtime/restart", {"reason": "x"})
        self.assertEqual(422, status)
        self.assertIn("confirmed=true", body["message"])
        self.assertEqual([], self.queued)
        self.assertFalse((self.instance / RESTART_REQUEST_FILE_NAME).exists())

    def test_post_queues_on_this_instance_with_the_rest_source(self):
        status, body = self.call("/ca/work/api/v1/runtime/restart",
                                 {"confirmed": True, "reason": "deploy fix", "runtime": "claude", "resume": False,
                                  "client_pid": FAKE_PID, "session_id": "claude:one"})
        self.assertEqual(200, status)
        self.assertTrue(body["ok"] and body["queued"])
        self.assertEqual("rest", body["source"])
        self.assertEqual([{"source": "rest", "reason": "deploy fix", "runtime": "claude", "client_pid": FAKE_PID,
                           "resume": False}], self.queued)
        stored = json.loads((self.instance / RESTART_REQUEST_FILE_NAME).read_text(encoding="utf-8"))
        self.assertEqual(("rest", FAKE_PID, False, "claude"),
                         (stored["source"], stored["target_pid"], stored["resume"], stored["runtime"]))
        self.assertEqual(stored["id"], body["request"]["id"])
        self.assertEqual(stored["id"], body["restart"]["pending_request"]["id"])

    def test_a_signed_in_browser_is_recorded_as_web(self):
        status, body = self.call("/ca/work/api/v1/runtime/restart", {"confirmed": True},
                                 headers={"Cookie": "ciel_session=browser-session"})
        self.assertEqual(200, status)
        self.assertEqual("web", body["source"])
        self.assertEqual("web", json.loads((self.instance / RESTART_REQUEST_FILE_NAME).read_text())["source"])

    def test_a_client_of_another_instance_is_refused(self):
        status, body = self.call("/ca/work/api/v1/runtime/restart", {"confirmed": True, "client_pid": OTHER_PID})
        self.assertEqual(409, status)
        self.assertEqual("restart_not_queued", body["error"])
        self.assertIn("not an active client", body["message"])
        self.assertFalse((self.instance / RESTART_REQUEST_FILE_NAME).exists())

    def test_fail_closed_rules_and_field_validation(self):
        for payload, needle in (({"confirmed": True, "session_id": "claude:two"}, "current conversation"),
                                ({"confirmed": True, "extra": 1}, "unsupported restart fields"),
                                ({"confirmed": True, "runtime": "grok"}, "runtime must be"),
                                ({"confirmed": True, "resume": "yes"}, "resume must be boolean"),
                                ({"confirmed": True, "client_pid": -1}, "client_pid"),
                                ({"confirmed": True, "reason": "a\nb"}, "single-line")):
            status, body = self.call("/ca/work/api/v1/runtime/restart", payload)
            self.assertEqual(422, status, payload)
            self.assertIn(needle, body["message"], payload)
        self.assertEqual([], self.queued)

    def test_a_router_without_a_current_conversation_refuses(self):
        self.serve(self.build(lambda: {}, lambda **_k: {"ok": True}, identity=None))
        status, body = self.call("/ca/work/api/v1/runtime/restart", {"confirmed": True})
        self.assertEqual(422, status)
        self.assertIn("identity unavailable", body["message"])

    def test_without_restart_ports_the_view_says_unavailable(self):
        self.serve(self.build(None, None))
        self.assertEqual({"available": False}, self.call("/ca/work/api/v1/runtime")[1]["runtime"]["restart"])
        status, body = self.call("/ca/work/api/v1/runtime/restart", {"confirmed": True})
        self.assertEqual(422, status)
        self.assertIn("unavailable", body["message"])

    def test_help_names_the_restart_route(self):
        self.assertIn("/ca/work/api/v1/runtime/restart", WorkHttpController.help_payload()["v1"]["restart"])


class RouterWiringTests(unittest.TestCase):
    def test_build_work_http_binds_status_and_queue_to_this_instance(self):
        from ciel_runtime_support.work_http_runtime import build_work_http

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service, instance, _logs = make_service(root)
            routes = build_work_http(root / "state", instance, lambda: {}, lambda _cfg: ("p", {}), lambda _cfg: "a",
                                     HttpResponseAdapter.write_json, "v", "f", "i", SimpleNamespace(snapshot=lambda _s: {}),
                                     restart_service=lambda: service, inputs_since=lambda _epoch: 2)
            services = routes.api.services
            result = services.restart_queue(source="rest", reason="r", runtime="", client_pid=0, resume=True)
            self.assertTrue(result["ok"])
            self.assertEqual(FAKE_PID, json.loads((instance / RESTART_REQUEST_FILE_NAME).read_text())["target_pid"])
            restart_hold.begin(instance, FAKE_PID, "a" * 32)
            view = services.restart_status()
            self.assertEqual("rest", view["pending_request"]["source"])
            self.assertEqual(2, view["holds"][0]["inputs_since_hold"])

    def test_the_router_passes_its_restart_service(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        self.assertIn("restart_service=lambda: runtime_session_restart_service()", source)
        self.assertIn("inputs_since=lambda epoch: _runtime_input_scan_max_id() - "
                      "_runtime_input_scan_max_id_before_epoch(epoch)", source)


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service, self.instance, _ = make_service(Path(self.temp.name))

    def test_panel_lists_live_sessions_queued_request_and_holds(self):
        restart_hold.begin(self.instance, FAKE_PID, "b" * 32)
        self.service.queue(source="mcp", instance_dir=self.instance)
        rows, values = menu.panel_rows(self.service)
        self.assertEqual(len(rows), len(values))
        self.assertIn(f"restart:inst-a:{FAKE_PID}", values)
        self.assertTrue(any(row.startswith(f"Restart pid {FAKE_PID}") for row in rows))
        self.assertTrue(any("queued request" in row and "source mcp" in row for row in rows))
        self.assertTrue(any("holding inputs since" in row and "b" * 12 in row for row in rows))
        self.assertEqual(("Back", "back"), (rows[-1], values[-1]))
        self.assertIn("1 live session · 1 holding input", menu.summary(self.service))

    def test_cancel_queues_nothing(self):
        answers = iter(["no"])
        messages = menu.apply(self.service, f"restart:inst-a:{FAKE_PID}", lambda _label, _default: next(answers))
        self.assertEqual(["Restart cancelled; nothing was queued."], messages)
        self.assertFalse((self.instance / RESTART_REQUEST_FILE_NAME).exists())

    def test_confirmed_restart_is_queued_with_the_menu_source(self):
        asked = []
        answers = iter(["yes", "menu  reason", "no"])

        def prompt(label, default):
            asked.append((label, default))
            return next(answers)

        messages = menu.apply(self.service, f"restart:inst-a:{FAKE_PID}", prompt)
        self.assertIn("Restart queued", messages[0])
        self.assertEqual("no", asked[0][1])  # the confirmation defaults to no
        stored = json.loads((self.instance / RESTART_REQUEST_FILE_NAME).read_text(encoding="utf-8"))
        self.assertEqual(("menu", "menu reason", FAKE_PID, False),
                         (stored["source"], stored["reason"], stored["target_pid"], stored["resume"]))

    def test_a_session_that_ended_is_not_queued(self):
        messages = menu.apply(self.service, f"restart:inst-a:{OTHER_PID}", lambda *_a: "yes")
        self.assertIn("no longer a live session", messages[0])
        self.assertFalse((self.instance / RESTART_REQUEST_FILE_NAME).exists())

    def test_prelaunch_panel_keeps_the_script_rows_and_one_back(self):
        from ciel_runtime_support import prelaunch

        with mock.patch.object(menu, "menu_service", lambda: self.service), \
                mock.patch.object(prelaunch, "_workspace_state_dir", lambda: Path(self.temp.name) / "ws"):
            rows, values = prelaunch._restart_panel_rows()
        self.assertEqual(len(rows), len(values))
        self.assertIn("write", values)  # unattended restart script
        self.assertIn(f"restart:inst-a:{FAKE_PID}", values)
        self.assertEqual(1, values.count("back"))
        self.assertEqual("back", values[-1])

    def test_prelaunch_dispatches_restart_rows_to_the_safe_restart(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime_support" / "prelaunch.py").read_text(
            encoding="utf-8")
        self.assertIn('if value.startswith("restart:"):', source)
        self.assertIn("runtime_session_restart_menu.apply(", source)
        self.assertIn("runtime_session_restart_menu.menu_service()", source)


class WebConsoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = render_work_web_ui()

    def test_runtime_tab_shows_holds_and_offers_a_confirmed_restart(self):
        self.assertIn('id="tab-runtime"', self.html)
        self.assertIn('id="panel-runtime"', self.html)
        self.assertIn("if(name==='runtime'){renderRuntime(panel,data);return}", self.html)
        self.assertIn("inputs since hold '+(typeof hold.inputs_since_hold==='number'", self.html)
        self.assertIn(":'unknown')", self.html)
        self.assertIn("request('/runtime/restart',{method:'POST'", self.html)
        self.assertIn("confirmDanger('Restart CLI session pid '", self.html)
        self.assertIn("body={confirmed:true,reason:reason.value.trim(),runtime:runtime.value,resume:resume.checked,"
                      "client_pid:pid}", self.html)
        for control in ("restart-reason", "restart-runtime", "restart-client", "restart-resume"):
            self.assertIn(control, self.html)


class CtlParityTests(unittest.TestCase):
    def test_ctl_restart_session_is_the_same_service_command(self):
        import ciel_runtime

        parser = ciel_runtime.build_parser()
        self.assertEqual("ciel-runtimectl", parser.prog)
        args = parser.parse_args(["restart-session", "--reason", "deploy", "--runtime", "claude", "--pid",
                                  str(FAKE_PID), "--workspace", "W:/repo", "--no-resume"])
        self.assertIs(RuntimeSessionRestartService.command, args.func.__func__)
        self.assertEqual(("deploy", "claude", FAKE_PID, "W:/repo", True),
                         (args.reason, args.runtime, args.pid, args.workspace, args.no_resume))

    def test_ctl_command_queues_the_selected_client(self):
        with tempfile.TemporaryDirectory() as directory:
            service, instance, _ = make_service(Path(directory))
            printed = []
            args = SimpleNamespace(reason="r", runtime="claude", pid=FAKE_PID, workspace="", no_resume=True)
            session_restart_command(args, SessionRestartCliPorts(service.instances, service.queue,
                                                                 lambda _w: "digest", printed.append))
            stored = json.loads((instance / RESTART_REQUEST_FILE_NAME).read_text(encoding="utf-8"))
            self.assertEqual(("cli", FAKE_PID, False, "claude"),
                             (stored["source"], stored["target_pid"], stored["resume"], stored["runtime"]))
            self.assertIn("queued request_id=" + stored["id"], printed[-1])


if __name__ == "__main__":
    unittest.main()
