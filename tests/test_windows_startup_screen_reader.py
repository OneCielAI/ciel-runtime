"""Owned headless ConPTY startup must use a current child viewport."""

import os
import sys
import time
import unittest

from ciel_runtime_support.startup_prompt_answers import StartupPromptResponder
from ciel_runtime_support.windows_conpty import WindowsConPtySession
from ciel_runtime_support.windows_startup_screen_reader import WindowsStartupScreenReader


@unittest.skipUnless(os.name == "nt", "Windows ConPTY")
class ChildScreenTests(unittest.TestCase):
    def test_headless_modal_is_answered_and_ready_without_parent_console(self):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI"))}
        program = (
            "import sys,ctypes; k=ctypes.windll.kernel32; "
            "h=k.GetStdHandle(-10); mode=ctypes.c_ulong(); k.GetConsoleMode(h,ctypes.byref(mode)); "
            "k.SetConsoleMode(h,mode.value & ~6); "
            r"sys.stdout.write('\x1b[2J\x1b[HAccessing workspace:\n1. Yes, I trust this folder\n2. No, exit\n');sys.stdout.flush(); "
            "key=sys.stdin.read(1); "
            r"sys.stdout.write('\x1b[2J\x1b[H? for shortcuts\n' if key=='1' else 'BAD_KEY');sys.stdout.flush(); "
            "import time;time.sleep(20)"
        )
        child = WindowsConPtySession([sys.executable, "-I", "-u", "-c", program], env,
                                     log=lambda *_: None, mirror_output=False, forward_stdin=False)
        reader = WindowsStartupScreenReader(child.pid)
        responder = StartupPromptResponder(screen_snapshot=reader.snapshot)
        try:
            deadline = time.monotonic() + 8
            initial = None
            while time.monotonic() < deadline:
                initial = reader.snapshot()
                if initial and "Accessing workspace:" in initial:
                    break
            self.assertIn("Accessing workspace:", initial or "")
            self.assertFalse(responder.input_ready())
            answered = False
            cursor = 0
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                output, cursor = child.output_since(cursor)
                responder.feed(output)
                keys = responder.due()
                if keys:
                    child.write(keys)
                    answered = True
                if responder.rendered_input_ready():
                    break
                time.sleep(0.05)
            self.assertTrue(answered)
            self.assertTrue(responder.input_ready(), repr(reader.snapshot()) + " / " + repr(child.output_tail()))
            self.assertTrue(responder.rendered_input_ready())
            self.assertIn("? for shortcuts", reader.snapshot())
        finally:
            reader.close()
            child.close()
        self.assertIsNone(reader.snapshot())

    def test_missing_child_never_proves_readiness(self):
        reader = WindowsStartupScreenReader(4294967294)
        try:
            self.assertIsNone(reader.snapshot())
            responder = StartupPromptResponder(screen_snapshot=reader.snapshot)
            self.assertEqual(b"", responder.due())
            self.assertFalse(responder.input_ready())
        finally:
            reader.close()
