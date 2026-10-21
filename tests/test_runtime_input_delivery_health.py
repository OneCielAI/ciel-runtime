import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from ciel_runtime_support.chat_http_controller import ChatHttpController, ChatHttpReadServices, ChatHttpWriteServices
from ciel_runtime_support.runtime_input_status import RuntimeInputStatusRepository
from ciel_runtime_support.runtime_input_delivery_health import runtime_input_delivery_health, delivery_health_text
from ciel_runtime_support.statusline_script import STATUSLINE_SCRIPT
from ciel_runtime_support.tui_observation import TuiObservationBus, TuiObservationHttpAdapter, TuiObservationHttpPorts


class DeliveryHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "runtime-input-status.jsonl"
        self.inputs = self.root / "runtime-inputs.jsonl"
        self.status = RuntimeInputStatusRepository(self.path, Mock(), Mock(), threading.RLock())

    def write_rows(self, path, rows):
        path.write_bytes(b"".join(json.dumps(row).encode() + bytes([10]) for row in rows))

    def fixture(self, corrupt=False):
        self.write_rows(self.path, [
            {"request_id": 1, "status": "queued", "updated_at_epoch": 100},
            {"request_id": 1, "status": "queued", "updated_at_epoch": 190},
            {"request_id": 2, "status": "submitted", "updated_at_epoch": 110},
            {"request_id": 3, "status": "queued", "updated_at_epoch": 120},
        ])
        self.write_rows(self.inputs, [
            {"id": 1, "created_at_epoch": 90, "channel": "external:default", "message": "SECRET_PAYLOAD"},
            {"id": 2, "created_at_epoch": 100, "channel": "external:default"},
            {"id": 3, "created_at_epoch": 110, "channel": "web"},
        ])
        if corrupt:
            with self.path.open("ab") as stream:
                stream.write(bytes([0]) + b"secret corrupt data" + bytes([10]))

    def test_original_age_receiver_scope_and_payload_free(self):
        self.fixture()
        h = runtime_input_delivery_health(self.status, receiver_id="default", now=200)
        self.assertEqual((1, 110, "complete"), (h["known_queued_count"], h["oldest_queued_age_seconds"], h["queued_coverage"]))
        self.assertFalse(h["admission_blocked"])
        self.assertNotIn("SECRET", json.dumps(h))
        self.assertEqual(2, runtime_input_delivery_health(self.status, now=200)["known_queued_count"])

    def test_incomplete_history_blocks_admission_without_claiming_launcher_hold(self):
        self.fixture(True)
        before = self.path.read_bytes(), self.inputs.read_bytes()
        h = runtime_input_delivery_health(self.status, receiver_id="default", now=200)
        self.assertTrue(h["admission_blocked"])
        self.assertFalse(h["launcher_hold_observed"])
        self.assertEqual((5, "partial", 1), (h["line_number"], h["queued_coverage"], h["known_queued_count"]))
        self.assertIn("launcher hold unobserved", delivery_health_text(h))
        self.assertNotIn("secret", json.dumps(h))
        self.assertEqual(before, (self.path.read_bytes(), self.inputs.read_bytes()))

    def test_cached_scans_still_advance_age(self):
        self.fixture()
        with patch.object(type(self.status), "history_health", wraps=type(self.status).history_health, autospec=True) as health:
            health.side_effect = lambda instance: {"complete": True, "coverage": "complete", "diagnostics": []}
            one = runtime_input_delivery_health(self.status, now=200)
            two = runtime_input_delivery_health(self.status, now=210)
        self.assertEqual(1, health.call_count)
        self.assertEqual(10, two["oldest_queued_age_seconds"] - one["oldest_queued_age_seconds"])

    def test_public_chat_is_not_private_queue(self):
        self.write_rows(self.root / "chat-messages.jsonl", [{"id": 100, "message": "SECRET"}])
        h = runtime_input_delivery_health(self.status, now=200)
        self.assertEqual(0, h["known_queued_count"])

    def test_request_alias_and_tui_status_are_additive(self):
        self.fixture(True)
        replies = []
        controller = ChatHttpController("http://local", ChatHttpReadServices(
            lambda *a: [], lambda *a: [], threading.Condition(), lambda v, limit: v,
            self.root, self.status.get, self.status.list_latest,
        ), ChatHttpWriteServices(lambda handler, body, code=200: replies.append((body, code)), Mock(), Mock()))
        handler = Mock(path="/ca/channel/requests/1")
        self.assertTrue(controller.get(handler, handler.path))
        self.assertEqual("queued", replies[-1][0]["request"]["status"])
        self.assertEqual("admission_blocked", replies[-1][0]["delivery"]["state"])
        adapter = TuiObservationHttpAdapter(TuiObservationHttpPorts(
            TuiObservationBus(), lambda handler, body: replies.append((body, 200)), Mock(), Mock(),
            lambda: runtime_input_delivery_health(self.status),
        ))
        adapter.handle_get(handler, "/ca/tui/status", {})
        self.assertTrue(replies[-1][0]["ok"])
        self.assertTrue(replies[-1][0]["delivery"]["admission_blocked"])

    def test_generated_statusline_runs_outside_package(self):
        self.fixture(True)
        script = self.root / "status.py"
        script.write_text(STATUSLINE_SCRIPT, encoding="utf8")
        env = {key: value for key, value in os.environ.items() if not key.startswith("CIEL_RUNTIME_")}
        env.update(CIEL_RUNTIME_CONFIG_DIR=str(self.root), CIEL_RUNTIME_STATE_DIR=str(self.root),
                   CIEL_RUNTIME_WORKSPACE_STATE_DIR=str(self.root), CIEL_RUNTIME_STATUSLINE_FORCE="1",
                   CIEL_RUNTIME_STATUSLINE_ANSI="0", PYTHONIOENCODING="utf-8")
        result = subprocess.run([sys.executable, str(script)], input="{}", text=True, capture_output=True,
                                cwd=self.root, env=env, check=True)
        self.assertIn("session delivery admission_blocked", result.stdout)
        self.assertIn("wake_history_incomplete line 5", result.stdout)
        self.assertNotIn("SECRET", result.stdout)

    def test_connected_receiver_and_menu_show_separate_delivery_blocker(self):
        from ciel_runtime_support.external_event_receiver import ExternalEventReceiverService, EventReceiverSecretVault
        from ciel_runtime_support.external_event_menu import panel_rows
        from ciel_runtime_support.runtime_input_gateway import RuntimeInputGateway
        self.fixture(True)
        config = {"enabled": True, "transport": "sse", "url": "https://example.invalid/events"}
        service = ExternalEventReceiverService(
            load_config=lambda: {"external_event_receivers": {"workspace": {"default": config}}},
            save_config=Mock(), write_json=Mock(),
            submit_event=RuntimeInputGateway(Mock(), status=self.status).submit_external_event,
            vault=EventReceiverSecretVault(self.root / "vault.json"), workspace_key="workspace", log=Mock(),
        )
        service._status["default"] = {"state": "connected"}
        status = service.public_receiver("default", config)["status"]
        self.assertEqual("connected", status["state"])
        self.assertEqual("admission_blocked", status["delivery"]["state"])
        rows, values = panel_rows(service, "http://localhost", control=lambda action: {"available": True, "status": status})
        self.assertEqual(len(rows), len(values))
        self.assertTrue(any("Session delivery" in row and "admission_blocked" in row for row in rows))
        self.assertFalse(self.path.with_suffix(".json").exists())

    def test_http_request_status_runs_over_loopback_without_mutation(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import urllib.request
        self.fixture(True)
        before = self.path.read_bytes(), self.inputs.read_bytes()
        def write_json(handler, body, code=200):
            raw = json.dumps(body).encode()
            handler.send_response(code)
            handler.send_header("Content-Length", str(len(raw)))
            handler.end_headers()
            handler.wfile.write(raw)
        controller = ChatHttpController("http://local", ChatHttpReadServices(
            lambda *a: [], lambda *a: [], threading.Condition(), lambda v, limit: v,
            self.root, self.status.get, self.status.list_latest,
        ), ChatHttpWriteServices(write_json, Mock(), Mock()))
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                controller.get(self, self.path)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/ca/channel/requests/1", timeout=3) as response:
                body = json.load(response)
            self.assertTrue(body["ok"])
            self.assertEqual("queued", body["request"]["status"])
            self.assertEqual("admission_blocked", body["delivery"]["state"])
            self.assertEqual(before, (self.path.read_bytes(), self.inputs.read_bytes()))
        finally:
            server.shutdown()
            server.server_close()
            worker.join(3)
