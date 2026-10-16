"""Observation-only urllib response wrapper; no selection, retries or body logging.

Use ``wrap_gateway_response(urlopen(...), protocol, on_result=callback)`` after
selecting a gateway. Catch open errors separately with ``observe_gateway_error``.
Callbacks receive metadata only and must not drive this response's delivery.
``cancel()``/``close()`` before EOF deliberately abandon observation. Supply
``client_disconnected`` when a concurrent reader may outlive its downstream.
Supported consuming methods are read/read1/readline/readlines/readinto and
iteration; access through delegated fp/raw attributes bypasses observation.
"""
from __future__ import annotations

import errno
import http.client
import json
import socket
import ssl
import urllib.error
from dataclasses import dataclass
from typing import Any, Callable, Literal

GatewayProtocol = Literal["anthropic", "responses", "chat"]


@dataclass(frozen=True, slots=True)
class GatewayObservation:
    """Metadata-only result; success describes transport, not model quality.

    response.incomplete/failed are complete wire terminals, not missing EOF
    markers. Their terminal names are retained without retaining their bodies.
    observation_incomplete is unknown (not gateway failure evidence): the bounded
    observer could not inspect a JSON-only event.
    """
    success: bool
    reason: str
    status: int | None = None
    terminal: str | None = None


Callback = Callable[[GatewayObservation], None]


def _notify(callback: Callback | None, result: GatewayObservation) -> None:
    if callback is not None:
        try:
            callback(result)
        except Exception:
            # An observation consumer must never replace a transport exception
            # or consume bytes that the caller was about to receive.
            pass


def _error_reason(error: BaseException) -> str | None:
    if isinstance(error, urllib.error.HTTPError):
        return "http_5xx" if 500 <= error.code <= 599 else None
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(reason, TimeoutError) or (isinstance(reason, OSError) and reason.errno == errno.ETIMEDOUT):
        return "timeout"
    if isinstance(reason, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
        return "connection_reset"
    if isinstance(reason, (EOFError, http.client.IncompleteRead, http.client.RemoteDisconnected, ssl.SSLEOFError)):
        return "eof"
    if isinstance(error, urllib.error.URLError) or isinstance(reason, (ConnectionError, socket.gaierror, ssl.SSLError, http.client.HTTPException)):
        return "network_error"
    if isinstance(reason, OSError) and reason.errno in {errno.ECONNRESET, errno.ECONNABORTED, errno.EPIPE, errno.ECONNREFUSED, errno.ENETUNREACH, errno.EHOSTUNREACH}:
        return "network_error"
    return None


def observe_gateway_error(error: BaseException, *, on_result: Callback | None) -> BaseException:
    """Observe urlopen HTTP 5xx/network errors; return the identical exception.

    Call inside an except block followed by bare ``raise``. Does not read an
    HTTPError body or inspect exception messages (which may contain secrets).
    Request/auth/other 4xx errors and programming errors do not score gateways.
    """
    reason = _error_reason(error)
    if reason is not None:
        _notify(on_result, GatewayObservation(False, reason, getattr(error, "code", None)))
    return error


class _SseTerminal:
    """Bounded SSE observation, with independent event metadata and data budgets.

    Explicit event fields survive oversized bodies, including when they follow
    the data. JSON-only events beyond the budget remain unknown: absence of an
    observed terminal is not evidence of truncation after dropping data.
    """
    def __init__(self, protocol: GatewayProtocol, limit: int) -> None:
        self.protocol, self.limit = protocol, limit
        self.line = bytearray()
        self.data = bytearray()
        self.event = b""
        self.line_overflow = False
        self.data_overflow = False
        self.incomplete = False
        self.after_cr = False
        self.terminal: str | None = None

    def feed(self, chunk: bytes) -> None:
        # Never retain/copy an unbounded caller chunk. A line prefix suffices
        # to identify its field; overflow is tracked separately from its value.
        for char in memoryview(chunk).cast("B"):
            if self.after_cr:
                self.after_cr = False
                if char == 10:
                    continue
            if char in (10, 13):
                self._line()
                self.after_cr = char == 13
            elif len(self.line) < self.limit - len(self.data):
                self.line.append(char)
            else:
                self.line_overflow = True
            if self.terminal:
                self.clear()
                return

    def _line(self) -> None:
        line = bytes(self.line)
        overflow = self.line_overflow
        self.line.clear()
        self.line_overflow = False
        if not line and not overflow:
            self._event()
        elif line.startswith(b"data:"):
            value = line[5:]
            value = value[1:] if value.startswith(b" ") else value
            if overflow or len(self.data) + len(value) + 1 > self.limit - 32:
                self.data_overflow = True
                self.data.clear()
            elif not self.data_overflow:
                self.data.extend(value)
                self.data.extend(b"\n")
        elif line.startswith(b"event:"):
            # An overflowing event field must not match its retained prefix.
            self.event = b"" if overflow else line[6:].removeprefix(b" ")
            if overflow:
                self.incomplete = True

    def _event(self) -> None:
        data = bytes(self.data).removesuffix(b"\n")
        try:
            payload = json.loads(data) if not self.data_overflow else None
        except (ValueError, UnicodeError, RecursionError):
            payload = None
        kind = payload.get("type") if isinstance(payload, dict) else None
        if self.protocol == "anthropic" and (kind == "message_stop" or self.event == b"message_stop"):
            self.terminal = "message_stop"
        elif self.protocol == "responses":
            for name in ("response.completed", "response.incomplete", "response.failed"):
                if kind == name or self.event == name.encode():
                    self.terminal = name
        elif self.protocol == "chat":
            if data == b"[DONE]" and not self.data_overflow:
                self.terminal = "[DONE]"
            elif isinstance(payload, dict):
                choices = payload.get("choices")
                if isinstance(choices, list) and choices and all(isinstance(c, dict) and c.get("finish_reason") is not None for c in choices):
                    self.terminal = "finish_reason"
        if self.data_overflow:
            self.incomplete = True
        self.data.clear()
        self.data_overflow = False
        self.event = b""

    def finish(self) -> None:
        # SSE dispatch requires the blank line. Retained partial data cannot
        # be accepted, but budget loss on an unfinished line is still unknown.
        if self.line_overflow and self.line.startswith((b"data:", b"event:")):
            self.incomplete = True
        if self.data_overflow:
            self.incomplete = True

    def clear(self) -> None:
        self.line.clear()
        self.data.clear()
        self.event = b""


class CielAiRouterGatewayTransport:
    """Single-reader response proxy with at-most-once result callback.

    Success is reported at a complete SSE terminal frame, so consumers may stop
    reading at that marker. A finish_reason is accepted at EOF (or close) for
    Chat endpoints omitting [DONE]; [DONE] itself is accepted immediately.
    Missing terminal EOF is observed, never raised. Budget loss without an
    explicit terminal reports observation_incomplete, not proven truncation. ``stream=False`` observes
    normal HTTP body completion instead. HTTP 5xx is failure even if readable.
    Explicit close/abort without completion yields no result, not a failure.
    """
    def __init__(self, response: Any, protocol: GatewayProtocol, *,
                 on_result: Callback | None = None, stream: bool = True,
                 max_sse_buffer: int = 65536,
                 client_disconnected: Callable[[], bool] | None = None) -> None:
        if protocol not in {"anthropic", "responses", "chat"}:
            raise ValueError("unsupported gateway protocol")
        if not isinstance(max_sse_buffer, int) or max_sse_buffer < 64:
            raise ValueError("max_sse_buffer must be an integer >= 64")
        self._response = response
        self._callback = on_result
        self._stream = stream
        self._parser = _SseTerminal(protocol, max_sse_buffer)
        self._client_disconnected = client_disconnected
        self._cancelled = False
        self._reported = False
        self._status = getattr(response, "status", getattr(response, "code", None))
        if self._status is not None and 500 <= self._status <= 599:
            self._report(False, "http_5xx")

    @property
    def terminal_observed(self) -> str | None:
        return self._parser.terminal

    @property
    def closed_intentionally(self) -> bool:
        return self._cancelled

    def _abandoned(self) -> bool:
        if self._cancelled:
            return True
        if self._client_disconnected is not None:
            try:
                return bool(self._client_disconnected())
            except Exception:
                # Unknown downstream state cannot safely score a gateway.
                return True
        return False

    def _report(self, success: bool, reason: str) -> None:
        if self._reported or self._abandoned():
            return
        self._reported = True
        if self._status is None or 200 <= self._status <= 299 or reason == "http_5xx":
            _notify(self._callback, GatewayObservation(success, reason, self._status, self._parser.terminal))
        self._parser.clear()

    def _observe(self, data: bytes, *, eof: bool = False) -> None:
        if self._reported or self._abandoned():
            return
        if self._stream:
            self._parser.feed(data)
            terminal = self._parser.terminal
            if terminal is not None and terminal != "finish_reason":
                self._report(True, "terminal_event")
        if eof:
            if self._stream:
                self._parser.finish()
                reason = ("terminal_event" if self._parser.terminal else
                          "observation_incomplete" if self._parser.incomplete else "eof_before_terminal")
                self._report(bool(self._parser.terminal), reason)
            else:
                self._report(True, "http_complete")

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        try:
            return getattr(self._response, method)(*args, **kwargs)
        except Exception as error:
            reason = _error_reason(error)
            if reason is not None:
                self._report(False, reason)
            raise

    def read(self, amt: int | None = None) -> bytes:
        data = self._call("read") if amt is None else self._call("read", amt)
        # read(0) is not EOF. A read-all operation returns only after EOF.
        self._observe(data, eof=amt != 0 and (not data or amt is None or amt < 0))
        return data

    def read1(self, n: int = -1) -> bytes:
        data = self._call("read1", n)
        self._observe(data, eof=n != 0 and not data)
        return data

    def readline(self, limit: int = -1) -> bytes:
        data = self._call("readline") if limit == -1 else self._call("readline", limit)
        self._observe(data, eof=limit != 0 and not data)
        return data

    def readinto(self, buffer: Any) -> int:
        count = self._call("readinto", buffer)
        if count is not None:
            self._observe(memoryview(buffer).cast("B")[:count], eof=bool(len(buffer)) and count == 0)
        return count

    def readlines(self, hint: int = -1) -> list[bytes]:
        lines = []
        total = 0
        while True:
            line = self.readline()
            if not line:
                return lines
            lines.append(line)
            total += len(line)
            if hint > 0 and total >= hint:
                return lines

    def __iter__(self) -> CielAiRouterGatewayTransport:
        return self

    def __next__(self) -> bytes:
        line = self.readline()
        if not line:
            raise StopIteration
        return line

    def cancel(self) -> None:
        """Suppress future scoring for a client disconnect or deliberate abort."""
        self._cancelled = True
        self._parser.clear()

    def close(self) -> Any:
        if self._parser.terminal == "finish_reason":
            self._report(True, "terminal_event")
        self.cancel()
        return self._response.close()

    def __enter__(self) -> CielAiRouterGatewayTransport:
        self._response.__enter__()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> Any:
        # Context exceptions originate in consumer code, not upstream reads.
        if exc_type is None and self._parser.terminal == "finish_reason":
            self._report(True, "terminal_event")
        self.cancel()
        return self._response.__exit__(exc_type, exc, tb)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._response, name)


def wrap_gateway_response(response: Any, protocol: GatewayProtocol, **kwargs: Any) -> CielAiRouterGatewayTransport:
    """Wrap an already-selected gateway's urlopen response; do not open/retry."""
    return CielAiRouterGatewayTransport(response, protocol, **kwargs)


__all__ = ["GatewayObservation", "CielAiRouterGatewayTransport", "wrap_gateway_response", "observe_gateway_error"]
