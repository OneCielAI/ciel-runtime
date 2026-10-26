"""Request-scoped retries for pre-output rejections and typed open timeouts.

The generation dispatcher enters request_scope; reusable config never owns a
mutable budget. Typed TimeoutError (also inside URLError) during open may
retry under that same budget; other ambiguous transport failures do not.
A timeout does not prove the upstream did no work, so replay may duplicate
upstream generation or billing. Accepted-stream reads never reopen here.

open_once must not expose downstream bytes. Only text/event-stream success
responses are inspected: at most 64 KiB for at most 100 ms (also limited by
the monotonic request deadline), using only ready bytes. Unknown streams or
incomplete HTTP framing are returned without a blocking probe. Prefix bytes
are replayed unchanged through the response contract; the first non-error,
unknown or mixed dispatch closes inspection, never a full-stream buffer.
Every byte returned by that adapter marks the budget exposed. A caller may
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
import json
import math
import threading
import time
import urllib.error
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

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
    # Only default scopes size their allowance from the first resolved opener
    # timeout. Explicitly supplied budgets retain their exact deadline.
    timeout_pending: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        self.limit = max(0, min(MAX_RETRIES, int(self.limit)))
        if self.started is None:
            self.started = self.clock()

    def remaining(self) -> float:
        return max(0.0, self.started + self.deadline_seconds - self.clock())

    def consume_external_retry(self) -> bool:
        """Reserve a reopen for an existing specialized recovery owner."""
        with self.lock:
            if self.stopped or self.exposed or self.remaining() <= 0 or self.attempts >= self.limit:
                return False
            self.attempts += 1
            self.opens += 1
            return True

    def next_wait(self, error: urllib.error.HTTPError, raw: bytes,
                  headers: Mapping[str, Any] | None = None) -> float | None:
        if not _retryable_verdict(error.code, raw):
            return None
        return self._schedule_wait(headers)

    def timeout_wait(self, error: BaseException) -> float | None:
        if retry_handled(error) or not _typed_open_timeout(error):
            return None
        return self._schedule_wait()

    def _schedule_wait(self, headers: Mapping[str, Any] | None = None) -> float | None:
        with self.lock:
            if self.stopped or self.exposed or self.attempts >= self.limit or self.remaining() <= 0:
                return None
            hinted = _retry_after(headers)
            scheduled = WAIT_SCHEDULE_SECONDS[min(self.attempts, len(WAIT_SCHEDULE_SECONDS) - 1)]
            wait = min(max(MIN_WAIT_SECONDS, hinted if hinted is not None else scheduled),
                       MAX_WAIT_SECONDS, self.remaining())
            # A wait that consumes all remaining time cannot admit an open;
            # do not count a retry that can never happen (socket rounding).
            if wait <= 0 or wait >= self.remaining():
                return None
            self.attempts += 1
            self.waited_seconds += wait
            return wait

    def note(self) -> str:
        return (f"Ciel Runtime retried this request {self.attempts} time(s) over "
                f"{self.waited_seconds:.2f}s after transient upstream rejection or timeout; giving up.")


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


@contextlib.contextmanager
def request_scope(config: Any = None, provider: str = PROVIDER, *,
                  budget: TransientRetryBudget | None = None):
    """Start an independent operation; nested transports use budget_for."""
    active = _SCOPE.get()
    if active is not None and active.owner is not None:
        raise RuntimeError("cannot replace an active transient retry owner")
    current = budget
    if current is None and _limit(config, provider):
        current = TransientRetryBudget(_limit(config, provider), DEFAULT_SECONDS, timeout_pending=True)
    token = _SCOPE.set(current)
    try:
        yield current
    finally:
        _SCOPE.reset(token)


def budget_for(config: Any, provider: str) -> TransientRetryBudget | None:
    return _SCOPE.get() if _limit(config, provider) else None


def bounded_timeout(config: Any, provider: str, timeout: Any) -> Any:
    """Clip an active retry attempt's socket timeout, never another provider's.

    None and invalid/nonpositive/nonfinite timeouts cannot remove the active
    budget's bound. Outside an enabled request scope, preserve the input exactly.
    This bounds individual socket operations, not DNS or an entire opener call.
    """
    budget = budget_for(config, provider)
    if budget is None:
        return timeout
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
            raise _mark(TimeoutError("transient retry deadline reached before open"), budget)
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
    if set(value) - {"type", "error", "request_id"} or value.get("type", "error") != "error":
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


def _retryable_verdict(status: int, raw: bytes) -> bool:
    verdict = _error_verdict(raw)
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
    error._ciel_transient_retry_note = budget.note()
    return error


class _ReplayResponse:
    """Untouched read-ahead bytes followed by the original response."""
    def __init__(self, response: Any, prefix: bytes, budget: TransientRetryBudget):
        self._response = response
        self._prefix = io.BytesIO(prefix)
        self._budget = budget
        self.closed = False

    def __getattr__(self, name):
        return getattr(self._response, name)

    def _expose(self, data):
        if data:
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
                              model: str = "") -> Any:
    if not _limit(config, provider):
        _check_cancelled(cancelled)
        return open_once()
    budget = budget_for(config, provider)
    if budget is None:
        with request_scope(config, provider):
            return open_with_transient_retry(open_once, provider=provider, config=config,
                log=log, publish=publish, sleep=sleep, cancelled=cancelled,
                request=request, reselect=reselect, model=model)
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


def _execute(open_once, budget, log, publish, sleep, cancelled, request, reselect, model):
    last = None
    while True:
        _check_cancelled(cancelled)
        if budget.remaining() <= 0 or budget.opens >= budget.limit + 1:
            budget.stopped = True
            raise _mark(last or RuntimeError("transient retry deadline or attempt limit reached"), budget)
        _close(last)
        budget.opens += 1
        error, response = None, None
        try:
            response = open_once()
        except urllib.error.HTTPError as caught:
            error = caught
        except BaseException as caught:
            if not _typed_open_timeout(caught):
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
                return release_read_timeout(response, budget)
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
                classification = urllib.error.HTTPError(error.url, 503, error.msg, error.hdrs, None)
            wait = budget.next_wait(classification, raw, error.headers)
            kind, status = "http_rejection", error.code
        else:
            wait = budget.timeout_wait(error)
            kind, status = "transport_timeout", None
        if wait is None:
            budget.stopped = True
            log("WARN", f"cielairouter_transient_stopped operation_id={budget.operation_id} "
                        f"kind={kind} attempts={budget.attempts} opens={budget.opens} "
                        f"elapsed={budget.clock() - budget.started:.2f}s")
            raise _mark(error, budget)
        log("WARN", f"cielairouter_transient_retry operation={_operation(request)} "
                    f"operation_id={budget.operation_id} model={model or '-'} kind={kind} "
                    f"{'status=' + str(status) if status is not None else 'error=' + type(error).__name__} "
                    f"attempt={budget.attempts}/{budget.limit} wait={wait:.2f}s "
                    f"elapsed={budget.clock() - budget.started:.2f}s "
                    f"deadline_seconds={budget.deadline_seconds:.2f} "
                    f"requested_timeout={budget.requested_timeout if budget.requested_timeout is not _UNSET else 'unknown'}")
        if publish is not None:
            publish(level="warn", category="router.retry", message="Transient CielAiRouter rejection or timeout; retrying",
                    provider=PROVIDER, model=model,
                    data={"kind": kind, "status": status, "attempt": budget.attempts,
                          "total": budget.limit, "wait_seconds": wait, "operation_id": budget.operation_id})
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
           "consume_external_retry", "retry_handled", "open_with_transient_retry", "owns_transient_retries",
           "release_read_timeout", "request_scope"]
