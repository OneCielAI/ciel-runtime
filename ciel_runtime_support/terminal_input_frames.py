"""Reassemble short VT input sequences across parent-console reads."""
from __future__ import annotations

import threading
from collections.abc import Callable


class TerminalInputFrames:
    """Do not expose a partial VT sequence to a child's keyboard parser.

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
                introducer = self.pending[1]
                # OSC (including palette replies) and other control strings
                # end at ST, not at their first printable byte. OSC also
                # accepts BEL. An ESC ending one read may start ST in the next.
                if introducer in b']PX^_':
                    complete = (
                        self.pending.endswith(b'\x1b\\')
                        or (introducer == ord(']') and byte == 0x07)
                        or byte in (0x18, 0x1a)  # CAN/SUB cancel the sequence.
                        or len(self.pending) >= 4096
                    )
                else:
                    # CSI/SS3 final byte, or a two-byte ESC/Alt key.
                    complete = (
                        len(self.pending) == 2 and introducer not in b'[O'
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
