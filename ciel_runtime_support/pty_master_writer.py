"""Writes into a POSIX PTY master that never stop draining the child's output.

A TUI child such as Codex reads its input on the same thread that renders, so
while its terminal output is backed up it stops reading input.  A blocking
write into the master that waits for input space without also reading the
master therefore deadlocks both sides: 2026-09-27 on robert-ai the runtime sat
in write(ptmx, 5031) while Codex sat in write(stdout, 1024), both in
wait_woken (journal docs/journal/2026/09/27/fixes/pty/bidirectional-relay.okf).

Every PTY input path (typed keys, channel prompts, compaction, synthetic Enter)
goes through ``write``, so they all keep draining output while they wait.
"""

from __future__ import annotations

import os
import select
import time
from collections.abc import Callable
from typing import Any

READ_CHUNK_BYTES = 4096
STALL_LOG_SECONDS = 5.0


class PtyMasterWriter:
    """Input side of a non-blocking PTY master.

    ``write`` returns once all bytes are in the child's input queue, like the
    blocking write it replaces, but forwards child output through
    ``forward_output`` whenever the input queue is full.
    """

    def __init__(
        self,
        master_fd: int,
        forward_output: Callable[[bytes], Any],
        child_alive: Callable[[], bool],
        log: Callable[[str, str], Any],
        *,
        wait: Callable[..., Any] = select.select,
        clock: Callable[[], float] = time.monotonic,
        stall_log_seconds: float = STALL_LOG_SECONDS,
    ) -> None:
        self._fd = master_fd
        self._forward_output = forward_output
        self._child_alive = child_alive
        self._log = log
        self._wait = wait
        self._clock = clock
        self._stall_log_seconds = stall_log_seconds
        os.set_blocking(master_fd, False)

    def fileno(self) -> int:
        return self._fd

    def read_output(self) -> bytes | None:
        """Read one chunk of child output; b"" when none is ready, None at EOF."""

        try:
            data = os.read(self._fd, READ_CHUNK_BYTES)
        except (BlockingIOError, InterruptedError):
            return b""
        return data or None

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        stalled_at: float | None = None
        stall_logged = False
        while view:
            try:
                written = os.write(self._fd, view)
            except (BlockingIOError, InterruptedError):
                written = 0
            if written:
                view = view[written:]
                continue
            if not self._child_alive():
                self._log("WARN", f"pty_input_dropped reason=child_exited pending_bytes={len(view)}")
                return
            now = self._clock()
            if stalled_at is None:
                stalled_at = now
            elif not stall_logged and now - stalled_at >= self._stall_log_seconds:
                stall_logged = True
                self._log(
                    "WARN",
                    f"pty_input_backpressure pending_bytes={len(view)} waited={now - stalled_at:.1f}s",
                )
            try:
                readable, _, _ = self._wait([self._fd], [self._fd], [], 0.2)
            except InterruptedError:
                continue
            if self._fd in readable:
                try:
                    output = self.read_output()
                except OSError as exc:
                    # EIO: the child closed its side while input was pending.
                    self._log("WARN", f"pty_input_dropped reason=master_read errno={exc.errno} pending_bytes={len(view)}")
                    return
                if output is None:
                    self._log("WARN", f"pty_input_dropped reason=master_eof pending_bytes={len(view)}")
                    return
                if output:
                    self._forward_output(output)
        if stall_logged and stalled_at is not None:
            self._log("INFO", f"pty_input_backpressure_cleared waited={self._clock() - stalled_at:.1f}s")

    def pause(self, seconds: float) -> None:
        """Wait ``seconds`` while forwarding whatever the child draws meanwhile.

        Channel injection runs inside the relay loop, so a plain sleep there
        keeps the child's reaction to the prompt out of the terminal; a
        submission check that compares tmux pane snapshots then never sees
        the TUI change and reports a prompt the child did accept as not
        submitted (robert-ai 2026-09-28: 495 of 618 Walkie inputs).
        """

        deadline = self._clock() + max(0.0, float(seconds))
        while True:
            remaining = deadline - self._clock()
            if remaining <= 0:
                return
            try:
                readable, _, _ = self._wait([self._fd], [], [], min(0.2, remaining))
            except InterruptedError:
                continue
            if self._fd not in readable:
                continue
            try:
                output = self.read_output()
            except OSError:
                return
            if output is None:
                return
            if output:
                self._forward_output(output)


__all__ = ["PtyMasterWriter"]
