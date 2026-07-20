import unittest
from unittest import mock

from ciel_runtime_support.channel_terminal_input import (
    enter_bytes_from_user_input,
    platform_default_enter_bytes,
    resolve_enter_bytes,
    synthetic_enter_bytes_from_user_input,
    wake_input_bytes,
    windows_console_input_handle,
)


class ChannelTerminalInputTests(unittest.TestCase):
    def test_platform_default_is_submit_safe(self):
        self.assertEqual(b"\r\n", platform_default_enter_bytes("linux", "posix"))
        self.assertEqual(b"\r\n", platform_default_enter_bytes("win32", "nt"))

    def test_override_parser_accepts_only_known_enter_sequences(self):
        self.assertEqual(b"\n", resolve_enter_bytes("lf", b"\r\n"))
        self.assertEqual(b"\r", resolve_enter_bytes("return", b"\r\n"))
        self.assertEqual(b"\r\n", resolve_enter_bytes("unknown", b"\r\n"))

    def test_user_input_observation_preserves_crlf_and_normalizes_bare_cr(self):
        self.assertEqual(b"\r\n", enter_bytes_from_user_input(b"text\r\n"))
        self.assertEqual(b"\r\n", synthetic_enter_bytes_from_user_input(b"\r"))
        self.assertEqual(
            b"\r",
            synthetic_enter_bytes_from_user_input(b"\r", normalize_bare_cr=False),
        )

    def test_wake_input_clears_line_before_prompt_and_submit(self):
        self.assertEqual(b"\x15wake\r\n", wake_input_bytes("wake", b"\r\n"))

    def test_console_handle_is_absent_off_windows(self):
        with mock.patch("ciel_runtime_support.channel_terminal_input.os.name", "posix"):
            self.assertIsNone(windows_console_input_handle())


if __name__ == "__main__":
    unittest.main()
