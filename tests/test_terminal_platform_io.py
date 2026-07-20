import os
import unittest
from unittest import mock

from ciel_runtime_support.terminal_platform_io import (
    TERMINAL_INPUT_MODE_RESET,
    TerminalInputModeResetPolicy,
    read_clipboard_text,
    apply_pty_winsize,
    terminal_winsize_from_fd,
)


class TerminalPlatformIoTests(unittest.TestCase):
    def test_clipboard_adapter_returns_trimmed_first_successful_command(self):
        completed = mock.Mock(returncode=0, stdout=" copied value\n")
        with mock.patch("ciel_runtime_support.terminal_platform_io.os.name", "nt"), mock.patch(
            "ciel_runtime_support.terminal_platform_io.subprocess.run", return_value=completed
        ) as run:
            self.assertEqual("copied value", read_clipboard_text())
        self.assertEqual("powershell", run.call_args.args[0][0])

    class _Stream:
        def __init__(self) -> None:
            self.value = ""

        def write(self, text: str) -> None:
            self.value += text

        def flush(self) -> None:
            return None

    def _policy(
        self,
        *,
        platform_name: str = "posix",
        environment=None,
        stream=None,
    ) -> TerminalInputModeResetPolicy:
        environment = environment if environment is not None else {}
        stream = stream if stream is not None else self._Stream()
        return TerminalInputModeResetPolicy(
            platform_name=platform_name,
            environment=environment,
            parse_bool=lambda value, default=False: (
                default if value is None else value == "1"
            ),
            default_stream=lambda: stream,
        )

    def test_terminal_size_uses_positive_fallback(self):
        with (
            mock.patch.object(
                os,
                "get_terminal_size",
                return_value=os.terminal_size((0, 0)),
            ),
            mock.patch(
                "ciel_runtime_support.terminal_platform_io.shutil.get_terminal_size",
                return_value=os.terminal_size((-1, 0)),
            ),
        ):
            self.assertEqual((24, 80), terminal_winsize_from_fd(1))

    def test_non_posix_or_invalid_size_does_not_apply_pty_ioctl(self):
        self.assertFalse(
            apply_pty_winsize(3, 24, 80, platform_name="nt")
        )
        self.assertFalse(
            apply_pty_winsize(3, 0, 80, platform_name="posix")
        )

    def test_windows_reset_is_disabled_without_explicit_opt_in(self):
        stream = self._Stream()
        policy = self._policy(platform_name="nt", stream=stream)

        policy.write()

        self.assertEqual("", stream.value)

    def test_reset_interval_is_clamped_and_sequence_is_written(self):
        stream = self._Stream()
        policy = self._policy(
            environment={
                "CIEL_RUNTIME_TERMINAL_INPUT_MODE_RESET": "1",
                "CIEL_RUNTIME_TERMINAL_INPUT_MODE_RESET_INTERVAL_SECONDS": "100",
            },
            stream=stream,
        )

        self.assertEqual(60.0, policy.interval_seconds())
        policy.write()
        self.assertEqual(TERMINAL_INPUT_MODE_RESET, stream.value)


if __name__ == "__main__":
    unittest.main()
