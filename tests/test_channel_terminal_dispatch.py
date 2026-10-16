import unittest
from unittest import mock
from types import SimpleNamespace

from ciel_runtime_support.channel_terminal_proxy import run_windows_channel_terminal_proxy
from pathlib import Path

from ciel_runtime_support.channel_terminal_dispatch import (
    ChannelDirectProcessPorts,
    ChannelTerminalDispatchService,
    ChannelTerminalDispatchSettings,
    ChannelTerminalProxyPorts,
)


class _Process:
    pid = 42

    def __init__(self, return_code: int = 0) -> None:
        self.return_code = return_code

    def wait(self) -> int:
        return self.return_code


class ChannelTerminalDispatchServiceTests(unittest.TestCase):
    def _service(
        self,
        *,
        platform_name: str,
        windows_supported=True,
        run_windows=lambda *_args, **_kwargs: 11,
        run_posix=lambda *_args, **_kwargs: 22,
        direct_call=lambda *_args, **_kwargs: 33,
        popen=lambda *_args, **_kwargs: _Process(),
        events=None,
    ) -> ChannelTerminalDispatchService:
        events = events if events is not None else []
        return ChannelTerminalDispatchService(
            settings=ChannelTerminalDispatchSettings(
                platform_name=platform_name,
                stdin_isatty=lambda: True,
                stdout_isatty=lambda: True,
            ),
            proxy=ChannelTerminalProxyPorts(
                windows_supported=lambda: windows_supported,
                run_windows=run_windows,
                run_posix=run_posix,
                posix_services=lambda: "services",
            ),
            direct=ChannelDirectProcessPorts(
                call=direct_call,
                popen=popen,
                write_record=lambda path, pid, cmd: events.append(
                    ("write", path, pid, cmd)
                ),
                terminate=lambda proc, label: events.append(
                    ("terminate", proc.pid, label)
                ),
                release_record=lambda path, pid: events.append(
                    ("release", path, pid)
                ),
            ),
            log=lambda level, message: events.append(
                ("log", level, message)
            ),
        )

    def test_windows_console_adapter_receives_launch_options(self):
        calls = []
        service = self._service(
            platform_name="nt",
            run_windows=lambda *args, **kwargs: calls.append(
                (args, kwargs)
            )
            or 17,
        )

        self.assertEqual(
            17,
            service.dispatch(
                ["codex"],
                {"PATH": "x"},
                wake_for_llm_delivery=True,
                channel_wake_submit_retries=4,
            ),
        )
        self.assertEqual((["codex"], {"PATH": "x"}), calls[0][0])
        self.assertTrue(calls[0][1]["wake_for_llm_delivery"])
        self.assertEqual(4, calls[0][1]["channel_wake_submit_retries"])

    def test_windows_adapter_failure_falls_back_to_direct_process(self):
        events = []
        service = self._service(
            platform_name="nt",
            run_windows=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("console")
            ),
            direct_call=lambda *_args, **_kwargs: 29,
            events=events,
        )

        self.assertEqual(29, service.dispatch(["codex"], {}))
        self.assertIn("channel_windows_console_proxy_failed", events[0][2])
        self.assertIn("channel_stdin_proxy_unavailable", events[1][2])

    def test_posix_tty_uses_posix_adapter_and_composed_services(self):
        calls = []
        service = self._service(
            platform_name="posix",
            run_posix=lambda *args, **kwargs: calls.append(
                (args, kwargs)
            )
            or 23,
        )

        self.assertEqual(23, service.dispatch(["claude"], {"PATH": "x"}))
        self.assertEqual("services", calls[0][0][2])

    def test_parent_unattended_flag_is_passed_to_child(self):
        calls = []
        service = self._service(platform_name="nt", run_windows=lambda *args, **kwargs: calls.append(args) or 0)
        with mock.patch.dict("os.environ", {"CIEL_RUNTIME_STARTUP_AUTO_ANSWER": "1"}):
            service.dispatch(["codex"], {})
        self.assertEqual("1", calls[0][1]["CIEL_RUNTIME_STARTUP_AUTO_ANSWER"])

    def test_direct_unattended_launch_reports_no_screen_automation(self):
        events = []
        service = self._service(platform_name="other", events=events)
        service.call_direct(["codex"], {"CIEL_RUNTIME_STARTUP_AUTO_ANSWER": "1"})
        self.assertTrue(any("transport=direct rendered_screen=false automatic_injection=false" in str(event) for event in events))

    def test_console_fallback_holds_all_automatic_injection(self):
        proc = mock.Mock(pid=42)
        proc.poll.side_effect = [None, 0]
        proc.wait.return_value = 0
        logs = []
        writer = mock.Mock()
        process = SimpleNamespace(popen=lambda *a, **k: proc, write_child_record=mock.Mock(),
                                  terminate_child=mock.Mock(), release_child_record=mock.Mock())
        policy = SimpleNamespace(initial_cursor=lambda: 0, enter_bytes=lambda _: b"x", enter_label=lambda _: "x",
                                 unseen_retry_seconds=lambda: 1, inflight_is_stale=lambda *a: False,
                                 log=lambda *entry: logs.append(entry))
        polling = SimpleNamespace(inject_compact=mock.Mock(), file_marker=lambda: None,
                                  should_check=lambda *a: False, active_tool_call=lambda: False,
                                  active_turn=lambda: False, input_busy=lambda: False, inject_pending=mock.Mock(),
                                  wake_state=lambda _: None, inflight_effects=lambda: None,
                                  runtime_interaction=lambda: None)
        console = SimpleNamespace(open_conpty=lambda *a: None, reset_input_mode=mock.Mock(),
                                  mouse_guard=lambda: mock.Mock(), input_writer=lambda: writer,
                                  startup_grace_seconds=lambda: 0, reset_interval_seconds=lambda x: x,
                                  active_turn=lambda: False, sleep=lambda _: None)
        services = SimpleNamespace(process=process, policy=policy, polling=polling, console=console)
        with mock.patch("ciel_runtime_support.channel_terminal_proxy.poll_pending_compaction", side_effect=lambda *a, **k: a[5]) as compact, mock.patch("ciel_runtime_support.channel_terminal_proxy.poll_pending_channel_messages", side_effect=lambda *a, **k: a[3]) as pending:
            self.assertEqual(0, run_windows_channel_terminal_proxy(["codex"], {"CIEL_RUNTIME_STARTUP_AUTO_ANSWER": "1"}, services))
        self.assertFalse(compact.call_args.kwargs["input_ready"])
        self.assertFalse(pending.call_args.kwargs["input_ready"])
        writer.write.assert_not_called()
        self.assertTrue(any("transport=console-input" in message for _, message in logs))

    def test_direct_record_lifecycle_is_always_released(self):
        events = []
        process = _Process(return_code=7)
        service = self._service(
            platform_name="other",
            popen=lambda *_args, **_kwargs: process,
            events=events,
        )
        record = Path("child.json")

        self.assertEqual(7, service.call_direct(["codex"], {}, record))
        self.assertEqual(("write", record, 42, ["codex"]), events[0])
        self.assertEqual(("terminate", 42, "current Codex"), events[1])
        self.assertEqual(("release", record, 42), events[2])


if __name__ == "__main__":
    unittest.main()
