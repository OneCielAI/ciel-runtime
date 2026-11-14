"""A retry window never carries pre-output work past 540 s after router arrival.

Team-lead ruling 1 (2026-11-13, journal fixes/router-upstream-error-retry): the
420 s window is approved only with a hard bound measured from the moment the
request reaches the router, not from the first upstream open. No attempt or wait
is admitted past it, each attempt's socket timeout is clipped to it, and the
final error is honest about how many retries happened. The real claude.exe
aborted a stream with no events 600.1-750.1 s after dispatch (T2/T3/T4).
Clock-driven tests inject time; loopback tests use the real clock for socket reads.
"""
import io
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ciel_runtime_support import transient_retry as retry

NL = bytes([10])
ERROR = NL.join([b"event: error",
                 b'data: {"type":"error","error":{"type":"server_error","message":"Model timed out (HTTP 504)"}}',
                 b"", b""])
OUTPUT = NL.join([b"event: message_start", b'data: {"type":"message_start","message":{"id":"msg_ok"}}', b"", b""])
MORE = NL.join([b"event: message_stop", b'data: {"type":"message_stop"}', b"", b""])


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Response(io.BytesIO):
    def __init__(self):
        super().__init__(b'{"id":"ok"}')
        self.headers = {"content-type": "application/json"}
        self.status = 200


def rejection():
    return urllib.error.HTTPError("http://fixture/v1/messages", 503, "x", {}, io.BytesIO(ERROR))


def bounded(clock, *, arrival=1000.0, window=420.0, ceiling=None):
    return retry.TransientRetryBudget(retry.WINDOW_RETRY_CEILING if ceiling is None else ceiling, window,
                                      clock=clock, window_mode=True, start_on_first_open=True,
                                      arrival_started=arrival, rand=lambda: 0.0)


def run(budget, open_once, clock, **kwargs):
    with retry.request_scope({}, budget=budget):
        return retry.open_with_transient_retry(open_once, provider="cielairouter", config={},
                                               log=kwargs.pop("log", lambda *_: None),
                                               sleep=clock.sleep, **kwargs)


class ArrivalBoundTests(unittest.TestCase):
    def test_the_bound_is_540_seconds(self):
        self.assertEqual(540.0, retry.HARD_PREOUTPUT_SECONDS)
        self.assertEqual(540.0, bounded(Clock()).hard_preoutput_seconds)

    def test_socket_timeout_is_clipped_to_the_arrival_bound_not_the_window(self):
        # 123 s of catalog work before the first open re-bases the window to
        # end at arrival+543 s; the arrival bound still ends it at +540 s.
        clock = Clock()
        budget = bounded(clock)
        clock.now += 123.0
        with retry.request_scope({}, budget=budget):
            self.assertEqual(300.0, retry.bounded_timeout({}, "cielairouter", 300.0))
            self.assertEqual(1123.0, budget.started)
            clock.now = 1000.0 + 530.0
            self.assertAlmostEqual(10.0, retry.bounded_timeout({}, "cielairouter", 300.0))

    def test_no_wait_is_scheduled_that_would_end_past_the_bound(self):
        clock = Clock()
        budget = bounded(clock)
        clock.now += 123.0
        budget.start_at_first_open()  # the window now ends at arrival+543 s
        budget.attempts = 3  # next scheduled backoff is 8 s
        clock.now = 1000.0 + 535.0
        self.assertIsNone(budget.next_wait(rejection(), ERROR))
        self.assertEqual("preoutput_bound", budget.stop_reason)
        self.assertEqual(3, budget.attempts)

    def test_window_end_before_the_bound_still_reports_the_window(self):
        clock = Clock()
        budget = bounded(clock, window=100.0)
        clock.now = 1000.0 + 99.5
        self.assertIsNone(budget.next_wait(rejection(), ERROR))
        self.assertEqual("deadline", budget.stop_reason)

    def test_zero_attempt_failure_at_the_bound_is_honest_and_carries_no_note(self):
        clock = Clock()
        budget = bounded(clock)
        clock.now = 1000.0 + 540.0
        calls = []
        with self.assertRaises(RuntimeError) as caught:
            run(budget, lambda: calls.append(1) or Response(), clock)
        self.assertEqual([], calls)
        self.assertEqual("router pre-output time limit passed before the first upstream open (0 attempts)",
                         str(caught.exception))
        self.assertFalse(hasattr(caught.exception, "_ciel_transient_retry_note"))
        self.assertTrue(retry.retry_handled(caught.exception))

    def test_live_shaped_slow_failures_never_cross_the_bound(self):
        # Live 2026-11-12: about 88 s from open to the gateway's SSE error; B4:
        # 123 s of catalog stalls before the first open.
        clock = Clock()
        budget = bounded(clock)
        clock.now += 123.0
        opens, events = [], []

        def open_once():
            timeout = retry.bounded_timeout({}, "cielairouter", 300.0)
            opens.append((clock.now - 1000.0, timeout))
            if timeout < 88.0:  # the clipped socket timeout fires first
                clock.now += timeout
                raise TimeoutError("timed out")
            clock.now += 88.0
            raise rejection()

        with self.assertRaises(TimeoutError) as caught:
            run(budget, open_once, clock, activity=lambda *args, **fields: events.append((args[0], fields)))
        self.assertEqual([(123.0, 300.0), (212.0, 300.0), (302.0, 238.0), (394.0, 146.0), (490.0, 50.0)],
                         [(round(e, 6), round(t, 6)) for e, t in opens])
        for elapsed, timeout in opens:
            self.assertLessEqual(elapsed + timeout, 540.0 + 1e-9)
        self.assertLessEqual(clock.now - 1000.0, 540.0 + 1e-9)
        self.assertEqual("preoutput_bound", budget.stop_reason)
        self.assertEqual(len(opens) - 1, budget.attempts)
        note = caught.exception._ciel_transient_retry_note
        self.assertIn(f"retried this request {budget.attempts} time(s)", note)
        self.assertIn("(preoutput_bound)", note)
        # (d) every retry was visible while it happened, then the final stop.
        self.assertEqual(["retry"] * budget.attempts + ["error"], [event for event, _ in events])
        self.assertEqual("pre_output_retries_exhausted", events[-1][1]["reason"])

    def test_rejection_arriving_exactly_at_the_bound_names_the_bound(self):
        # No time is left to inspect the body, so it is relayed as received
        # and the note names the arrival bound, not a generic stop.
        clock = Clock()
        budget = bounded(clock)
        clock.now += 123.0  # catalog work: the window now outlasts the bound
        calls = []

        def open_once():
            calls.append(1)
            timeout = retry.bounded_timeout({}, "cielairouter", 300.0)
            clock.now += 400.0 if len(calls) == 1 else timeout
            raise rejection()

        with self.assertRaises(urllib.error.HTTPError) as caught:
            run(budget, open_once, clock)
        self.assertEqual(ERROR, caught.exception.read())
        caught.exception.close()
        self.assertEqual(2, len(calls))
        self.assertEqual("preoutput_bound", budget.stop_reason)
        self.assertIn("(preoutput_bound)", caught.exception._ciel_transient_retry_note)

    def test_gateway_recovering_before_the_last_admitted_open_is_answered(self):
        # 88 s failures from t=0: opens at 0, 89, 179, 271, 367 s. An outage
        # that ends by 367 s is answered on the fifth open, inside the bound.
        clock = Clock()
        budget = bounded(clock)
        opens = []

        def open_once():
            timeout = retry.bounded_timeout({}, "cielairouter", 300.0)
            opens.append(clock.now - 1000.0)
            if clock.now - 1000.0 < 360.0:
                clock.now += min(88.0, timeout)
                raise rejection()
            return Response()

        with run(budget, open_once, clock) as response:
            self.assertEqual(b'{"id":"ok"}', response.read())
        self.assertEqual([0.0, 89.0, 179.0, 271.0, 367.0], opens)
        self.assertEqual(4, budget.attempts)

    def test_nested_default_scope_keeps_the_request_arrival(self):
        clock = Clock()
        outer = bounded(clock)
        clock.now += 200.0
        original = retry.RETRY_WINDOW_SECONDS
        retry.RETRY_WINDOW_SECONDS = 420.0
        try:
            with retry.request_scope({}, budget=outer):
                with retry.request_scope({"current_model": "m"}) as inner:
                    self.assertEqual(1000.0, inner.arrival_started)
        finally:
            retry.RETRY_WINDOW_SECONDS = original

    def test_count_policy_has_no_arrival_bound(self):
        clock = Clock()
        budget = retry.TransientRetryBudget(3, 2000.0, clock=clock)
        clock.now += 1000.0
        self.assertIsNone(budget.arrival_started)
        self.assertEqual(1000.0, budget.remaining())


class LoopbackReadBoundTests(unittest.TestCase):
    """The pre-output read of an accepted SSE response obeys the arrival bound."""

    def serve(self, script):
        hits = []

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                hits.append(time.monotonic())
                steps = script[min(len(hits) - 1, len(script) - 1)]
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.flush()
                try:
                    for delay, chunk in steps:
                        time.sleep(delay)
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    time.sleep(2.0)
                except OSError:
                    pass

            def log_message(self, *_):
                pass

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            block_on_close = False

        server = Server(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(5)))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/v1/messages", data=b"payload")
        return hits, lambda: opener.open(req, timeout=retry.bounded_timeout({}, "cielairouter", 5.0)), req

    def budget(self, left):
        now = time.monotonic()
        return retry.TransientRetryBudget(retry.WINDOW_RETRY_CEILING, 420.0, window_mode=True,
                                          arrival_started=now - (540.0 - left), rand=lambda: 0.0)

    def read_all(self, budget, open_once, req, events=None):
        logs = []
        with retry.request_scope({}, budget=budget):
            response = retry.open_with_transient_retry(
                open_once, provider="cielairouter", config={}, log=lambda level, text: logs.append(text),
                sleep=lambda _: None, request=req,
                activity=(lambda *args, **fields: events.append((args[0], fields))) if events is not None else None)
            with response:
                started = time.monotonic()
                data = response.read()
                return data, time.monotonic() - started, chr(10).join(logs)

    def test_silent_stream_ends_at_the_bound_with_an_honest_zero_retry_error(self):
        hits, open_once, req = self.serve([[(3.0, OUTPUT)]])
        budget = self.budget(0.8)
        data, waited, logs = self.read_all(budget, open_once, req)
        self.assertEqual(1, len(hits))
        self.assertLess(waited, 2.0)
        self.assertIn(b"event: error", data)
        self.assertIn(b"router pre-output time limit (540s since the request arrived) was reached (0 retries)", data)
        self.assertNotIn(b"message_start", data)
        self.assertIn("cielairouter_preoutput_bound", logs)
        self.assertEqual("preoutput_bound", budget.stop_reason)

    def test_bound_after_retries_names_them_and_reports_activity(self):
        hits, open_once, req = self.serve([[(0.05, ERROR)], [(6.0, OUTPUT)]])
        budget = self.budget(2.5)
        events = []
        data, _, _ = self.read_all(budget, open_once, req, events)
        self.assertEqual(2, len(hits))
        self.assertIn(b"Ciel Runtime retried 1 time(s)", data)
        self.assertIn(b"request reached the router pre-output time limit", data)
        self.assertEqual(["retry", "error"], [event for event, _ in events])
        self.assertEqual("preoutput_bound", events[-1][1]["stop"])

    def test_first_frame_inside_the_bound_restores_the_callers_read_timeout(self):
        # The bound covers the wait for the first frame only; a slower later
        # frame is read under the caller's own 5 s timeout.
        hits, open_once, req = self.serve([[(0.05, OUTPUT), (1.2, MORE)]])
        budget = self.budget(0.6)
        data, _, _ = self.read_all(budget, open_once, req)
        self.assertEqual(OUTPUT + MORE, data)
        self.assertEqual(1, len(hits))


if __name__ == "__main__":
    unittest.main()
