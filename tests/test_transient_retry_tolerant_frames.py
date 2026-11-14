"""Pre-output gateway error frames with unknown sibling fields are retried.

Measured live shape (router 9524, 2026-11-12 20:23:33 local, operations 2ade4926 and
2ed19387; journal docs/journal/2026/11/13/fixes/router-upstream-error-retry): an
``event: error`` frame whose JSON has error/request_id/type plus two unknown top-level
fields, and whose error object has code/message/type plus one unknown field;
error.type was ``server_error`` and the message ``Gateway response stream was
interrupted``. The unknown field names and the code value were never recorded, so
the names and the code below are placeholders, and request_id is a fixture value.
"""
import io
import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ciel_runtime_support import transient_retry as retry

NL = bytes([10])
OUTPUT = NL.join([b"event: message_start", b'data: {"type":"message_start","message":{"id":"msg_ok"}}', b"", b""])
# The live held prefix: one ping frame and one comment-only frame before the error.
PREAMBLE = NL.join([b"event: ping", b'data: {"type":"ping"}', b"", b": keepalive", b"", b""])
MESSAGE = "Gateway response stream was interrupted"


def live_value(**changes):
    value = {"type": "error",
             "error": {"type": "server_error", "code": "placeholder_code", "message": MESSAGE,
                       "unknown_error_field": "placeholder"},
             "request_id": "req_fixture",
             "unknown_top_a": "placeholder", "unknown_top_b": 1}
    value.update(changes)
    return value


def frame(value, event=b"error"):
    head = [b"event: " + event] if event else []
    return NL.join(head + [b"data: " + json.dumps(value, separators=(",", ":")).encode(), b"", b""])


LIVE = frame(live_value())
TOOL_START = NL.join([b"event: content_block_start",
                      b'data: {"type":"content_block_start","index":0,"content_block":'
                      b'{"type":"tool_use","id":"toolu_fixture","name":"Bash","input":{}}}', b"", b""])


def serve(bodies, config=None):
    """Loopback upstream; returns (relayed bytes, request bodies, guard log text)."""
    hits, logs = [], []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            hits.append(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            body = bodies[min(len(hits) - 1, len(bodies) - 1)]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            time.sleep(.25)
            self.wfile.write(body)
            self.wfile.flush()

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/v1/messages", data=b"same-payload")
    try:
        response = retry.open_with_transient_retry(
            lambda: opener.open(req, timeout=3), provider="cielairouter", config=config or {},
            log=lambda level, text: logs.append(text), sleep=lambda _: None, request=req)
        with response:
            result = response.read()
        return result, hits, chr(10).join(logs)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


class TolerantPreOutputFrameTests(unittest.TestCase):
    def test_measured_live_shape_before_output_is_retried(self):
        result, hits, logs = serve([PREAMBLE + LIVE, OUTPUT])
        self.assertEqual(result, OUTPUT)
        self.assertEqual(hits, [b"same-payload"] * 2)
        self.assertIn("cielairouter_late_sse_retry", logs)
        self.assertNotIn("reason=unrecognized_frame", logs.split("cielairouter_late_sse_retry")[0])

    def test_each_kind_of_unknown_sibling_alone_is_retried(self):
        top_only = live_value(error={"type": "server_error", "code": "placeholder_code", "message": MESSAGE})
        error_only = {"type": "error", "error": {"type": "server_error", "message": MESSAGE, "unknown": 1}}
        numeric_status = {"type": "error", "error": {"type": "server_error", "message": MESSAGE, "status": 502}}
        nested_unknown = live_value(unknown_top_a={"nested": [1, 2]})
        for value in (top_only, error_only, numeric_status, nested_unknown):
            with self.subTest(value=value):
                result, hits, _ = serve([PREAMBLE + frame(value), OUTPUT])
                self.assertEqual(result, OUTPUT)
                self.assertEqual(len(hits), 2)

    def test_live_shape_after_message_start_never_replays(self):
        raw = OUTPUT + LIVE
        result, hits, _ = serve([raw, OUTPUT])
        self.assertEqual(result, raw)
        self.assertEqual(len(hits), 1)

    def test_live_shape_after_tool_use_never_replays(self):
        raw = TOOL_START + LIVE
        result, hits, _ = serve([raw, OUTPUT])
        self.assertEqual(result, raw)
        self.assertEqual(len(hits), 1)

    def test_known_terminal_types_never_replay_with_or_without_unknown_siblings(self):
        cases = (
            ("authentication_error", "invalid x-api-key"),
            ("permission_error", "not allowed for this organization"),
            ("invalid_request_error", "prompt is too long"),
            ("billing_error", "payment required"),
            ("server_error", "credit balance is too low"),
            ("server_error", "quota exceeded for this billing period"),
            ("rate_limit_error", "insufficient_quota for this account"),
        )
        for kind, text in cases:
            for extras in (True, False):
                with self.subTest(kind=kind, text=text, extras=extras):
                    value = live_value() if extras else {"type": "error", "error": {}}
                    value["error"].update(type=kind, message=text)
                    raw = PREAMBLE + frame(value)
                    result, hits, logs = serve([raw, OUTPUT])
                    self.assertEqual(result, raw)
                    self.assertEqual(len(hits), 1)
                    self.assertIn("reason=terminal_verdict", logs)
                    self.assertIn("verdict=tolerant" if extras else "verdict=strict", logs)
                    self.assertNotIn(text, logs)

    def test_lone_generation_frame_is_labelled_non_error_not_unrecognized(self):
        for raw in (OUTPUT, frame(live_value(type="message_start")),
                    frame(live_value(), event=b"content_block_delta")):
            with self.subTest(raw=raw[:50]):
                result, hits, logs = serve([raw, OUTPUT])
                self.assertEqual(result, raw)
                self.assertEqual(len(hits), 1)
                self.assertIn("reason=non_error_frame", logs)
                self.assertNotIn("reason=unrecognized_frame", logs)

    def test_generation_like_siblings_stay_unrecognized(self):
        for key, val in (("delta", {}), ("content_block", {}), ("index", 0), ("usage", {}),
                         ("message", {"id": "msg_fixture"}), ("choices", []), ("output", []),
                         ("response", {}), ("tool_calls", []), ("content", [])):
            with self.subTest(key=key):
                value = live_value(**{key: val})
                raw = PREAMBLE + frame(value)
                result, hits, logs = serve([raw, OUTPUT])
                self.assertEqual(result, raw)
                self.assertEqual(len(hits), 1)
                self.assertIn("reason=unrecognized_frame", logs)

    def test_non_error_type_or_event_stays_unrecognized(self):
        for raw in (frame(live_value(type="message_start")),
                    frame(live_value(), event=b"content_block_delta"),
                    frame({"type": "error", "error": {"type": 5, "message": MESSAGE}}),
                    frame({"type": "error", "error": {"type": "server_error", "message": 7}}),
                    frame({"type": "error", "error": "server_error"})):
            with self.subTest(raw=raw[:60]):
                result, hits, logs = serve([PREAMBLE + raw, OUTPUT])
                self.assertEqual(result, PREAMBLE + raw)
                self.assertEqual(len(hits), 1)

    def test_logs_never_carry_message_or_unknown_field_contents(self):
        _, _, logs = serve([PREAMBLE + LIVE, OUTPUT])
        for secret in (MESSAGE, "placeholder", "unknown_top_a", "unknown_error_field", "req_fixture"):
            self.assertNotIn(secret, logs)

    def test_exhaustion_relays_the_final_error_after_the_budget(self):
        result, hits, logs = serve([PREAMBLE + LIVE], {"gateway_retries": 1})
        self.assertEqual(len(hits), 2)
        self.assertIn(b"server_error", result)
        self.assertIn("reason=budget_exhausted", logs)

    def test_open_time_http_rejection_with_unknown_siblings_is_retried(self):
        raw = json.dumps(live_value()).encode()
        calls = []

        def open_once():
            calls.append(1)
            if len(calls) == 1:
                raise urllib.error.HTTPError("http://fixture", 503, "unavailable", {}, io.BytesIO(raw))
            return io.BytesIO(b"ok")

        with retry.open_with_transient_retry(open_once, provider="cielairouter", config={},
                                             log=lambda *_: None, sleep=lambda _: None) as response:
            self.assertEqual(response.read(), b"ok")
        self.assertEqual(len(calls), 2)

    def test_strict_verdict_is_unchanged_for_metadata_contract(self):
        # The published-metadata verdict keeps rejecting unknown siblings; only the
        # retry decision tolerates them.
        self.assertIsNone(retry._error_verdict(LIVE))
        self.assertEqual(retry._error_only_verdict(LIVE), ("server_error", MESSAGE))
        self.assertTrue(retry._retryable_verdict(503, LIVE))


if __name__ == "__main__":
    unittest.main()
