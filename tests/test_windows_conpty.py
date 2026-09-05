import codecs
import ctypes
import hashlib
import os
import sys
import threading
import time
import unittest
from unittest import mock

from ciel_runtime_support.channel_injection import (
    ChannelPromptInjector,
    PromptInjection,
    RuntimeInjectionPolicy,
)
from ciel_runtime_support.terminal_platform_io import TERMINAL_INPUT_MODE_RESET
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

    def test_prompt_ready_detects_korean_split_by_ansi_sequences(self) -> None:
        prompt = "한글 ANSI 입력을 감지합니다"
        rendered = (
            b"\x1b[31m" + "한".encode("utf-8") + b"\x1b[0m"
            + "글".encode("utf-8") + b"\x1b[2CANSI "
            + "입력을 감지합니다".encode("utf-8")
        )

        self.assertTrue(
            WindowsConPtySession._prompt_rendered_in_output(rendered, prompt)
        )

    def test_resize_failure_is_logged_with_hresult(self) -> None:
        session = WindowsConPtySession.__new__(WindowsConPtySession)
        session._hpc = 7
        session._kernel32 = mock.Mock()
        session._kernel32.ResizePseudoConsole.return_value = -2147024809
        session._last_size = (80, 25)
        session._terminal_size = lambda: (120, 40)
        session._coord_type = lambda cols, rows: (cols, rows)
        session._output_lock = threading.Lock()
        session._output_total_bytes = 0
        logs = []
        session._log = lambda level, message: logs.append((level, message))

        self.assertFalse(session.resize_if_needed())
        self.assertEqual((80, 25), session._last_size)
        self.assertTrue(
            any(
                level == "ERROR"
                and "channel_windows_conpty_resize_failed" in message
                and "hresult=0x80070057" in message
                for level, message in logs
            )
        )

    def test_resize_success_records_tui_redraw_observation(self) -> None:
        session = WindowsConPtySession.__new__(WindowsConPtySession)
        session._hpc = 7
        session._kernel32 = mock.Mock()
        session._kernel32.ResizePseudoConsole.return_value = 0
        session._last_size = (80, 25)
        session._terminal_size = lambda: (120, 40)
        session._coord_type = lambda cols, rows: (cols, rows)
        session._output_lock = threading.Lock()
        session._output_total_bytes = 15
        session._wait_for_output_change = mock.Mock(return_value=True)
        logs = []
        session._log = lambda level, message: logs.append((level, message))

        self.assertTrue(session.resize_if_needed())
        self.assertEqual((120, 40), session._last_size)
        session._wait_for_output_change.assert_called_once_with(15, 0.75)
        self.assertIn(
            ("INFO", "channel_windows_conpty_resize cols=120 rows=40 redraw=observed"),
            logs,
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

    def test_parent_console_preserves_code_pages_and_restores_exact_handles_and_modes(self):
        kernel32 = mock.MagicMock()
        input_handle = 101
        output_handle = 202
        old_input_mode = 0x001F
        old_output_mode = 0x0020
        handles = {
            -10 & 0xFFFFFFFF: input_handle,
            -11 & 0xFFFFFFFF: output_handle,
        }
        kernel32.GetStdHandle.side_effect = handles.__getitem__

        def get_console_mode(handle, pointer):
            pointer._obj.value = {
                input_handle: old_input_mode,
                output_handle: old_output_mode,
            }[handle]
            return 1

        kernel32.GetConsoleMode.side_effect = get_console_mode
        session = object.__new__(WindowsConPtySession)
        session._kernel32 = kernel32
        session._stdin_console_handle = None
        session._stdout_console_handle = None
        session._old_input_mode = None
        session._old_output_mode = None
        session._parent_vt_output_ready = False

        session._configure_parent_console()

        self.assertEqual(input_handle, session._stdin_console_handle)
        self.assertEqual(output_handle, session._stdout_console_handle)
        self.assertTrue(session._parent_vt_output_ready)
        self.assertEqual(old_input_mode, session._old_input_mode)
        self.assertEqual(old_output_mode, session._old_output_mode)
        self.assertEqual(
            [
                mock.call(input_handle, 0x0200),
                mock.call(output_handle, old_output_mode | 0x0001 | 0x0004),
            ],
            kernel32.SetConsoleMode.call_args_list,
        )
        kernel32.SetConsoleCP.assert_not_called()
        kernel32.SetConsoleOutputCP.assert_not_called()

        session._restore_parent_console()

        self.assertIsNone(session._stdin_console_handle)
        self.assertIsNone(session._stdout_console_handle)
        self.assertFalse(session._parent_vt_output_ready)
        self.assertEqual(
            [
                mock.call(input_handle, 0x0200),
                mock.call(output_handle, old_output_mode | 0x0001 | 0x0004),
                mock.call(input_handle, old_input_mode),
                mock.call(output_handle, old_output_mode),
            ],
            kernel32.SetConsoleMode.call_args_list,
        )
        self.assertEqual(2, kernel32.GetStdHandle.call_count)

    def test_parent_output_vt_activation_failure_never_writes_reset_and_input_restores(self):
        kernel32 = mock.MagicMock()
        input_handle = 303
        output_handle = 404
        old_input_mode = 0x001F
        old_output_mode = 0x0020
        handles = {
            -10 & 0xFFFFFFFF: input_handle,
            -11 & 0xFFFFFFFF: output_handle,
        }
        kernel32.GetStdHandle.side_effect = handles.__getitem__

        def get_console_mode(handle, pointer):
            pointer._obj.value = {
                input_handle: old_input_mode,
                output_handle: old_output_mode,
            }[handle]
            return 1

        def set_console_mode(handle, _mode):
            return int(handle != output_handle)

        kernel32.GetConsoleMode.side_effect = get_console_mode
        kernel32.SetConsoleMode.side_effect = set_console_mode
        session = object.__new__(WindowsConPtySession)
        session._kernel32 = kernel32
        session._stdin_console_handle = None
        session._stdout_console_handle = None
        session._old_input_mode = None
        session._old_output_mode = None
        session._parent_vt_output_ready = False
        session._mirror_output = True
        session._stdout_fd = 91
        session._write_console_text = mock.Mock()

        session._configure_parent_console()

        self.assertEqual(input_handle, session._stdin_console_handle)
        self.assertIsNone(session._stdout_console_handle)
        self.assertIsNone(session._old_output_mode)
        self.assertFalse(session._parent_vt_output_ready)
        with mock.patch("ciel_runtime_support.windows_conpty.os.write") as write:
            session._reset_parent_terminal_modes()
        session._write_console_text.assert_not_called()
        write.assert_not_called()

        session._restore_parent_console()

        self.assertIn(mock.call(input_handle, old_input_mode), kernel32.SetConsoleMode.call_args_list)
        self.assertNotIn(mock.call(output_handle, old_output_mode), kernel32.SetConsoleMode.call_args_list)
        self.assertEqual(2, kernel32.GetStdHandle.call_count)

    def test_startup_resets_terminal_modes_before_starting_pumps(self):
        events = []

        def record(name):
            def invoke(*_args, **_kwargs):
                events.append(name)

            return invoke

        with (
            mock.patch("ciel_runtime_support.windows_conpty.os.name", "nt"),
            mock.patch.object(WindowsConPtySession, "_create", side_effect=record("create")),
            mock.patch.object(
                WindowsConPtySession,
                "_configure_parent_console",
                side_effect=record("configure"),
            ),
            mock.patch.object(
                WindowsConPtySession,
                "_reset_parent_terminal_modes",
                side_effect=record("reset"),
            ),
            mock.patch.object(
                WindowsConPtySession,
                "_start_pumps",
                side_effect=record("start_pumps"),
            ),
        ):
            WindowsConPtySession(
                ["child.exe"],
                {},
                log=lambda _level, _message: None,
                stdin_fd=10,
                stdout_fd=11,
            )

        self.assertEqual(["create", "configure", "reset", "start_pumps"], events)

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
        session._mirror_output = True
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
        session._parent_vt_output_ready = True
        session._write_console_text = mock.Mock()

        session._reset_parent_terminal_modes()

        reset = session._write_console_text.call_args.args[0]
        self.assertEqual(TERMINAL_INPUT_MODE_RESET, reset)
        self.assertIn("\x1b[?1003l", reset)
        self.assertIn("\x1b[?1006l", reset)

    def test_headless_conpty_does_not_write_terminal_reset(self):
        session = object.__new__(WindowsConPtySession)
        session._mirror_output = False
        session._stdout_console_handle = 44
        session._parent_vt_output_ready = True
        session._write_console_text = mock.Mock()

        session._reset_parent_terminal_modes()

        session._write_console_text.assert_not_called()

    def test_shutdown_resets_before_restore_and_suppresses_late_mirror_output(self):
        events = []
        input_handle = 505
        output_handle = 606
        old_input_mode = 0x001F
        old_output_mode = 0x0020
        kernel32 = mock.MagicMock()

        def set_console_mode(handle, mode):
            events.append(("restore", handle, mode))
            return 1

        kernel32.SetConsoleMode.side_effect = set_console_mode
        session = object.__new__(WindowsConPtySession)
        session._kernel32 = kernel32
        session._mirror_output = True
        session._stdout_fd = 91
        session._stdin_console_handle = input_handle
        session._stdout_console_handle = output_handle
        session._old_input_mode = old_input_mode
        session._old_output_mode = old_output_mode
        session._parent_vt_output_ready = True
        session._write_console_text = mock.Mock(
            side_effect=lambda sequence: events.append(("reset", sequence))
        )

        session._reset_and_restore_parent_console()

        self.assertEqual(
            [
                ("reset", TERMINAL_INPUT_MODE_RESET),
                ("restore", input_handle, old_input_mode),
                ("restore", output_handle, old_output_mode),
            ],
            events,
        )
        self.assertFalse(session._mirror_output)
        self.assertFalse(session._parent_vt_output_ready)
        with mock.patch("ciel_runtime_support.windows_conpty.os.write") as write:
            session._mirror_bytes(b"late child output")
        write.assert_not_called()

    def test_terminal_reset_write_failure_does_not_prevent_console_restore(self):
        input_handle = 707
        output_handle = 808
        kernel32 = mock.MagicMock()
        session = object.__new__(WindowsConPtySession)
        session._kernel32 = kernel32
        session._mirror_output = True
        session._stdin_console_handle = input_handle
        session._stdout_console_handle = output_handle
        session._old_input_mode = 0x001F
        session._old_output_mode = 0x0020
        session._parent_vt_output_ready = True
        session._write_console_text = mock.Mock(side_effect=OSError("console closed"))

        session._reset_and_restore_parent_console()

        self.assertEqual(
            [mock.call(input_handle, 0x001F), mock.call(output_handle, 0x0020)],
            kernel32.SetConsoleMode.call_args_list,
        )
        self.assertFalse(session._mirror_output)
        self.assertFalse(session._parent_vt_output_ready)

    def test_input_restore_exception_does_not_skip_output_restore(self):
        kernel32 = mock.MagicMock()
        kernel32.SetConsoleMode.side_effect = [
            RuntimeError("input restore failed"),
            True,
        ]
        session = object.__new__(WindowsConPtySession)
        session._kernel32 = kernel32
        session._stdin_console_handle = 909
        session._stdout_console_handle = 1001
        session._old_input_mode = 0x001F
        session._old_output_mode = 0x0020
        session._parent_vt_output_ready = True

        session._restore_parent_console()

        self.assertEqual(
            [mock.call(909, 0x001F), mock.call(1001, 0x0020)],
            kernel32.SetConsoleMode.call_args_list,
        )
        self.assertIsNone(session._old_input_mode)
        self.assertIsNone(session._old_output_mode)
        self.assertFalse(session._parent_vt_output_ready)

    def test_pump_input_preserves_split_f9_escape_sequence_as_raw_bytes(self):
        session = object.__new__(WindowsConPtySession)
        session._stop = threading.Event()
        session._read_input_bytes = mock.Mock(side_effect=[b"\x1b[", b"20~", b""])
        session.write = mock.Mock()

        session._pump_input()

        self.assertEqual(
            [mock.call(b"\x1b["), mock.call(b"20~")],
            session.write.call_args_list,
        )
        self.assertEqual(
            b"\x1b[20~",
            b"".join(call.args[0] for call in session.write.call_args_list),
        )

    def test_parent_text_draft_stays_active_until_submit(self):
        session = object.__new__(WindowsConPtySession)

        session._observe_parent_input("첫".encode("utf-8")[:1])
        session._observe_parent_input("첫".encode("utf-8")[1:])

        self.assertTrue(session.manual_input_active())
        session._observe_parent_input(b"\r")
        self.assertFalse(session.manual_input_active())

    def test_parent_function_key_does_not_create_manual_draft(self):
        session = object.__new__(WindowsConPtySession)

        session._observe_parent_input(b"\x1b[")
        session._observe_parent_input(b"20~")

        self.assertFalse(session.manual_input_active())

    def test_bracketed_paste_newline_remains_a_draft_until_submit(self):
        session = object.__new__(WindowsConPtySession)

        session._observe_parent_input(b"\x1b[200~first\r\nsecond\x1b[201~")

        self.assertTrue(session.manual_input_active())
        session._observe_parent_input(b"\r")
        self.assertFalse(session.manual_input_active())

    def test_explicit_redirect_keeps_raw_bytes(self):
        session = object.__new__(WindowsConPtySession)
        session._mirror_output = True
        session._stdout_console_handle = None
        session._stdout_fd = 91
        session._output_decoder = codecs.getincrementaldecoder("utf-8")("replace")

        with mock.patch("ciel_runtime_support.windows_conpty.os.write", return_value=3) as write:
            session._mirror_bytes(b"abc")

        write.assert_called_once_with(91, memoryview(b"abc"))

    @unittest.skipUnless(os.name == "nt", "requires Windows ConPTY")
    def test_native_conpty_detects_ansi_fragmented_korean_prompt_and_submits(self):
        prompt = "한글 ANSI 입력 감지"
        child = (
            "import ctypes,sys; from ctypes import wintypes; "
            "k=ctypes.WinDLL('kernel32'); h=k.GetStdHandle(-10); "
            "m=wintypes.DWORD(); k.GetConsoleMode(h,ctypes.byref(m)); "
            "k.SetConsoleMode(h,(m.value & ~0x1f) | 0x200); print('READY',flush=True); data=b''; "
            "\nwhile not data.endswith(b'\\x1b[201~'):\n data+=sys.stdin.buffer.read(1)\n"
            "start=data.find(b'\\x1b[200~'); text=data[start+6:-6].decode('utf-8'); "
            "sys.stdout.write(''.join('\\x1b[31m'+c+'\\x1b[0m' for c in text)); sys.stdout.flush(); "
            "key=sys.stdin.buffer.read(1); print('\\nTURN_STARTED',flush=True); "
            "raise SystemExit(0 if key in (b'\\r',b'\\n') else 8)"
        )
        logs = []
        session = WindowsConPtySession(
            [sys.executable, "-c", child],
            dict(os.environ),
            log=lambda level, message: logs.append((level, message)),
            mirror_output=False,
            forward_stdin=False,
        )
        try:
            deadline = time.monotonic() + 5
            while b"READY" not in session.output_tail() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIn(b"READY", session.output_tail())
            submitted = ChannelPromptInjector(
                sleep=time.sleep,
                retry_delay_seconds=lambda: 0.05,
                snapshot=lambda: None,
                log=lambda level, message: logs.append((level, message)),
            ).inject(
                session,
                PromptInjection(
                    prompt=prompt,
                    policy=RuntimeInjectionPolicy(
                        runtime="codex",
                        clear_input=b"\x15",
                        submit_input=b"\r",
                        submit_delay_seconds=0.0,
                        submit_attempts=2,
                        confirm_submission=True,
                        bracketed_paste=True,
                        prompt_render_timeout_seconds=5.0,
                    ),
                ),
            )
            self.assertTrue(submitted, session.output_tail())
            self.assertEqual(0, session.wait(timeout=5))
            self.assertIn(b"TURN_STARTED", session.output_tail())
            self.assertTrue(
                any("channel_input_prompt_ready result=observed" in message for _level, message in logs)
            )
        finally:
            session.close()

    @unittest.skipUnless(os.name == "nt", "requires Windows ConPTY")
    def test_native_conpty_resize_is_observed_as_child_redraw(self):
        logs = []
        child = (
            "import os,time; "
            "last=os.get_terminal_size(1); print(f'READY {last.columns}x{last.lines}',flush=True); "
            "deadline=time.time()+8; "
            "\nwhile time.time()<deadline:\n"
            " current=os.get_terminal_size(1)\n"
            " if current!=last:\n"
            "  print(f'REDRAW {current.columns}x{current.lines}',flush=True); last=current\n"
            " time.sleep(0.02)\n"
        )
        session = WindowsConPtySession(
            [sys.executable, "-c", child],
            dict(os.environ),
            log=lambda level, message: logs.append((level, message)),
            mirror_output=False,
            forward_stdin=False,
        )
        try:
            deadline = time.monotonic() + 5
            while b"READY" not in session.output_tail() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIn(b"READY", session.output_tail())
            old_cols, old_rows = session._last_size
            new_size = (old_cols + 3, old_rows + 2)
            session._terminal_size = lambda: new_size

            self.assertTrue(session.resize_if_needed())
            deadline = time.monotonic() + 3
            expected_redraw = f"REDRAW {new_size[0]}x{new_size[1]}".encode()
            while expected_redraw not in session.output_tail() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIn(
                expected_redraw,
                session.output_tail(),
            )
            self.assertTrue(
                any("channel_windows_conpty_resize" in message and "redraw=observed" in message for _level, message in logs)
            )
        finally:
            session.close()

    @unittest.skipUnless(os.name == "nt", "requires Windows ConPTY")
    def test_native_conpty_transports_bytes_and_reaps_child(self):
        payload = (
            b"\x1b[20~\x1b[200~"
            + ("head-" + "한글🚀" * 4096 + "-tail").encode("utf-8")
            + b"\x1b[201~"
        )
        expected = hashlib.sha256(payload).hexdigest()
        child = (
            "import ctypes,hashlib,sys; from ctypes import wintypes; "
            "k=ctypes.WinDLL('kernel32'); h=k.GetStdHandle(-10); "
            "m=wintypes.DWORD(); k.GetConsoleMode(h,ctypes.byref(m)); "
            "k.SetConsoleMode(h,(m.value & ~0x1f) | 0x200); "
            "print('READY', flush=True); "
            f"value=sys.stdin.buffer.read({len(payload)}); "
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
            session.write(payload)
            try:
                result = session.wait(timeout=5)
            except Exception as exc:
                self.fail(f"ConPTY child did not consume input: {exc}; output={session.output_tail()!r}")
            self.assertEqual(0, result, session.output_tail())
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
