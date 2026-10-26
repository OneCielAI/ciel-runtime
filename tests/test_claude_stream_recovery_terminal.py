import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

from ciel_runtime_support.channel_pending_poll import ChannelPendingPollState
from ciel_runtime_support.channel_terminal_context import (
    ChannelTerminalContext,
    ChannelTerminalDispatchPorts,
    ChannelTerminalIoPorts,
    ChannelTerminalPolicyPorts,
    ChannelTerminalPollingPorts,
    ChannelTerminalProcessPorts,
    ChannelTerminalWindowsPorts,
)
from ciel_runtime_support.channel_terminal_proxy import (
    ChannelTerminalPolicy,
    ChannelTerminalPolling,
    ChannelTerminalProcess,
    ChannelWindowsConsole,
    ChannelWindowsServices,
    TerminalRecoveryAdmissionReporter,
    _terminal_manual_input,
    run_windows_channel_terminal_proxy,
    terminal_operator_input,
    terminal_recovery_admission_snapshot,
)

ROOT = Path(__file__).resolve().parents[1]


def _policy(logs, compaction=None, admission=None):
    return ChannelTerminalPolicy(
        initial_cursor=lambda: 0,
        enter_bytes=lambda _value: b"\r",
        enter_label=lambda _value: "cr",
        enter_is_fixed=lambda: True,
        unseen_retry_seconds=lambda: 30.0,
        inflight_is_stale=lambda *_args, **_kwargs: False,
        log=lambda level, message: logs.append((level, message)),
        compaction=compaction,
        recovery_admission=admission,
    )


def _polling(*, busy=lambda: False):
    return ChannelTerminalPolling(
        inject_compact=lambda *_args, **_kwargs: "none",
        file_marker=lambda: (0.0, -1),
        should_check=lambda *_args: False,
        active_tool_call=lambda: False,
        active_turn=busy,
        inject_pending=lambda *_args, **_kwargs: 0,
        wake_state=lambda _message_id: "",
        inflight_effects=lambda: None,
    )


def _unused(*_args, **_kwargs):
    return None


class _Clock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


class _FakeConPty:
    pid = 4242

    def __init__(self, output, *, manual=False, polls=3):
        self.output, self.manual, self.polls, self.closed = output, manual, polls, False

    def poll(self):
        if self.polls <= 0:
            return 0
        self.polls -= 1
        return None

    def wait(self):
        return 0

    def resize_if_needed(self):
        pass

    def output_since(self, cursor):
        return self.output[cursor:], len(self.output)

    def manual_input_active(self):
        return self.manual

    def close(self):
        self.closed = True

    def write(self, _data):
        raise AssertionError("recovery reporting must not write terminal input")


class _ConsoleProc:
    pid = 77

    def __init__(self):
        self.polls = 2

    def poll(self):
        if self.polls <= 0:
            return 0
        self.polls -= 1
        return None

    def wait(self):
        return 0


def _windows_services(snapshots, logs, *, conpty=None, popen=None, writer=None, busy=lambda: False):
    guard = SimpleNamespace(apply=lambda: None, restore=lambda: None)
    return ChannelWindowsServices(
        process=ChannelTerminalProcess(
            popen=popen or (lambda *_args, **_kwargs: None),
            write_child_record=lambda *_args: None,
            terminate_child=lambda *_args: None,
            release_child_record=lambda *_args: None,
        ),
        policy=_policy(logs, admission=snapshots.append),
        polling=_polling(busy=busy),
        console=ChannelWindowsConsole(
            reset_input_mode=lambda: None,
            mouse_guard=lambda: guard,
            input_writer=lambda: writer,
            startup_grace_seconds=lambda: 0.0,
            reset_interval_seconds=lambda value: value,
            active_turn=lambda: False,
            sleep=lambda _seconds: None,
            open_conpty=lambda _cmd, _env, _log: conpty,
        ),
    )


class RecoveryAdmissionSnapshotTests(unittest.TestCase):
    def test_ready_requires_every_clean_observation(self):
        clean = dict(input_ready=True, manual_input=False, busy=False, compaction=False,
                     pending_state=ChannelPendingPollState(last_id=0))
        snapshot = terminal_recovery_admission_snapshot(5.0, **clean)
        self.assertEqual({"at": 5.0, "ready": True, "manual_input": False, "manual_input_known": True,
                          "busy": False, "compaction": False, "pending": False}, snapshot)
        for change in (dict(input_ready=False), dict(manual_input=True), dict(busy=True),
                       dict(compaction=True), dict(interaction_pending=True), dict(modal_ready=False),
                       dict(modal_ready=None)):
            with self.subTest(change=change):
                self.assertFalse(terminal_recovery_admission_snapshot(5.0, **{**clean, **change})["ready"])

    def test_unknown_draft_is_reported_as_manual_and_not_ready(self):
        snapshot = terminal_recovery_admission_snapshot(
            1.0, input_ready=True, manual_input=None, busy=False, compaction=False, pending_state=None,
        )
        self.assertEqual((False, True, False), (snapshot["ready"], snapshot["manual_input"], snapshot["manual_input_known"]))

    def test_pending_reflects_inflight_message_and_backlog(self):
        inflight = ChannelPendingPollState(last_id=0)
        inflight.inflight_message_id = 9
        backlog = ChannelPendingPollState(last_id=0)
        backlog.inflight.backlog.append(4)
        for state in (inflight, backlog):
            snapshot = terminal_recovery_admission_snapshot(
                1.0, input_ready=True, manual_input=False, busy=False, compaction=False, pending_state=state,
            )
            self.assertEqual((True, False), (snapshot["pending"], snapshot["ready"]))

    def test_manual_input_probe_fails_closed(self):
        def broken():
            raise OSError("closed")

        self.assertIsNone(_terminal_manual_input(object()))
        self.assertIsNone(_terminal_manual_input(SimpleNamespace(manual_input_active=broken)))
        self.assertIsNone(_terminal_manual_input(SimpleNamespace(manual_input_active=lambda: "yes")))
        self.assertTrue(_terminal_manual_input(SimpleNamespace(manual_input_active=lambda: True)))
        self.assertFalse(_terminal_manual_input(SimpleNamespace(manual_input_active=lambda: False)))

    def test_terminal_replies_are_not_operator_input(self):
        for data in (b"\x1b[I", b"\x1b[O", b"\x1b[12;40R", b"\x1b[?62;22c", b"\x1b[?2004;1$y",
                     b"\x1b[?1u", b"\x1b]11;rgb:0000/0000/0000\x07", b"\x1bP>|xterm\x1b\\", b""):
            with self.subTest(data=data):
                self.assertFalse(terminal_operator_input(data))
        for data in (b"a", b"\r", b"\x03", b"\x15", b"\x1b", b"\x1b[A", b"\x1b[200~x\x1b[201~",
                     b"\x1b[I" + b"x", "첫".encode("utf-8")[:1]):
            with self.subTest(data=data):
                self.assertTrue(terminal_operator_input(data))


class RecoveryAdmissionReporterTests(unittest.TestCase):
    def test_busy_and_compaction_are_sampled_once_per_interval(self):
        snapshots, logs, clock, calls = [], [], _Clock(), []
        compaction = SimpleNamespace(external_admission_paused=lambda: calls.append("compaction") or False)
        polling = _polling(busy=lambda: calls.append("busy") or False)
        reporter = TerminalRecoveryAdmissionReporter(polling, _policy(logs, compaction, snapshots.append), clock=clock)

        for step in (0.0, 0.5, 0.99):
            clock.now = 100.0 + step
            reporter.report(None, input_ready=True, manual_input=False)
        clock.now = 101.0
        reporter.report(None, input_ready=True, manual_input=False)

        self.assertEqual(["busy", "compaction", "busy", "compaction"], calls)
        self.assertEqual([100.0, 100.5, 100.99, 101.0], [item["at"] for item in snapshots])
        self.assertTrue(all(item["ready"] for item in snapshots))

    def test_unreadable_busy_state_withdraws_admission(self):
        def broken():
            raise OSError("transcript unavailable")

        snapshots = []
        reporter = TerminalRecoveryAdmissionReporter(_polling(busy=broken), _policy([], admission=snapshots.append),
                                                     clock=_Clock())
        reporter.report(None, input_ready=True, manual_input=False)
        self.assertEqual((False, True, True), (snapshots[0]["ready"], snapshots[0]["busy"], snapshots[0]["compaction"]))

    def test_close_withdraws_admission_and_ignores_later_reports(self):
        snapshots = []
        reporter = TerminalRecoveryAdmissionReporter(_polling(), _policy([], admission=snapshots.append), clock=_Clock())
        reporter.report(None, input_ready=True, manual_input=False)
        reporter.close()
        reporter.report(None, input_ready=True, manual_input=False)
        reporter.close()
        self.assertEqual([True, False], [item["ready"] for item in snapshots])
        self.assertEqual(False, snapshots[-1]["manual_input_known"])

    def test_callback_failure_is_logged_once_and_never_raised(self):
        logs = []

        def broken(_snapshot):
            raise RuntimeError("monitor unavailable")

        reporter = TerminalRecoveryAdmissionReporter(_polling(), _policy(logs, admission=broken), clock=_Clock())
        reporter.report(None, input_ready=True, manual_input=False)
        reporter.report(None, input_ready=True, manual_input=False)
        reporter.close()
        self.assertEqual([("WARN", "channel_terminal_recovery_report_failed error=RuntimeError")], logs)

    def test_missing_callback_reports_nothing(self):
        calls = []
        polling = ChannelTerminalPolling(*([lambda *_args, **_kwargs: calls.append("port")] * 8))
        reporter = TerminalRecoveryAdmissionReporter(polling, _policy([]))
        reporter.report(None, input_ready=True, manual_input=False)
        reporter.close()
        self.assertEqual([], calls)


class WindowsProxyRecoveryAdmissionTests(unittest.TestCase):
    def test_conpty_idle_prompt_reports_ready_then_withdraws_on_exit(self):
        snapshots, logs = [], []
        conpty = _FakeConPty(b"\x1b[2m? for shortcuts\x1b[0m")
        result = run_windows_channel_terminal_proxy(
            ["claude"], {}, _windows_services(snapshots, logs, conpty=conpty), inject_channel_messages=False,
        )
        self.assertEqual(0, result)
        self.assertTrue(conpty.closed)
        self.assertTrue(snapshots[0]["ready"])
        self.assertEqual({"ready": False, "manual_input": True, "manual_input_known": False},
                         {key: snapshots[-1][key] for key in ("ready", "manual_input", "manual_input_known")})

    def test_conpty_draft_dialog_and_busy_turn_are_not_ready(self):
        cases = (
            ("draft", _FakeConPty(b"? for shortcuts", manual=True), lambda: False, "manual_input"),
            ("dialog", _FakeConPty(b"? for shortcuts Yes, I trust this folder"), lambda: False, None),
            ("busy", _FakeConPty(b"? for shortcuts"), lambda: True, "busy"),
        )
        for name, conpty, busy, flag in cases:
            with self.subTest(case=name):
                snapshots = []
                run_windows_channel_terminal_proxy(
                    ["claude"], {}, _windows_services(snapshots, [], conpty=conpty, busy=busy),
                    inject_channel_messages=False,
                )
                self.assertFalse(any(item["ready"] for item in snapshots))
                if flag is not None:
                    self.assertTrue(snapshots[0][flag])

    def test_console_input_transport_cannot_prove_clean_draft(self):
        snapshots = []
        run_windows_channel_terminal_proxy(
            ["claude"], {}, _windows_services(snapshots, [], popen=lambda *_args, **_kwargs: _ConsoleProc(),
                                              writer=SimpleNamespace()),
            inject_channel_messages=False,
        )
        self.assertTrue(snapshots)
        self.assertFalse(any(item["ready"] for item in snapshots))
        self.assertFalse(any(item["manual_input_known"] for item in snapshots))


class RecoveryAdmissionPortForwardingTests(unittest.TestCase):
    def _context(self, admission):
        unused = _unused
        return ChannelTerminalContext(
            process=ChannelTerminalProcessPorts(unused, unused, unused, unused),
            policy=ChannelTerminalPolicyPorts(lambda: 0, lambda _v: b"\r", lambda _v: "cr", lambda: True,
                                              lambda: 1.0, unused, unused, None, admission),
            polling=ChannelTerminalPollingPorts(unused, unused, unused, unused, unused, unused, unused, unused,
                                                lambda: None, lambda: False),
            io=ChannelTerminalIoPorts(unused, unused, unused, unused, unused, unused),
            windows=ChannelTerminalWindowsPorts(unused, unused, unused, unused, lambda: 0.0, lambda v: v,
                                                lambda: False, unused, unused),
            dispatch_ports=ChannelTerminalDispatchPorts("nt", lambda: True, lambda: True, unused, unused, unused,
                                                        unused, lambda: True),
        )

    def test_context_forwards_callback_to_every_policy_service(self):
        snapshots = []
        context = self._context(snapshots.append)
        for services in (context.policy_services(), context.posix_services().policy,
                         context.windows_services().policy):
            services.recovery_admission({"ready": False})
        self.assertEqual([{"ready": False}] * 3, snapshots)

    def test_context_default_has_no_callback(self):
        unused = _unused
        ports = ChannelTerminalPolicyPorts(*([unused] * 7))
        self.assertIsNone(ports.recovery_admission)

    def test_facade_policy_constructor_publishes_to_stream_recovery_monitor(self):
        source = (ROOT / "ciel_runtime.py").read_text(encoding="utf-8")
        function = next(node for node in ast.parse(source).body
                        if isinstance(node, ast.FunctionDef) and node.name == "channel_terminal_context")
        call = next(node for node in ast.walk(function)
                    if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "ChannelTerminalPolicyPorts")
        self.assertEqual(9, len(call.args))
        self.assertFalse(call.keywords)
        callback = call.args[-1]
        self.assertIsInstance(callback, ast.Lambda)
        expression = compile(ast.Expression(callback), "ciel_runtime.py", "eval")

        received = []
        monitor = SimpleNamespace(set_terminal_admission=received.append)
        forward = eval(expression, {"_STREAM_RECOVERY": SimpleNamespace(monitor=monitor), "getattr": getattr})
        forward({"ready": True, "at": 1.0})
        self.assertEqual([{"ready": True, "at": 1.0}], received)

        no_op = eval(expression, {"_STREAM_RECOVERY": SimpleNamespace(monitor=object()), "getattr": getattr})
        self.assertIsNone(no_op({"ready": True}))


if __name__ == "__main__":
    unittest.main()
