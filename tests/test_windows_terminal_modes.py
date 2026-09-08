import codecs
import os
import sys
import threading
import time
import unittest
from unittest import mock

from ciel_runtime_support.windows_conpty import WindowsConPtySession
from ciel_runtime_support.windows_terminal_modes import WindowsTerminalModeFilter


class WindowsTerminalModeTests(unittest.TestCase):
    def test_every_split_preserves_display_paste_and_unicode(self):
        source = "한글\x1b[?9001h\x1b[?1003;1006;25;2004h\x1b[31m🚀"
        expected = "한글\x1b[?25;2004h\x1b[31m🚀".encode()
        payload = source.encode()
        for split in range(len(payload) + 1):
            parser = WindowsTerminalModeFilter()
            self.assertEqual(expected, parser.feed(payload[:split]) + parser.feed(payload[split:], final=True))

    def test_control_string_contents_and_literal_text_are_unchanged(self):
        for prefix, end in ((b"\x1b]", b"\x07"), (b"\x1bP", b"\x1b\\")):
            payload = prefix + b"literal \x1b[?9001h" + end + b"[?9001h plain"
            parser = WindowsTerminalModeFilter()
            self.assertEqual(payload, b"".join(parser.feed(bytes([value])) for value in payload))
            self.assertEqual(b"", parser.feed(b"\x1b[?9001h"))

    def test_pending_memory_is_bounded(self):
        parser = WindowsTerminalModeFilter()
        payload = b"\x1b[?" + b"1" * 4096
        self.assertEqual(payload, parser.feed(payload, final=True))
        self.assertFalse(parser._pending)

    def test_close_failure_still_restores_parent(self):
        session = object.__new__(WindowsConPtySession)
        session._closed = False
        session._process_handle = session._input_handle = None
        session._hpc = 1
        session._kernel32 = mock.Mock()
        session._kernel32.ClosePseudoConsole.side_effect = OSError("close failed")
        session._stop = threading.Event()
        session._reset_and_restore_parent_console = mock.Mock()
        with self.assertRaisesRegex(OSError, "close failed"):
            session.close()
        session._reset_and_restore_parent_console.assert_called_once()
        self.assertTrue(session._stop.is_set())

    @unittest.skipUnless(os.name == "nt", "requires native Windows ConPTY")
    def test_real_child_modes_cannot_escape_and_crash_resets_parent(self):
        captured = []

        class CapturedParent(WindowsConPtySession):
            def _configure_parent_console(self):
                self._stdout_console_handle = 1
                self._parent_vt_output_ready = True
                self._output_decoder = codecs.getincrementaldecoder("utf-8")("replace")

            def _write_console_text(self, text):
                captured.append(text)

            def _restore_parent_console(self):
                pass  # No actual parent console was modified by this fixture.

        child = (
            "import sys,time; "
            "sys.stdout.write('\x1b[?9001h\x1b[?1003;1006hREADY'); "
            "sys.stdout.flush(); time.sleep(0.2); sys.exit(7)"
        )
        session = CapturedParent(
            [sys.executable, "-c", child], dict(os.environ),
            log=lambda *_: None, forward_stdin=False,
        )
        try:
            self.assertEqual(7, session.wait(timeout=5))
            deadline = time.monotonic() + 2
            while b"READY" not in session.output_tail() and time.monotonic() < deadline:
                time.sleep(0.01)
            raw = session.output_tail()
        finally:
            session.close()
        output = "".join(captured)
        self.assertIn(b"\x1b[?9001h", raw)
        self.assertIn("READY", output)
        self.assertNotIn("\x1b[?9001h", output)
        self.assertNotIn("\x1b[?1003;1006h", output)
        self.assertTrue(output.endswith("\x1b[?9001l"))
        print("Native ConPTY: child exit=7; raw 9001h observed; parent enable blocked; final 9001l observed")


if __name__ == "__main__":
    unittest.main()
