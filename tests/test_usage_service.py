import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ciel_runtime_support.usage_events import UsageEvent
from ciel_runtime_support.usage_service import (
    LegacyUsageBackfillService,
    SqliteUsageLedger,
    UsageApiKeyRepository,
    UsageHttpAdapter,
    UsagePushDeliveryService,
    UsagePushEndpoint,
    usage_jsonl_enabled,
    usage_push_endpoints,
)


class _Headers(dict):
    def get(self, key, default=None):
        lowered = str(key).casefold()
        for name, value in self.items():
            if str(name).casefold() == lowered:
                return value
        return default


class _Handler:
    def __init__(self, key=""):
        self.headers = _Headers({"Authorization": f"Bearer {key}"} if key else {})
        self.client_address = ("127.0.0.1", 1)


class UsageServiceTests(unittest.TestCase):
    def _services(self, directory, *, clock=lambda: 1000.0, environ=None):
        root = Path(directory)
        ledger = SqliteUsageLedger(root / "usage.sqlite3", "workspace-a", clock=clock)
        keys = UsageApiKeyRepository(ledger, root / "pepper", environ or {}, clock=clock)
        return ledger, keys

    def test_ledger_persists_detailed_usage_and_period_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger, _keys = self._services(directory)
            ledger.record(
                UsageEvent(
                    "openrouter", "model-a", input_tokens=7, output_tokens=5,
                    cache_read_input_tokens=11, cache_write_input_tokens=3,
                    input_tokens_total=21, reasoning_output_tokens=2,
                    runtime="codex", request_completed_at=900, event_id="event-a",
                )
            )
            event = ledger.events(start=800, end=950)[0]
            self.assertEqual(21, event["input_tokens_total"])
            self.assertEqual(26, event["total_tokens"])
            self.assertEqual("workspace-a", event["workspace_id"])
            summary = ledger.summary(800, 950)
            self.assertEqual(1, summary["totals"]["requests"])
            self.assertEqual(11, summary["totals"]["cache_read_input_tokens"])
            self.assertEqual(26, summary["totals"]["total_tokens"])

    def test_environment_keys_are_imported_and_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger, keys = self._services(
                directory,
                environ={"CIEL_RUNTIME_USAGE_API_KEYS": json.dumps([
                    {"id": "reader", "key": "read-secret", "scopes": ["usage:read"]},
                    {"id": "streamer", "key": "stream-secret", "scopes": ["usage:stream"]},
                ])},
            )
            self.assertEqual(2, keys.bootstrap_environment())
            self.assertEqual("reader", keys.authenticate(_Handler("read-secret"), "usage:read")["key_id"])
            self.assertIsNone(keys.authenticate(_Handler("read-secret"), "usage:stream"))
            self.assertEqual("streamer", keys.authenticate(_Handler("stream-secret"), "usage:stream")["key_id"])
            rows = keys.list()
            self.assertNotIn("secret_hash", rows[0])
            self.assertTrue(keys.revoke("reader"))
            self.assertIsNone(keys.authenticate(_Handler("read-secret"), "usage:read"))

    def test_snapshot_endpoint_requires_usage_key_and_returns_period(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger, keys = self._services(directory)
            issued = keys.issue("auditor", ["usage:read"])
            ledger.record(UsageEvent("p", "m", 4, 6, event_id="one", request_completed_at=900))
            responses = []
            adapter = UsageHttpAdapter(
                ledger, keys,
                lambda _handler, value, status=200: responses.append((status, value)),
                lambda *_args: False, lambda: {}, lambda *_args: None,
            )
            self.assertTrue(adapter.handle_get(_Handler(), "/ca/usage/snapshot", {"from": ["800"], "to": ["950"]}))
            self.assertEqual(401, responses[-1][0])
            self.assertTrue(adapter.handle_get(_Handler(issued["api_key"]), "/ca/usage/snapshot", {"from": ["800"], "to": ["950"]}))
            self.assertEqual(10, responses[-1][1]["snapshot"]["totals"]["total_tokens"])
            self.assertEqual(issued["key_id"], responses[-1][1]["consumer_key_id"])

    def test_push_failure_retries_same_event_and_advances_cursor_only_after_2xx(self):
        received = []
        statuses = [500, 204]

        class Receiver(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                received.append((self.headers.get("Idempotency-Key"), json.loads(body)))
                self.send_response(statuses.pop(0))
                self.end_headers()

            def log_message(self, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                ledger, _keys = self._services(directory)
                ledger.record(UsageEvent("p", "m", 3, 2, event_id="stable-event"))
                endpoint = UsagePushEndpoint(
                    "receiver", f"http://127.0.0.1:{server.server_port}/usage", "Bearer receiver-key",
                    start_mode="beginning", audit_emit_on_start=False,
                )
                service = UsagePushDeliveryService(ledger, lambda: {}, {}, lambda *_args: None)
                self.assertEqual(0, service.poll_once([endpoint]))
                self.assertEqual(1, service.poll_once([endpoint]))
                self.assertEqual(0, service.poll_once([endpoint]))
                self.assertEqual(["stable-event", "stable-event"], [item[0] for item in received])
                self.assertEqual("ai.oneciel.ciel-runtime.usage.recorded", received[0][1]["type"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_audit_window_is_stable_on_retry_and_contains_daily_summary(self):
        received = []
        statuses = [500, 200]

        class Receiver(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                received.append(json.loads(body))
                self.send_response(statuses.pop(0))
                self.end_headers()

            def log_message(self, *_args):
                return

        now = [200000.0]
        server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                ledger, _keys = self._services(directory, clock=lambda: now[0])
                ledger.record(UsageEvent("p", "m", 8, 2, event_id="daily", request_completed_at=150000))
                endpoint = UsagePushEndpoint(
                    "audit", f"http://127.0.0.1:{server.server_port}/audit", "Bearer audit-key",
                    start_mode="tail", audit_interval_seconds=86400, audit_emit_on_start=True,
                )
                service = UsagePushDeliveryService(ledger, lambda: {}, {}, lambda *_args: None, clock=lambda: now[0])
                self.assertEqual(0, service.poll_once([endpoint]))
                now[0] += 10
                self.assertEqual(1, service.poll_once([endpoint]))
                self.assertEqual(received[0]["id"], received[1]["id"])
                self.assertEqual(received[0]["data"]["period"], received[1]["data"]["period"])
                self.assertEqual(10, received[1]["data"]["totals"]["total_tokens"])
                self.assertEqual("ai.oneciel.ciel-runtime.usage.audit", received[1]["type"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_environment_push_settings_override_config(self):
        endpoints = usage_push_endpoints(
            {"usage": {"push_endpoints": [{"id": "config", "url": "https://config"}]}},
            {
                "CIEL_RUNTIME_USAGE_PUSH_URL": "https://environment",
                "CIEL_RUNTIME_USAGE_PUSH_API_KEY": "secret",
                "CIEL_RUNTIME_USAGE_AUDIT_INTERVAL_SECONDS": "3600",
            },
        )
        self.assertEqual("https://environment", endpoints[0].url)
        self.assertEqual("Bearer secret", endpoints[0].authorization)
        self.assertEqual(3600, endpoints[0].audit_interval_seconds)

    def test_jsonl_setting_uses_config_and_environment_override(self):
        self.assertFalse(usage_jsonl_enabled({"usage": {"jsonl_enabled": False}}, {}))
        self.assertTrue(usage_jsonl_enabled({"usage": {"jsonl_enabled": False}}, {"CIEL_RUNTIME_USAGE_LOG": "true"}))
        self.assertFalse(usage_jsonl_enabled({"usage": {"jsonl_enabled": True}}, {"CIEL_RUNTIME_USAGE_LOG": "off"}))

    def test_legacy_jsonl_backfill_is_incremental_and_rotation_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger, _keys = self._services(directory)
            source = root / "usage-events.jsonl"
            first = {"provider": "p", "model": "m", "input_tokens": 2,
                     "output_tokens": 3, "timestamp": 100}
            second = {"provider": "p", "model": "m", "input_tokens": 5,
                      "output_tokens": 7, "timestamp": 200}
            source.write_text(json.dumps(first) + "\n", encoding="utf-8")
            backfill = LegacyUsageBackfillService(ledger, lambda *_args: None)
            self.assertEqual(1, backfill.run([source])["records"])
            self.assertEqual(0, backfill.run([source])["records"])
            with source.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(second) + "\n")
            self.assertEqual(1, backfill.run([source])["records"])
            rotated = root / "usage-events.jsonl.1"
            source.replace(rotated)
            self.assertEqual(0, backfill.run([rotated])["records"])
            summary = ledger.summary(0, 300)
            self.assertEqual(2, summary["totals"]["requests"])
            self.assertEqual(17, summary["totals"]["total_tokens"])
            self.assertEqual(2, summary["totals"]["incomplete_events"])


if __name__ == "__main__":
    unittest.main()
