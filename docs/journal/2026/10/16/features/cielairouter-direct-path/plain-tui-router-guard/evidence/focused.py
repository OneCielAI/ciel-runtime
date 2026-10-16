"""Run selected test files with the repository's isolated state (scripts/run_test_group.py)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(r"C:\Users\djlov\ciel-runtime")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from run_test_group import TEST_DIR, isolate_runtime_state  # noqa: E402

state = isolate_runtime_state()
suite = unittest.TestSuite()
for name in sys.argv[1:]:
    suite.addTests(unittest.defaultTestLoader.discover(str(TEST_DIR), pattern=name))
try:
    ok = unittest.TextTestRunner(verbosity=int(__import__("os").environ.get("FOCUSED_VERBOSITY", "1"))).run(suite).wasSuccessful()
finally:
    state.cleanup()
raise SystemExit(0 if ok else 1)
