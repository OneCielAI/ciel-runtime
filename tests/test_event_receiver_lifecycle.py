import json
import socket
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from ciel_runtime_support.event_receiver_connection import ReceiverWorker, SseConnection
from ciel_runtime_support.event_receiver_diagnostics import receiver_failure
from ciel_runtime_support.external_event_receiver import EventReceiverSecretVault, ExternalEventReceiverService


class RetrySignal:
    def __init__(self, service, limit):
        self.service, self.limit, self.delays = service, limit, []

    def is_set(self):
        return False

    def wait(self, delay):
        self.delays.append(delay)
        if len(self.delays) >= self.limit:
            self.service._stop.set()
        return False


class Response:
    headers = {"content-type": "text/event-stream"}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        return iter(())


class ReceiverLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = {"external_event_receivers": {"workspace": {"default": {
            "enabled": True, "transport": "sse", "url": "https://example.invalid/events"}}}}
        self.service = ExternalEventReceiverService(
            load_config=lambda: self.config, save_config=lambda value: self.config.update(value),
            write_json=mock.Mock(), submit_event=mock.Mock(return_value={}),
            vault=EventReceiverSecretVault(Path(self.directory.name) / "vault.json"),
            workspace_key="workspace", log=mock.Mock(), urlopen=mock.Mock(return_value=Response()))

    def run_attempts(self, count):
        worker = ReceiverWorker()
        signal = RetrySignal(self.service, count)
        worker.reconnect = signal
        self.service._run_sse("default", worker)
        return signal.delays

    def test_empty_eof_has_capped_backoff_not_header_recovery(self):
        self.assertEqual([1, 2, 4, 8, 16, 30, 30], self.run_attempts(7))
        status = self.service._status["default"]
        self.assertEqual("", status["error"])
        self.assertEqual("eof", status["last_failure"]["category"])
        self.assertEqual(7, status["attempt_count"])
        self.assertNotIn("last_recovery_at", status)

    def test_config_cursor_cleanup_and_log_faults_do_not_kill_reader(self):
        for operation in ("config", "cursor", "cleanup", "log"):
            with self.subTest(operation=operation):
                self.service._stop.clear()
                self.service.log = mock.Mock(side_effect=RuntimeError("secret"))
                if operation == "config":
                    patcher = mock.patch.object(ExternalEventReceiverService, "receiver_configs", side_effect=RuntimeError("secret"))
                elif operation == "cursor":
                    patcher = mock.patch.object(ExternalEventReceiverService, "_read_sse_cursor", side_effect=RuntimeError("secret"))
                elif operation == "cleanup":
                    original = SseConnection.close
                    def failing_close(connection):
                        original(connection)
                        raise RuntimeError("secret")
                    patcher = mock.patch.object(SseConnection, "close", failing_close)
                else:
                    patcher = mock.patch.object(self.service, "urlopen", side_effect=RuntimeError("secret"))
                with patcher:
                    self.assertEqual([1, 2, 4], self.run_attempts(3))
                self.assertNotIn("secret", json.dumps(self.service._status))

    def test_short_heartbeat_does_not_claim_recovery(self):
        captured = []
        service = self.service
        class ActiveResponse(Response):
            def __iter__(self):
                yield b": heartbeat" + bytes([10])
                captured.append(dict(service._status["default"]))
                service._stop.set()
        self.service.urlopen.side_effect = [OSError(5, "secret"), ActiveResponse()]
        self.assertEqual([1], self.run_attempts(10))
        status = captured[0]
        self.assertEqual("connected", status["state"])
        self.assertIsNone(status["diagnostic"])
        self.assertIsNone(status["retry_seconds"])
        self.assertIsNone(status["next_retry_at"])
        self.assertEqual(1, status["consecutive_failures"])
        self.assertNotIn("last_recovery_at", status)
        self.assertEqual(60, status["effective_idle_timeout_seconds"])

    def test_save_preserves_omitted_timeout_and_clears_explicit_empty(self):
        body = {"enabled": True, "transport": "sse", "url": "https://example.invalid/events"}
        self.service.save_receiver("default", {**body, "idle_timeout_seconds": 12})
        self.service.save_receiver("default", body)
        self.assertEqual(12, self.service.receiver_configs()["default"]["idle_timeout_seconds"])
        for value in (None, ""):
            self.service.save_receiver("default", {**body, "idle_timeout_seconds": 12})
            self.service.save_receiver("default", {**body, "idle_timeout_seconds": value})
            self.assertNotIn("idle_timeout_seconds", self.service.receiver_configs()["default"])
        self.assertEqual({}, self.service._threads)

    def test_reconnect_missing_worker_requires_running_enabled_sse(self):
        with mock.patch.object(ExternalEventReceiverService, "_start_worker") as start:
            self.assertFalse(self.service.reconnect("default"))
            self.service._running = True
            self.assertTrue(self.service.reconnect("default"))
            start.assert_called_once_with("default")
            start.reset_mock()
            self.service._stop.set()
            self.assertFalse(self.service.reconnect("default"))
            self.service._stop.clear()
            self.config["external_event_receivers"]["workspace"]["default"]["enabled"] = False
            self.assertFalse(self.service.reconnect("default"))
            start.assert_not_called()

    def test_reconcile_interrupts_active_and_permanently_stops_disabled(self):
        self.service._running = True
        worker = ReceiverWorker()
        thread = mock.Mock()
        thread.is_alive.return_value = True
        self.service._threads["default"] = thread
        self.service._workers["default"] = worker
        self.assertTrue(self.service.reconcile("default"))
        self.assertTrue(worker.reconnect.is_set())
        self.assertFalse(worker.stop.is_set())
        self.config["external_event_receivers"]["workspace"]["default"]["transport"] = "webhook"
        self.assertFalse(self.service.reconcile("default"))
        self.assertTrue(worker.stop.is_set())

    def test_reconnect_route_is_dispatched_before_save(self):
        with mock.patch.object(ExternalEventReceiverService, "reconnect", return_value=True) as reconnect:
            with mock.patch.object(ExternalEventReceiverService, "save_receiver") as save:
                self.assertTrue(self.service.handle_config_post(None, "/ca/events/receivers/default/reconnect", {}))
                reconnect.assert_called_once_with("default")
                save.assert_not_called()
                self.service.write_json.assert_called_once_with(None, {"ok": True, "reconnecting": True}, 200)

    def test_router_save_reconciles_only_after_config_and_vault_save(self):
        observed = []
        def reconcile(service, receiver_id):
            observed.append((service.receiver_configs()[receiver_id]["url"], service.vault.load(receiver_id)["authorization"]))
            return False
        with mock.patch.object(ExternalEventReceiverService, "reconcile", reconcile):
            self.service.handle_config_post(None, "/ca/events/receivers/default", {
                "enabled": True, "transport": "sse", "url": "https://example.invalid/new", "authorization": "private"})
        self.assertEqual([("https://example.invalid/new", "private")], observed)
        self.assertEqual({}, self.service._threads)

    def test_public_status_reports_worker_liveness(self):
        config = self.service.receiver_configs()["default"]
        self.assertFalse(self.service.public_receiver("default", config)["status"]["worker_alive"])
        self.service._threads["default"] = mock.Mock(is_alive=lambda: True)
        self.assertTrue(self.service.public_receiver("default", config)["status"]["worker_alive"])

    def test_safe_diagnostics_retains_only_typed_bounded_fields(self):
        error = urllib.error.URLError(socket.gaierror(11001, "https://private/token?payload=secret"))
        result = receiver_failure(error, "connect")
        self.assertEqual("dns", result["reason"]["category"])
        self.assertEqual(11001, result["reason"]["errno"])
        self.assertEqual("connect", result["phase"])
        self.assertNotIn("private", json.dumps(result))
        http = urllib.error.HTTPError("https://private", 401, "Bearer secret", {"Authorization": "secret"}, None)
        self.assertEqual(401, receiver_failure(http, "headers")["http_status"])
        for _ in range(10):
            error = urllib.error.URLError(error)
        nested = receiver_failure(error, "connect")
        self.assertLess(len(json.dumps(nested)), 500)
        self.assertNotIn("secret", json.dumps(nested))

    def test_cursor_write_failure_replays_old_cursor_after_admission(self):
        raw = '{"specversion":"1.0","id":"new","source":"/x","type":"demo"}'
        service = self.service
        requests = []
        class EventResponse(Response):
            def __iter__(self):
                return iter(("id: new" + chr(10) + "data: " + raw + chr(10) * 2).encode().splitlines(keepends=True))
        def open_stream(request, timeout):
            requests.append(request.get_header("Last-event-id"))
            if len(requests) == 2:
                service._stop.set()
            return EventResponse()
        service.urlopen = open_stream
        with mock.patch.object(ExternalEventReceiverService, "_read_sse_cursor", return_value="old"):
            with mock.patch.object(ExternalEventReceiverService, "_write_sse_cursor", side_effect=OSError(5, "private")):
                self.run_attempts(10)
        self.assertEqual(["old", "old"], requests)
        self.assertEqual(1, service.submit_event.call_count)

    def test_response_exit_failure_retries_and_retains_cleanup_phase(self):
        class BadExit(Response):
            def __iter__(self):
                self_service._stop.set()
                return iter(())
            def __exit__(self, *args):
                self_service._stop.clear()
                raise RuntimeError("private")
        self_service = self.service
        self.service.urlopen.return_value = BadExit()
        self.assertEqual([1, 2], self.run_attempts(2))
        self.assertEqual("cleanup", self.service._status["default"]["last_failure"]["phase"])

    def test_short_heartbeat_eof_keeps_exponential_backoff(self):
        class HeartbeatResponse(Response):
            def __iter__(self):
                return iter([b": heartbeat" + bytes([10]), bytes([10])])
        self.service.urlopen.return_value = HeartbeatResponse()
        self.assertEqual([1, 2, 4, 8], self.run_attempts(4))
        self.assertNotIn("last_recovery_at", self.service._status["default"])

    def test_committed_cursor_is_not_forwarded_to_a_different_endpoint(self):
        self.service.cursor_path = Path(self.directory.name) / "cursors.json"
        self.service._write_sse_cursor("default", "old-private-cursor")
        self.assertEqual("old-private-cursor", self.service._read_sse_cursor("default"))
        self.config["external_event_receivers"]["workspace"]["default"]["url"] = "https://different.invalid/stream"
        self.assertEqual("", self.service._read_sse_cursor("default"))
        self.service._write_sse_cursor("default", "new-cursor")
        self.assertEqual("new-cursor", self.service._read_sse_cursor("default"))

    def test_numeric_exception_attributes_cannot_break_diagnostics(self):
        class HostileError(Exception):
            @property
            def errno(self):
                raise RuntimeError("private")
        self.assertEqual("HostileError", receiver_failure(HostileError(), "read")["class"])
