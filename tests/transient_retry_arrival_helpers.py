"""Fake-clock helpers for tests of the 540 s pre-output arrival bound.

Supplied to continue-pty for R2 (lead-rulings-independent-review-sse-retry-20261113.md):
a bridge request with a slow pre-open phase and an opener that keeps returning 503
must get its answer before 540 s from router arrival.

One clock drives every time source the bound reads:
- the router's arrival stamp (patch ``router_http.time.monotonic`` with ``clock``);
- every default retry scope (``TransientRetryBudget.clock`` is bound at import, so
  ``default_scopes_on`` rebinds each factory-made budget to ``clock``);
- waits (pass ``clock.sleep`` as the executor's ``sleep``, or patch ``time.sleep``).

Usage (pty's bridge test):

    clock = ArrivalClock()
    opener = Rejecting503(clock, seconds_per_attempt=88.0)
    with window_on(420.0), default_scopes_on(clock), \\
            mock.patch.object(router_http.time, "monotonic", clock):
        ... drive the bridge branch; its pre-open work calls clock.advance(T) ...
    assert_answer_before_bound(self, clock.now, opener.arrival_hint or clock.start)
"""
from __future__ import annotations

import contextlib
import io
import urllib.error
from unittest import mock

from ciel_runtime_support import transient_retry as retry

STRICT_503 = b'{"type":"error","error":{"type":"overloaded_error","message":"Service temporarily unavailable"}}'


class ArrivalClock:
    """A monotonic clock under test control; ``sleep`` advances it."""

    def __init__(self, start: float = 1000.0):
        self.start = self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)

    sleep = advance

    def since_start(self) -> float:
        return self.now - self.start


@contextlib.contextmanager
def window_on(seconds: float = 420.0):
    """The shipped window, explicitly (tests must not depend on the current constant)."""
    with mock.patch.object(retry, "RETRY_WINDOW_SECONDS", float(seconds)):
        yield


@contextlib.contextmanager
def default_scopes_on(clock: ArrivalClock):
    """Every factory-made retry scope reads ``clock``; an arrival stamp passed in is kept."""
    original = retry._default_budget

    def make(config, provider, **kwargs):
        budget = original(config, provider, **kwargs)
        if budget is not None:
            budget.clock = clock
            budget.started = clock()
            if budget.window_mode and kwargs.get("arrival_started") is None:
                budget.arrival_started = budget.started
        return budget

    with mock.patch.object(retry, "_default_budget", make):
        yield


class Rejecting503:
    """An opener that spends ``seconds_per_attempt`` (or its clipped timeout) and returns 503.

    ``opens`` records (seconds since clock start, clipped socket timeout) per attempt.
    Set ``succeed_after`` to an attempt count to return ``response()`` from then on.
    """

    def __init__(self, clock: ArrivalClock, seconds_per_attempt: float = 88.0, requested_timeout: float = 300.0,
                 config: dict | None = None, provider: str = "cielairouter", succeed_after: int | None = None,
                 response=None):
        self.clock, self.seconds, self.requested = clock, float(seconds_per_attempt), float(requested_timeout)
        self.config, self.provider = config if config is not None else {}, provider
        self.succeed_after, self.response = succeed_after, response
        self.opens: list[tuple[float, float]] = []

    def __call__(self):
        timeout = retry.bounded_timeout(self.config, self.provider, self.requested)
        self.opens.append((self.clock.since_start(), float(timeout)))
        if self.succeed_after is not None and len(self.opens) > self.succeed_after:
            return self.response()
        if timeout < self.seconds:
            self.clock.advance(timeout)
            raise TimeoutError("timed out")
        self.clock.advance(self.seconds)
        raise urllib.error.HTTPError("http://fixture/v1/messages", 503, "x",
                                     {"content-type": "application/json"}, io.BytesIO(STRICT_503))


def assert_answer_before_bound(testcase, answered_at: float, arrival: float,
                               bound: float | None = None) -> None:
    """The client's answer (success or final error) was sent within the arrival bound."""
    limit = retry.HARD_PREOUTPUT_SECONDS if bound is None else bound
    testcase.assertLessEqual(answered_at - arrival, limit + 1e-9,
                             f"answer sent {answered_at - arrival:.2f}s after arrival (bound {limit:.0f}s)")


__all__ = ["ArrivalClock", "Rejecting503", "STRICT_503", "assert_answer_before_bound",
           "default_scopes_on", "window_on"]
