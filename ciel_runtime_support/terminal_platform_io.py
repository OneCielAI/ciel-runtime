"""Terminal sizing, POSIX PTY sizing, and input-mode reset policy."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


TERMINAL_INPUT_MODE_RESET = (
    "\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1004l"
    "\x1b[?1005l\x1b[?1006l\x1b[?1015l"
)


def terminal_winsize_from_fd(fd: int) -> tuple[int, int]:
    """Return terminal size as (rows, columns), never 0x0."""
    try:
        size = os.get_terminal_size(fd)
        rows = int(size.lines)
        columns = int(size.columns)
    except Exception:
        rows = 0
        columns = 0
    if rows > 0 and columns > 0:
        return rows, columns
    fallback = shutil.get_terminal_size((80, 24))
    rows = int(getattr(fallback, "lines", 0) or 0)
    columns = int(getattr(fallback, "columns", 0) or 0)
    if rows <= 0:
        rows = 24
    if columns <= 0:
        columns = 80
    return rows, columns


def apply_pty_winsize(
    pty_fd: int,
    rows: int,
    columns: int,
    *,
    platform_name: str | None = None,
) -> bool:
    if (platform_name or os.name) != "posix" or rows <= 0 or columns <= 0:
        return False
    try:
        import fcntl
        import struct
        import termios

        fcntl.ioctl(
            pty_fd,
            termios.TIOCSWINSZ,
            struct.pack("HHHH", rows, columns, 0, 0),
        )
        return True
    except Exception:
        return False


@dataclass(frozen=True, slots=True)
class TerminalInputModeResetPolicy:
    platform_name: str
    environment: Mapping[str, str]
    parse_bool: Callable[..., bool]
    default_stream: Callable[[], Any]

    def enabled(self) -> bool:
        configured = self.environment.get(
            "CIEL_RUNTIME_TERMINAL_INPUT_MODE_RESET"
        )
        if self.platform_name == "nt" and configured is None:
            return False
        return self.parse_bool(configured, True)

    def interval_seconds(self, default: float = 2.0) -> float:
        raw = self.environment.get(
            "CIEL_RUNTIME_TERMINAL_INPUT_MODE_RESET_INTERVAL_SECONDS"
        )
        if raw is None:
            return default
        try:
            return max(0.25, min(60.0, float(raw)))
        except Exception:
            return default

    def write(self, stream: Any | None = None) -> None:
        if not self.enabled():
            return
        target = stream if stream is not None else self.default_stream()
        try:
            if hasattr(target, "isatty") and not target.isatty():
                return
            target.write(TERMINAL_INPUT_MODE_RESET)
            target.flush()
        except Exception:
            return
