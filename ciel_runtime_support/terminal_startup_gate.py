"""Launch-local readiness shared by a remote TUI and its RPC injector.

The terminal call stays synchronous through the launcher adapters. A scoped
binding avoids process-global or persisted readiness and leaves non-TUI callers
open. The injector keeps the explicit gate, including on its polling thread.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from threading import Event
from typing import Iterator


class TerminalStartupGate:
    def __init__(self, *, require_screen: bool = False) -> None:
        self._ready = Event()
        if not require_screen:
            self._ready.set()

    def ready(self) -> bool:
        return self._ready.is_set()

    def observe(self, *, rendered_ready: bool) -> None:
        # One startup boundary, never a timeout or a persisted previous launch.
        if rendered_ready:
            self._ready.set()


_CURRENT: ContextVar[TerminalStartupGate | None] = ContextVar("terminal_startup_gate", default=None)


def current_terminal_startup_gate() -> TerminalStartupGate | None:
    return _CURRENT.get()


@contextmanager
def terminal_startup_scope(gate: TerminalStartupGate) -> Iterator[None]:
    token = _CURRENT.set(gate)
    try:
        yield
    finally:
        _CURRENT.reset(token)
