"""Real loopback control HTTP against a real receiver and runtime input gateway."""

import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

from ciel_runtime_support.external_event_control import ExternalEventControl
from ciel_runtime_support.external_event_menu import panel_rows, update_config
from ciel_runtime_support.external_event_receiver import EventReceiverSecretVault, ExternalEventReceiverService
from ciel_runtime_support.router_access import RouterAccessPolicy
from ciel_runtime_support.runtime_input_gateway import RuntimeInputGateway


class ReceiverControlHttpTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.workspace = str(self.root / "workspace")
        self.requests, self.messages = [], []
        self.lock, self.halt = threading.Lock(), threading.Event()
        self.force_external = False
        fixture = self

        class SseHandler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(handler):
                fixture.requests.append(handler.headers.get("Last-Event-ID", ""))
                ident = "fixture-" + str(len(fixture.requests))
                handler.send_response(200)
                handler.send_header("Content-Type", "text/event-stream")
                handler.end_headers()
                event = {"specversion": "1.0", "id": ident, "type": "fixture.control", "source": "/fixture"}
                try:
                    handler.wfile.write(("id: " + ident + chr(10) + "data: " + json.dumps(event) + chr(10) * 2).encode())
                    handler.wfile.flush()
                    while not fixture.halt.wait(0.05):
                        handler.wfile.write((": heartbeat" + chr(10) * 2).encode())
                        handler.wfile.flush()
                except OSError:
                    pass

        self.sse = ThreadingHTTPServer(("127.0.0.1", 0), SseHandler)
        self.sse.daemon_threads = True
        self.url = "http://127.0.0.1:" + str(self.sse.server_port) + "/events"
        self.config = {"router_debug_external_access": True,
                       "router_debug_external_access_confirmed": True,
                       "external_event_receivers": {"fixture-workspace": {"default": {
                           "enabled": True, "transport": "sse", "url": self.url}}}}

        def append(payload):
            with self.lock:
                row = {**payload, "id": len(self.messages) + 1}
                with (self.root / "inputs.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(row) + chr(10))
                    stream.flush()
                    os.fsync(stream.fileno())
                self.messages.append(row)
                return row

        gateway = RuntimeInputGateway(append)
        self.receiver = ExternalEventReceiverService(
            load_config=lambda: self.config, save_config=lambda value: self.config.update(value),
            write_json=self.write_json, submit_event=gateway.submit_external_event,
            vault=EventReceiverSecretVault(self.root / "vault.json"),
            workspace_key="fixture-workspace", log=lambda *_args: None,
            cursor_path=self.root / "cursors.json")
        self.policy = RouterAccessPolicy(
            environ={}, parse_bool=lambda value, default: default if value is None else bool(value),
            parse_env_bool=lambda value, default: default, load_config=lambda: self.config)

        class ControlHandler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def allowed(handler):
                # Exercise production external-address policy without binding LAN.
                request = SimpleNamespace(
                    client_address=("192.0.2.1", 1) if fixture.force_external else handler.client_address,
                    headers=handler.headers, path=handler.path)
                if fixture.policy.request_allowed(request, fixture.config,
                                                  lambda: "fixture-admin-token", lambda: ""):
                    return True
                fixture.write_json(handler, {"ok": False}, 403)
                return False

            def do_GET(handler):
                if not handler.allowed():
                    return
                if handler.path == "/health":
                    fixture.write_json(handler, {"ok": True, "workspace": fixture.workspace})
                elif not fixture.receiver.handle_get(handler, handler.path):
                    fixture.write_json(handler, {"ok": False}, 404)

            def do_POST(handler):
                if not handler.allowed():
                    return
                body = json.loads(handler.rfile.read(int(handler.headers.get("Content-Length", "0"))))
                if not fixture.receiver.handle_config_post(handler, handler.path, body):
                    fixture.write_json(handler, {"ok": False}, 404)

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), ControlHandler)
        self.http.daemon_threads = True
        self.base = "http://127.0.0.1:" + str(self.http.server_port)
        self.control = ExternalEventControl(self.base, self.workspace)
        self.threads = [threading.Thread(target=server.serve_forever,
                                        kwargs={"poll_interval": 0.02}, daemon=True)
                        for server in (self.sse, self.http)]
        for thread in self.threads:
            thread.start()
        self.addCleanup(self.cleanup)

    @staticmethod
    def write_json(handler, payload, status=200):
        raw = json.dumps(payload).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(raw)))
        handler.end_headers()
        handler.wfile.write(raw)

    def cleanup(self):
        self.halt.set()
        workers = list(self.receiver._threads.values())
        self.receiver.stop()
        for worker in workers:
            self.assertFalse(worker.is_alive(), "fixture receiver thread did not stop")
        for server in (self.sse, self.http):
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(2)
        self.directory.cleanup()

    def wait(self, condition, seconds=5):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if condition():
                return True
            time.sleep(0.01)
        return False

    def test_http_status_reconnect_and_menu_use_single_router_owned_reader(self):
        self.receiver.start()
        self.assertTrue(self.wait(lambda: len(self.messages) == 1))
        self.assertTrue(self.wait(lambda: self.receiver._read_sse_cursor("default") == "fixture-1"))
        worker = self.receiver._threads["default"]
        live = self.control.call("status")
        self.assertTrue(live["available"])
        self.assertTrue(live["status"]["worker_alive"])
        self.assertEqual("connected", live["status"]["state"])
        rows, values = panel_rows(self.receiver, self.base, control=self.control.call)
        self.assertIn("reconnect_receiver", values)
        self.assertIn("connected", next(row for row in rows if row.startswith("Live receiver")))
        result = update_config(self.receiver, "reconnect_receiver", None, control=self.control.call)
        self.assertIn("requested", result[0])
        self.assertTrue(self.wait(lambda: len(self.messages) == 2))
        self.assertIs(worker, self.receiver._threads["default"])
        self.assertEqual(1, len(self.receiver._threads))
        self.assertEqual(["", "fixture-1"], self.requests)
        self.assertEqual(["fixture-1", "fixture-2"], [row["meta"]["event_id"] for row in self.messages])
        durable = [json.loads(line) for line in (self.root / "inputs.jsonl").read_text().splitlines()]
        self.assertEqual(2, len(durable))
        self.assertTrue(all(row["visibility"] == "private_runtime" for row in durable))
        print("real_http_control: connected -> reconnect -> cursor_resume -> gateway_queue=2; single_worker=true")

    def test_http_save_disables_reenables_and_distinguishes_rejected_reconnect(self):
        self.receiver.start()
        self.assertTrue(self.wait(lambda: len(self.messages) == 1))
        result = self.control.call("save", {"enabled": False, "transport": "sse", "url": self.url})
        self.assertTrue(result["accepted"])
        self.assertTrue(self.wait(lambda: not self.receiver._threads))
        rejected = self.control.call("reconnect")
        self.assertEqual({"available": True, "accepted": False, "reason": "receiver_unavailable"}, rejected)
        result = self.control.call("save", {"enabled": True, "transport": "sse", "url": self.url,
                                           "idle_timeout_seconds": 10})
        self.assertTrue(result["accepted"])
        self.assertTrue(self.wait(lambda: len(self.messages) == 2))
        self.assertEqual(10, self.receiver.receiver_configs()["default"]["idle_timeout_seconds"])
        self.assertEqual(1, len(self.receiver._threads))
        print("real_http_config: disabled -> reconnect_rejected409 -> enabled -> event_received; single_worker=true")

    def test_workspace_gate_and_external_auth_reject_before_mutation(self):
        mismatch = ExternalEventControl(self.base, str(self.root / "other")).call("reconnect")
        self.assertEqual("workspace_mismatch", mismatch["reason"])
        self.assertEqual({}, self.receiver._threads)
        self.force_external = True
        unauthorized = self.control.call("reconnect")
        self.assertEqual("router_rejected", unauthorized["reason"])
        self.assertEqual(403, unauthorized["http_status"])
        authorized = ExternalEventControl(self.base, self.workspace,
                                         {"Authorization": "Bearer fixture-admin-token"}).call("status")
        self.assertTrue(authorized["available"])
        self.assertEqual({}, self.receiver._threads)
        print("real_http_policy: workspace_mismatch rejected; external_identity missing_token=403; valid_token=status_ok")


if __name__ == "__main__":
    unittest.main()
