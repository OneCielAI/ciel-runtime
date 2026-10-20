"""Bounded, value-free SSE failure diagnostics safe for public status and logs."""
from __future__ import annotations

import re
import socket
import ssl
import urllib.error
from typing import Any

_PHASES = {"config", "cursor", "authorization", "request", "connect", "headers", "read", "admission", "cursor_write", "cleanup", "worker", "ack"}


def receiver_failure(exc: Exception, phase: str) -> dict[str, Any]:
    """Never stringify exceptions, URLs, reason strings, payloads or tracebacks."""
    def describe(error: BaseException, depth: int) -> dict[str, Any]:
        name = type(error).__name__
        name = name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", name) else "Exception"
        category = "internal"
        if isinstance(error, urllib.error.HTTPError):
            category = "http"
        elif isinstance(error, socket.gaierror):
            category = "dns"
        elif isinstance(error, TimeoutError):
            category = "timeout"
        elif isinstance(error, ssl.SSLError):
            category = "tls"
        elif isinstance(error, urllib.error.URLError):
            category = "network"
        elif isinstance(error, EOFError):
            category = "eof"
        elif isinstance(error, ConnectionError):
            category = "network"
        elif isinstance(error, OSError):
            category = "io"
        elif isinstance(error, ValueError):
            category = "validation"
        result: dict[str, Any] = {"class": name, "category": category}
        for attribute, key in (("code", "http_status"), ("errno", "errno"), ("winerror", "winerror")):
            try:
                value = getattr(error, attribute, None)
            except Exception:
                continue
            if type(value) is int and -(2 ** 31) <= value < 2 ** 32:
                result[key] = value
        if depth < 3 and isinstance(error, urllib.error.URLError):
            reason = error.reason
            if isinstance(reason, BaseException) and reason is not error:
                result["reason"] = describe(reason, depth + 1)
        return result

    result = describe(exc, 0)
    result["phase"] = phase if phase in _PHASES else "worker"
    nested = result
    while isinstance(nested.get("reason"), dict):
        nested = nested["reason"]
    if phase in {"config", "authorization", "request"}:
        result["category"] = "configuration"
    elif phase in {"cursor", "cursor_write", "ack"}:
        result["category"] = "persistence"
    elif "reason" in result:
        result["category"] = nested["category"]
    return result
