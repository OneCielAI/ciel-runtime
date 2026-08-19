import hashlib
import codecs
import os
import sys
import time
import unittest
from unittest import mock

from ciel_runtime_support.windows_conpty import WindowsConPtySession, conpty_enabled


class WindowsConPtyPolicyTests(unittest.TestCase):
    def test_enabled_by_default_only_on_windows(self):
        self.assertTrue(conpty_enabled({}, platform_name="nt"))
        self.assertFalse(conpty_enabled({}, platform_name="posix"))

    def test_operator_can_disable_compatibility_rollout(self):
        for value in ("0", "false", "NO", "off"):
            with self.subTest(value=value):
                self.assertFalse(
                    conpty_enabled(
                        {"CIEL_RUNTIME_WINDOWS_CONPTY": value},
                        platform_name="nt",
                    )
                )

    def test_environment_block_is_sorted_and_double_terminated(self):
        block = WindowsConPtySession._environment_block({"z": "2", "A": "1"})

        self.assertEqual("A=1\0z=2\0\0", block)

    def test_batch_command_is_wrapped_by_comspec(self):
        with mock.patch.dict(os.environ, {"COMSPEC": "C:\\Windows\\cmd.exe"}):
            command = WindowsConPtySession._command_line(
                [
                    "C:\\Tools\\agent.cmd",
                    "-c",
                    'model_providers.ciel-runtime.name="Ciel Runtime Codex"',
                    "--version",
                ]
            )

        self.assertEqual(
            'C:\\Windows\\cmd.exe /d /s /c "C:\\Tools\\agent.cmd -c '
            '\"model_providers.ciel-runtime.name=\\\"Ciel Runtime Codex\\\"\" '
            '--version"',
            command,
        )
        self.assertNotIn('\\\\\\"Ciel Runtime Codex', command)

    def test_prompt_normalization_prevents_embedded_submit(self):
        self.assertEqual(
            "first second third",
            WindowsConPtySession.normalize_prompt("first\r\nsecond\tthird"),
        )

    def test_parent_console_uses_utf8_input_and_output_then_restores_both(self):
        kernel32 = mock.MagicMock()
        kernel32.GetStdHandle.side_effect = lambda value: value

        def get_console_mode(_handle, pointer):
            pointer._obj.value = 0x001F
            return 1

        kernel32.GetConsoleMode.side_effect = get_console_mode
        kernel32.GetConsoleCP.return_value = 437
        kernel32.GetConsoleOutputCP.return_value = 949
        session = object.__new__(WindowsConPtySession)
        session._kernel32 = kernel32
        session._old_input_mode = None
        session._old_output_mode = None
        session._old_input_cp = None
        session._old_output_cp = None

        session._configure_parent_console()

        kernel32.SetConsoleCP.assert_called_once_with(65001)
        kernel32.SetConsoleOutputCP.assert_called_once_with(65001)

        session._restore_parent_console()

        self.assertEqual([mock.call(65001), mock.call(437)], kernel32.SetConsoleCP.call_args_list)
        self.assertEqual(
            [mock.call(65001), mock.call(949)],
            kernel32.SetConsoleOutputCP.call_args_list,
        )

    def test_console_mirror_decodes_utf8_across_arbitrary_pipe_chunks(self):
        captured = []
        session = object.__new__(WindowsConPtySession)
        session._stdout_console_handle = 44
        session._stdout_fd = -1
        session._output_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        session._write_console_text = captured.append
        payload = "한글 ─ 상태 🚀"
        encoded = payload.encode("utf-8")

        for byte in encoded:
            session._mirror_bytes(bytes([byte]))
        session._mirror_bytes(b"", final=True)

        self.assertEqual(payload, "".join(captured))
        self.assertIsNone(session._output_decoder)

    def test_explicit_redirect_keeps_raw_bytes(self):
        session = object.__new__(WindowsConPtySession)
        session._stdout_console_handle = None
        session._stdout_fd = 91
        session._output_decoder = codecs.getincrementaldecoder("utf-8")("replace")

        with mock.patch("ciel_runtime_support.windows_conpty.os.write", return_value=3) as write:
            session._mirror_bytes(b"abc")

        write.assert_called_once_with(91, memoryview(b"abc"))

    @unittest.skipUnless(os.name == "nt", "requires Windows ConPTY")
    def test_native_conpty_transports_bytes_and_reaps_child(self):
        payload = ("head-" + "한글🚀" * 4096 + "-tail").encode("utf-8")
        expected = hashlib.sha256(payload).hexdigest()
        child = (
            "import hashlib,sys; print('READY', flush=True); "
            "value=sys.stdin.buffer.readline().rstrip(b'\\r\\n'); "
            f"raise SystemExit(0 if hashlib.sha256(value).hexdigest() == '{expected}' else 9)"
        )
        session = WindowsConPtySession(
            [sys.executable, "-c", child],
            dict(os.environ),
            log=lambda _level, _message: None,
            mirror_output=False,
            forward_stdin=False,
        )
        try:
            deadline = time.monotonic() + 5
            while b"READY" not in session.output_tail() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIn(b"READY", session.output_tail())
            session.write(payload + b"\r")
            try:
                result = session.wait(timeout=5)
            except Exception as exc:
                self.fail(f"ConPTY child did not consume input: {exc}; output={session.output_tail()!r}")
            self.assertEqual(0, result, session.output_tail())
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
