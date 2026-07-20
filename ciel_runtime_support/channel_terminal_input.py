from __future__ import annotations

import os
import sys
from typing import Any


_WINDOWS_CONSOLE_INPUT_FALLBACK_HANDLE: Any = None


def windows_console_input_handle() -> Any:
    """Return a validated Windows console input handle, opening CONIN$ if needed."""
    global _WINDOWS_CONSOLE_INPUT_FALLBACK_HANDLE
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetConsoleMode.restype = wintypes.BOOL
        handle = kernel32.GetStdHandle(wintypes.DWORD(-10 & 0xFFFFFFFF))
        handle_value = int(handle) if isinstance(handle, int) else int(getattr(handle, "value", 0) or 0)
        invalid_handle = int(ctypes.c_void_p(-1).value or -1)
        if handle_value and handle_value != invalid_handle:
            mode = wintypes.DWORD(0)
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                return handle

        cached = _WINDOWS_CONSOLE_INPUT_FALLBACK_HANDLE
        cached_value = int(cached) if isinstance(cached, int) else int(getattr(cached, "value", 0) or 0)
        if cached_value and cached_value != invalid_handle:
            mode = wintypes.DWORD(0)
            if kernel32.GetConsoleMode(cached, ctypes.byref(mode)):
                return cached

        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        console_handle = kernel32.CreateFileW(
            "CONIN$",
            0x80000000 | 0x40000000,
            0x00000001 | 0x00000002,
            None,
            3,
            0,
            None,
        )
        console_value = (
            int(console_handle)
            if isinstance(console_handle, int)
            else int(getattr(console_handle, "value", 0) or 0)
        )
        if not console_value or console_value == invalid_handle:
            return None
        mode = wintypes.DWORD(0)
        if not kernel32.GetConsoleMode(console_handle, ctypes.byref(mode)):
            return None
        _WINDOWS_CONSOLE_INPUT_FALLBACK_HANDLE = console_handle
        return console_handle
    except Exception:
        return None


class TerminalMouseInputFilter:
    """Strip terminal mouse reports that can leak into TUI prompt buffers."""

    def __init__(self) -> None:
        self._pending = b""

    def feed(self, data: bytes) -> bytes:
        if not data:
            return b""
        buffer = self._pending + data
        self._pending = b""
        output = bytearray()
        index = 0
        while index < len(buffer):
            if buffer[index] != 0x1B:
                output.append(buffer[index])
                index += 1
                continue
            if index + 1 >= len(buffer):
                self._pending = buffer[index:]
                break
            if buffer[index + 1] != ord("["):
                output.append(buffer[index])
                index += 1
                continue
            if index + 2 >= len(buffer):
                self._pending = buffer[index:]
                break
            marker = buffer[index + 2]
            if marker == ord("<"):
                end = index + 3
                while end < len(buffer) and (48 <= buffer[end] <= 57 or buffer[end] == ord(";")):
                    end += 1
                if end >= len(buffer):
                    self._pending = buffer[index:]
                    break
                if end > index + 3 and buffer[end] in (ord("M"), ord("m")):
                    index = end + 1
                    continue
                output.append(buffer[index])
                index += 1
                continue
            if marker == ord("M"):
                if index + 6 <= len(buffer):
                    index += 6
                    continue
                self._pending = buffer[index:]
                break
            if 48 <= marker <= 57:
                end = index + 2
                semicolons = 0
                while end < len(buffer) and (48 <= buffer[end] <= 57 or buffer[end] == ord(";")):
                    if buffer[end] == ord(";"):
                        semicolons += 1
                    end += 1
                if end >= len(buffer):
                    self._pending = buffer[index:]
                    break
                if semicolons >= 2 and buffer[end] in (ord("M"), ord("m")):
                    index = end + 1
                    continue
                output.append(buffer[index])
                index += 1
                continue
            output.append(buffer[index])
            index += 1
        return bytes(output)

    def flush(self) -> bytes:
        pending = self._pending
        self._pending = b""
        return pending


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
