"""Read an owned ConPTY child's rendered screen without detaching its launcher."""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time


class WindowsStartupScreenReader:
    """A separate console attachment; failures return unknown, never readiness."""

    def __init__(self, pid: int) -> None:
        self._closed = False
        self._rows: queue.Queue[str | None] = queue.Queue(maxsize=8)
        self._sequence = 0
        self._sampled_at = 0.0
        self._screen: str | None = None
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI"))}
        self._proc = subprocess.Popen(
            [sys.executable, "-I", "-u", str(Path(__file__).resolve()), str(int(pid))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        try:
            for line in self._proc.stdout:
                try:
                    self._rows.put_nowait(line)
                except queue.Full:
                    return
        finally:
            try:
                self._rows.put_nowait(None)
            except queue.Full:
                pass

    def snapshot(self) -> str | None:
        if self._closed or self._proc.poll() is not None:
            return None
        now = time.monotonic()
        if now - self._sampled_at < 0.25:
            return self._screen
        self._sequence += 1
        sequence = self._sequence
        try:
            self._proc.stdin.write(str(sequence) + chr(10))
            self._proc.stdin.flush()
            deadline = time.monotonic() + 1.0
            while True:
                line = self._rows.get(timeout=max(0.001, deadline - time.monotonic()))
                if line is None:
                    raise OSError("screen reader ended")
                row = json.loads(line)
                if row.get("sequence") == sequence:
                    screen = row.get("screen")
                    self._screen = screen if isinstance(screen, str) else None
                    self._sampled_at = time.monotonic()
                    return self._screen
                if time.monotonic() >= deadline:
                    raise OSError("screen reader response timed out")
        except (OSError, ValueError, queue.Empty):
            self.close()
            return None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._proc.stdin.close()
            self._proc.wait(timeout=1.0)
        except (OSError, subprocess.TimeoutExpired):
            self._proc.terminate()
            try:
                self._proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=1.0)
        finally:
            self._proc.stdout.close()
            self._reader.join(timeout=1.0)


def _serve(pid: int) -> None:
    import ctypes
    from ctypes import wintypes

    # -I omits the script directory, so add only this trusted package's root.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from ciel_runtime_support.startup_prompt_answers import windows_startup_screen

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.AttachConsole.argtypes = [wintypes.DWORD]
    kernel.AttachConsole.restype = wintypes.BOOL
    kernel.FreeConsole()
    attached = bool(kernel.AttachConsole(pid))
    try:
        for line in sys.stdin:
            sequence = int(line.strip())
            screen = windows_startup_screen() if attached else None
            print(json.dumps({"sequence": sequence, "screen": screen}), flush=True)
    finally:
        if attached:
            kernel.FreeConsole()


if __name__ == "__main__":
    _serve(int(sys.argv[1]))
