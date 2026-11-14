"""Request-scoped retries for pre-output rejections and typed open timeouts.

The generation dispatcher enters request_scope; reusable config never owns a
mutable budget. Typed TimeoutError (also inside URLError) during open may
retry under that same budget. So may a DNS resolution failure or a refused
connection: both fail during connection setup, before any request byte can
reach the upstream, so replaying them cannot duplicate work. Typed connection
reset/remote-disconnect/abort, a broken pipe while sending and a TLS EOF during
connect or handshake, all raised during open, also retry before a response is
exposed, like open timeouts: upstream computation/billing may be duplicated (R3,
2026-11-13). Untyped or string-only transport failures do not authorize replay.
A timeout does not prove the upstream did no work, so replay may duplicate
upstream generation or billing. After message_start or any unknown generation
frame, reads never reopen. Before that boundary, delayed explicit SSE rejections
may reopen under the same budget; no visible output or tool use is replayed.

open_once must not expose downstream bytes. Only text/event-stream success
responses are inspected: at most 64 KiB for at most 100 ms (also limited by
the monotonic request deadline), using only ready bytes. Unknown streams or
incomplete HTTP framing are returned without a blocking probe. Prefix bytes
are replayed unchanged through the response contract; the first non-error,
unknown or mixed dispatch closes inspection, never a full-stream buffer.
Accepted SSE responses additionally have a read-time guard that holds at most
64 KiB of comment/ping preamble until the first generation/unknown frame. It uses
the existing stream idle timeout, not a longer synchronous open-time probe. Only
an error-only complete transient verdict can reopen; unknown/mixed/partial or
post-message-start errors stay unchanged. Terminal auth/quota remain terminal.
Every byte returned by the outer adapter marks the budget exposed. A caller may
sequentially reopen an unconsumed response within the same scope, spending the
same allowance. Materially different checkpoint recovery after terminal 400
must explicitly enter a fresh request_scope; the executor never resets one.

Default scopes size one finite aggregate allowance from the first resolved
opener timeout: all allowed opens plus capped waits and inspection. This does
not reset the original start time or an explicitly supplied budget. Unknown
or unbounded opener timeouts retain the finite default allowance.

The deadline limits attempt admission, backoff and bounded inspection. Transport
openers must use bounded_timeout for each attempt's socket timeout. It is not a
hard wall-clock interruption: DNS resolution and a synchronous opener's multiple
socket operations can outlast that timeout. Streaming after acceptance uses the
caller's normal idle-timeout contract, not this pre-output retry deadline.

A 429 is replayed only when the strict structured verdict names a rate limit:
usage, quota, billing and authentication bodies stay terminal, because replaying
them cannot succeed. A retried rate limit spends the same attempt ceiling and the
same bounded Retry-After wait as a server rejection; it never starts a second
retry budget.
"""
from __future__ import annotations

import contextlib
import contextvars
import io
import http.client
import json
import math
import random
import re
import socket
import ssl
import threading
import time
import urllib.error
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .gateway_error_metadata import valid_error_metadata
from .sse_diagnostics import prefix_shape
from .upstream_error_policy import UpstreamFailure, terminal_usage_limit_error
from .upstream_stream_io import UpstreamClientDisconnected

BUDGET_CONFIG_KEY = "_ciel_transient_retry_budget"  # Compatibility; never stored.
PROVIDER = "cielairouter"
DEFAULT_RETRIES = 3
MAX_RETRIES = 10
DEFAULT_SECONDS = 60.0
_UNSET = object()
RETRYABLE_STATUSES = frozenset({500, 502, 503, 504, 529, 408})
# 429 is only replayable after a strict rate-limit verdict; see _retryable_verdict.
RATE_LIMIT_STATUSES = frozenset({429})
_INSPECTED_STATUSES = RETRYABLE_STATUSES | RATE_LIMIT_STATUSES
WAIT_SCHEDULE_SECONDS = (1.0, 2.0, 4.0, 8.0, 15.0)
MAX_WAIT_SECONDS = 30.0
MIN_WAIT_SECONDS = 0.25
MAX_ERROR_BYTES = 65536
PREFLIGHT_SECONDS = 0.1
# Deadline-bounded pre-output retry. With gateway_retries unset, a positive
# window replaces the fixed count: retries continue while the request is
# younger than the window, up to a ceiling, with equal-jitter backoff and the
# gateway's retry-after hint as a floor. 0 keeps the count-only policy. The
# value must come from the measured client tolerance (journal 2026-11-13
# fixes/router-upstream-error-retry), not from a guess.
# 420 s: team-lead ruling 2026-11-13 after the real claude.exe 2.1.323 probe
# (evidence/claude-window-330): a 330 s outage was answered at 332 s with no
# stream_idle_timeout; 13 retries; bounded by HARD_PREOUTPUT_SECONDS below.
RETRY_WINDOW_SECONDS = 420.0
# A retry window is subordinate to one router-arrival wall-clock bound. The
# window may start at the first upstream open (B4 catalog work does not spend
# it), but pre-output work can never continue beyond this request-age cap.
HARD_PREOUTPUT_SECONDS = 540.0
WINDOW_RETRY_CEILING = 20
MAX_WINDOW_RETRIES = 30
WINDOW_SCHEDULE_SECONDS = (1.0, 2.0, 4.0, 8.0, 15.0, 30.0)
JITTER_FRACTION = 0.5
MAX_HINT_WAIT_SECONDS = 120.0
GATEWAY_RETRY_AFTER_HEADER = "x-ciel-ai-router-retry-after-seconds"
_TERMINAL_MARKERS = ("quota", "billing", "usage_limit", "usage limit", "credit",
                     "payment", "authentication", "unauthorized", "permission",
                     "invalid_api_key", "invalid api key", "access denied")


@dataclass(slots=True)
class TransientRetryBudget:
    limit: int
    deadline_seconds: float
    clock: Callable[[], float] = time.monotonic
    attempts: int = 0
    waited_seconds: float = 0.0
    started: float | None = None
    operation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    opens: int = 0
    exposed: bool = False
    stopped: bool = False
    owner: int | None = None
    lock: Any = field(default_factory=threading.RLock, repr=False)
    # The caller's own read timeout, before bounded_timeout clipped it to the
    # retry deadline; an accepted response gets it back (release_read_timeout).
    requested_timeout: Any = field(default=_UNSET, repr=False)
    # Default request scopes start their allowance at the first upstream open,
    # after request-path normalization/catalog work. Explicitly supplied
    # budgets retain the caller's exact start and deadline.
    start_on_first_open: bool = field(default=False, repr=False)
    # Only default count scopes size their allowance from the first resolved
    # opener timeout. Explicitly supplied budgets retain their exact deadline.
    timeout_pending: bool = field(default=False, repr=False)
    # Deadline-bounded scope (RETRY_WINDOW_SECONDS): jittered backoff, hint floor.
    window_mode: bool = False
    # Router request-arrival clock, independent of the first-open re-base. Only
    # window scopes use it; fixed-count/explicit budgets keep their old contract.
    arrival_started: float | None = field(default=None, repr=False)
    hard_preoutput_seconds: float | None = field(default=None, repr=False)
    # A semantic recovery (gateway_retries=0) under an active dispatch budget
    # is a child of it (R1 amendment 2, lead-rulings-independent-review-sse-
    # retry-20261113.md): the parent's window end, arrival bound and only its
    # remaining attempts. The child's attempts write back to the parent when
    # its scope ends; prior_* keep reports at the request's totals.
    recovery_parent: Any = field(default=None, repr=False)
    prior_attempts: int = field(default=0, repr=False)
    prior_waited_seconds: float = field(default=0.0, repr=False)
    # How much of this child's attempts/waits the parent has been charged; a
    # late pre-output reopen after the scope exits is charged exactly once.
    charged_attempts: int = field(default=0, repr=False)
    charged_waited_seconds: float = field(default=0.0, repr=False)
    rand: Callable[[], float] = field(default=random.random, repr=False)
    # Why the last _schedule_wait admitted nothing (fixed tokens, for logs).
    stop_reason: str = ""
    hint_action: str = "none"
    # RuntimeActivityRepository.router_activity(event, provider, model, **fields):
    # publishes router.<event> on the event bus and writes router-activity.json.
    activity: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        ceiling = MAX_WINDOW_RETRIES if self.window_mode else MAX_RETRIES
        self.limit = max(0, min(ceiling, int(self.limit)))
        if self.started is None:
            self.started = self.clock()
        if self.window_mode:
            if self.arrival_started is None:
                self.arrival_started = self.started
            if self.hard_preoutput_seconds is None:
                self.hard_preoutput_seconds = HARD_PREOUTPUT_SECONDS

    def elapsed(self) -> float:
        return max(0.0, self.clock() - self.started)

    def hard_remaining(self) -> float:
        """Seconds left in the router-arrival cap; infinite outside window mode."""
        if self.arrival_started is None or self.hard_preoutput_seconds is None:
            return math.inf
        return max(0.0, self.arrival_started + self.hard_preoutput_seconds - self.clock())

    def start_at_first_open(self) -> None:
        """Re-base a default scope's allowance once, at its first upstream open.

        Request-path work before the opener (model resolution, catalog lookups,
        normalization) must not spend the message's pre-open allowance (B4,
        2026-11-13). Only while nothing was opened, retried, exposed or stopped.
        """
        with self.lock:
            if (self.start_on_first_open and self.opens == 0 and self.attempts == 0
                    and not self.exposed and not self.stopped):
                self.started = self.clock()
            self.start_on_first_open = False

    def remaining(self) -> float:
        return min(max(0.0, self.started + self.deadline_seconds - self.clock()),
                   self.hard_remaining())

    def deadline_reason(self) -> str:
        """Which bound ended admission: the retry window or the router-arrival cap."""
        window_left = max(0.0, self.started + self.deadline_seconds - self.clock())
        return "preoutput_bound" if self.hard_remaining() <= window_left else "deadline"

    def consume_external_retry(self) -> bool:
        """Reserve a reopen for an existing specialized recovery owner."""
        # There are no product callers today. If one is added and reserves the
        # first open, start the allowance here rather than at scope entry.
        self.start_at_first_open()
        with self.lock:
            if self.stopped or self.exposed or self.remaining() <= 0 or self.attempts >= self.limit:
                return False
            self.attempts += 1
            self.opens += 1
            return True

    def next_wait(self, error: urllib.error.HTTPError, raw: bytes,
                  headers: Mapping[str, Any] | None = None) -> float | None:
        empty_gateway_rejection = (not raw and error.code in {502, 503, 504}
                                   and not getattr(error, '_ciel_sse_error', False))
        if not empty_gateway_rejection and not _retryable_verdict(error.code, raw):
            self.stop_reason = "not_retryable"
            return None
        return self._schedule_wait(headers, raw)

    def timeout_wait(self, error: BaseException) -> float | None:
        if retry_handled(error) or not (_typed_open_timeout(error) or _unsent_open_failure(error) or _pre_response_disconnect(error)):
            self.stop_reason = "not_retryable"
            return None
        return self._schedule_wait()

    def _schedule_wait(self, headers: Mapping[str, Any] | None = None, raw: bytes = b"") -> float | None:
        with self.lock:
            if self.stopped or self.exposed:
                self.stop_reason = "stopped" if self.stopped else "exposed"
                return None
            if self.attempts >= self.limit:
                self.stop_reason = "attempt_ceiling"
                return None
            if self.remaining() <= 0:
                self.stop_reason = self.deadline_reason()
                return None
            if self.window_mode:
                wait = self._window_wait(headers, raw)
                if wait is None:
                    return None
            else:
                hinted = _retry_after(headers)
                scheduled = WAIT_SCHEDULE_SECONDS[min(self.attempts, len(WAIT_SCHEDULE_SECONDS) - 1)]
                wait = min(max(MIN_WAIT_SECONDS, hinted if hinted is not None else scheduled),
                           MAX_WAIT_SECONDS, self.remaining())
            # A wait that consumes all remaining time cannot admit an open;
            # do not count a retry that can never happen (socket rounding).
            if wait <= 0 or wait >= self.remaining():
                self.stop_reason = self.deadline_reason()
                return None
            self.attempts += 1
            self.waited_seconds += wait
            # A recovery child charges the request at once, wherever its read
            # happens (R1A2-F1: claude_router reads after the scope exits).
            _write_back(self)
            return wait

    def _window_wait(self, headers: Mapping[str, Any] | None, raw: bytes) -> float | None:
        """Equal-jitter backoff with the gateway/HTTP retry-after as a floor."""
        action, body_hint = _recovery_hint(raw)
        self.hint_action = action
        hints = [value for value in (_retry_after(headers), _gateway_retry_after(headers), body_hint)
                 if value is not None]
        hint = max(hints) if hints else None
        base = WINDOW_SCHEDULE_SECONDS[min(self.attempts, len(WINDOW_SCHEDULE_SECONDS) - 1)]
        try:
            draw = min(1.0, max(0.0, float(self.rand())))
        except (TypeError, ValueError):
            draw = 0.0  # an unusable random source keeps the full backoff
        wait = max(MIN_WAIT_SECONDS, base * (1.0 - JITTER_FRACTION * draw))
        if hint is not None:
            if hint >= self.remaining():
                self.stop_reason = "hint_beyond_deadline"
                return None
            wait = max(wait, min(hint, MAX_HINT_WAIT_SECONDS))
        return min(wait, max(MAX_WAIT_SECONDS, min(hint or 0.0, MAX_HINT_WAIT_SECONDS)))

    def request_attempts(self) -> int:
        """Retries of the whole client request: a recovery child adds its parent's."""
        return self.prior_attempts + self.attempts

    def note(self) -> str:
        return (f"Ciel Runtime retried this request {self.request_attempts()} time(s) over "
                f"{self.elapsed():.0f}s (waited {self.prior_waited_seconds + self.waited_seconds:.2f}s) "
                f"after transient upstream rejection or timeout before any output; "
                f"giving up ({self.stop_reason or 'stopped'}).")

    def exhaustion_suffix(self) -> str:
        """The bounded text appended to a relayed final error; fixed words, numbers and tokens only."""
        stop = _STOP_WORDS.get(self.stop_reason, "retry budget spent")
        return (f"Ciel Runtime retried {self.request_attempts()} time(s) over {self.elapsed():.0f}s "
                f"before any output; giving up: {stop}")


_STOP_WORDS = {
    "attempt_ceiling": "retry limit reached",
    "deadline": "retry window ended",
    "preoutput_bound": "request reached the router pre-output time limit",
    "hint_beyond_deadline": "gateway asked to wait longer than the retry window",
    "not_retryable": "error is not retryable",
    "stopped": "retry stopped",
    "exposed": "output already started",
}
_HTTP_STATUS_IN_MESSAGE = re.compile(r"\bHTTP (\d{3})\b")
_FINAL_MESSAGE_LIMIT = 2000


def _failure_code(status: Any, raw: bytes) -> Any:
    """An HTTP status, or for an SSE error the 3-digit status its message names, else its type token."""
    if isinstance(status, int) and status != 200 and not isinstance(status, bool):
        return status
    verdict = _error_only_verdict(raw) if raw else None
    if verdict is None:
        return None
    error_type, message = verdict
    match = _HTTP_STATUS_IN_MESSAGE.search(message)
    if match:
        return int(match.group(1))
    return error_type if error_type in _ERROR_TYPE_TOKENS else "other"


_ERROR_TYPE_TOKENS = frozenset({"api_error", "server_error", "timeout_error", "overloaded_error",
                                "rate_limit_error", "gateway_timeout", "upstream_error", "provider_error"})


_EXHAUSTED_STOPS = frozenset({"attempt_ceiling", "deadline", "hint_beyond_deadline", "preoutput_bound"})


def _stop_event_reason(budget: "TransientRetryBudget") -> str:
    return "pre_output_retries_exhausted" if budget.stop_reason in _EXHAUSTED_STOPS else "pre_output_retry_stopped"


def _exhausted_frame(error_bytes: bytes, budget: "TransientRetryBudget") -> bytes | None:
    """The final error frame with a bounded retry summary appended to its message; type kept."""
    value = _payload(error_bytes)
    detail = value.get("error") if value is not None else None
    if not isinstance(detail, dict) or not isinstance(detail.get("message"), str):
        return None
    suffix = budget.exhaustion_suffix()
    head = detail["message"][: max(0, _FINAL_MESSAGE_LIMIT - len(suffix) - 3)]
    detail["message"] = f"{head} ({suffix})" if head else suffix
    data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return b"event: error" + bytes([10]) + b"data: " + data + bytes([10, 10])


def transient_final_fields(error: BaseException | None) -> dict[str, Any]:
    """Retry summary for a caller's own final error activity (fixed tokens and counts).

    A caller that writes its own final "error" router activity after this
    executor raised merges these fields, so the last activity record still
    says the request was retried and why it stopped.
    """
    for candidate in (error, getattr(error, "__cause__", None)):
        summary = getattr(candidate, "_ciel_transient_retry_summary", None)
        if isinstance(summary, dict):
            return dict(summary)
    return {}


def _observe(budget: "TransientRetryBudget", publish: Any, model: str, event: str, *,
             kind: str, code: Any = None, wait: float | None = None, reason: str = "") -> None:
    """Report a retry or the final stop; an observation never fails the request."""
    fields: dict[str, Any] = {"kind": kind, "attempt": budget.request_attempts(),
                              "total": budget.prior_attempts + budget.limit,
                              "elapsed": round(budget.elapsed(), 2), "operation_id": budget.operation_id}
    if budget.window_mode:
        fields["window"] = budget.deadline_seconds
    if code is not None:
        fields["code"] = code
    if wait is not None:
        fields["wait"] = round(wait, 2)
    if event == "error":
        fields.update(reason=reason, attempts=budget.request_attempts(), stop=budget.stop_reason or "-")
    try:
        if budget.activity is not None:
            budget.activity(event, PROVIDER, model or None, **fields)
        elif publish is not None and event == "error":
            publish(level="error", category="router.error",
                    message="CielAiRouter pre-output retries exhausted", provider=PROVIDER, model=model, data=fields)
    except Exception:
        pass


_SCOPE: contextvars.ContextVar[TransientRetryBudget | None] = contextvars.ContextVar(
    "ciel_transient_request", default=None)


def configured_retry_limit(config: Any) -> int:
    """``gateway_retries`` as a bounded count: unset means 3, explicit 0 disables.

    Ambiguous values (booleans, all floats, infinities, text that is not an
    integer) fall back to the default instead of guessing a different count;
    anything above the cap is clamped so a menu or config edit cannot create an
    unbounded replay loop.
    """

    if not isinstance(config, dict):
        return DEFAULT_RETRIES
    value = config.get("gateway_retries")
    if value is None or isinstance(value, bool):
        return DEFAULT_RETRIES
    if not isinstance(value, (int, str)):
        return DEFAULT_RETRIES
    if isinstance(value, str) and not value.strip().lstrip("+-").isdecimal():
        return DEFAULT_RETRIES
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_RETRIES
    return max(0, min(MAX_RETRIES, number))


def owns_transient_retries(provider: str) -> bool:
    """Whether this provider uses the shared explicit-rejection retry policy."""
    return provider == PROVIDER


def _limit(config: Any, provider: str) -> int:
    if not owns_transient_retries(provider) or not isinstance(config, dict):
        return 0
    return configured_retry_limit(config)


def _explicit_retry_limit(config: Any) -> int | None:
    """The user's own integer ``gateway_retries``; None when unset or ambiguous."""
    value = config.get("gateway_retries") if isinstance(config, dict) else None
    if value is None or isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    if isinstance(value, str) and not value.strip().lstrip("+-").isdecimal():
        return None
    return configured_retry_limit(config)


def _default_budget(config: Any, provider: str, *,
                    arrival_started: float | None = None) -> TransientRetryBudget | None:
    limit = _limit(config, provider)
    if not limit:
        return None
    window = RETRY_WINDOW_SECONDS
    if (isinstance(window, (int, float)) and not isinstance(window, bool) and math.isfinite(window)
            and window > 0 and _explicit_retry_limit(config) is None):
        return TransientRetryBudget(WINDOW_RETRY_CEILING, float(window), window_mode=True,
                                    start_on_first_open=True, arrival_started=arrival_started)
    return TransientRetryBudget(limit, DEFAULT_SECONDS, start_on_first_open=True, timeout_pending=True)


@contextlib.contextmanager
def request_scope(config: Any = None, provider: str = PROVIDER, *,
                  budget: TransientRetryBudget | None = None,
                  arrival_started: float | None = None,
                  inherit_arrival: bool = True):
    """Start an independent operation; nested transports use budget_for.

    ``arrival_started`` is the router's monotonic request-arrival timestamp. It
    supplies the hard cap for a window scope while ``started`` may still re-base
    once at the first upstream open. Explicit budgets keep their own clocks.

    A nested scope inherits the active scope's arrival bound. A 0-retry
    semantic recovery under an active dispatch budget gets a recovery child
    (``_recovery_child``) that shares what the request has left.
    ``inherit_arrival=False`` marks the committed compaction final open
    (claude_router), whose documented contract is one independent open outside
    the dispatch's pre-output bound (R1 amendment, option B).
    """
    active = _SCOPE.get()
    if active is not None and active.owner is not None:
        raise RuntimeError("cannot replace an active transient retry owner")
    if inherit_arrival and arrival_started is None and active is not None:
        # A nested default scope (context recovery inside one dispatch) belongs
        # to the same client request, so it keeps the request's arrival time.
        arrival_started = active.arrival_started
    current = budget if budget is not None else _default_budget(
        config, provider, arrival_started=arrival_started)
    if (budget is None and current is None and inherit_arrival and active is not None
            and owns_transient_retries(provider)):
        current = _recovery_child(active)
    token = _SCOPE.set(current)
    try:
        yield current
    finally:
        _SCOPE.reset(token)
        if current is not None and current.recovery_parent is not None:
            _write_back(current)


def _recovery_child(parent: TransientRetryBudget) -> TransientRetryBudget:
    """R1 amendment 2: a semantic recovery shares the dispatch's remaining allowance.

    Same window mode, start and deadline (the same window end, no first-open
    re-base, no fresh window), the same arrival bound, and only the attempts the
    request has left. The terminal 400 that stopped the parent is the reason the
    recovery exists, so ``parent.stopped`` is ignored; committed output
    (``parent.exposed``) allows no retry. Retries still come only from
    _execute's classifiers; the recovery itself stays one body change.
    """
    with parent.lock:
        left = 0 if parent.exposed else max(0, parent.limit - parent.attempts)
        child = TransientRetryBudget(
            left, parent.deadline_seconds, clock=parent.clock, started=parent.started,
            window_mode=parent.window_mode, arrival_started=parent.arrival_started,
            hard_preoutput_seconds=parent.hard_preoutput_seconds, rand=parent.rand,
            activity=parent.activity, requested_timeout=parent.requested_timeout,
            recovery_parent=parent, prior_attempts=parent.request_attempts(),
            prior_waited_seconds=parent.prior_waited_seconds + parent.waited_seconds)
        # The constructor clamps to a ceiling; never above what is left.
        child.limit = min(child.limit, left)
        return child


def _write_back(child: TransientRetryBudget) -> None:
    """Charge the child's not-yet-charged retries to its parent, exactly once.

    Called when a retry is admitted and when the recovery scope ends, whatever
    ended it. Lock order is always child, then parent.
    """
    parent = child.recovery_parent
    if parent is None:
        return
    with child.lock:
        attempts = child.attempts - child.charged_attempts
        waited = child.waited_seconds - child.charged_waited_seconds
        child.charged_attempts = child.attempts
        child.charged_waited_seconds = child.waited_seconds
        if attempts or waited:
            with parent.lock:
                parent.attempts += attempts
                parent.waited_seconds += waited


def budget_for(config: Any, provider: str) -> TransientRetryBudget | None:
    active = _SCOPE.get()
    if _limit(config, provider):
        return active
    # A 0-retry semantic recovery under a dispatch: its recovery child.
    if active is not None and active.recovery_parent is not None and owns_transient_retries(provider):
        return active
    return None


def open_deadline(config: Any, provider: str) -> float | None:
    """time.monotonic() deadline for one upstream open under the arrival bound, or None.

    U1 (lead-rulings-independent-review-sse-retry-20261113.md, option 1): the
    provider opener reads this to bound a whole open (connect, TLS, request
    send, header wait) instead of each socket operation separately. Only a
    scope with a router-arrival bound publishes one; count-mode, other
    providers and no-scope opens get None and keep their identical call.
    """
    budget = budget_for(config, provider)
    if budget is None:
        return None
    left = budget.hard_remaining()
    if not math.isfinite(left):
        return None
    return time.monotonic() + max(0.0, left)


def bounded_timeout(config: Any, provider: str, timeout: Any) -> Any:
    """Clip an active retry attempt's socket timeout, never another provider's.

    None and invalid/nonpositive/nonfinite timeouts cannot remove the active
    budget's bound. Outside an enabled request scope, preserve the input exactly.
    This bounds individual socket operations, not DNS or an entire opener call.
    """
    budget = budget_for(config, provider)
    if budget is None:
        return timeout
    # Openers that bypass _execute (remote bridge, a selected gateway without
    # reselection) still start the allowance at their first open.
    budget.start_at_first_open()
    with budget.lock:
        budget.requested_timeout = timeout
        try:
            seconds = float(timeout) if not isinstance(timeout, bool) else math.nan
        except (TypeError, ValueError, OverflowError):
            seconds = math.nan
        if budget.timeout_pending:
            budget.timeout_pending = False
            # Fix the aggregate allowance once, without resetting its start.
            # The old 60s total clipped a configured 300s first open to 60s
            # and left zero retries (9489, 2026-10-23 13:18:02).
            if (not budget.stopped and not budget.exposed and budget.opens <= 1
                    and budget.remaining() > 0 and math.isfinite(seconds) and seconds > 0):
                total = ((budget.limit + 1) * (seconds + PREFLIGHT_SECONDS)
                         + budget.limit * MAX_WAIT_SECONDS)
                if math.isfinite(total):
                    budget.deadline_seconds = max(budget.deadline_seconds, total)
        remaining = budget.remaining()
        if remaining <= 0:
            budget.stopped = True
            budget.stop_reason = budget.deadline_reason()
            raise _mark(TimeoutError("transient retry deadline reached before open"), budget)
        # remaining() includes a window scope's router-arrival hard bound, so the
        # returned socket timeout can never carry an attempt past that bound.
        return min(seconds, remaining) if math.isfinite(seconds) and seconds > 0 else remaining


def consume_external_retry(config: Any, provider: str) -> bool:
    budget = budget_for(config, provider)
    return budget is not None and budget.consume_external_retry()


def retry_handled(error: BaseException) -> bool:
    return bool(getattr(error, "_ciel_transient_retry_handled", False)
                or getattr(error, "_ciel_transient_retry_exhausted", False))


def _strict_json(text: str) -> Any:
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate key")
            value[key] = item
        return value

    def constant(_value):
        raise ValueError("nonfinite number")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def _sse_payload(text: str) -> str | None:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.endswith("\n\n"):
        return None
    event, data, dispatched = None, [], False
    for line in text.split("\n"):
        if line == "":
            if data:
                dispatched = True
            elif event is not None:
                return None
            continue
        if line.startswith(":"):
            continue
        if dispatched:
            return None
        key, sep, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if not sep or key not in {"event", "data", "id", "retry"}:
            return None
        if key == "event":
            if event is not None or value != "error":
                return None
            event = value
        elif key == "data":
            data.append(value)
        elif key == "retry" and not value.isdecimal():
            return None
    return "\n".join(data) if dispatched else None


def _error_verdict(raw: bytes) -> tuple[str, str] | None:
    """Strict error-only JSON or one complete SSE error dispatch."""
    if not raw or len(raw) > MAX_ERROR_BYTES:
        return None
    try:
        text = raw.decode("utf-8")
        if not text.lstrip().startswith("{"):
            text = _sse_payload(text)
            if text is None:
                return None
        value = _strict_json(text)
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError):
        return None
    if not isinstance(value, dict):
        return None
    if (set(value) - {"type", "error", "request_id", "diagnostics", "recovery_hint"}
            or value.get("type", "error") != "error" or not valid_error_metadata(value)):
        return None
    detail = value.get("error")
    if not isinstance(detail, dict) or set(detail) - {"type", "message", "code", "param"}:
        return None
    error_type = detail.get("type", detail.get("code"))
    message = detail.get("message")
    if not isinstance(error_type, str) or not error_type.strip() or not isinstance(message, str):
        return None
    if any(item is not None and not isinstance(item, str) for item in detail.values()):
        return None
    if "request_id" in value and not isinstance(value["request_id"], str):
        return None
    return error_type, message


# Top-level fields that carry generation in Anthropic, OpenAI Chat/Responses or
# Gemini payloads. A body carrying any of them is not error-only.
_GENERATION_KEYS = frozenset({"content", "content_block", "delta", "index", "usage", "choices",
                              "output", "output_text", "response", "item", "candidates",
                              "tool_calls", "tool_use", "function_call"})


def _error_only_verdict(raw: bytes) -> tuple[str, str] | None:
    """An error-only body for the retry decision; unknown siblings are tolerated.

    Same framing and JSON strictness as _error_verdict (one complete error
    dispatch or one JSON object; no duplicate keys or non-finite numbers), a
    top-level ``type`` that is absent or "error", no generation field, and an
    ``error`` object whose type (or code) and message are strings. Unknown
    fields at either level do not make the body unrecognized: gateway builds
    add them (live 2026-11-12: two top-level and one error-level field on a
    pre-output ``server_error``, committed with zero retries). Published
    metadata contents are never retry authority, so they are not validated
    here; _error_verdict still validates them for the strict contract.
    """
    value = _payload(raw)
    if value is None or value.get("type", "error") != "error":
        return None
    if _GENERATION_KEYS.intersection(value) or not isinstance(value.get("message", ""), str):
        return None
    detail = value.get("error")
    if not isinstance(detail, dict):
        return None
    error_type = detail.get("type", detail.get("code"))
    message = detail.get("message")
    if not isinstance(error_type, str) or not error_type.strip() or not isinstance(message, str):
        return None
    if any(detail.get(key) is not None and not isinstance(detail.get(key), str)
           for key in ("type", "message", "code", "param")):
        return None
    return error_type, message


_GENERATION_EVENTS = frozenset({"message_start", "content_block_start", "content_block_delta",
                                "content_block_stop", "message_delta", "message_stop"})


def _generation_frame(frame: bytes) -> bool:
    """One SSE frame that is ordinary output (its event or JSON type names a generation event)."""
    event, data = None, []
    try:
        lines = frame.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    except UnicodeDecodeError:
        return False
    for line in lines:
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if event in _GENERATION_EVENTS:
        return True
    try:
        value = json.loads("\n".join(data)) if data else None
    except (ValueError, RecursionError):
        return False
    return isinstance(value, dict) and value.get("type") in _GENERATION_EVENTS


def _verdict_kind(raw: bytes) -> str:
    """Fixed token for logs: which verdict recognized this error body."""
    if _error_verdict(raw) is not None:
        return "strict"
    return "tolerant" if _error_only_verdict(raw) is not None else "none"


def _retryable_verdict(status: int, raw: bytes) -> bool:
    verdict = _error_only_verdict(raw)
    if status not in _INSPECTED_STATUSES or verdict is None:
        return False
    error_type, message = verdict
    folded = raw.decode("utf-8").casefold()
    if terminal_usage_limit_error(message) or any(word in folded for word in _TERMINAL_MARKERS):
        return False
    failure = UpstreamFailure(PROVIDER, "", status=status, error_type=error_type,
                              message=message, output_started=False)
    if status in RATE_LIMIT_STATUSES:
        # Only a declared rate limit authorizes replay. The terminal-marker
        # check above already removed usage, quota, billing and auth bodies,
        # and a provider label that classifies as anything else stays terminal.
        return failure.rate_limited
    return failure.retryable


def _sse_error_status(budget: "TransientRetryBudget", raw: bytes) -> int:
    """The status an HTTP-200 SSE error frame is classified at.

    Normally 503. Under the deadline window, a declared rate limit with no
    quota/billing/credit/usage/auth marker is classified as 429 so it can be
    retried like an open-time 429 (design point 3, 2026-11-13); terminal
    markers keep it terminal either way.
    """
    if budget.window_mode and _retryable_verdict(429, raw):
        return 429
    return 503


def _payload(raw: bytes) -> dict | None:
    """The JSON object of one complete error body/dispatch, or None."""
    if not raw or len(raw) > MAX_ERROR_BYTES:
        return None
    try:
        text = raw.decode("utf-8")
        if not text.lstrip().startswith("{"):
            text = _sse_payload(text)
            if text is None:
                return None
        value = _strict_json(text)
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _recovery_hint(raw: bytes) -> tuple[str, float | None]:
    """(action, retry_after_seconds) from the gateway's published recovery hint.

    Only a hint that passes the published schema counts; the action is one
    of four fixed tokens, so it is safe to log. A hint never makes a terminal
    error retryable; it only sets a minimum wait.
    """
    value = _payload(raw)
    if value is None:
        return "none", None
    candidates = [value.get("recovery_hint")]
    diagnostics = value.get("diagnostics")
    if isinstance(diagnostics, dict):
        candidates.append(diagnostics.get("recovery"))
    for hint in candidates:
        if isinstance(hint, dict) and valid_error_metadata({"recovery_hint": hint}):
            seconds = hint.get("retry_after_seconds")
            return hint["action"], float(seconds) if isinstance(seconds, int) and seconds > 0 else None
    return "none", None


def _gateway_retry_after(headers: Mapping[str, Any] | None) -> float | None:
    """The gateway's mirrored hint header (positive integer seconds, at most 3600)."""
    try:
        value = headers.get(GATEWAY_RETRY_AFTER_HEADER) if headers else None
        if value is None or not str(value).strip().isdecimal():
            return None
        seconds = int(str(value).strip())
    except (AttributeError, TypeError, ValueError):
        return None
    return float(seconds) if 0 < seconds <= 3600 else None


def _retry_after(headers: Mapping[str, Any] | None) -> float | None:
    try:
        value = (headers.get("retry-after") or headers.get("Retry-After")) if headers else None
        seconds = float(str(value).strip())
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


def _close(response: Any) -> None:
    try:
        response.close()
    except Exception:
        pass


def _mark(error: BaseException, budget: TransientRetryBudget) -> BaseException:
    error._ciel_transient_retry_handled = True
    # Older guards only check exhausted: terminal/malformed also forbid replay.
    error._ciel_transient_retry_exhausted = True
    # A note claims retries; a failure that never retried (0 attempts, e.g. a
    # terminal first answer or a deadline before the first open) carries none.
    # A recovery child counts the whole request's retries (R1 amendment 2).
    if budget.request_attempts() > 0:
        error._ciel_transient_retry_note = budget.note()
    return error


class _ReplayResponse:
    """Untouched read-ahead bytes followed by the original response."""
    def __init__(self, response: Any, prefix: bytes, budget: TransientRetryBudget):
        self._response = response
        self._prefix = io.BytesIO(prefix)
        self._budget = budget
        self.closed = False
        self._defer_exposure = False

    def __getattr__(self, name):
        return getattr(self._response, name)

    def _expose(self, data):
        if data and not self._defer_exposure:
            self._budget.exposed = True
        return data

    def read(self, size=-1):
        prefix = self._prefix.read(size)
        if size is None or size < 0:
            return self._expose(prefix + self._response.read())
        return self._expose(prefix + (self._response.read(size - len(prefix)) if len(prefix) < size else b""))

    def read1(self, size=-1):
        prefix = self._prefix.read(size)
        if prefix:
            return self._expose(prefix)
        reader = getattr(self._response, "read1", self._response.read)
        return self._expose(reader(size))

    def readline(self, size=-1):
        prefix = self._prefix.readline(size)
        if prefix.endswith(b"\n") or (size is not None and size >= 0 and len(prefix) >= size):
            return self._expose(prefix)
        rest = self._response.readline() if size is None or size < 0 else self._response.readline(size - len(prefix))
        return self._expose(prefix + rest)

    def readinto(self, buffer):
        data = self.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)

    def readinto1(self, buffer):
        data = self.read1(len(buffer))
        buffer[:len(data)] = data
        return len(data)

    def readlines(self, hint=-1):
        lines, size = [], 0
        for line in self:
            lines.append(line)
            size += len(line)
            if hint > 0 and size >= hint:
                break
        return lines

    def __iter__(self):
        return self

    def __next__(self):
        line = self.readline()
        if not line:
            raise StopIteration
        return line

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self):
        if not self.closed:
            self.closed = True
            self._prefix.close()
            _close(self._response)


def _check_cancelled(cancelled):
    if cancelled is not None and cancelled():
        raise UpstreamClientDisconnected("downstream client disconnected during transient retry")


class _FailAfter:
    """Raise a deferred upstream read failure once the bytes read before it are delivered."""
    def __init__(self, response, error):
        self._response, self._error = response, error

    def __getattr__(self, name):
        return getattr(self._response, name)

    def _raise(self, *_args, **_kwargs):
        raise self._error

    read = read1 = readline = _raise

    def close(self):
        _close(self._response)


class _PreOutputSSE(_ReplayResponse):
    """Read-time rejection guard, bounded by bytes and the existing idle timeout.

    Headers may already be downstream; no generation event is exposed before
    this guard commits. Unknown/mixed events commit unchanged. Read exceptions
    never authorize replay; held bytes are delivered before such a failure. Held
    bytes are returned on their own, so a sized read never couples them with an
    upstream read that may fail. A relay may close this response from another
    thread while a retry waits; no reopen starts, and none survives, after close.
    """
    def __init__(self, response, budget, reopen, log, publish, sleep, cancelled, request, reselect, model):
        super().__init__(response, b"", budget)
        self._swap = threading.Lock()
        response._defer_exposure = True
        self._pending = bytearray()
        self._committed = False
        self._ciel_preoutput_diagnostic = {"state": "armed", "operation_id": budget.operation_id}
        self._reopen = reopen
        self._log, self._publish, self._sleep = log, publish, sleep
        self._cancelled, self._request, self._reselect, self._model = cancelled, request, reselect, model

    @staticmethod
    def _heartbeat(frame):
        lines = [line for line in frame.split(bytes([10])) if line and not line.startswith(b":")]
        if not lines:
            return True
        if len(lines) != 2 or lines[0] != b"event: ping" or not lines[1].startswith(b"data:"):
            return False
        try:
            return _strict_json(lines[1][5:].strip().decode("utf-8")) == {"type": "ping"}
        except (ValueError, UnicodeDecodeError, TypeError, RecursionError):
            return False

    def _check_open(self):
        if self.closed:
            raise UpstreamClientDisconnected("response closed during pre-output transient retry")

    def close(self):
        with self._swap:
            super().close()

    def _accept(self, reason="accepted", verdict=None):
        # Only fixed tokens, field types and lengths; never arbitrary upstream
        # strings. Unknown streams must still be relayed, not replayed.
        self._ciel_preoutput_diagnostic = {"state": "committed", "reason": reason,
                                          "operation_id": self._budget.operation_id}
        shape = json.dumps(prefix_shape(bytes(self._pending)), separators=(",", ":"))
        self._log("INFO", f"cielairouter_preoutput_commit operation_id={self._budget.operation_id} "
                  f"reason={reason} held_bytes={len(self._pending)} "
                  f"attempts={self._budget.attempts} opens={self._budget.opens} "
                  + (f"verdict={verdict} " if verdict else "") + f"shape={shape}")
        self._committed = True
        self._prefix = io.BytesIO(bytes(self._pending))
        self._pending.clear()
        self._response._defer_exposure = False
        if self._budget.window_mode:
            # Past the first frame the stream is read under the caller's own
            # timeout again, not the pre-output arrival bound.
            release_read_timeout(self._response, self._budget)

    def _clip_preoutput_read(self) -> bool:
        """Clip a window scope's pre-output socket read to the arrival bound.

        False when the bound is already spent. Outside window mode the read
        keeps the caller's timeout, exactly as before.
        """
        if not self._budget.window_mode:
            return True
        left = self._budget.hard_remaining()
        if left <= 0:
            return False
        sock = _socket(self._response)
        requested = self._budget.requested_timeout
        try:
            limit = float(requested) if requested is not _UNSET and requested is not None else math.inf
        except (TypeError, ValueError, OverflowError):
            limit = math.inf
        if not math.isfinite(limit) or limit <= 0:
            limit = math.inf
        if sock is not None and hasattr(sock, "settimeout"):
            try:
                sock.settimeout(min(limit, left))
            except (OSError, ValueError):
                pass
        return True

    def _preoutput_bound_reached(self):
        """The arrival bound ended the wait for the first frame: an honest final error."""
        budget = self._budget
        budget.stopped = True
        budget.stop_reason = "preoutput_bound"
        self._log("WARN", f"cielairouter_preoutput_bound operation_id={budget.operation_id} "
                  f"attempts={budget.attempts} opens={budget.opens} "
                  f"arrival_elapsed={budget.hard_preoutput_seconds - budget.hard_remaining():.2f}s "
                  f"held_bytes={len(self._pending)}")
        if budget.request_attempts() > 0:
            _observe(budget, self._publish, self._model, "error", kind="preoutput_bound",
                     reason=_stop_event_reason(budget))
        message = (f"Ciel Runtime stopped waiting for the first upstream output: the router pre-output "
                   f"time limit ({budget.hard_preoutput_seconds:.0f}s since the request arrived) was reached")
        message += (f" ({budget.exhaustion_suffix()})." if budget.request_attempts() > 0
                    else " (0 retries).")
        data = json.dumps({"type": "error", "error": {"type": "api_error", "message": message}},
                          separators=(",", ":")).encode("utf-8")
        _close(self._response)
        self._response = _ReplayResponse(io.BytesIO(b""), b"", budget)
        self._pending = bytearray(b"event: error" + bytes([10]) + b"data: " + data + bytes([10, 10]))
        self._accept("preoutput_bound")

    def _inspect(self):
        if self._committed:
            return
        while len(self._pending) < MAX_ERROR_BYTES:
            _check_cancelled(self._cancelled)
            if not self._clip_preoutput_read():
                self._preoutput_bound_reached()
                return
            try:
                chunk = self._response.read1(min(8192, MAX_ERROR_BYTES - len(self._pending)))
            except Exception as error:
                if (self._budget.window_mode and _typed_open_timeout(error)
                        and self._budget.hard_remaining() <= 0.05):
                    self._preoutput_bound_reached()
                    return
                if not self._pending:
                    raise
                self._accept("read_failure_with_prefix")
                self._response = _FailAfter(self._response, error)
                return
            if not chunk:
                self._accept("eof")
                return
            self._pending.extend(chunk)
            raw = bytes(self._pending).replace(bytes([13, 10]), bytes([10])).replace(bytes([13]), bytes([10]))
            frames = raw.split(bytes([10, 10]))
            meaningful = [f for f in frames[:-1] if not self._heartbeat(f)]
            if not meaningful:
                continue
            if len(meaningful) != 1 or frames[-1]:
                self._accept("multiple_frames" if len(meaningful) != 1 else "trailing_bytes")
                return
            error_bytes = meaningful[0] + bytes([10, 10])
            late_status = _sse_error_status(self._budget, error_bytes)
            if not _retryable_verdict(late_status, error_bytes):
                if _error_only_verdict(error_bytes) is not None:
                    reason = "terminal_verdict"
                elif _generation_frame(error_bytes):
                    reason = "non_error_frame"
                else:
                    reason = "unrecognized_frame"
                self._accept(reason, verdict=_verdict_kind(error_bytes))
                return
            error = urllib.error.HTTPError(getattr(self._request, "full_url", ""), late_status,
                                           "Delayed pre-output SSE error", self.headers, io.BytesIO(error_bytes))
            wait = self._budget.next_wait(error, error_bytes, self.headers)
            if wait is None:
                self._budget.stopped = True
                self._log("WARN", f"cielairouter_late_sse_stopped operation_id={self._budget.operation_id} "
                          f"attempts={self._budget.attempts} reason=budget_exhausted "
                          f"stop={self._budget.stop_reason or '-'} elapsed={self._budget.elapsed():.2f}s "
                          f"verdict={_verdict_kind(error_bytes)} hint={_recovery_hint(error_bytes)[0]}")
                if self._budget.request_attempts() > 0:
                    final = _exhausted_frame(error_bytes, self._budget)
                    if final is not None:
                        self._pending = bytearray(final)
                    _observe(self._budget, self._publish, self._model, "error", kind="late_sse_error",
                             code=_failure_code(None, error_bytes), reason=_stop_event_reason(self._budget))
                self._accept("budget_exhausted")
                return
            _close(self._response)
            self._log("WARN", f"cielairouter_late_sse_retry operation_id={self._budget.operation_id} "
                      f"attempt={self._budget.attempts}/{self._budget.limit} wait={wait:.2f}s "
                      f"message_start=False output_exposed=False verdict={_verdict_kind(error_bytes)} "
                      f"hint={_recovery_hint(error_bytes)[0]} elapsed={self._budget.elapsed():.2f}s")
            if self._budget.activity is not None:
                _observe(self._budget, None, self._model, "retry", kind="late_sse_error",
                         code=_failure_code(None, error_bytes), wait=wait)
            elif self._publish is not None:
                self._publish(level="warn", category="router.retry", message="Pre-output upstream SSE error; retrying",
                    provider=PROVIDER, model=self._model, data={"kind": "late_sse_error",
                    "attempt": self._budget.request_attempts(),
                    "total": self._budget.prior_attempts + self._budget.limit,
                    "operation_id": self._budget.operation_id})
            _check_cancelled(self._cancelled)
            self._check_open()
            self._sleep(wait)
            _check_cancelled(self._cancelled)
            self._check_open()
            if self._reselect is not None:
                self._reselect()
            try:
                with self._budget.lock:
                    if self._budget.owner is not None:
                        raise RuntimeError("pre-output retry cannot overlap an active opener")
                    self._budget.owner = threading.get_ident()
                # The reopen runs under this response's own budget even when the
                # caller reads after its request_scope exited (claude_router's
                # recovery reads, R1A2-F1): bounded_timeout and the U1 open
                # deadline then clip it to the request's remaining allowance.
                token = _SCOPE.set(self._budget)
                try:
                    response = _execute(self._reopen, self._budget, self._log, self._publish, self._sleep,
                                        self._cancelled, self._request, self._reselect, self._model)
                finally:
                    _SCOPE.reset(token)
                    with self._budget.lock:
                        self._budget.owner = None
            except urllib.error.HTTPError as failure:
                body = failure.read(MAX_ERROR_BYTES + 1)
                _close(failure)
                if _error_only_verdict(body) is None:
                    raise
                if body.lstrip().startswith(b"{"):
                    body = b"event: error" + bytes([10]) + b"data: " + body + bytes([10, 10])
                if self._budget.request_attempts() > 0 and self._budget.stop_reason in _EXHAUSTED_STOPS:
                    body = _exhausted_frame(body, self._budget) or body
                self._response = _ReplayResponse(io.BytesIO(b""), b"", self._budget)
                self._pending = bytearray(body)
                self._accept("reopen_http_error")
                return
            with self._swap:
                if self.closed:
                    _close(response)
                    raise UpstreamClientDisconnected("response closed during pre-output transient reopen")
                self._response = response
            self._pending.clear()
            self._committed = True
            self._ciel_preoutput_diagnostic = {"state": "reopened", "operation_id": self._budget.operation_id}
            return
        self._accept("byte_bound")

    def _held(self, size):
        left = len(self._prefix.getbuffer()) - self._prefix.tell()
        if left <= 0 or (size is None or size < 0) and not isinstance(self._response, _FailAfter):
            return None
        return self._expose(self._prefix.read(left if size is None or size < 0 else min(size, left)))

    def read(self, size=-1):
        if size == 0:
            return b""
        self._inspect()
        held = self._held(size)
        return held if held is not None else super().read(size)

    def read1(self, size=-1):
        if size == 0:
            return b""
        self._inspect()
        held = self._held(size)
        return held if held is not None else super().read1(size)

    def readline(self, size=-1):
        if size == 0:
            return b""
        self._inspect()
        return super().readline(size)


def _socket(response):
    raw = getattr(getattr(response, "fp", None), "raw", None)
    return getattr(raw, "_sock", None) or getattr(response, "sock", None)


def _ready_bytes(response, sock) -> int:
    """Bytes read1 can consume without a blocking framing read; -1 unknown."""
    if isinstance(response, io.BytesIO) and sock is None:
        return len(response.getbuffer()) - response.tell()
    if sock is None:
        return -1
    fp = getattr(response, "fp", None)
    peek = getattr(fp, "peek", None)
    if peek is None:
        return -1
    old = sock.gettimeout()
    try:
        sock.setblocking(False)
        available = peek(MAX_ERROR_BYTES + 1)
    except (OSError, ValueError):
        return -1
    finally:
        sock.settimeout(old)
    if not available:
        return 0
    if not getattr(response, "chunked", False):
        return len(available)
    # HTTPResponse.read1 may make several blocking reads for a chunk header,
    # separator and trailer. Only enter it when its entire next frame is in
    # the BufferedReader; otherwise leave every byte untouched downstream.
    left = getattr(response, "chunk_left", None)
    if left:
        return min(left, len(available))
    offset = 2 if left == 0 else 0
    end = available.find(b"\r\n", offset)
    if end < 0:
        return 0
    try:
        length = int(available[offset:end].split(b";", 1)[0], 16)
    except ValueError:
        return 0
    if length == 0:
        return -2 if b"\r\n\r\n" in available[end:] else 0
    if length < 0 or len(available) < end + 2 + length:
        return 0
    return length


def _read_error(error, budget, cancelled):
    """Inspect bounded ready bytes, retaining the original stream if partial."""
    prefix = bytearray()
    sock = _socket(error.fp)
    response = error.fp
    if isinstance(response, io.BytesIO) and response.tell() == len(response.getbuffer()):
        return b'', True
    reader = getattr(response, "read1", None)
    if reader is None and isinstance(response, io.BytesIO):
        reader = response.read
    if reader is None:
        return b"", False
    end = min(budget.clock() + PREFLIGHT_SECONDS, budget.started + budget.deadline_seconds)
    wall_end = time.monotonic() + min(PREFLIGHT_SECONDS, budget.remaining())
    while len(prefix) <= MAX_ERROR_BYTES and budget.clock() < end and time.monotonic() < wall_end:
        _check_cancelled(cancelled)
        if getattr(response, "length", None) == 0 or getattr(response, "fp", True) is None:
            return bytes(prefix), True
        ready = _ready_bytes(response, sock)
        if ready == -2:
            reader(1)
            return bytes(prefix), True
        if ready < 0 or (ready == 0 and sock is None):
            break
        if ready == 0:
            time.sleep(min(0.005, max(0.0, wall_end - time.monotonic())))
            continue
        size = min(ready, MAX_ERROR_BYTES + 1 - len(prefix))
        try:
            chunk = reader(size)
        except Exception as exc:
            partial = getattr(exc, "partial", b"")
            if isinstance(partial, bytes):
                prefix.extend(partial)
            return bytes(prefix), False
        if not chunk:
            break
        prefix.extend(chunk)
        if len(prefix) > MAX_ERROR_BYTES:
            return bytes(prefix), False
        if isinstance(response, io.BytesIO):
            complete = response.tell() == len(response.getbuffer())
        else:
            complete = getattr(response, "length", None) == 0 or getattr(response, "fp", True) is None
        if complete:
            return bytes(prefix), True
    return bytes(prefix), False


def _preflight(response, budget, cancelled):
    headers = getattr(response, "headers", {}) or {}
    content_type = str(headers.get("Content-Type", headers.get("content-type", ""))).lower()
    status = getattr(response, "status", 200)
    if not isinstance(status, int) or not 200 <= status < 300 or "text/event-stream" not in content_type:
        return _ReplayResponse(response, b"", budget), None
    reader = getattr(response, "read1", None)
    if reader is None:
        if isinstance(response, io.BytesIO):
            reader = response.read
        else:
            return _ReplayResponse(response, b"", budget), None
    prefix = bytearray()
    end = min(budget.clock() + PREFLIGHT_SECONDS, budget.started + budget.deadline_seconds)
    wall_end = time.monotonic() + min(PREFLIGHT_SECONDS, budget.remaining())
    sock = _socket(response)
    try:
        while len(prefix) < MAX_ERROR_BYTES and budget.clock() < end and time.monotonic() < wall_end:
            _check_cancelled(cancelled)
            ready = _ready_bytes(response, sock)
            if ready == -2:
                reader(1)
                break
            if ready < 0 or (ready == 0 and sock is None):
                break
            if ready == 0:
                time.sleep(min(0.005, max(0.0, wall_end - time.monotonic())))
                continue
            chunk = reader(min(ready, MAX_ERROR_BYTES - len(prefix)))
            if not chunk:
                break
            prefix.extend(chunk)
            normalized = bytes(prefix).replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            if b"\n\n" in normalized:
                if _error_verdict(bytes(prefix)) is not None:
                    return response, bytes(prefix)
                frames = normalized.split(b"\n\n")
                if any(any(line and not line.startswith(b":") for line in frame.split(b"\n")) for frame in frames[:-1]):
                    break
    except BaseException:
        _close(response)
        raise
    return _ReplayResponse(response, bytes(prefix), budget), None


def release_read_timeout(response: Any, budget: TransientRetryBudget | None) -> Any:
    """Give an accepted response the caller's read timeout back.

    bounded_timeout clips each socket operation to the retry deadline so a
    stalled open cannot outlive it. That deadline bounds acceptance only: once
    a response is accepted the stream is read under the caller's own timeout,
    exactly as when no retry scope is active.
    """
    if response is None or budget is None:
        return response
    requested = budget.requested_timeout
    if requested is _UNSET or isinstance(requested, bool):
        return response
    if requested is not None:
        try:
            requested = float(requested)
        except (TypeError, ValueError, OverflowError):
            return response
        if not math.isfinite(requested) or requested <= 0:
            return response
    sock = _socket(response)
    if sock is not None and hasattr(sock, "settimeout"):
        try:
            sock.settimeout(requested)
        except (OSError, ValueError):
            pass
    return response


def open_with_transient_retry(open_once: Callable[[], Any], *, provider: str,
                              config: dict[str, Any], log: Callable[[str, str], None],
                              publish: Callable[..., Any] | None = None,
                              sleep: Callable[[float], None] = time.sleep,
                              cancelled: Callable[[], bool] | None = None,
                              request: Any = None, reselect: Callable[[], Any] | None = None,
                              model: str = "", activity: Callable[..., Any] | None = None) -> Any:
    """Open under the request's transient budget.

    ``activity`` is RuntimeActivityRepository.router_activity: each retry and
    the final stop are reported through it (event bus router.retry /
    router.error, router-activity.json for the statusline) instead of
    ``publish``.
    """
    budget = budget_for(config, provider)
    if not _limit(config, provider) and (budget is None or budget.recovery_parent is None):
        _check_cancelled(cancelled)
        if provider == PROVIDER:
            log("INFO", "cielairouter_preoutput_guard state=bypassed reason=retries_disabled")
        return open_once()
    # A recovery child (gateway_retries=0 under a dispatch) runs the executor
    # below with the request's remaining allowance (R1 amendment 2).
    if budget is None:
        with request_scope(config, provider):
            return open_with_transient_retry(open_once, provider=provider, config=config,
                log=log, publish=publish, sleep=sleep, cancelled=cancelled,
                request=request, reselect=reselect, model=model, activity=activity)
    if activity is not None and budget.activity is None:
        budget.activity = activity
    with budget.lock:
        if budget.owner is not None:
            if budget.owner != threading.get_ident():
                raise RuntimeError("transient retry scope cannot open concurrently")
            _check_cancelled(cancelled)
            if budget.remaining() <= 0:
                budget.stopped = True
                raise _mark(TimeoutError("transient retry deadline reached before nested open"), budget)
            return open_once()
        if budget.exposed or budget.stopped:
            raise _mark(RuntimeError("transient retry operation is already committed or stopped"), budget)
        if budget.opens:
            if budget.attempts >= budget.limit or budget.remaining() <= 0:
                budget.stopped = True
                raise _mark(RuntimeError("transient retry allowance exhausted"), budget)
            budget.attempts += 1
        budget.owner = threading.get_ident()
    try:
        return _execute(open_once, budget, log, publish, sleep, cancelled, request, reselect, model)
    finally:
        with budget.lock:
            budget.owner = None


def _typed_open_timeout(error: BaseException) -> bool:
    return isinstance(error, TimeoutError) or (
        isinstance(error, urllib.error.URLError) and isinstance(error.reason, TimeoutError))


def _pre_response_disconnect(error: BaseException) -> bool:
    """Typed disconnect during open; no response has been exposed to the CLI.

    Like an open timeout, this can duplicate upstream computation or billing;
    it does not prove the request was unsent. Read-time failures stay excluded.
    """
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    # R3 (lead-rulings-independent-review-sse-retry-20261113.md): the live :9524
    # log showed WinError 10053 (ConnectionAbortedError) inside urlopen before any
    # header, then a 502 with zero retries. Also admitted: a broken pipe while the
    # request is sent and a TLS EOF during connect/handshake. Typed only; a
    # string mentioning these errors is never classified.
    return isinstance(reason, (ConnectionResetError, http.client.RemoteDisconnected,
                               ConnectionAbortedError, BrokenPipeError, ssl.SSLEOFError))


def _unsent_open_failure(error: BaseException) -> bool:
    """Connection setup failed before any request byte could reach the upstream."""
    reason = error
    if isinstance(error, urllib.error.URLError) and not isinstance(error, urllib.error.HTTPError):
        reason = error.reason
    return isinstance(reason, (socket.gaierror, ConnectionRefusedError))


def _execute(open_once, budget, log, publish, sleep, cancelled, request, reselect, model):
    last = None
    while True:
        _check_cancelled(cancelled)
        budget.start_at_first_open()
        with budget.lock:
            if budget.remaining() <= 0:
                budget.stopped = True
                budget.stop_reason = budget.deadline_reason()
                if budget.opens == 0:
                    stage = ("router pre-output time limit" if budget.stop_reason == "preoutput_bound"
                             else "request deadline")
                    where = ("the recovery open" if budget.recovery_parent is not None
                             else "the first upstream open")
                    # A recovery child names the request's retries, never its own alone.
                    raise _mark(RuntimeError(
                        f"{stage} passed before {where} ({budget.request_attempts()} attempts)"), budget)
                raise _mark(last or RuntimeError("transient retry deadline reached"), budget)
            if budget.opens >= budget.limit + 1:
                budget.stopped = True
                budget.stop_reason = "attempt_ceiling"
                raise _mark(last or RuntimeError("transient retry attempt limit reached"), budget)
            budget.opens += 1
        _close(last)
        error, response = None, None
        try:
            response = open_once()
        except urllib.error.HTTPError as caught:
            error = caught
        except BaseException as caught:
            if not (_typed_open_timeout(caught) or _unsent_open_failure(caught) or _pre_response_disconnect(caught)):
                budget.stopped = True
                _mark(caught, budget)
                raise
            error = caught
        if cancelled is not None and cancelled():
            _close(error if error is not None else response)
            budget.stopped = True
            _check_cancelled(lambda: True)
        if error is not None and retry_handled(error):
            budget.stopped = True
            raise error
        if isinstance(error, urllib.error.HTTPError):
            try:
                raw, complete = _read_error(error, budget, cancelled)
            except BaseException:
                _close(error)
                budget.stopped = True
                raise
            if not complete:
                budget.stopped = True
                if budget.remaining() <= 0 and not budget.stop_reason:
                    # No time was left to inspect the body: name the bound that ended it.
                    budget.stop_reason = budget.deadline_reason()
                replay = urllib.error.HTTPError(error.url, error.code, error.msg, error.hdrs,
                                                _ReplayResponse(error, raw, budget))
                raise _mark(replay, budget)
            _close(error)
            error = urllib.error.HTTPError(error.url, error.code, error.msg, error.hdrs, io.BytesIO(raw))
        elif error is None:
            try:
                response, raw = _preflight(response, budget, cancelled)
            except BaseException as caught:
                budget.stopped = True
                _mark(caught, budget)
                raise
            if raw is None:
                accepted = release_read_timeout(response, budget)
                headers = getattr(response, "headers", {})
                ctype = str(headers.get("Content-Type", headers.get("content-type", ""))).lower()
                if isinstance(accepted, _ReplayResponse) and "text/event-stream" in ctype:
                    log("INFO", f"cielairouter_preoutput_guard operation_id={budget.operation_id} state=armed")
                    return _PreOutputSSE(accepted, budget, open_once, log, publish, sleep, cancelled, request, reselect, model)
                log("INFO", f"cielairouter_preoutput_guard operation_id={budget.operation_id} "
                    "state=not_armed reason=not_event_stream")
                return accepted
            headers = getattr(response, "headers", {})
            status = getattr(response, "status", 200)
            url = getattr(response, "url", getattr(request, "full_url", ""))
            _close(response)
            error = urllib.error.HTTPError(url, status, "Upstream SSE error", headers, io.BytesIO(raw))
            error._ciel_sse_error = True
        last = error
        if isinstance(error, urllib.error.HTTPError):
            classification = error
            if getattr(error, "_ciel_sse_error", False):
                classification = urllib.error.HTTPError(error.url, _sse_error_status(budget, raw), error.msg,
                                                        error.hdrs, None)
            wait = budget.next_wait(classification, raw, error.headers)
            kind, status = "http_rejection", error.code
        else:
            wait = budget.timeout_wait(error)
            kind = ("transport_timeout" if _typed_open_timeout(error) else
                    "transport_disconnect" if _pre_response_disconnect(error) else "transport_unsent")
            status = None
        failure_raw = raw if isinstance(error, urllib.error.HTTPError) else b""
        if wait is None:
            budget.stopped = True
            log("WARN", f"cielairouter_transient_stopped operation_id={budget.operation_id} "
                        f"kind={kind} attempts={budget.attempts} opens={budget.opens} "
                        f"elapsed={budget.clock() - budget.started:.2f}s stop={budget.stop_reason or '-'}")
            if budget.request_attempts() > 0:
                _observe(budget, publish, model, "error", kind=kind, code=_failure_code(status, failure_raw),
                         reason=_stop_event_reason(budget))
                error._ciel_transient_retry_summary = {
                    "reason": _stop_event_reason(budget), "attempts": budget.request_attempts(),
                    "stop": budget.stop_reason or "-", "retry_kind": kind}
            raise _mark(error, budget)
        log("WARN", f"cielairouter_transient_retry operation={_operation(request)} "
                    f"operation_id={budget.operation_id} model={model or '-'} kind={kind} "
                    f"{'status=' + str(status) if status is not None else 'error=' + type(error).__name__} "
                    f"attempt={budget.attempts}/{budget.limit} wait={wait:.2f}s "
                    f"elapsed={budget.clock() - budget.started:.2f}s "
                    f"deadline_seconds={budget.deadline_seconds:.2f} "
                    f"requested_timeout={budget.requested_timeout if budget.requested_timeout is not _UNSET else 'unknown'}"
                    + (f" verdict={_verdict_kind(raw) if raw else 'empty'}" if kind == "http_rejection" else ""))
        if budget.activity is not None:
            _observe(budget, None, model, "retry", kind=kind, code=_failure_code(status, failure_raw), wait=wait)
        elif publish is not None:
            publish(level="warn", category="router.retry", message="Transient CielAiRouter rejection or timeout; retrying",
                    provider=PROVIDER, model=model,
                    data={"kind": kind, "status": status, "attempt": budget.request_attempts(),
                          "total": budget.prior_attempts + budget.limit, "wait_seconds": wait,
                          "operation_id": budget.operation_id})
        try:
            _check_cancelled(cancelled)
            sleep(wait)
            _check_cancelled(cancelled)
        except BaseException as caught:
            _close(error)
            budget.stopped = True
            _mark(caught, budget)
            raise
        if budget.remaining() <= 0:
            budget.stopped = True
            raise _mark(error, budget)
        if reselect is not None:
            try:
                reselect()
            except BaseException as caught:
                _close(error)
                budget.stopped = True
                _mark(caught, budget)
                raise


def _operation(request: Any) -> str:
    url = str(getattr(request, "full_url", "") or "")
    return "responses" if "/responses" in url else "messages" if "/messages" in url else "chat"


def reselect_config_factory(apply, restore):
    return lambda config: apply(PROVIDER, restore(config))


__all__ = ["BUDGET_CONFIG_KEY", "DEFAULT_RETRIES", "DEFAULT_SECONDS", "MAX_RETRIES",
           "TransientRetryBudget", "budget_for", "bounded_timeout", "configured_retry_limit",
           "consume_external_retry", "retry_handled", "open_deadline", "open_with_transient_retry", "owns_transient_retries",
           "release_read_timeout", "request_scope", "transient_final_fields"]
