"""Interruptible SSE connection ownership; only the reader closes its response."""

from __future__ import annotations

import math
import socket
import threading
import time
from typing import Any, Callable


DEFAULT_HEARTBEAT_TIMEOUT_SECONDS = 60.0


def idle_timeout_seconds(value: Any) -> float | None:
    """None preserves legacy streams; explicit timeouts are bounded to 1..300s."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("idle_timeout_seconds must be a number from 1 to 300")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("idle_timeout_seconds must be a number from 1 to 300") from exc
    if not math.isfinite(number) or not 1 <= number <= 300:
        raise ValueError("idle_timeout_seconds must be a number from 1 to 300")
    return number


def response_socket(response: Any) -> Any:
    """urllib HTTP/HTTPS responses expose their transport here (including TLS)."""
    return getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)


class SseConnection:
    def __init__(self, response: Any, timeout: float | None = None,
                 *, clock: Callable[[], float] = time.monotonic,
                 on_activity: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.response = response
        self.socket = response_socket(response)
        self.timeout = timeout
        self.clock = clock
        self.last_activity = clock()
        self.last_activity_at = time.time()
        self.heartbeat_observed = False
        self.timed_out = False
        self.interrupted = False
        self.on_activity = on_activity
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._watcher: threading.Thread | None = None

    def activity(self, raw_line: Any) -> None:
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else str(raw_line)
        with self._lock:
            self.last_activity = self.clock()
            self.last_activity_at = time.time()
            if line.startswith(":") or line.strip() in {"event: heartbeat", "event: ping", "event: keepalive"}:
                self.heartbeat_observed = True
            metadata = {"last_activity_at": self.last_activity_at,
                        "heartbeat_observed": self.heartbeat_observed}
        if self.on_activity is not None:
            self.on_activity(metadata)

    def metadata(self) -> dict[str, Any]:
        with self._lock:
            return {"last_activity_at": self.last_activity_at,
                    "heartbeat_observed": self.heartbeat_observed,
                    "idle_timeout_seconds": self._timeout()}

    def _timeout(self) -> float | None:
        return self.timeout if self.timeout is not None else (
            DEFAULT_HEARTBEAT_TIMEOUT_SECONDS if self.heartbeat_observed else None)

    def check_idle(self) -> bool:
        with self._lock:
            timeout = self._timeout()
            expired = timeout is not None and self.clock() - self.last_activity >= timeout
            if expired:
                self.timed_out = True
        if expired:
            self.interrupt()
        return expired

    def interrupt(self) -> None:
        # HTTPResponse.close() can wait on the BufferedReader lock held by readline.
        # Shutdown the cached socket instead; the sole reader owns response cleanup.
        with self._lock:
            self.interrupted = True
            sock = self.socket
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def start(self) -> None:
        self._watcher = threading.Thread(target=self._watch, name="ciel-sse-watchdog", daemon=True)
        self._watcher.start()

    def _watch(self) -> None:
        while not self._done.wait(0.1):
            if self.check_idle():
                return

    def close(self) -> None:
        self._done.set()
        if self._watcher is not None:
            self._watcher.join(timeout=1)

    def lines(self) -> Any:
        for line in self.response:
            self.activity(line)
            if self.interrupted:
                break
            yield line


class ReceiverWorker:
    """A cancellation token is never cleared or reused after stop()."""
    def __init__(self) -> None:
        self.stop = threading.Event()
        self.reconnect = threading.Event()
        self.connection: SseConnection | None = None
        self.lock = threading.Lock()

    def interrupt(self, *, stop: bool = False) -> None:
        with self.lock:
            if stop:
                self.stop.set()
            self.reconnect.set()
            if self.connection is not None:
                self.connection.interrupt()

    def bind(self, connection: SseConnection | None) -> None:
        with self.lock:
            self.connection = connection
            if connection is not None and (self.stop.is_set() or self.reconnect.is_set()):
                connection.interrupt()
