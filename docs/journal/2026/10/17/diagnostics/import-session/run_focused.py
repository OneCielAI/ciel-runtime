"""Isolated, selected tests only; never launch the runtime or read user sessions."""
import os
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[7]
for key in tuple(os.environ):
    if key.startswith("CIEL_RUNTIME_"):
        os.environ.pop(key)
sys.path.insert(0, str(ROOT))
from scripts.run_test_group import isolate_runtime_state
with isolate_runtime_state():
    if "--baseline" in sys.argv:
        import subprocess
        import ciel_runtime_support.request_shortcuts as shortcuts
        baseline = subprocess.check_output(
            ["git", "show", "HEAD:ciel_runtime_support/request_shortcuts.py"],
            cwd=ROOT, text=True, encoding="utf-8")
        exec(compile(baseline, "HEAD:request_shortcuts.py", "exec"), shortcuts.__dict__)
        print("BASELINE: HEAD parser loaded in memory; working tree untouched", flush=True)
    suite = unittest.TestSuite()
    for name in ("test_request_shortcuts.py", "test_session_import_regression.py", "test_local_request_controllers.py", "test_channel_mcp_http_controller.py"):
        suite.addTests(unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=name))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
