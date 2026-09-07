"""Reassemble short VT input sequences across parent-console reads."""
from __future__ import annotations

import threading
from collections.abc import Callable


class TerminalInputFrames:
    """Do not expose a partial CSI to a child's keyboard-event parser.

    A bounded idle timer preserves a standalone Escape key. Nothing is
    stripped or rewritten, including literal text resembling paste markers.
    """

    def __init__(self, write: Callable[[bytes], None], *, idle_seconds: float = 0.1):
        self.write = write
        self.idle_seconds = idle_seconds
        self.pending = bytearray()
        self.lock = threading.Lock()
        self.timer: threading.Timer | None = None
        self.generation = 0

    def feed(self, data: bytes) -> None:
        with self.lock:
            self._cancel()
            ready = bytearray()
            for byte in data:
                if not self.pending:
                    if byte == 0x1b:
                        self.pending.append(byte)
                    else:
                        ready.append(byte)
                    continue
                self.pending.append(byte)
                # CSI/SS3 sequences end with a final byte; other ESC keys
                # (including Alt+key) are complete after the next byte.
                complete = (
                    len(self.pending) == 2 and byte not in (ord('['), ord('O'))
                ) or (
                    len(self.pending) >= 3 and 0x40 <= byte <= 0x7e
                ) or len(self.pending) >= 64
                if complete:
                    ready.extend(self.pending)
                    self.pending.clear()
            if ready:
                self.write(bytes(ready))
            if self.pending:
                generation = self.generation
                self.timer = threading.Timer(self.idle_seconds, self._expire, args=(generation,))
                self.timer.daemon = True
                self.timer.start()

    def _cancel(self) -> None:
        self.generation += 1
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None

    def _expire(self, generation: int) -> None:
        with self.lock:
            if generation != self.generation:
                return
            self.timer = None
            self._flush()

    def _flush(self) -> None:
        if self.pending:
            data = bytes(self.pending)
            self.pending.clear()
            try:
                self.write(data)
            except OSError:
                pass  # Child may have exited while Escape was pending.

    def close(self) -> None:
        with self.lock:
            self._cancel()
            self._flush()
