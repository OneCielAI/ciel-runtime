"""Socket adapter for cancellable upstream response streams."""

from __future__ import annotations

import select
import socket
import time
from collections.abc import Callable, Iterable
from typing import Any


class UpstreamClientDisconnected(Exception):
    """The downstream client closed while an upstream stream was active."""


def stream_idle_timeout(
    config: dict[str, Any],
    *,
    positive_int: Callable[[Any], int],
    request_timeout: Callable[[dict[str, Any]], float],
) -> float:
    milliseconds = positive_int(config.get("stream_idle_timeout_ms"))
    if milliseconds:
        return max(5.0, milliseconds / 1000.0)
    return max(30.0, min(request_timeout(config), 300.0))


def set_stream_read_timeout(response: Any, timeout: float) -> None:
    try:
        if hasattr(response, "fp") and getattr(response, "fp") is not None:
            raw = getattr(response.fp, "raw", None)
            sock = getattr(raw, "_sock", None)
            if sock is not None and hasattr(sock, "settimeout"):
                sock.settimeout(timeout)
                return
        sock = getattr(response, "sock", None)
        if sock is not None and hasattr(sock, "settimeout"):
            sock.settimeout(timeout)
    except (AttributeError, OSError, TypeError, ValueError):
        pass


def client_connection_closed(handler: Any) -> bool:
    connection = getattr(handler, "connection", None)
    if connection is None:
        return False
    try:
        readable, _, _ = select.select([connection], [], [], 0)
    except (OSError, ValueError):
        return True
    except (AttributeError, TypeError):
        return False
    if not readable:
        return False
    try:
        data = connection.recv(1, getattr(socket, "MSG_PEEK", 0))
        return data == b""
    except BlockingIOError:
        return False
    except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
        return True


def iter_lines_until_disconnect(
    handler: Any,
    response: Any,
    idle_timeout: float,
    *,
    set_timeout: Callable[[Any, float], None],
    disconnected: Callable[[Any], bool],
) -> Iterable[bytes]:
    try:
        idle = max(1.0, float(idle_timeout))
    except (TypeError, ValueError, OverflowError):
        idle = 30.0
    set_timeout(response, idle)
    while True:
        if disconnected(handler):
            raise UpstreamClientDisconnected("downstream client disconnected")
        try:
            raw = response.readline()
        except (TimeoutError, OSError) as exc:
            if disconnected(handler):
                raise UpstreamClientDisconnected(
                    "downstream client disconnected during upstream read"
                ) from exc
            raise
        if raw in (b"", ""):
            return
        yield raw


def sleep_until_disconnect(
    handler: Any,
    seconds: float,
    *,
    disconnected: Callable[[Any], bool],
) -> bool:
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        if disconnected(handler):
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        time.sleep(min(0.25, remaining))
