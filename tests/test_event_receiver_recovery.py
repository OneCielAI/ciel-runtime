import json
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from ciel_runtime_support.external_event_receiver import (
    EventReceiverSecretVault,
    ExternalEventReceiverService,
)
from ciel_runtime_support.event_receiver_connection import ReceiverWorker, SseConnection


class Response:
    headers = {"content-type": "text/event-stream"}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def __iter__(self):
        return iter(())


class ImmediateEvent:
    def __init__(self, service, limit):
        self.service, self.limit, self.waits = service, limit, []

    def is_set(self):
        return False

    def clear(self):
        pass

    def wait(self, delay):
        self.waits.append(delay)
        if len(self.waits) >= self.limit:
            self.service._stop.set()
        return False


class EventReceiverRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = {
            "external_event_receivers": {
                "workspace": {
                    "default": {
                        "enabled": True,
                        "transport": "sse",
                        "url": "http://127.0.0.1:1/events",
                    }
                }
            }
        }
        self.logs, self.admitted = [], []
        self.service = ExternalEventReceiverService(
            load_config=lambda: self.config,
            save_config=lambda v: self.config.update(v),
            write_json=lambda *_a: None,
            submit_event=lambda raw, **_k: self.admitted.append(json.loads(raw)["id"])
            or {"id": len(self.admitted)},
            vault=EventReceiverSecretVault(self.root / "vault.json"),
            workspace_key="workspace",
            cursor_path=self.root / "cursor.json",
            log=lambda _l, t: self.logs.append(t),
        )

    def bounded(self, limit=3):
        worker = ReceiverWorker()
        worker.reconnect = ImmediateEvent(self.service, limit)
        self.service._run_sse("default", worker)
        return worker.reconnect.waits

    def test_eof_backoff_grows_despite_successful_headers(self):
        self.service.urlopen = lambda *_a, **_k: Response()
        self.assertEqual([1, 2, 4], self.bounded())
        self.assertTrue(any("EOFError" in line for line in self.logs))

    def test_config_read_failure_recovers(self):
        loads = []

        def load():
            loads.append(1)
            if len(loads) == 1:
                raise OSError("secret-config")
            return self.config

        def opened(*_a, **_k):
            self.service._stop.set()
            return Response()

        self.service.load_config, self.service.urlopen = load, opened
        self.assertEqual([1], self.bounded())
        self.assertEqual(2, len(loads))
        self.assertNotIn("secret-config", repr(self.logs))

    def test_logging_failure_does_not_stop_retries(self):
        self.service.urlopen = lambda *_a, **_k: Response()
        self.service.log = mock.Mock(side_effect=RuntimeError("secret-log"))
        self.assertEqual([1, 2, 4], self.bounded())
        self.assertEqual(3, self.service.log.call_count)

    def test_cleanup_failure_does_not_stop_retries(self):
        self.service.urlopen = lambda *_a, **_k: Response()
        original = SseConnection.close

        def close(conn):
            original(conn)
            raise OSError("secret-cleanup")

        with mock.patch.object(SseConnection, "close", close):
            self.assertEqual([1, 2, 4], self.bounded())
        self.assertNotIn("secret-cleanup", repr(self.logs))

    def test_timeout_omission_preserves_and_null_clears(self):
        base = {"enabled": True, "transport": "sse", "url": "http://127.0.0.1:1/events"}
        self.service.save_receiver("default", {**base, "idle_timeout_seconds": 20})
        self.service.save_receiver("default", base)
        self.assertEqual(
            20, self.service.receiver_configs()["default"]["idle_timeout_seconds"]
        )
        self.service.save_receiver("default", {**base, "idle_timeout_seconds": None})
        self.assertNotIn(
            "idle_timeout_seconds", self.service.receiver_configs()["default"]
        )

    def test_stopped_or_disabled_reconnect_does_not_start_worker(self):
        self.assertFalse(self.service.reconnect("default"))
        self.service._running = True
        self.config["external_event_receivers"]["workspace"]["default"]["enabled"] = (
            False
        )
        self.assertFalse(self.service.reconnect("default"))
        self.assertEqual({}, self.service._threads)

    def test_missing_enabled_worker_can_reconnect_without_duplicate(self):
        entered, release = threading.Event(), threading.Event()

        def opened(*_a, **_k):
            entered.set()
            release.wait(4)
            return Response()

        self.service.urlopen, self.service._running = opened, True
        thread = None
        try:
            self.assertTrue(self.service.reconnect("default"))
            self.assertTrue(entered.wait(2))
            thread = self.service._threads["default"]
            self.assertTrue(self.service.reconnect("default"))
            self.assertIs(thread, self.service._threads["default"])
        finally:
            release.set()
            self.service.stop()
        self.assertFalse(thread.is_alive())

    def test_nested_dns_failure_diagnostics_exclude_secret_text(self):
        def opened(*_a, **_k):
            raise urllib.error.URLError(
                socket.gaierror(-2, "secret.endpoint/?token=sentinel")
            )

        self.service.urlopen = opened
        self.bounded(1)
        status = self.service.public_receiver(
            "default", self.service.receiver_configs()["default"]
        )["status"]
        encoded = json.dumps(status) + repr(self.logs)
        self.assertNotIn("secret.endpoint", encoded)
        self.assertNotIn("sentinel", encoded)
        self.assertIn("dns", encoded)
        self.assertFalse(status["worker_alive"])

    def test_real_socket_outage_restoration_twice_resumes_cursor_on_single_worker(self):
        lock, release = threading.Lock(), threading.Event()
        requests, active, maximum, event_id = [], [0], [0], ["event-0"]
        servers, threads = [], []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def do_GET(handler):
                with lock:
                    requests.append(handler.headers.get("Last-Event-ID", ""))
                    active[0] += 1
                    maximum[0] = max(maximum[0], active[0])
                    ident = event_id[0]
                try:
                    handler.send_response(200)
                    handler.send_header("Content-Type", "text/event-stream")
                    handler.send_header("Connection", "close")
                    handler.end_headers()
                    body = json.dumps(
                        {
                            "specversion": "1.0",
                            "id": ident,
                            "source": "/fixture",
                            "type": "demo",
                        }
                    )
                    handler.wfile.write(
                        (
                            "id: " + ident + chr(10) + "data: " + body + chr(10) * 2
                        ).encode()
                    )
                    handler.wfile.flush()
                    release.wait(10)
                finally:
                    with lock:
                        active[0] -= 1

        class Server(ThreadingHTTPServer):
            allow_reuse_address = True
            daemon_threads = True

        def start(port):
            server = Server(("127.0.0.1", port), Handler)
            thread = threading.Thread(
                target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
            )
            servers.append(server)
            threads.append(thread)
            thread.start()
            return server

        def wait(predicate, timeout=9):
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                if predicate():
                    return True
                time.sleep(0.02)
            return False

        server = start(0)
        port, worker = server.server_port, None
        self.config["external_event_receivers"]["workspace"]["default"]["url"] = (
            f"http://127.0.0.1:{port}/events"
        )
        try:
            self.service.start()
            worker = self.service._threads["default"]
            self.assertTrue(wait(lambda: self.admitted == ["event-0"]))
            self.assertTrue(
                wait(lambda: self.service._read_sse_cursor("default") == "event-0")
            )
            for cycle in (1, 2):
                start_failure = len(self.logs)
                server.shutdown()
                server.server_close()
                release.set()
                self.assertTrue(wait(lambda: active[0] == 0))
                self.assertTrue(
                    wait(
                        lambda: any(
                            "URLError" in line for line in self.logs[start_failure:]
                        )
                    ),
                    self.logs,
                )
                release.clear()
                event_id[0] = f"event-{cycle}"
                server = start(port)
                self.assertTrue(
                    wait(lambda: len(self.admitted) == cycle + 1), self.logs
                )
                self.assertTrue(
                    wait(
                        lambda: self.service._read_sse_cursor("default")
                        == f"event-{cycle}"
                    )
                )
                self.assertIs(worker, self.service._threads["default"])
            self.assertEqual(["event-0", "event-1", "event-2"], self.admitted)
            self.assertEqual(["", "event-0", "event-1"], requests)
            self.assertEqual(1, maximum[0])
        finally:
            release.set()
            self.service.stop()
            for item in servers:
                item.shutdown()
                item.server_close()
            for thread in threads:
                thread.join(2)
        self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()
