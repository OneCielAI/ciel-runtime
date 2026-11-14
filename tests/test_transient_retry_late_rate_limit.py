"""A late pre-output SSE rate_limit_error is retried inside the window (design point 3).

Live 2026-11-12 (journal 2026-11-13 fixes/router-upstream-error-retry): open-time
429s were retried, but two late SSE `rate_limit_error` frames carrying the published
envelope (diagnostics + recovery_hint) were committed as terminal with zero retries.
Behind the window gate only; quota, billing, credit and usage-limit markers stay terminal.
Envelope shape from OneCiel/ciel-ai-router error.ts 9d80dc15 / pinRecovery.ts ceabe957
(synthetic fixture, not a captured live body).
"""
import json
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from ciel_runtime_support import transient_retry as retry

NL = bytes([10])
OUTPUT = NL.join([b"event: message_start", b'data: {"type":"message_start","message":{"id":"msg_ok"}}', b"", b""])
PING = NL.join([b"event: ping", b'data: {"type":"ping"}', b"", b""])


def envelope(message="Rate limit reached for this combo", action="try-auto", seconds=5):
    hint = {"action": action, "next_step": "Every model in this combo failed. Switch to model: auto, or wait a few seconds."}
    if seconds is not None:
        hint["retry_after_seconds"] = seconds
    value = {"error": {"type": "rate_limit_error", "code": "429", "message": message},
             "diagnostics": {"poolSize": 2, "attempted": 2, "excluded": [],
                             "attemptOrder": [{"provider": "fixture", "model": "model"}],
                             "terminalReason": "all_models_failed", "recovery": dict(hint)},
             "recovery_hint": hint}
    return NL.join([b"event: error", b"data: " + json.dumps(value).encode(), b"", b""])


class LateRateLimitTests(unittest.TestCase):
    def serve(self, bodies, window=900.0, delay=.25):
        hits, logs, slept = [], [], []

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                hits.append(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                payload = bodies[min(len(hits) - 1, len(bodies) - 1)]
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                time.sleep(delay)
                self.wfile.write(payload)
                self.wfile.flush()

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(2)))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/v1/messages", data=b"same-payload")
        with mock.patch.object(retry, "RETRY_WINDOW_SECONDS", window):
            response = retry.open_with_transient_retry(
                lambda: opener.open(req, timeout=3), provider="cielairouter", config={},
                log=lambda level, text: logs.append(text), sleep=slept.append, request=req)
            with response:
                result = response.read()
        return result, hits, slept, chr(10).join(logs)

    def test_late_rate_limit_is_retried_with_the_hint_as_a_floor(self):
        result, hits, slept, logs = self.serve([PING + envelope(seconds=5), OUTPUT])
        self.assertEqual(OUTPUT, result)
        self.assertEqual([b"same-payload"] * 2, hits)
        self.assertEqual([5.0], slept)
        self.assertIn("hint=try-auto", logs)

    def test_switch_combo_is_backoff_only_never_a_model_switch(self):
        result, hits, slept, _ = self.serve([envelope(action="switch-combo", seconds=None), OUTPUT])
        self.assertEqual(OUTPUT, result)
        self.assertEqual([b"same-payload"] * 2, hits)
        self.assertEqual(1, len(slept))
        self.assertLessEqual(slept[0], retry.WINDOW_SCHEDULE_SECONDS[0])

    def test_hint_beyond_the_window_stops_at_once(self):
        result, hits, slept, logs = self.serve([envelope(seconds=1000), OUTPUT])
        self.assertEqual(1, len(hits))
        self.assertEqual([], slept)
        self.assertIn(b"rate_limit_error", result)
        self.assertIn("stop=hint_beyond_deadline", logs)

    def test_quota_billing_credit_and_usage_markers_stay_terminal(self):
        for message in ("insufficient_quota: monthly quota exceeded", "billing hard limit reached",
                        "credit balance is too low", "usage limit reached for this plan"):
            with self.subTest(message=message):
                raw = envelope(message=message)
                result, hits, slept, logs = self.serve([raw, OUTPUT])
                self.assertEqual(raw, result)
                self.assertEqual(1, len(hits))
                self.assertIn("reason=terminal_verdict", logs)

    def test_window_off_keeps_the_late_rate_limit_terminal(self):
        raw = envelope()
        result, hits, slept, logs = self.serve([raw, OUTPUT], window=0.0)
        self.assertEqual(raw, result)
        self.assertEqual(1, len(hits))
        self.assertIn("reason=terminal_verdict", logs)

    def test_immediate_sse_rate_limit_at_open_is_retried_too(self):
        # Caught by the 100 ms open-time preflight instead of the read-time guard.
        result, hits, slept, _ = self.serve([envelope(seconds=3), OUTPUT], delay=0)
        self.assertEqual(OUTPUT, result)
        self.assertEqual(2, len(hits))
        self.assertEqual([3.0], [wait for wait in slept if wait >= retry.MIN_WAIT_SECONDS])

    def test_rate_limit_after_message_start_never_replays(self):
        raw = OUTPUT + envelope()
        result, hits, slept, _ = self.serve([raw, OUTPUT])
        self.assertEqual(raw, result)
        self.assertEqual(1, len(hits))


if __name__ == "__main__":
    unittest.main()
