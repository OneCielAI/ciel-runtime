"""Time spent before the first upstream open never consumes the message's retry allowance.

Live 2026-11-13 (journal 2026-11-13 fixes/router-upstream-error-retry, B4): request-path
model-catalog lookups stalled 41 s each (a 20 s timeout, 1 s backoff, a second 20 s)
while the default 60 s pre-open deadline was already running from scope entry, so the
first open was refused and the client got a 500 "transient retry deadline or attempt
limit reached" with zero upstream opens.
"""
import io
import unittest
import urllib.error
from unittest import mock

from ciel_runtime_support import transient_retry as retry


class Response(io.BytesIO):
    def __init__(self):
        super().__init__(b'{"id":"ok"}')
        self.headers = {"content-type": "application/json"}
        self.status = 200


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def rejection():
    raw = b'{"type":"error","error":{"type":"overloaded_error","message":"busy"}}'
    return urllib.error.HTTPError("http://a/v1/messages", 503, "x", {}, io.BytesIO(raw))


def default_scopes_use(clock):
    """Default request scopes built on the injected clock (the dataclass default is bound at import)."""
    original = retry._default_budget

    def make(config, provider, **kwargs):
        budget = original(config, provider, **kwargs)
        if budget is not None:
            budget.clock = clock
            budget.started = clock()
            if kwargs.get("arrival_started") is None and budget.arrival_started is not None:
                # The arrival bound was stamped with the real monotonic clock at
                # construction. Re-stamp it on the injected clock too: on a freshly
                # booted runner the real value is below this fake clock and the
                # 540 s hard cap read as already spent (CI 2e799ad, Linux).
                budget.arrival_started = budget.started
        return budget
    return mock.patch.object(retry, "_default_budget", make)


class PreOpenTimeTests(unittest.TestCase):

    def test_catalog_time_before_the_first_open_is_not_spent(self):
        clock, calls = Clock(), []

        def open_once():
            calls.append(clock.now)
            retry.bounded_timeout({}, "cielairouter", 300)
            return Response()

        with default_scopes_use(clock):
            with retry.request_scope({}) as budget:
                clock.now += 123.0  # three 41 s catalog stalls, measured
                with retry.open_with_transient_retry(open_once, provider="cielairouter", config={},
                                                     log=lambda *_: None, sleep=lambda _: None) as response:
                    self.assertEqual(b'{"id":"ok"}', response.read())
        self.assertEqual(1, len(calls))
        self.assertEqual(1, budget.opens)

    def test_retries_still_follow_the_first_open(self):
        clock, calls = Clock(), []

        def open_once():
            calls.append(clock.now)
            retry.bounded_timeout({}, "cielairouter", 300)
            if len(calls) == 1:
                raise rejection()
            return Response()

        with default_scopes_use(clock):
            with retry.request_scope({}) as budget:
                clock.now += 123.0
                with retry.open_with_transient_retry(open_once, provider="cielairouter", config={},
                                                     log=lambda *_: None, sleep=lambda _: None) as response:
                    response.read()
        self.assertEqual(2, len(calls))
        self.assertEqual(1, budget.attempts)

    def test_explicit_budget_keeps_its_own_start(self):
        clock = Clock()
        budget = retry.TransientRetryBudget(3, 60.0, clock=clock)
        clock.now += 61.0
        calls = []
        with retry.request_scope({}, budget=budget):
            with self.assertRaises(RuntimeError) as caught:
                retry.open_with_transient_retry(lambda: calls.append(1) or Response(), provider="cielairouter",
                                                config={}, log=lambda *_: None, sleep=lambda _: None)
        self.assertEqual([], calls)
        self.assertNotIn("attempt limit", str(caught.exception))
        self.assertIn("before the first upstream open (0 attempts)", str(caught.exception))
        self.assertTrue(retry.retry_handled(caught.exception))

    def test_zero_open_failure_reports_no_activity_or_exhaustion(self):
        clock = Clock()
        budget = retry.TransientRetryBudget(3, 60.0, clock=clock)
        clock.now += 61.0
        events = []
        with retry.request_scope({}, budget=budget):
            with self.assertRaises(Exception):
                retry.open_with_transient_retry(Response, provider="cielairouter", config={},
                                                log=lambda *_: None, sleep=lambda _: None,
                                                activity=lambda *args, **fields: events.append((args, fields)))
        self.assertEqual([], events)

    def test_the_rebase_happens_once(self):
        clock, calls = Clock(), []

        def open_once():
            calls.append(clock.now)
            retry.bounded_timeout({}, "cielairouter", 300)
            clock.now += 400.0  # each attempt is slow
            raise rejection()

        with default_scopes_use(clock):
            with retry.request_scope({}) as budget:
                clock.now += 50.0
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    retry.open_with_transient_retry(open_once, provider="cielairouter", config={},
                                                    log=lambda *_: None, sleep=lambda _: None)
                caught.exception.close()
        started = calls[0]
        self.assertLessEqual(budget.clock() - started, budget.deadline_seconds + 400.0 + retry.MAX_WAIT_SECONDS)
        self.assertEqual(budget.started, started)

    def test_opener_that_bypasses_the_executor_also_starts_at_its_first_open(self):
        # Remote-bridge / selected-gateway paths call open_once() directly; its
        # bounded_timeout is the first open and must not see catalog time.
        clock = Clock()
        with default_scopes_use(clock):
            with retry.request_scope({}) as budget:
                clock.now += 123.0
                timeout = retry.bounded_timeout({}, "cielairouter", 300)
        self.assertEqual(300, timeout)
        self.assertEqual(1123.0, budget.started)

    def test_zero_open_failure_carries_no_retry_note(self):
        clock = Clock()
        budget = retry.TransientRetryBudget(3, 60.0, clock=clock)
        clock.now += 61.0
        with retry.request_scope({}, budget=budget):
            with self.assertRaises(RuntimeError) as caught:
                retry.open_with_transient_retry(Response, provider="cielairouter", config={},
                                                log=lambda *_: None, sleep=lambda _: None)
        self.assertTrue(getattr(caught.exception, "_ciel_transient_retry_exhausted", False))
        self.assertFalse(hasattr(caught.exception, "_ciel_transient_retry_note"))

    def test_first_attempt_terminal_answer_carries_no_retry_note(self):
        auth = b'{"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"}}'

        def open_once():
            raise urllib.error.HTTPError("http://a/v1/messages", 401, "x", {}, io.BytesIO(auth))

        with self.assertRaises(urllib.error.HTTPError) as caught:
            retry.open_with_transient_retry(open_once, provider="cielairouter", config={},
                                            log=lambda *_: None, sleep=lambda _: None)
        self.assertTrue(retry.retry_handled(caught.exception))
        self.assertFalse(hasattr(caught.exception, "_ciel_transient_retry_note"))
        caught.exception.close()

    def test_consume_external_retry_starts_the_allowance_at_its_first_open(self):
        clock = Clock()
        budget = retry.TransientRetryBudget(3, 60.0, clock=clock, start_on_first_open=True)
        clock.now += 50.0
        self.assertTrue(budget.consume_external_retry())
        self.assertEqual(1050.0, budget.started)
        self.assertEqual((1, 1), (budget.attempts, budget.opens))

    def test_rebase_never_moves_an_exposed_stopped_or_already_opened_budget(self):
        for field, value in (("exposed", True), ("stopped", True), ("opens", 1), ("attempts", 1)):
            with self.subTest(field=field):
                clock = Clock()
                budget = retry.TransientRetryBudget(3, 60.0, clock=clock, start_on_first_open=True)
                setattr(budget, field, value)
                clock.now += 50.0
                budget.start_at_first_open()
                self.assertEqual(1000.0, budget.started)
                self.assertFalse(budget.start_on_first_open)


if __name__ == "__main__":
    unittest.main()
