import os
import select
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from ciel_runtime_support.terminal_modal_guard import TerminalModalGuard

ROOT = Path(__file__).resolve().parents[1]
POSIX_PTY = os.name == "posix" and hasattr(os, "openpty")

# Claude Code 2.1.283 positions words with cursor moves, not spaces.
TRUST_DIALOG = (
    b"\x1b[?25l\x1b[2J Accessing\x1b[1Cworkspace:\r\n /home/sarah-ai\r\n"
    b" Quick\x1b[1Csafety\x1b[1Ccheck:\x1b[1CIs\x1b[1Cthis\x1b[1Ca\x1b[1Cproject\r\n"
    b" \x1b[36m\xe2\x9d\xaf\x1b[39m No,\x1b[1Cexit\r\n   Yes,\x1b[1CI\x1b[1Ctrust\x1b[1Cthis\x1b[1Cfolder\r\n"
    b" Enter\x1b[1Cto\x1b[1Cconfirm\x1b[1C\xc2\xb7\x1b[1CEsc\x1b[1Cto\x1b[1Ccancel\r\n"
)
MAIN_SCREEN = b"\x1b[2J\x1b7 \xe2\x9d\xaf Try \"how does <filepath> work?\"\r\n  ? for shortcuts\x1b8"


class TerminalModalGuardTests(unittest.TestCase):
    def test_startup_holds_until_the_first_input_screen_or_the_hold_ends(self) -> None:
        now = [100.0]
        guard = TerminalModalGuard(clock=lambda: now[0], startup_hold_seconds=15.0)
        self.assertTrue(guard.blocking)  # nothing drawn yet
        guard.feed(MAIN_SCREEN)
        self.assertFalse(guard.blocking)

        quiet = TerminalModalGuard(clock=lambda: now[0], startup_hold_seconds=15.0)
        quiet.feed(b"$ some other runtime prompt> ")
        self.assertTrue(quiet.blocking)
        now[0] += 15.1
        self.assertFalse(quiet.blocking)  # a CLI without known markers still gets input

    def test_startup_trust_dialog_holds_until_the_input_screen_is_drawn(self) -> None:
        guard = TerminalModalGuard(startup_hold_seconds=0.0)
        self.assertFalse(guard.blocking)

        guard.feed(TRUST_DIALOG)
        self.assertTrue(guard.blocking)

        guard.feed(MAIN_SCREEN)
        self.assertFalse(guard.blocking)

    def test_markers_and_escapes_cut_by_read_boundaries(self) -> None:
        guard = TerminalModalGuard(startup_hold_seconds=0.0)
        data = TRUST_DIALOG
        for start in range(0, len(data), 7):  # 7-byte reads split words, escapes and UTF-8
            guard.feed(data[start : start + 7])
        self.assertTrue(guard.blocking)
        for start in range(0, len(MAIN_SCREEN), 5):
            guard.feed(MAIN_SCREEN[start : start + 5])
        self.assertFalse(guard.blocking)

    def test_codex_trust_prompt_and_busy_footer(self) -> None:
        guard = TerminalModalGuard(startup_hold_seconds=0.0)
        guard.feed(b"> Do you trust the contents of this directory? Working with untrusted contents\r\n")
        self.assertTrue(guard.blocking)
        guard.feed(b"\x1b[2K Working (3s \xe2\x80\xa2 esc to interrupt)")
        self.assertFalse(guard.blocking)

    def test_long_output_without_markers_does_not_block(self) -> None:
        guard = TerminalModalGuard(startup_hold_seconds=0.0)
        guard.feed(MAIN_SCREEN)
        for _ in range(200):
            guard.feed(b"\x1b[32mline of model output with confirm and trust words\x1b[0m\r\n")
        self.assertFalse(guard.blocking)


# A Claude-like child: a trust dialog whose default is "No, exit" (any key exits 1);
# the user accepts after 2 s, then the input screen reads one prompt line.
FAKE_CLAUDE = textwrap.dedent(
    r"""
    import os, select, sys, time, tty
    tty.setraw(0)
    os.write(1, b" Quick safety check: Is this a project you trust?\r\n \xe2\x9d\xaf No, exit\r\n   Yes, I trust this folder\r\n Enter to confirm \xc2\xb7 Esc to cancel\r\n")
    deadline = time.time() + 2.0
    while time.time() < deadline:
        if select.select([0], [], [], 0.05)[0]:
            os.read(0, 4096)
            os.write(1, b"\r\nexit: No\r\n")
            sys.exit(1)
    os.write(1, b"\x1b[2J \xe2\x9d\xaf \r\n  ? for shortcuts\r\n")
    line = b""
    while not line.endswith(b"\r"):
        if select.select([0], [], [], 10)[0]:
            line += os.read(0, 4096)
        else:
            break
    os.write(1, b"GOT:" + line.strip() + b"\r\n")
    """
)

RUNNER = textwrap.dedent(
    """
    import subprocess, sys, time
    sys.path.insert(0, sys.argv[1])
    from ciel_runtime_support.channel_terminal_proxy import (
        ChannelTerminalIO, ChannelTerminalPolicy, ChannelTerminalPolling, ChannelTerminalProcess,
        ChannelTerminalServices, run_posix_channel_terminal_proxy)
    from ciel_runtime_support.channel_wake_context import ChannelWakeContext

    sent = []

    class PassFilter:
        def feed(self, data):
            return data

    def inject_pending(writer, last_id, _enter, **_options):
        if not sent:
            sent.append(1)
            ChannelWakeContext.write_all(writer, b"[walkie] hello")
            time.sleep(0.2)
            ChannelWakeContext.write_all(writer, b"\\r")
        return last_id

    def terminate(proc, _reason):
        if proc.poll() is None:
            proc.kill()

    logs = []
    services = ChannelTerminalServices(
        process=ChannelTerminalProcess(subprocess.Popen, lambda *_a: None, terminate, lambda *_a: None),
        io=ChannelTerminalIO(lambda _fd: (24, 80), lambda *_a: False, ChannelWakeContext.write_all,
                             PassFilter, lambda *_a, **_k: None, lambda: None),
        policy=ChannelTerminalPolicy(lambda: 0, lambda _v: b"\\r", repr, lambda: True, lambda: 30.0,
                                     lambda *_a, **_k: False, lambda level, message: logs.append(message)),
        polling=ChannelTerminalPolling(lambda *_a, **_k: "none", lambda: (0.0, 0), lambda *_a: True,
                                       lambda: False, lambda: False, inject_pending,
                                       lambda _i: None, lambda: None),
    )
    code = run_posix_channel_terminal_proxy([sys.executable, sys.argv[2]], {"PATH": "/usr/bin:/bin"}, services)
    sys.stdout.write(f"\\r\\nCHILD_EXIT={code} HELD={'channel_stdin_proxy_input_held reason=cli_dialog' in logs}\\r\\n")
    sys.stdout.flush()
    """
)


@unittest.skipUnless(POSIX_PTY, "needs a POSIX pty")
class PosixRelayDialogTests(unittest.TestCase):
    def test_channel_prompt_waits_for_the_trust_dialog_instead_of_answering_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            child = Path(tmp) / "fake_claude.py"
            runner = Path(tmp) / "runner.py"
            child.write_text(FAKE_CLAUDE, encoding="utf-8")
            runner.write_text(RUNNER, encoding="utf-8")
            master, slave = os.openpty()
            proc = subprocess.Popen(
                [sys.executable, str(runner), str(ROOT), str(child)],
                stdin=slave, stdout=slave, stderr=slave, close_fds=True, start_new_session=True,
            )
            os.close(slave)
            output = bytearray()
            try:
                deadline = time.time() + 20
                while b"CHILD_EXIT=" not in output and time.time() < deadline:
                    if select.select([master], [], [], 0.2)[0]:
                        try:
                            chunk = os.read(master, 65536)
                        except OSError:
                            break
                        if not chunk:
                            break
                        output.extend(chunk)
                time.sleep(0.2)
                while select.select([master], [], [], 0)[0]:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    output.extend(chunk)
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=5)
                os.close(master)

        text = bytes(output)
        self.assertNotIn(b"exit: No", text, text[-300:])
        self.assertIn(b"GOT:[walkie] hello", text, text[-300:])
        self.assertIn(b"CHILD_EXIT=0 HELD=True", text, text[-300:])


if __name__ == "__main__":
    unittest.main()
