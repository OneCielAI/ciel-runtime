"""Fatal-fault record of a Ciel process (router crashes of 2026-11-15)."""

import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from ciel_runtime_support import fault_log

ROOT = Path(__file__).resolve().parents[1]


class FaultLogTests(unittest.TestCase):
    def test_previous_fault_reads_only_the_last_run(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / fault_log.FILE_NAME
            path.write_text(
                "\n=== router pid=1 started=2026-11-15T07:00:00 python=3.14.2\n"
                "Windows fatal exception: access violation\n\nCurrent thread 0x0001 (most recent call first):\n"
                '  File "C:\\\\x\\\\old.py", line 1 in old\n'
                "\n=== router pid=2 started=2026-11-15T07:21:50 python=3.14.2\n", encoding="utf-8")
            self.assertEqual("", fault_log.previous_fault(path))
            with path.open("a", encoding="utf-8") as stream:
                stream.write("Windows fatal exception: access violation\n\nThread 0x0002 (most recent call first):\n"
                             '  File "C:\\\\x\\\\other.py", line 9 in other\n\n'
                             "Current thread 0x0003 (most recent call first):\n"
                             '  File "C:\\\\x\\\\router.py", line 42 in poll\n')
            summary = fault_log.previous_fault(path)
            self.assertIn("Windows fatal exception: access violation", summary)
            self.assertIn("run started 2026-11-15T07:21:50", summary)
            self.assertIn('line 42 in poll', summary)

    def test_a_real_access_violation_is_recorded_and_reported_by_the_next_start(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / fault_log.FILE_NAME
            crash = textwrap.dedent(f"""
                import ctypes, sys
                sys.path.insert(0, {str(ROOT)!r})
                from pathlib import Path
                from ciel_runtime_support import fault_log
                fault_log.enable(Path({str(path)!r}), "router")
                def poll_board():
                    ctypes.string_at(0)
                poll_board()
            """)
            done = subprocess.run([sys.executable, "-c", crash], capture_output=True, text=True, timeout=60)
            self.assertNotEqual(0, done.returncode)
            text = path.read_text(encoding="utf-8", errors="replace")
            self.assertIn("=== router pid=", text)
            self.assertRegex(text, r"(Windows fatal exception: access violation|Fatal Python error: Segmentation fault)")
            self.assertIn("poll_board", text)
            # The next start of the same process reports it once, in one line.
            restart = textwrap.dedent(f"""
                import sys
                sys.path.insert(0, {str(ROOT)!r})
                from pathlib import Path
                from ciel_runtime_support import fault_log
                print(fault_log.enable(Path({str(path)!r}), "router"))
                print(repr(fault_log.enable(Path({str(path)!r}), "router")))
            """)
            again = subprocess.run([sys.executable, "-c", restart], capture_output=True, text=True, timeout=60)
            first, second = again.stdout.splitlines()[:2]
            self.assertIn("poll_board", first)
            self.assertIn("run started", first)
            self.assertEqual("''", second)

    def test_router_runtime_reports_the_previous_fault_through_its_effect(self):
        from ciel_runtime_support import router_server_runtime as runtime
        effects = runtime.RouterServerEffects(lambda *_: None, None, None, None, lambda _h: [])
        self.assertEqual("", effects.enable_fault_log(Path("unused")))


if __name__ == "__main__":
    unittest.main()
