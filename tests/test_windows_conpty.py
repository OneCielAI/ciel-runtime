import hashlib
import codecs
import ctypes
import os
import sys
import threading
import time
import unittest
from unittest import mock

from ciel_runtime_support.windows_conpty import WindowsConPtySession, conpty_enabled


class WindowsConPtyPolicyTests(unittest.TestCase):
    def test_input_snapshot_decodes_captured_output_tail(self) -> None:
        session = WindowsConPtySession.__new__(WindowsConPtySession)
        session._output_lock = threading.Lock()
        session._output_tail = bytearray("[Pasted Content 1048 chars]".encode("utf-8"))
        session._output_total_bytes = len(session._output_tail)

        self.assertEqual("[Pasted Content 1048 chars]", session.input_snapshot())
        self.assertFalse(session.supports_input_snapshot)

    def test_prompt_checkpoint_detects_new_render_after_same_prompt_rolled_in_tail(self) -> None:
        session = WindowsConPtySession.__new__(WindowsConPtySession)
        session._output_lock = threading.Lock()
        prompt = "[ciel-wake] pending_ids=1238 short event body"
        old = prompt.encode("utf-8")
        new = b"\x1b[39m\x1b[22m " + old
        session._output_tail = bytearray(old + new)
        session._output_total_bytes = len(session._output_tail)

        self.assertTrue(
            session.wait_until_prompt_ready(
                len(old),
                0.0,
                expected_prompt=prompt,
            )
        )

    def test_prompt_checkpoint_rejects_unrelated_startup_output(self) -> None:
        session = WindowsConPtySession.__new__(WindowsConPtySession)
        session._output_lock = threading.Lock()
        output = b"MCP startup complete"
        session._output_tail = bytearray(output)
        session._output_total_bytes = len(output)

        self.assertFalse(
            session.wait_until_prompt_ready(
                0,
                0.0,
                expected_prompt="[ciel-wake] pending_ids=1238 short event body",
            )
        )

    def test_prompt_checkpoint_preserves_blank_line_as_two_visible_spaces(self) -> None:
        session = WindowsConPtySession.__new__(WindowsConPtySession)
        session._output_lock = threading.Lock()
        prompt = "[ciel-wake] pending_ids=1240\n\n[ciel-runtime event body]"
        rendered = (
            b"\x1b[22m [ciel-wake] pending_ids=1240  "
            b"[ciel-runtime event body]"
        )
        session._output_tail = bytearray(rendered)
        session._output_total_bytes = len(rendered)

        self.assertTrue(
            session.wait_until_prompt_ready(
                0,
                0.0,
                expected_prompt=prompt,
            )
        )

    def test_prompt_checkpoint_accepts_claude_collapsed_paste_marker(self) -> None:
        session = WindowsConPtySession.__new__(WindowsConPtySession)
        session._output_lock = threading.Lock()
        output = b"\x1b[?25l[Pasted\x1b[1Ctext\x1b[1C#1]"
        session._output_tail = bytearray(output)
        session._output_total_bytes = len(output)

        self.assertTrue(
            session.wait_until_prompt_ready(
                0,
                0.0,
                expected_prompt=(
                    "[ciel-runtime web reply required one-shot] "
                    "Respond exactly with ENTER_SUBMIT_OK"
                ),
            )
        )

    def test_prompt_ready_requires_the_injected_body_render(self) -> None:
        baseline = "Codex starting"

        self.assertFalse(
            WindowsConPtySession._prompt_rendered_since(
                baseline,
                baseline + "\nMCP startup complete",
                "[ciel-wake] pending_ids=1236 full event body",
            )
        )
        self.assertTrue(
            WindowsConPtySession._prompt_rendered_since(
                baseline,
                baseline + "\n[Pasted Content 1048 chars]",
                "[ciel-wake] pending_ids=1236 full event body",
            )
        )
        self.assertTrue(
            WindowsConPtySession._prompt_rendered_since(
                baseline,
                baseline + "\n[Pasted\x1b[1Ctext\x1b[1C#1]",
                "[ciel-runtime web reply required one-shot] full event body",
            )
        )

    def test_prompt_ready_accepts_new_visible_prompt_prefix(self) -> None:
        prompt = "[ciel-wake] pending_ids=1236 short event body"

        self.assertFalse(
            WindowsConPtySession._prompt_rendered_since(prompt, prompt, prompt)
        )
        self.assertTrue(
            WindowsConPtySession._prompt_rendered_since(
                "idle",
                "idle\n[ciel-wake] pending_ids=1236 short event body",
                prompt,
            )
        )

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

    def test_parent_console_preserves_code_pages_and_restores_modes(self):
        kernel32 = mock.MagicMock()
        kernel32.GetStdHandle.side_effect = lambda value: value

        def get_console_mode(_handle, pointer):
            pointer._obj.value = 0x001F
            return 1

        kernel32.GetConsoleMode.side_effect = get_console_mode
        session = object.__new__(WindowsConPtySession)
        session._kernel32 = kernel32
        session._stdin_console_handle = None
        session._old_input_mode = None
        session._old_output_mode = None

        session._configure_parent_console()

        self.assertEqual(-10 & 0xFFFFFFFF, session._stdin_console_handle)
        kernel32.SetConsoleCP.assert_not_called()
        kernel32.SetConsoleOutputCP.assert_not_called()

        session._restore_parent_console()

        self.assertIsNone(session._stdin_console_handle)
        kernel32.SetConsoleMode.assert_any_call(-10 & 0xFFFFFFFF, 0x001F)

    def test_console_input_reads_wide_korean_and_encodes_utf8(self):
        kernel32 = mock.MagicMock()

        def read_console(_handle, buffer, _length, read_pointer, _reserved):
            text = "한글 입력 🚀"
            for index, unit in enumerate(text.encode("utf-16-le")):
                ctypes.memmove(ctypes.addressof(buffer) + index, bytes((unit,)), 1)
            read_pointer._obj.value = len(text.encode("utf-16-le")) // 2
            return 1

        kernel32.ReadConsoleW.side_effect = read_console
        session = object.__new__(WindowsConPtySession)
        session._kernel32 = kernel32
        session._stdin_console_handle = 44
        session._stdin_fd = -1

        self.assertEqual("한글 입력 🚀".encode("utf-8"), session._read_input_bytes())

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

    def test_close_reset_disables_terminal_mouse_reporting_after_tui_crash(self):
        session = object.__new__(WindowsConPtySession)
        session._mirror_output = True
        session._stdout_console_handle = 44
        session._write_console_text = mock.Mock()

        session._reset_parent_terminal_modes()

        reset = session._write_console_text.call_args.args[0]
        self.assertIn("\x1b[?1003l", reset)
        self.assertIn("\x1b[?1006l", reset)

    def test_headless_conpty_does_not_write_terminal_reset(self):
        session = object.__new__(WindowsConPtySession)
        session._mirror_output = False
        session._stdout_console_handle = 44
        session._write_console_text = mock.Mock()

        session._reset_parent_terminal_modes()

        session._write_console_text.assert_not_called()

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
