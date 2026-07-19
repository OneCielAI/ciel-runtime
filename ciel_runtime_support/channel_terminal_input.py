from __future__ import annotations

import os
import sys
from typing import Any


def platform_default_enter_bytes(
    platform: str | None = None,
    os_name: str | None = None,
) -> bytes:
    sys_platform = str(platform if platform is not None else sys.platform).lower()
    os_family = str(os_name if os_name is not None else os.name).lower()
    if os_family == "nt" or sys_platform.startswith(("win", "cygwin", "msys")):
        return b"\r\n"
    if os_family == "posix":
        return b"\r\n"
    return b"\r\n"


def resolve_enter_bytes(value: str | bytes | None, default: bytes) -> bytes:
    raw = value
    if isinstance(raw, bytes):
        return raw if raw in (b"\n", b"\r", b"\r\n") else default
    normalized = str(raw or "").strip().lower()
    if normalized in {"", "auto", "default", "platform"}:
        return default
    if normalized in {"lf", "nl", "newline", "linefeed", "\\n"}:
        return b"\n"
    if normalized in {"cr", "return", "carriage-return", "carriage_return", "\\r"}:
        return b"\r"
    if normalized in {"crlf", "cr-lf", "return-newline", "\\r\\n"}:
        return b"\r\n"
    return default


def wake_enter_env_is_fixed() -> bool:
    raw = os.environ.get("CIEL_RUNTIME_CHANNEL_WAKE_ENTER")
    if raw is None:
        return False
    return str(raw).strip().lower() not in {"", "auto", "default", "platform"}


def enter_bytes_from_user_input(data: bytes) -> bytes | None:
    if not data:
        return None
    if data in (b"\n", b"\r", b"\r\n"):
        return data
    last_lf = data.rfind(b"\n")
    last_cr = data.rfind(b"\r")
    if last_lf < 0 and last_cr < 0:
        return None
    if last_lf > last_cr:
        return b"\r\n" if last_lf > 0 and data[last_lf - 1 : last_lf] == b"\r" else b"\n"
    return b"\r\n" if last_cr + 1 < len(data) and data[last_cr + 1 : last_cr + 2] == b"\n" else b"\r"


def synthetic_enter_bytes_from_user_input(
    data: bytes,
    *,
    normalize_bare_cr: bool = True,
) -> bytes | None:
    observed = enter_bytes_from_user_input(data)
    if observed == b"\r" and normalize_bare_cr:
        return b"\r\n"
    return observed


def enter_label(enter_bytes: bytes) -> str:
    if enter_bytes == b"\r":
        return "cr"
    if enter_bytes == b"\r\n":
        return "crlf"
    return "lf"


def wake_input_bytes(prompt: str, enter_bytes: bytes) -> bytes:
    return b"\x15" + prompt.encode("utf-8", errors="replace") + enter_bytes


def bounded_delay_seconds(
    raw: Any,
    default_seconds: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    try:
        return max(minimum, min(maximum, float(raw) / 1000.0))
    except (TypeError, ValueError):
        return default_seconds


def wake_submit_delay_seconds() -> float:
    raw = os.environ.get("CIEL_RUNTIME_CHANNEL_WAKE_SUBMIT_DELAY_MS")
    return bounded_delay_seconds(raw, 0.08, minimum=0.0, maximum=2.0) if raw is not None else 0.08


def wake_submit_retry_delay_seconds() -> float:
    raw = os.environ.get("CIEL_RUNTIME_CHANNEL_WAKE_SUBMIT_RETRY_DELAY_MS")
    return bounded_delay_seconds(raw, 0.9, minimum=0.05, maximum=5.0) if raw is not None else 0.9
