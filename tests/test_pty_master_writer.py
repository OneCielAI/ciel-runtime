import hashlib
import os
import select
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

from ciel_runtime_support.pty_master_writer import PtyMasterWriter

ROOT = Path(__file__).resolve().parents[1]
POSIX_PTY = os.name == "posix" and hasattr(os, "openpty")

# A TUI that renders on the same thread that reads input: it only reads a few
# bytes between output writes, so a full output queue stops it reading input.
FLOODING_CHILD = textwrap.dedent(
    """
    import hashlib, os, select, sys, time, tty
    tty.setraw(0)
    expected = int(sys.argv[1])
    digest = hashlib.sha256()
    got = 0
    line = b"o" * 1022 + b"\\r\\n"
    deadline = time.time() + 20
    while got < expected and time.time() < deadline:
        os.write(1, line)
        if select.select([0], [], [], 0)[0]:
            chunk = os.read(0, 64)
            digest.update(chunk)
            got += len(chunk)
    os.write(1, f"\\r\\nCHILD_DONE received={got} sha256={digest.hexdigest()}\\r\\n".encode())
    """
)

# A TUI that echoes its draft, drops bracketed-paste markers and logs each
# submitted line on CR, the way Codex shows a prompt it has taken.
ECHO_TUI_CHILD = textwrap.dedent(
    """
    import os, sys, tty
    tty.setraw(0)
    os.write(1, b"ready\\r\\n> ")
    draft = b""
    while True:
        data = os.read(0, 4096)
        if not data:
            break
        data = data.replace(b"\\x1b[200~", b"").replace(b"\\x1b[201~", b"")
        for byte in data:
            ch = bytes([byte])
            if ch == b"\\x15":
                draft = b""
            elif ch == b"\\r":
                if draft:
                    with open(sys.argv[1], "ab") as log:
                        log.write(draft + b"\\n")
                    os.write(1, b"\\r\\n[SUBMITTED] " + draft + b"\\r\\n> ")
                    draft = b""
            else:
                draft += ch
                os.write(1, ch)
    """
)


class PtyMasterWriterTests(unittest.TestCase):
    def _writer(self, *, alive=lambda: True, clock=None, stall_log_seconds=5.0):
        forwarded: list[bytes] = []
        logs: list[tuple[str, str]] = []
        with mock.patch("ciel_runtime_support.pty_master_writer.os.set_blocking", create=True):
            writer = PtyMasterWriter(
                7,
                forwarded.append,
                alive,
                lambda level, message: logs.append((level, message)),
                wait=lambda read, _write, _error, _timeout: (read, [], []),
                clock=clock or time.monotonic,
                stall_log_seconds=stall_log_seconds,
            )
        return writer, forwarded, logs

    def test_full_input_queue_drains_child_output_until_write_completes(self):
        writer, forwarded, logs = self._writer()
        writes = iter([BlockingIOError(), BlockingIOError(), 3, 2])
        reads = iter([b"frame-1", b"frame-2"])

        def fake_write(_fd, view):
            result = next(writes)
            if isinstance(result, BaseException):
                raise result
            return result

        with (
            mock.patch("ciel_runtime_support.pty_master_writer.os.write", side_effect=fake_write),
            mock.patch("ciel_runtime_support.pty_master_writer.os.read", side_effect=lambda _fd, _n: next(reads)),
        ):
            writer.write(b"hello")

        self.assertEqual([b"frame-1", b"frame-2"], forwarded)
        self.assertEqual([], logs)

    def test_child_exit_while_input_is_blocked_drops_the_rest(self):
        writer, forwarded, logs = self._writer(alive=lambda: False)
        with mock.patch(
            "ciel_runtime_support.pty_master_writer.os.write", side_effect=BlockingIOError()
        ):
            writer.write(b"abc")

        self.assertEqual([], forwarded)
        self.assertIn(("WARN", "pty_input_dropped reason=child_exited pending_bytes=3"), logs)

    def test_long_backpressure_is_logged_once_without_input_content(self):
        now = [100.0]

        def clock():
            now[0] += 2.0
            return now[0]

        writer, _forwarded, logs = self._writer(clock=clock, stall_log_seconds=5.0)
        writes = iter([BlockingIOError()] * 5 + [6])

        def fake_write(_fd, _view):
            result = next(writes)
            if isinstance(result, BaseException):
                raise result
            return result

        with (
            mock.patch("ciel_runtime_support.pty_master_writer.os.write", side_effect=fake_write),
            mock.patch("ciel_runtime_support.pty_master_writer.os.read", side_effect=BlockingIOError()),
        ):
            writer.write(b"secret")

        stalls = [message for level, message in logs if "pty_input_backpressure pending" in message]
        self.assertEqual(1, len(stalls))
        self.assertIn("pending_bytes=6", stalls[0])
        self.assertTrue(all("secret" not in message for _level, message in logs))
        self.assertTrue(any("pty_input_backpressure_cleared" in message for _level, message in logs))

    def test_pause_forwards_child_output_until_the_deadline(self):
        now = [10.0]

        def clock():
            now[0] += 0.1
            return now[0]

        writer, forwarded, _logs = self._writer(clock=clock)
        reads = iter([b"frame-1", BlockingIOError(), b"frame-2"])

        def fake_read(_fd, _n):
            result = next(reads, b"")
            if isinstance(result, BaseException):
                raise result
            return result

        with mock.patch("ciel_runtime_support.pty_master_writer.os.read", side_effect=fake_read):
            writer.pause(0.5)

        self.assertEqual([b"frame-1", b"frame-2"], forwarded)

    def test_pause_stops_at_child_eof(self):
        writer, forwarded, _logs = self._writer()
        with mock.patch("ciel_runtime_support.pty_master_writer.os.read", return_value=b""):
            writer.pause(30.0)

        self.assertEqual([], forwarded)

    @unittest.skipUnless(POSIX_PTY, "needs a POSIX pty")
    def test_confirmed_injection_sees_the_child_react_through_the_relay(self):
        # robert-ai 2026-09-28: the injector slept inside the relay loop, so
        # the terminal (tmux pane) never showed Codex taking the prompt and
        # 495 accepted Walkie inputs were recorded as prompt_not_submitted.
        from ciel_runtime_support import channel_injection as ci

        with tempfile.TemporaryDirectory() as tmp:
            child = Path(tmp) / "child.py"
            submitted = Path(tmp) / "submitted.log"
            child.write_text(ECHO_TUI_CHILD, encoding="utf-8")
            master, slave = os.openpty()
            proc = subprocess.Popen(
                [sys.executable, str(child), str(submitted)],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                close_fds=True,
            )
            os.close(slave)
            screen = bytearray()
            logs: list[str] = []
            try:
                writer = PtyMasterWriter(master, screen.extend, lambda: proc.poll() is None, lambda *_a: None)
                def pump(seconds):
                    end = time.time() + seconds
                    while time.time() < end:
                        if select.select([master], [], [], 0.05)[0]:
                            screen.extend(writer.read_output() or b"")

                deadline = time.time() + 10
                while b"ready" not in screen and time.time() < deadline:
                    pump(0.1)
                injector = ci.ChannelPromptInjector(
                    sleep=time.sleep,
                    retry_delay_seconds=lambda: 0.3,
                    snapshot=lambda: bytes(screen).decode("utf-8", "replace"),
                    log=lambda _level, message: logs.append(message),
                )
                accepted = injector.inject(
                    ci.CallableInputTransport(writer, lambda target, data: target.write(data)),
                    ci.PromptInjection(
                        prompt="walkie message 613",
                        policy=ci.RuntimeInjectionPolicy(
                            runtime="interactive-cli",
                            clear_input=b"\x15",
                            submit_input=b"\r",
                            submit_delay_seconds=0.1,
                            submit_attempts=4,
                            confirm_submission=True,
                            bracketed_paste=True,
                        ),
                    ),
                )
                pump(0.3)
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=5)
                os.close(master)
            received = submitted.read_text(encoding="utf-8").splitlines()

        self.assertTrue(accepted, logs)
        self.assertIn("channel_stdin_proxy_submit_confirmed attempt=1", logs)
        self.assertEqual(["walkie message 613"], received)

    @unittest.skipUnless(POSIX_PTY, "needs a POSIX pty")
    def test_real_pty_input_larger_than_the_queue_reaches_a_flooding_child(self):
        payload = bytes(range(256)) * 256  # 64 KiB, well past the 4 KiB N_TTY input queue
        with tempfile.TemporaryDirectory() as tmp:
            child = Path(tmp) / "child.py"
            child.write_text(FLOODING_CHILD, encoding="utf-8")
            master, slave = os.openpty()
            proc = subprocess.Popen(
                [sys.executable, str(child), str(len(payload))],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                close_fds=True,
            )
            os.close(slave)
            output = bytearray()
            try:
                writer = PtyMasterWriter(master, output.extend, lambda: proc.poll() is None, lambda *_a: None)
                time.sleep(0.3)
                writer.write(payload)
                deadline = time.time() + 20
                while b"CHILD_DONE" not in output and time.time() < deadline:
                    if select.select([master], [], [], 0.2)[0]:
                        chunk = writer.read_output()
                        if chunk is None:
                            break
                        output.extend(chunk)
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=5)
                os.close(master)

        expected = f"CHILD_DONE received={len(payload)} sha256={hashlib.sha256(payload).hexdigest()}"
        self.assertTrue(expected.encode() in output, f"child never finished; output tail {bytes(output[-120:])!r}")


@unittest.skipUnless(POSIX_PTY, "needs a POSIX pty")
class PosixChannelTerminalProxyDeadlockTests(unittest.TestCase):
    RUNNER = textwrap.dedent(
        """
        import os, subprocess, sys, time
        sys.path.insert(0, sys.argv[1])
        # The synthetic child draws no CLI input screen; do not wait the startup hold.
        os.environ["CIEL_RUNTIME_CHANNEL_STARTUP_HOLD_SECONDS"] = "0"
        from ciel_runtime_support.channel_terminal_proxy import (
            ChannelTerminalIO, ChannelTerminalPolicy, ChannelTerminalPolling, ChannelTerminalProcess,
            ChannelTerminalServices, run_posix_channel_terminal_proxy)
        from ciel_runtime_support.channel_wake_context import ChannelWakeContext

        payload = bytes(range(256)) * 80
        start = time.time()
        injected = []

        class PassFilter:
            def feed(self, data):
                return data

        def inject_pending(writer, last_id, _enter, **_options):
            if not injected and time.time() - start > 0.5:
                injected.append(1)
                ChannelWakeContext.write_all(writer, payload)
            return last_id

        def terminate(proc, _reason):
            if proc.poll() is None:
                proc.kill()

        services = ChannelTerminalServices(
            process=ChannelTerminalProcess(subprocess.Popen, lambda *_a: None, terminate, lambda *_a: None),
            io=ChannelTerminalIO(lambda _fd: (24, 80), lambda *_a: False, ChannelWakeContext.write_all,
                                 PassFilter, lambda *_a, **_k: None, lambda: None),
            policy=ChannelTerminalPolicy(lambda: 0, lambda _v: b"\\r", repr, lambda: True, lambda: 30.0,
                                         lambda *_a, **_k: False, lambda *_a: None),
            polling=ChannelTerminalPolling(lambda *_a, **_k: "none", lambda: (0.0, 0), lambda *_a: True,
                                           lambda: False, lambda: False, inject_pending,
                                           lambda _i: None, lambda: None),
        )
        sys.exit(run_posix_channel_terminal_proxy([sys.executable, sys.argv[2], str(len(payload))],
                                                  {"PATH": "/usr/bin:/bin"}, services))
        """
    )

    def test_channel_prompt_injected_while_child_floods_output_is_delivered(self):
        payload = bytes(range(256)) * 80
        with tempfile.TemporaryDirectory() as tmp:
            child = Path(tmp) / "child.py"
            runner = Path(tmp) / "runner.py"
            child.write_text(FLOODING_CHILD, encoding="utf-8")
            runner.write_text(self.RUNNER, encoding="utf-8")
            master, slave = os.openpty()
            proc = subprocess.Popen(
                [sys.executable, str(runner), str(ROOT), str(child)],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                close_fds=True,
                start_new_session=True,
            )
            os.close(slave)
            output = bytearray()
            try:
                deadline = time.time() + 20
                while b"CHILD_DONE" not in output and time.time() < deadline:
                    if select.select([master], [], [], 0.2)[0]:
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

        expected = f"CHILD_DONE received={len(payload)} sha256={hashlib.sha256(payload).hexdigest()}"
        self.assertTrue(expected.encode() in output, f"child never finished; output tail {bytes(output[-120:])!r}")


if __name__ == "__main__":
    unittest.main()
