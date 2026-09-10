import json
from pathlib import Path
import tempfile
import unittest
import threading
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ciel_runtime_support.runtime_error_events import project_runtime_errors
from ciel_runtime_support.observability import EventBus
from ciel_runtime_support.tui_observation import TuiObservationBus
from ciel_runtime_support.router_http import EventHttpAdapter, EventHttpPorts
from ciel_runtime_support.transcript_delta_delivery import TranscriptDeliveryPorts, TranscriptDeltaDeliveryService


class RuntimeErrorEventsTests(unittest.TestCase):
    def test_structured_errors_not_conversation_keywords(self):
        for runtime, record in [
            ("codex", {"type": "event_msg", "payload": {"type": "error", "message": "connection lost"}}),
            ("claude", {"type": "assistant", "isApiErrorMessage": True, "error": "rate_limit", "message": {"content": [{"type": "text", "text": "Quota exhausted"}]}}),
            ("claude", {"type": "result", "is_error": True, "errors": ["Usage exhausted"]}),
            ("claude", {"type": "system", "subtype": "api_retry", "error_status": 429, "retry_delay_ms": 1200}),
        ]:
            with self.subTest(runtime=runtime, record=record):
                result = project_runtime_errors(record, runtime)
                self.assertEqual(1, len(result))
                self.assertTrue(result[0]["message"])
        self.assertEqual([], project_runtime_errors({"type": "assistant", "message": {"content": "network error rate limit"}}, "claude"))
        self.assertEqual([], project_runtime_errors({"type": "user", "is_error": True}, "claude"))

    def test_transcript_errors_reach_shared_bus_with_tools_disabled_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transcript = root / "session.jsonl"
            transcript.touch()
            bus = EventBus()
            service = TranscriptDeltaDeliveryService(root / "cursor.json", "workspace", TranscriptDeliveryPorts(
                load_config=lambda: {"tool_call_events": {"enabled": False}},
                latest_transcript=lambda: transcript,
                scope=lambda: {"runtime": "codex", "session_id": "session", "turn_scan_path": transcript, "turn_scan_offset": 0},
                log=lambda *_: None, event_publish=bus.publish,
            ))
            service.poll_tool_call_events()
            transcript.write_text(json.dumps({"type": "event_msg", "payload": {"type": "error", "message": "connection lost"}}) + "\n", encoding="utf-8")
            self.assertEqual(1, service.poll_tool_call_events())
            self.assertEqual(0, service.poll_tool_call_events())
            event = bus.wait_after(0, timeout=0.1)[0]
            self.assertEqual("runtime.error", event["category"])
            self.assertEqual("session", event["session_id"])
            self.assertEqual("connection lost", event["message"])

    def test_router_error_reaches_same_bus(self):
        bus = EventBus()
        tui = TuiObservationBus(error_publish=bus.publish)
        tui.publish(kind="output.error", request_id="req", role="assistant", text="rate limit")
        self.assertEqual("runtime.error", bus.recent()[0]["category"])
        self.assertEqual("req", bus.recent()[0]["request_id"])

    def test_actual_http_sse_delivers_error(self):
        bus = EventBus()
        adapter = EventHttpAdapter(EventHttpPorts(
            bus.recent, bus.wait_after, lambda: "", lambda *_: None,
            lambda *_: None, lambda *_: None,
        ))

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                url = urllib.parse.urlsplit(self.path)
                adapter.handle_get(self, url.path, urllib.parse.parse_qs(url.query))

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            TuiObservationBus(error_publish=bus.publish).publish(
                kind="output.error", request_id="request-test", role="assistant", text="upstream unavailable",
            )
            url = f"http://127.0.0.1:{server.server_port}/ca/events/stream?category=runtime.error"
            with urllib.request.urlopen(url, timeout=3) as response:
                while True:
                    line = response.readline()
                    if line.startswith(b"data:"):
                        event = json.loads(line[5:])
                        break
                    self.assertTrue(line)
            self.assertEqual("runtime.error", event["category"])
            self.assertEqual("request-test", event["request_id"])
            print("HTTP SSE captured:", json.dumps(event))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
