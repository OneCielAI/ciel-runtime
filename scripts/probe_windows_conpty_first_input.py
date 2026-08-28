"""Probe the first parent-console character forwarded through Windows ConPTY."""

from __future__ import annotations

import os
import shutil
import sys
import time

from ciel_runtime_support.channel_injection import (
    ChannelPromptInjector,
    PromptInjection,
    RuntimeInjectionPolicy,
)
from ciel_runtime_support.windows_conpty import WindowsConPtySession
from ciel_runtime_support.windows_console_input import WindowsConsoleInputWriter


PAYLOAD = b"XYZ"


class ProbeSession(WindowsConPtySession):
    def _read_input_bytes(self) -> bytes:
        try:
            data = super()._read_input_bytes()
        except BaseException as exc:
            print(f"PARENT_READ_ERROR={type(exc).__name__}:{exc}", flush=True)
            raise
        print(f"PARENT_READ={data!r}", flush=True)
        return data


def probe_transport() -> int:
    child = (
        "import ctypes,sys; from ctypes import wintypes; "
        "k=ctypes.WinDLL('kernel32'); h=k.GetStdHandle(-10); "
        "m=wintypes.DWORD(); k.GetConsoleMode(h,ctypes.byref(m)); "
        "k.SetConsoleMode(h,(m.value & ~0x1f) | 0x200); "
        "print('CHILD_READY', flush=True); "
        f"data=sys.stdin.buffer.read({len(PAYLOAD)}); "
        "print('RECV=' + data.hex(), flush=True); "
        f"raise SystemExit(0 if data == {PAYLOAD!r} else 9)"
    )
    session = ProbeSession(
        [sys.executable, "-c", child],
        dict(os.environ),
        log=lambda _level, _message: None,
        mirror_output=False,
        forward_stdin=True,
    )
    try:
        deadline = time.monotonic() + 5.0
        while b"CHILD_READY" not in session.output_tail() and time.monotonic() < deadline:
            time.sleep(0.01)
        if b"CHILD_READY" not in session.output_tail():
            print(f"CHILD_NOT_READY tail={session.output_tail()!r}", flush=True)
            return 8
        print("HARNESS_READY injecting XYZ as Win32 KEY_EVENT records", flush=True)
        writer = WindowsConsoleInputWriter(
            lambda: session._stdin_console_handle,
            lambda: type("PassThrough", (), {"feed": staticmethod(lambda data: data)})(),
        )
        writer._write_chars(list(PAYLOAD.decode("ascii")))
        deadline = time.monotonic() + 2.0
        while not session.manual_input_active() and time.monotonic() < deadline:
            time.sleep(0.01)
        logs: list[str] = []
        submitted = ChannelPromptInjector(
            sleep=lambda _seconds: None,
            retry_delay_seconds=lambda: 0.0,
            snapshot=lambda: None,
            log=lambda _level, message: logs.append(message),
        ).inject(
            session,
            PromptInjection(
                prompt="external wake must not replace XYZ",
                policy=RuntimeInjectionPolicy(
                    runtime="claude",
                    clear_input=b"\x15",
                    submit_input=b"\r",
                    submit_delay_seconds=0.0,
                ),
            ),
        )
        print(
            f"DRAFT_GUARD active={session.manual_input_active()} "
            f"submitted={submitted} logs={logs!r}",
            flush=True,
        )
        if submitted or not session.manual_input_active():
            return 10
        result = session.wait(timeout=20.0)
        print(f"HARNESS_RC={result} TAIL={session.output_tail()!r}", flush=True)
        return int(result)
    finally:
        session.close()


def _wait_for_output_stable(session: WindowsConPtySession, timeout: float = 12.0) -> bytes:
    deadline = time.monotonic() + timeout
    last_size = -1
    stable_since = time.monotonic()
    while time.monotonic() < deadline:
        current = session.output_tail()
        size = len(current)
        if size != last_size:
            last_size = size
            stable_since = time.monotonic()
        elif size > 0 and time.monotonic() - stable_since >= 1.0:
            return current
        time.sleep(0.05)
    return session.output_tail()


def probe_claude_tui(*, after_local_command: bool = False) -> int:
    executable = shutil.which("claude")
    if not executable:
        print("CLAUDE_NOT_FOUND", flush=True)
        return 7
    session = WindowsConPtySession(
        [executable, "--dangerously-skip-permissions", "--permission-mode", "bypassPermissions"],
        dict(os.environ),
        log=lambda _level, _message: None,
        mirror_output=False,
        forward_stdin=False,
    )
    try:
        baseline = _wait_for_output_stable(session)
        print(f"CLAUDE_READY baseline={len(baseline)}", flush=True)
        if after_local_command:
            session.write(b"/help\r")
            _wait_for_output_stable(session)
            session.write(b"\x1b")
            baseline = _wait_for_output_stable(session, 5.0)
            print(f"CLAUDE_POST_COMMAND_READY baseline={len(baseline)}", flush=True)
        session.write(PAYLOAD)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            current = session.output_tail()
            if current.count(PAYLOAD) > baseline.count(PAYLOAD):
                print("CLAUDE_FIRST_INPUT_OK", flush=True)
                return 0
            time.sleep(0.05)
        current = session.output_tail()
        print(f"CLAUDE_FIRST_INPUT_MISSING tail={current[-2000:]!r}", flush=True)
        return 9
    finally:
        session.close()


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--claude-after-command":
        return probe_claude_tui(after_local_command=True)
    if len(sys.argv) > 1 and sys.argv[1] == "--claude":
        return probe_claude_tui()
    return probe_transport()


if __name__ == "__main__":
    raise SystemExit(main())
