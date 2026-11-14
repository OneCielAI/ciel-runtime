"""The POSIX relay reports the child's own exit status after a PTY hangup.

Linux fails the PTY master read with EIO once the child's last slave descriptor
is closed, which can be before a dying child can be reaped. The relay then saw
the hangup while ``poll()`` still said "running" and reported 1 for a child
that exited 0 (GitHub run 43196834134; cindy-ai 2026-09-27).

Scheduling decides how often that order happens, so these tests do not wait for
it: the child closes its descriptors itself and stays alive until the relay has
polled it, unreaped, after the EIO.
"""

import errno
import json
import os
import re
import select
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

from ciel_runtime_support import channel_terminal_proxy
from ciel_runtime_support.channel_terminal_proxy import resolve_posix_child_exit_status

ROOT = Path(__file__).resolve().parents[1]
# The hangup arriving as EIO on the master read is measured on Linux only; the
# PTY tests below build on it, so other POSIX systems are not covered by them.
LINUX_PTY = sys.platform.startswith("linux") and hasattr(os, "openpty")

HELD_CHILD = textwrap.dedent(
    """
    import os, signal, sys, time
    release, action = sys.argv[1], sys.argv[2]
    os.write(1, b"child ready\\r\\n")
    for fd in (0, 1, 2):
        os.close(fd)  # the last slave descriptors: the master read now fails with EIO
    deadline = time.monotonic() + 15
    while not os.path.exists(release):
        if time.monotonic() > deadline:
            os._exit(97)  # the relay never polled this child after the hangup
        time.sleep(0.005)
    if action == "stay":
        time.sleep(30)
    elif action == "sigterm":
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(30)
    os._exit(int(action) if action.isdigit() else 96)
    """
)

RUNNER = textwrap.dedent(
    """
    import errno, os, subprocess, sys, time
    root, child, release, action, wait_seconds, mode = sys.argv[1:7]
    sys.path.insert(0, root)
    # The synthetic child draws no CLI input screen; do not wait the startup hold.
    os.environ["CIEL_RUNTIME_CHANNEL_STARTUP_HOLD_SECONDS"] = "0"
    from ciel_runtime_support import channel_terminal_proxy as relay
    from ciel_runtime_support.channel_wake_context import ChannelWakeContext
    from ciel_runtime_support.pty_master_writer import PtyMasterWriter

    if wait_seconds != "-":
        relay.PTY_HANGUP_REAP_WAIT_SECONDS = float(wait_seconds)
    seen = {"eio": 0, "unreaped": 0, "eio_at": 0.0}
    children = []
    logs = []
    read_output = PtyMasterWriter.read_output

    def observed_read_output(self):
        try:
            return read_output(self)
        except OSError as exc:
            if exc.errno == errno.EIO and not seen["eio"]:
                seen["eio"] = 1
                seen["eio_at"] = time.monotonic()
            raise

    PtyMasterWriter.read_output = observed_read_output

    class HeldChild(subprocess.Popen):
        # Lets the child go only once the relay has polled it, still unreaped,
        # after the master read failed with EIO.
        def poll(self):
            code = super().poll()
            if code is None and seen["eio"] and not seen["unreaped"]:
                seen["unreaped"] = 1
                open(release, "w").close()
            return code

    def popen(*args, **kwargs):
        children.append(HeldChild(*args, **kwargs))
        return children[-1]

    def terminate(proc, _reason):
        # An exiting child gets the time to exit, so its own status is known.
        try:
            proc.wait(timeout=0 if action == "stay" else 10)
        except subprocess.TimeoutExpired:
            proc.kill()

    def log(level, message):
        logs.append(f"{level} {message}")

    class PassFilter:
        def feed(self, data):
            return data

    services = relay.ChannelTerminalServices(
        process=relay.ChannelTerminalProcess(popen, lambda *_a: None, terminate, lambda *_a: None),
        io=relay.ChannelTerminalIO(lambda _fd: (24, 80), lambda *_a: False, ChannelWakeContext.write_all,
                                   PassFilter, lambda *_a, **_k: None, lambda: None),
        policy=relay.ChannelTerminalPolicy(lambda: 0, lambda _v: b"\\r", repr, lambda: True, lambda: 30.0,
                                           lambda *_a, **_k: False, log),
        polling=relay.ChannelTerminalPolling(lambda *_a, **_k: "none", lambda: (0.0, 0), lambda *_a: True,
                                             lambda: False, lambda: False,
                                             lambda _writer, last_id, _enter, **_options: last_id,
                                             lambda _i: None, lambda: None),
    )
    cmd = [sys.executable, child, release, action]
    env = {"PATH": "/usr/bin:/bin"}

    if mode == "relay":
        code = relay.run_posix_channel_terminal_proxy(cmd, env, services)
    else:
        # The path a launch takes around the relay (runtime_launch.run_claude_process):
        # dispatch -> child session -> exit diagnostic -> the launcher's exit code.
        from pathlib import Path
        from ciel_runtime_support import runtime_launch
        from ciel_runtime_support.channel_terminal_dispatch import (
            ChannelDirectProcessPorts, ChannelTerminalDispatchService, ChannelTerminalDispatchSettings,
            ChannelTerminalProxyPorts)
        from ciel_runtime_support.router_client_lifecycle import (
            RoutedLaunchDiagnosticPorts, RoutedLaunchDiagnostics)

        dispatch = ChannelTerminalDispatchService(
            settings=ChannelTerminalDispatchSettings("posix", sys.stdin.isatty, sys.stdout.isatty),
            proxy=ChannelTerminalProxyPorts(lambda: False, lambda *_a, **_k: 98,
                                            relay.run_posix_channel_terminal_proxy, lambda: services),
            direct=ChannelDirectProcessPorts(lambda *_a, **_k: 98, popen, lambda *_a: None, terminate,
                                             lambda *_a: None),
            log=log,
        )
        diagnostics = RoutedLaunchDiagnostics(
            "http://router.test", Path(release + ".router.log"),
            RoutedLaunchDiagnosticPorts(lambda: None, lambda _health: "health=test",
                                        lambda _provider, _config: "upstream=test", log),
        )
        code = 1
        try:
            code = runtime_launch._child_session(
                "claude", "channel_proxy", lambda: dispatch.dispatch(cmd, env), log)
        finally:
            diagnostics.print_exit(code, "test", {}, log_offset=0)

    wait_ms = int((time.monotonic() - seen["eio_at"]) * 1000) if seen["eio"] else -1
    child_rc = children[0].wait(timeout=10) if children else None
    exit_log = " | ".join(m for m in logs if "channel_stdin_proxy_io_failed" in m
                          or "channel_stdin_proxy_child_exit" in m or "channel_stdin_proxy_exit_unresolved" in m)
    sys.stdout.write(
        f"\\r\\nRELAY_EXIT={code} CHILD_RC={child_rc} EIO_BEFORE_REAP={seen['unreaped']} "
        f"WAIT_MS={wait_ms} EXIT_LOG={exit_log}\\r\\n"
    )
    sys.stdout.flush()
    if mode == "launch":
        raise SystemExit(code)
    """
)

RESULT = re.compile(
    r"RELAY_EXIT=(?P<relay_exit>-?\d+) CHILD_RC=(?P<child_rc>-?\d+|None) "
    r"EIO_BEFORE_REAP=(?P<eio_before_reap>\d) WAIT_MS=(?P<wait_ms>-?\d+) EXIT_LOG=(?P<exit_log>[^\r\n]*)"
)

# What the relay prints when the wait for the child's exit has lasted a while.
# Kept literal so this test module still loads against the fail-before revisions.
WAIT_NOTICE = "Ciel Runtime: waiting for the CLI to finish exiting; press Ctrl-C to stop."

TERMINAL_CHILD = textwrap.dedent(
    """
    import os, signal, subprocess, sys, time
    work, scenario = sys.argv[1], sys.argv[2]

    def wait_for(name):
        deadline = time.monotonic() + 15
        while not os.path.exists(os.path.join(work, name)):
            if time.monotonic() > deadline:
                os._exit(97)  # nobody created the file
            time.sleep(0.005)

    if scenario == "stay":
        signal.signal(signal.SIGINT, signal.SIG_IGN)  # only the relay reacts to Ctrl-C
    if scenario == "nowait":
        # A grandchild keeps the terminal open, so this exit is no hangup and
        # the relay finds the child reaped at once.
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"])
        os.write(1, b"child ready\\r\\n")
        os._exit(0)
    os.write(1, b"child ready\\r\\n")
    if scenario == "raise":
        time.sleep(30)
        os._exit(96)
    for fd in (0, 1, 2):
        os.close(fd)  # the last slave descriptors: the master read now fails with EIO
    wait_for("waiting")  # the relay has polled this child, unreaped, after the EIO
    if scenario == "hold":
        wait_for("go")  # the outer test has looked at the terminal
    if scenario == "stay":
        time.sleep(30)
    os._exit(0)
    """
)

# Runs the relay as the foreground program of the outer PTY and records, in
# order, what the relay does to the terminal and which cleanup steps it takes.
# Every wrapper only calls through and appends one event.
TERMINAL_RUNNER = textwrap.dedent(
    """
    import errno, fcntl, json, os, signal, subprocess, sys, termios, time, tty
    root, child, work, scenario, wait_seconds = sys.argv[1:6]
    sys.path.insert(0, root)
    # The synthetic child draws no CLI input screen; do not wait the startup hold.
    os.environ["CIEL_RUNTIME_CHANNEL_STARTUP_HOLD_SECONDS"] = "0"
    # This session leader takes the outer PTY as its controlling terminal, so a
    # Ctrl-C typed there is a SIGINT for this process, as for a launcher in a shell.
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    signal.signal(signal.SIGINT, signal.default_int_handler)
    winch_before = signal.getsignal(signal.SIGWINCH)
    from ciel_runtime_support import channel_terminal_proxy as relay
    from ciel_runtime_support.channel_wake_context import ChannelWakeContext
    from ciel_runtime_support.pty_master_writer import PtyMasterWriter

    if wait_seconds != "-":
        relay.PTY_HANGUP_REAP_WAIT_SECONDS = float(wait_seconds)
    events = []
    seen = {"eio_at": None, "waiting": False, "master": None, "interaction_polls": 0}
    children = []
    logs = []

    tcsetattr = termios.tcsetattr

    def observed_tcsetattr(fd, when, attributes):
        tcsetattr(fd, when, attributes)
        events.append("termios_restore" if attributes[3] & termios.ICANON else "raw")

    termios.tcsetattr = tty.tcsetattr = observed_tcsetattr

    set_signal = signal.signal

    def observed_signal(signum, handler):
        if signum == signal.SIGWINCH:
            events.append("sigwinch_set")
        return set_signal(signum, handler)

    signal.signal = observed_signal

    open_pty = os.openpty

    def observed_openpty():
        master, slave = open_pty()
        seen["master"] = master
        return master, slave

    os.openpty = observed_openpty

    close_fd = os.close

    def observed_close(fd):
        if fd == seen["master"]:
            seen["master"] = None
            events.append("master_close")
        return close_fd(fd)

    os.close = observed_close

    close_admission = relay.TerminalRecoveryAdmissionReporter.close

    def observed_close_admission(self, *args, **kwargs):
        events.append("admission_close")
        return close_admission(self, *args, **kwargs)

    relay.TerminalRecoveryAdmissionReporter.close = observed_close_admission

    read_output = PtyMasterWriter.read_output

    def observed_read_output(self):
        try:
            return read_output(self)
        except OSError as exc:
            if exc.errno == errno.EIO and seen["eio_at"] is None:
                seen["eio_at"] = time.monotonic()
                events.append("eio")
            raise

    PtyMasterWriter.read_output = observed_read_output

    class WatchedChild(subprocess.Popen):
        # The first poll that finds the child unreaped after the EIO is where the
        # relay starts to wait. The child and the outer test are told through a file.
        def poll(self):
            code = super().poll()
            if code is None and seen["eio_at"] is not None and not seen["waiting"]:
                seen["waiting"] = True
                events.append("wait_begin")
                open(os.path.join(work, "waiting"), "w").close()
            return code

    def popen(*args, **kwargs):
        children.append(WatchedChild(*args, **kwargs))
        return children[-1]

    def terminate(proc, _reason):
        events.append("terminate_child")
        try:
            proc.wait(timeout=0 if scenario in ("stay", "raise") else 10)
        except subprocess.TimeoutExpired:
            proc.kill()

    def runtime_interaction():
        seen["interaction_polls"] += 1
        if scenario == "raise" and seen["interaction_polls"] == 3:
            raise RuntimeError("relay loop failure")
        return None

    class PassFilter:
        def feed(self, data):
            return data

    services = relay.ChannelTerminalServices(
        process=relay.ChannelTerminalProcess(popen, lambda *_a: None, terminate,
                                             lambda *_a: events.append("release_child_record")),
        io=relay.ChannelTerminalIO(lambda _fd: (24, 80), lambda *_a: False, ChannelWakeContext.write_all,
                                   PassFilter, lambda *_a, **_k: None,
                                   lambda: events.append("reset_input_mode")),
        policy=relay.ChannelTerminalPolicy(lambda: 0, lambda _v: b"\\r", repr, lambda: True, lambda: 30.0,
                                           lambda *_a, **_k: False,
                                           lambda level, message: logs.append(f"{level} {message}"),
                                           recovery_admission=lambda _snapshot: None),
        polling=relay.ChannelTerminalPolling(lambda *_a, **_k: "none", lambda: (0.0, 0), lambda *_a: True,
                                             lambda: False, lambda: False,
                                             lambda _writer, last_id, _enter, **_options: last_id,
                                             lambda _i: None, lambda: None,
                                             runtime_interaction=runtime_interaction),
    )
    outcome, code = "returned", None
    try:
        code = relay.run_posix_channel_terminal_proxy(
            [sys.executable, child, work, scenario], {"PATH": "/usr/bin:/bin"}, services)
    except BaseException as exc:
        outcome = type(exc).__name__
    returned_at = time.monotonic()
    flags = termios.tcgetattr(0)[3]
    result = {
        "outcome": outcome,
        "relay_exit": code,
        "child_rc": children[0].wait(timeout=10) if children else None,
        "events": events,
        "cooked_after": bool(flags & termios.ICANON and flags & termios.ISIG and flags & termios.ECHO),
        "winch_handler_restored": signal.getsignal(signal.SIGWINCH) == winch_before,
        "wait_ms": int((returned_at - seen["eio_at"]) * 1000) if seen["eio_at"] is not None else None,
        "exit_log": [m for m in logs if "channel_stdin_proxy_io_failed" in m
                     or "channel_stdin_proxy_child_exit" in m or "channel_stdin_proxy_exit_wait" in m],
    }
    with open(os.path.join(work, "result.json"), "w", encoding="utf-8") as handle:
        json.dump(result, handle)
    """
)

ORDINARY_CHILD = "import os, sys\nos.write(1, b'child ready\\r\\n')\nsys.exit(0)\n"

# No hook on poll or on the master read: an ordinary child that exits 0, with
# the relay and the child sharing one CPU. Nothing here forces the hangup to
# come before the reap; one CPU is what made it common where it was measured.
ORDINARY_RUNNER = textwrap.dedent(
    """
    import os, subprocess, sys
    root, child, rounds = sys.argv[1], sys.argv[2], int(sys.argv[3])
    sys.path.insert(0, root)
    os.environ["CIEL_RUNTIME_CHANNEL_STARTUP_HOLD_SECONDS"] = "0"
    os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
    from ciel_runtime_support import channel_terminal_proxy as relay
    from ciel_runtime_support.channel_wake_context import ChannelWakeContext

    children = []
    logs = []

    def popen(*args, **kwargs):
        children.append(subprocess.Popen(*args, **kwargs))
        return children[-1]

    def terminate(proc, _reason):
        # An exiting child gets the time to exit, so its own status is known.
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    class PassFilter:
        def feed(self, data):
            return data

    services = relay.ChannelTerminalServices(
        process=relay.ChannelTerminalProcess(popen, lambda *_a: None, terminate, lambda *_a: None),
        io=relay.ChannelTerminalIO(lambda _fd: (24, 80), lambda *_a: False, ChannelWakeContext.write_all,
                                   PassFilter, lambda *_a, **_k: None, lambda: None),
        policy=relay.ChannelTerminalPolicy(lambda: 0, lambda _v: b"\\r", repr, lambda: True, lambda: 30.0,
                                           lambda *_a, **_k: False,
                                           lambda level, message: logs.append(message)),
        polling=relay.ChannelTerminalPolling(lambda *_a, **_k: "none", lambda: (0.0, 0), lambda *_a: True,
                                             lambda: False, lambda: False,
                                             lambda _writer, last_id, _enter, **_options: last_id,
                                             lambda _i: None, lambda: None),
    )
    for _ in range(rounds):
        del children[:], logs[:]
        code = relay.run_posix_channel_terminal_proxy([sys.executable, child], {"PATH": "/usr/bin:/bin"}, services)
        child_rc = children[0].wait(timeout=10)
        event = next((m.split(" ")[0] for m in logs if "child_exit" in m or "io_failed" in m), "none")
        sys.stdout.write(f"\\r\\nROUND RELAY_EXIT={code} CHILD_RC={child_rc} EVENT={event}\\r\\n")
        sys.stdout.flush()
    """
)

ROUND = re.compile(r"ROUND RELAY_EXIT=(-?\d+) CHILD_RC=(-?\d+) EVENT=(\S+)")


def _run_on_outer_pty(argv, on_tick=None):
    """Run ``argv`` with a fresh PTY as its terminal: (output text, exit status).

    ``on_tick(master)`` is called about every 10 ms with the terminal's master
    side, where a test can read the terminal's mode or type into it.
    """

    master, slave = os.openpty()
    proc = subprocess.Popen(
        argv, stdin=slave, stdout=slave, stderr=slave, close_fds=True, start_new_session=True
    )
    os.close(slave)
    output = bytearray()
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            if on_tick is not None:
                on_tick(master)
            if not select.select([master], [], [], 0.2 if on_tick is None else 0.01)[0]:
                continue
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break  # the runner closed its side; its status comes from wait()
            if not chunk:
                break
            output.extend(chunk)
        try:
            status = proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            status = None
    finally:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)  # the runner and its child
            except ProcessLookupError:
                pass
            proc.wait(timeout=5)
        os.close(master)
    return bytes(output).decode("utf-8", "replace"), status


def _terminal_is_cooked(fd) -> bool:
    import termios

    flags = termios.tcgetattr(fd)[3]
    return bool(flags & termios.ICANON and flags & termios.ISIG and flags & termios.ECHO)


class _Child:
    """A child whose ``poll`` answers come from a script; the last one repeats."""

    def __init__(self, *answers):
        self._answers = list(answers)
        self.polls = 0

    def poll(self):
        self.polls += 1
        return self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]


class ResolvePosixChildExitStatusTests(unittest.TestCase):
    HANGUP = ("master_read", OSError(errno.EIO, "Input/output error"))

    def _resolve(self, child, io_failure, **options):
        logs: list[tuple[str, str]] = []
        status = resolve_posix_child_exit_status(
            child, lambda level, message: logs.append((level, message)), io_failure=io_failure, **options
        )
        return status, logs

    def test_child_not_yet_reaped_at_the_hangup_reports_its_own_exit_status(self):
        status, logs = self._resolve(_Child(None, None, 0), self.HANGUP)

        self.assertEqual(0, status, logs)
        self.assertTrue(
            any(
                level == "WARN"
                and "channel_stdin_proxy_child_exit_after_io_error stage=master_read errno=5" in message
                and "returncode=0 exit_status=0" in message
                for level, message in logs
            ),
            logs,
        )

    def test_signal_after_the_hangup_is_still_128_plus_the_signal(self):
        status, logs = self._resolve(_Child(None, -signal.SIGTERM), self.HANGUP)

        self.assertEqual(128 + signal.SIGTERM, status, logs)

    def test_wait_for_a_child_that_keeps_running_is_bounded(self):
        child = _Child(None)
        with mock.patch.object(channel_terminal_proxy, "PTY_HANGUP_REAP_WAIT_SECONDS", 0.2):
            started = time.monotonic()
            status, logs = self._resolve(child, self.HANGUP)
            elapsed = time.monotonic() - started

        self.assertEqual(1, status, logs)
        self.assertGreaterEqual(elapsed, 0.2)  # the child got the whole bound
        self.assertLess(elapsed, 3.0)  # and the relay did not wait on past it
        self.assertGreater(child.polls, 2)
        self.assertTrue(
            any(
                level == "ERROR"
                and "channel_stdin_proxy_io_failed stage=master_read errno=5" in message
                and "child_running=true exit_status=1" in message
                for level, message in logs
            ),
            logs,
        )

    def test_wait_long_enough_to_notice_tells_the_user_once(self):
        notices: list[str] = []
        with (
            mock.patch.object(channel_terminal_proxy, "PTY_HANGUP_REAP_WAIT_SECONDS", 0.3),
            mock.patch.object(channel_terminal_proxy, "PTY_HANGUP_NOTICE_AFTER_SECONDS", 0.1),
        ):
            status, logs = self._resolve(_Child(None), self.HANGUP, notify=notices.append)

        self.assertEqual(1, status, logs)
        self.assertEqual([WAIT_NOTICE], notices)
        self.assertIn("Ctrl-C", notices[0])
        self.assertNotIn("terminal", notices[0].lower())
        self.assertNotIn("failure", notices[0].lower())

    def test_wait_of_milliseconds_tells_the_user_nothing(self):
        notices: list[str] = []
        status, logs = self._resolve(_Child(None, None, 0), self.HANGUP, notify=notices.append)

        self.assertEqual(0, status, logs)
        self.assertEqual([], notices)

    def test_interrupted_wait_is_logged_and_the_interrupt_is_passed_on(self):
        class InterruptedChild:
            polls = 0

            def poll(self):
                self.polls += 1
                if self.polls > 1:
                    raise KeyboardInterrupt  # Ctrl-C while the relay waits
                return None

        logs: list[tuple[str, str]] = []
        with self.assertRaises(KeyboardInterrupt):
            resolve_posix_child_exit_status(
                InterruptedChild(), lambda level, message: logs.append((level, message)), io_failure=self.HANGUP
            )

        self.assertTrue(
            any("channel_stdin_proxy_exit_wait_interrupted stage=master_read" in message for _level, message in logs),
            logs,
        )

    def test_relay_failures_that_are_not_a_hangup_do_not_wait_for_the_child(self):
        for failure in (
            ("select", OSError(errno.EBADF, "Bad file descriptor")),
            ("select", OSError(errno.EIO, "Input/output error")),
            ("master_read", OSError(errno.EBADF, "Bad file descriptor")),
            None,
        ):
            with self.subTest(failure=failure):
                child = _Child(None, 0)  # a second poll would find it reaped
                status, logs = self._resolve(child, failure)

                self.assertEqual(1, status, logs)
                self.assertEqual(1, child.polls)
                self.assertTrue(
                    any(level == "ERROR" and "child_running=true" in message for level, message in logs), logs
                )


@unittest.skipUnless(LINUX_PTY, "needs a Linux pty: its master read fails with EIO at hangup")
class PosixRelayHangupBeforeReapTests(unittest.TestCase):
    def _relay(self, action: str, *, wait_seconds: str = "-", mode: str = "relay") -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            child = Path(tmp) / "held_child.py"
            runner = Path(tmp) / "runner.py"
            child.write_text(HELD_CHILD, encoding="utf-8")
            runner.write_text(RUNNER, encoding="utf-8")
            text, status = _run_on_outer_pty(
                [sys.executable, str(runner), str(ROOT), str(child), str(Path(tmp) / "release"),
                 action, wait_seconds, mode]
            )

        match = RESULT.search(text)
        self.assertIsNotNone(match, text[-800:])
        return {**match.groupdict(), "launcher_exit": status, "output": text, "line": match.group(0)}

    def test_child_exit_0_after_the_hangup_is_reported_as_0(self) -> None:
        result = self._relay("0")

        self.assertEqual("1", result["eio_before_reap"], result["line"])  # the order under test happened
        self.assertEqual("0", result["child_rc"], result["line"])
        self.assertEqual("0", result["relay_exit"], result["line"])

    def test_child_exit_code_after_the_hangup_is_reported_unchanged(self) -> None:
        result = self._relay("7")

        self.assertEqual("1", result["eio_before_reap"], result["line"])
        self.assertEqual("7", result["child_rc"], result["line"])
        self.assertEqual("7", result["relay_exit"], result["line"])

    def test_child_killed_by_a_signal_after_the_hangup_is_128_plus_the_signal(self) -> None:
        result = self._relay("sigterm")

        self.assertEqual("1", result["eio_before_reap"], result["line"])
        self.assertEqual(str(-signal.SIGTERM), result["child_rc"], result["line"])
        self.assertEqual(str(128 + signal.SIGTERM), result["relay_exit"], result["line"])

    def test_child_that_outlives_the_bounded_wait_is_still_a_relay_failure(self) -> None:
        result = self._relay("stay", wait_seconds="0.5")

        self.assertEqual("1", result["eio_before_reap"], result["line"])
        self.assertEqual("1", result["relay_exit"], result["line"])
        self.assertEqual(str(-signal.SIGKILL), result["child_rc"], result["line"])  # running until the relay let go
        self.assertIn("channel_stdin_proxy_io_failed stage=master_read errno=5", result["exit_log"])
        self.assertIn("child_running=true exit_status=1", result["exit_log"])
        waited = re.search(r"waited_ms=(\d+)", result["exit_log"])
        self.assertIsNotNone(waited, result["line"])  # the relay did wait for this child
        self.assertGreaterEqual(int(waited.group(1)), 500, result["line"])  # the whole 0.5 s bound
        self.assertGreaterEqual(int(result["wait_ms"]), 500, result["line"])
        self.assertLess(int(result["wait_ms"]), 4000, result["line"])  # and not the child's 30 s
        self.assertNotIn(WAIT_NOTICE, result["output"])  # too short a wait to tell the user about

    def test_shipped_bound_of_5_seconds_with_a_child_that_keeps_running(self) -> None:
        # The one slow test of this file (about 5 s). Every other test shortens the bound.
        self.assertEqual(5.0, channel_terminal_proxy.PTY_HANGUP_REAP_WAIT_SECONDS)

        result = self._relay("stay")

        self.assertEqual("1", result["eio_before_reap"], result["line"])
        self.assertEqual("1", result["relay_exit"], result["line"])
        self.assertEqual(str(-signal.SIGKILL), result["child_rc"], result["line"])
        waited = re.search(r"waited_ms=(\d+)", result["exit_log"])
        self.assertIsNotNone(waited, result["line"])
        self.assertGreaterEqual(int(waited.group(1)), 5000, result["line"])
        self.assertLess(int(waited.group(1)), 7500, result["line"])
        # A wait this long is announced once, so the quiet terminal is explained.
        self.assertEqual(1, result["output"].count(WAIT_NOTICE), result["output"][-800:])
        self.assertNotIn("closed its terminal", result["output"].lower())
        self.assertNotIn("failure", result["output"].lower())

    def test_launcher_exit_code_and_exit_diagnostic_follow_the_child(self) -> None:
        result = self._relay("0", mode="launch")

        self.assertEqual("1", result["eio_before_reap"], result["line"])
        self.assertEqual("0", result["child_rc"], result["line"])
        self.assertEqual(0, result["launcher_exit"], result["output"][-800:])
        self.assertNotIn("exited with code", result["output"], result["output"][-800:])


@unittest.skipUnless(LINUX_PTY, "needs a Linux pty: its master read fails with EIO at hangup")
class PosixRelayTerminalDuringExitWaitTests(unittest.TestCase):
    """The user's terminal while the relay waits for the child's exit status."""

    START = ["reset_input_mode", "sigwinch_set", "raw"]
    LEAVE = ["admission_close", "sigwinch_set", "termios_restore", "reset_input_mode"]
    END = ["master_close", "terminate_child", "release_child_record"]

    def _run(self, scenario: str, *, wait_seconds: str = "-", during_wait=None) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            child = work / "terminal_child.py"
            runner = work / "terminal_runner.py"
            child.write_text(TERMINAL_CHILD, encoding="utf-8")
            runner.write_text(TERMINAL_RUNNER, encoding="utf-8")
            observed = []

            def on_tick(master):
                # "waiting" appears when the relay has started to wait for the child.
                if during_wait is not None and not observed and (work / "waiting").exists():
                    observed.append(during_wait(master, work))

            text, status = _run_on_outer_pty(
                [sys.executable, str(runner), str(ROOT), str(child), str(work), scenario, wait_seconds],
                on_tick,
            )
            try:
                result = json.loads((work / "result.json").read_text(encoding="utf-8"))
            except OSError:
                self.fail(f"the runner left no result: status={status} output={text[-800:]!r}")
        return {**result, "during_wait": observed[0] if observed else None, "output": text}

    def _assert_terminal_restored_once(self, result: dict) -> None:
        self.assertEqual(1, result["events"].count("termios_restore"), result["events"])
        self.assertEqual(2, result["events"].count("reset_input_mode"), result["events"])  # start and end
        self.assertEqual(2, result["events"].count("sigwinch_set"), result["events"])  # install and restore
        self.assertEqual(1, result["events"].count("admission_close"), result["events"])
        self.assertTrue(result["cooked_after"], result)
        self.assertTrue(result["winch_handler_restored"], result)

    def test_terminal_is_cooked_while_the_relay_waits_for_the_child(self) -> None:
        def look_then_let_the_child_exit(master, work):
            cooked = _terminal_is_cooked(master)
            (work / "go").write_text("", encoding="utf-8")
            return cooked

        result = self._run("hold", during_wait=look_then_let_the_child_exit)

        self.assertIs(True, result["during_wait"], f"terminal still raw during the wait: {result['events']}")
        self.assertEqual(
            self.START + ["eio"] + self.LEAVE + ["wait_begin"] + self.END, result["events"]
        )
        self._assert_terminal_restored_once(result)
        self.assertEqual(("returned", 0, 0), (result["outcome"], result["relay_exit"], result["child_rc"]))
        self.assertNotIn(WAIT_NOTICE, result["output"])  # a wait of milliseconds prints nothing

    def test_ctrl_c_typed_during_the_wait_interrupts_it(self) -> None:
        result = self._run(
            "stay", wait_seconds="3", during_wait=lambda master, _work: os.write(master, b"\x03")
        )

        self.assertEqual("KeyboardInterrupt", result["outcome"], result)  # not the 3 s bound running out
        self.assertLess(result["wait_ms"], 2500, result)
        self.assertEqual(
            self.START + ["eio"] + self.LEAVE + ["wait_begin"] + self.END, result["events"]
        )
        self._assert_terminal_restored_once(result)
        self.assertEqual(-signal.SIGKILL, result["child_rc"], result)  # the cleanup still ended the child
        self.assertTrue(
            any("channel_stdin_proxy_exit_wait_interrupted" in line for line in result["exit_log"]), result
        )

    def test_terminal_is_restored_once_when_the_child_was_already_reaped(self) -> None:
        result = self._run("nowait")

        self.assertEqual(self.START + self.LEAVE + self.END, result["events"])
        self._assert_terminal_restored_once(result)
        self.assertEqual(("returned", 0, 0), (result["outcome"], result["relay_exit"], result["child_rc"]))

    def test_terminal_is_restored_once_after_a_waited_exit(self) -> None:
        result = self._run("exit0")

        self.assertEqual(
            self.START + ["eio"] + self.LEAVE + ["wait_begin"] + self.END, result["events"]
        )
        self._assert_terminal_restored_once(result)
        self.assertEqual(("returned", 0, 0), (result["outcome"], result["relay_exit"], result["child_rc"]))

    def test_terminal_is_restored_once_when_the_child_keeps_running(self) -> None:
        result = self._run("stay", wait_seconds="0.3")

        self.assertEqual(
            self.START + ["eio"] + self.LEAVE + ["wait_begin"] + self.END, result["events"]
        )
        self._assert_terminal_restored_once(result)
        self.assertEqual(
            ("returned", 1, -signal.SIGKILL), (result["outcome"], result["relay_exit"], result["child_rc"])
        )

    def test_terminal_is_restored_once_when_the_relay_loop_raises(self) -> None:
        result = self._run("raise")

        self.assertEqual(self.START + self.LEAVE + self.END, result["events"])
        self._assert_terminal_restored_once(result)
        self.assertEqual(("RuntimeError", None), (result["outcome"], result["relay_exit"]))
        self.assertEqual(-signal.SIGKILL, result["child_rc"], result)


@unittest.skipUnless(
    LINUX_PTY and hasattr(os, "sched_setaffinity"), "needs a Linux pty and CPU affinity"
)
class PosixRelayOrdinaryExitStatisticalObservations(unittest.TestCase):
    """A scheduling observation; forced-order tests above are the release gate."""

    ROUNDS = 25

    def test_observed_hangup_paths_for_ordinary_exit_0_are_reported_as_0(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            child = Path(tmp) / "ordinary_child.py"
            runner = Path(tmp) / "ordinary_runner.py"
            child.write_text(ORDINARY_CHILD, encoding="utf-8")
            runner.write_text(ORDINARY_RUNNER, encoding="utf-8")
            text, _status = _run_on_outer_pty(
                [sys.executable, str(runner), str(ROOT), str(child), str(self.ROUNDS)]
            )

        rounds = ROUND.findall(text)
        self.assertEqual(self.ROUNDS, len(rounds), text[-800:])
        self.assertEqual(
            [("0", "0")] * self.ROUNDS,
            [(relay_exit, child_rc) for relay_exit, child_rc, _event in rounds],
            rounds,
        )
        # This is an unforced statistical observation, not the deterministic
        # regression above. Require at least one observed hangup-error path so
        # the result is evidence about that path rather than a scheduling miss.
        events = [event for *_codes, event in rounds]
        self.assertIn("channel_stdin_proxy_child_exit_after_io_error", events, rounds)


if __name__ == "__main__":
    unittest.main()
