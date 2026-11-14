"""Deadline-bounded pre-output retry policy (gated by RETRY_WINDOW_SECONDS).

Live 2026-11-12 (journal 2026-11-13 fixes/router-upstream-error-retry): each failed
pre-output attempt took 43-89 s before the gateway's SSE error, and the fixed count of
three retries gave up after about 281 s while the sized deadline still had about
1009 s left. These tests pin the window policy with an injected clock, sleep and
random source; the window value itself is set only from the measured client
tolerance.
"""
import io
import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from ciel_runtime_support import transient_retry as retry

NL = bytes([10])
ERROR = NL.join([b"event: error",
                 b'data: {"type":"error","error":{"type":"server_error","message":"Model timed out (HTTP 504)"}}',
                 b"", b""])
OUTPUT = NL.join([b"event: message_start", b'data: {"type":"message_start","message":{"id":"msg_ok"}}', b"", b""])


def hinted(action="wait", seconds=None):
    hint = {"action": action, "next_step": "Wait for the cooldown and retry."}
    if seconds is not None:
        hint["retry_after_seconds"] = seconds
    value = {"type": "error", "error": {"type": "server_error", "message": "Model timed out (HTTP 504)"},
             "recovery_hint": hint}
    return NL.join([b"event: error", b"data: " + json.dumps(value).encode(), b"", b""])


def http_error(raw=ERROR, code=503, headers=None):
    return urllib.error.HTTPError("http://fixture", code, "x", headers or {}, io.BytesIO(raw))


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def window_budget(clock, window=900.0, rand=lambda: 0.0, ceiling=None):
    return retry.TransientRetryBudget(retry.WINDOW_RETRY_CEILING if ceiling is None else ceiling, window,
                                      clock=clock, window_mode=True, rand=rand)


class WindowScopeTests(unittest.TestCase):
    def test_window_ships_at_420_seconds_with_the_540_second_arrival_bound(self):
        # Team-lead ruling 2026-11-13 after the real claude.exe probe
        # (evidence/claude-window-330): ship 420; it was 0 until that run.
        self.assertEqual(retry.RETRY_WINDOW_SECONDS, 420.0)
        self.assertEqual(retry.HARD_PREOUTPUT_SECONDS, 540.0)
        with retry.request_scope({}) as budget:
            self.assertTrue(budget.window_mode)
            self.assertEqual(budget.limit, retry.WINDOW_RETRY_CEILING)
            self.assertEqual(budget.deadline_seconds, 420.0)
            self.assertEqual(budget.hard_preoutput_seconds, 540.0)
            self.assertFalse(budget.timeout_pending)

    def test_window_zero_restores_the_count_policy(self):
        with mock.patch.object(retry, "RETRY_WINDOW_SECONDS", 0.0):
            with retry.request_scope({}) as budget:
                self.assertFalse(budget.window_mode)
                self.assertEqual(budget.limit, retry.DEFAULT_RETRIES)
                self.assertTrue(budget.timeout_pending)
                self.assertIsNone(budget.arrival_started)

    def test_enabled_window_replaces_the_default_count(self):
        with mock.patch.object(retry, "RETRY_WINDOW_SECONDS", 900.0):
            with retry.request_scope({}) as budget:
                self.assertTrue(budget.window_mode)
                self.assertEqual(budget.limit, retry.WINDOW_RETRY_CEILING)
                self.assertEqual(budget.deadline_seconds, 900.0)
                self.assertFalse(budget.timeout_pending)

    def test_explicit_gateway_retries_keep_the_users_count(self):
        with mock.patch.object(retry, "RETRY_WINDOW_SECONDS", 900.0):
            for value, limit in ((3, 3), ("5", 5), (10, 10)):
                with self.subTest(value=value):
                    with retry.request_scope({"gateway_retries": value}) as budget:
                        self.assertFalse(budget.window_mode)
                        self.assertEqual(budget.limit, limit)
            with retry.request_scope({"gateway_retries": 0}) as budget:
                self.assertIsNone(budget)

    def test_ambiguous_or_invalid_window_values_keep_the_count_policy(self):
        for value in (-1.0, float("inf"), float("nan"), True):
            with self.subTest(value=value), mock.patch.object(retry, "RETRY_WINDOW_SECONDS", value):
                with retry.request_scope({}) as budget:
                    self.assertFalse(budget.window_mode)

    def test_other_providers_never_get_a_window_budget(self):
        with mock.patch.object(retry, "RETRY_WINDOW_SECONDS", 900.0):
            with retry.request_scope({}, "anthropic") as budget:
                self.assertIsNone(budget)

    def test_window_scope_keeps_router_arrival_separate_from_first_open(self):
        clock = Clock()
        with mock.patch.object(retry, "RETRY_WINDOW_SECONDS", 420.0), \
             mock.patch.object(retry.time, "monotonic", clock):
            with retry.request_scope({}, arrival_started=clock()) as budget:
                budget.clock, budget.started = clock, clock()
                clock.now += 100.0
                budget.start_at_first_open()
                self.assertEqual(1100.0, budget.started)
                self.assertEqual(1000.0, budget.arrival_started)
                self.assertEqual(420.0, budget.remaining())
                self.assertEqual(440.0, budget.hard_remaining())

    def test_explicit_budget_ignores_arrival_override(self):
        clock = Clock()
        budget = retry.TransientRetryBudget(3, 60.0, clock=clock)
        with retry.request_scope({}, budget=budget, arrival_started=0.0):
            self.assertIs(retry.budget_for({}, "cielairouter"), budget)
            self.assertIsNone(budget.arrival_started)
            self.assertEqual(60.0, budget.remaining())


class WindowWaitTests(unittest.TestCase):
    def test_live_timing_keeps_retrying_until_the_window_not_after_three(self):
        clock = Clock()
        # 420 s is the proposed window; it ends before the 540 s arrival bound.
        budget = window_budget(clock, window=420.0)
        admitted = []
        while True:
            clock.now += 88.0  # measured: about 88 s from open to the gateway's SSE error
            wait = budget.next_wait(http_error(), ERROR)
            if wait is None:
                break
            admitted.append(wait)
            clock.now += wait
        self.assertGreater(len(admitted), 3)
        self.assertEqual(budget.stop_reason, "deadline")
        self.assertLessEqual(clock.now - 1000.0, 420.0 + 88.0)

    def test_equal_jitter_stays_within_half_and_full_backoff(self):
        for draw, expected in ((0.0, 1.0), (1.0, 0.5), (0.5, 0.75)):
            with self.subTest(draw=draw):
                budget = window_budget(Clock(), rand=lambda d=draw: d)
                self.assertAlmostEqual(budget.next_wait(http_error(), ERROR), expected)
        budget = window_budget(Clock(), rand=lambda: 1.0)
        budget.attempts = 5
        self.assertAlmostEqual(budget.next_wait(http_error(), ERROR), 15.0)

    def test_broken_random_source_falls_back_to_full_backoff(self):
        budget = window_budget(Clock(), rand=lambda: "x")
        self.assertAlmostEqual(budget.next_wait(http_error(), ERROR), 1.0)

    def test_gateway_hints_are_a_floor_capped_and_bounded_by_the_window(self):
        budget = window_budget(Clock())
        self.assertAlmostEqual(budget.next_wait(http_error(), hinted("wait", 20)), 20.0)
        self.assertEqual(budget.hint_action, "wait")
        budget = window_budget(Clock())
        self.assertAlmostEqual(budget.next_wait(http_error(), hinted("wait", 300)), retry.MAX_HINT_WAIT_SECONDS)
        # A hint longer than the 540 s router-arrival bound cannot be honored.
        budget = window_budget(Clock())
        self.assertIsNone(budget.next_wait(http_error(), hinted("wait", 600)))
        self.assertEqual(budget.stop_reason, "hint_beyond_deadline")
        budget = window_budget(Clock())
        headers = {retry.GATEWAY_RETRY_AFTER_HEADER: "40"}
        self.assertAlmostEqual(budget.next_wait(http_error(headers=headers), ERROR, headers), 40.0)
        budget = window_budget(Clock(), window=30.0)
        self.assertIsNone(budget.next_wait(http_error(), hinted("wait", 60)))
        self.assertEqual(budget.stop_reason, "hint_beyond_deadline")
        self.assertEqual(budget.attempts, 0)

    def test_invalid_or_retry_hints_do_not_change_the_backoff(self):
        for raw in (hinted("generate", 20), hinted("retry"), hinted("wait", True)):
            with self.subTest(raw=raw[:80]):
                budget = window_budget(Clock())
                self.assertAlmostEqual(budget.next_wait(http_error(), raw), 1.0)

    def test_fast_failures_stop_at_the_attempt_ceiling(self):
        clock = Clock()
        budget = window_budget(clock, window=3600.0, ceiling=5)
        waits = []
        while (wait := budget.next_wait(http_error(), ERROR)) is not None:
            waits.append(wait)
            clock.now += wait
        self.assertEqual(len(waits), 5)
        self.assertEqual(budget.stop_reason, "attempt_ceiling")

    def test_terminal_and_exposed_never_retry_in_window_mode(self):
        auth = ERROR.replace(b"server_error", b"authentication_error").replace(
            b"Model timed out (HTTP 504)", b"invalid x-api-key")
        budget = window_budget(Clock())
        self.assertIsNone(budget.next_wait(http_error(), auth))
        self.assertEqual(budget.stop_reason, "not_retryable")
        budget = window_budget(Clock())
        budget.exposed = True
        self.assertIsNone(budget.next_wait(http_error(), ERROR))
        self.assertEqual(budget.stop_reason, "exposed")

    def test_note_names_attempts_elapsed_and_reason(self):
        clock = Clock()
        budget = window_budget(clock, window=10.0)
        clock.now += 9.5
        self.assertIsNone(budget.next_wait(http_error(), ERROR))
        self.assertIn("0 time(s) over 10s", budget.note())
        self.assertIn("(deadline)", budget.note())


class WindowLateSSETests(unittest.TestCase):
    def serve(self, bodies):
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
                time.sleep(.05)
                self.wfile.write(body)
                self.wfile.flush()

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(2)))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/v1/messages", data=b"same-payload")
        slept = []
        with mock.patch.object(retry, "RETRY_WINDOW_SECONDS", 900.0):
            response = retry.open_with_transient_retry(
                lambda: opener.open(req, timeout=3), provider="cielairouter", config={},
                log=lambda level, text: logs.append(text), sleep=slept.append, request=req)
            with response:
                result = response.read()
        return result, hits, slept, chr(10).join(logs)

    def test_more_than_three_pre_output_errors_still_recover(self):
        result, hits, slept, logs = self.serve([ERROR] * 6 + [OUTPUT])
        self.assertEqual(result, OUTPUT)
        self.assertEqual(hits, [b"same-payload"] * 7)
        self.assertEqual(len(slept), 6)
        self.assertIn("attempt=6/%d" % retry.WINDOW_RETRY_CEILING, logs)
        self.assertNotIn("Model timed out", logs)

    def test_output_then_error_is_still_never_replayed(self):
        result, hits, slept, _ = self.serve([OUTPUT + ERROR, OUTPUT])
        self.assertEqual(result, OUTPUT + ERROR)
        self.assertEqual(len(hits), 1)
        self.assertEqual(slept, [])


if __name__ == "__main__":
    unittest.main()
