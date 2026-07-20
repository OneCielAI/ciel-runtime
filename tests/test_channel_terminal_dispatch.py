import unittest
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
