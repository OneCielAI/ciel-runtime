"""Run bounded Work continuity tests in an owned environment."""
import os
import sys
from pathlib import Path
import json
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[8]
OUT = Path(__file__).resolve().parent
for key in list(os.environ):
    if key.startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI")):
        del os.environ[key]
with tempfile.TemporaryDirectory(prefix="owned-work-continuity-") as tmp:
    os.environ.update(CIEL_RUNTIME_CONFIG_DIR=tmp, CIEL_RUNTIME_TEST_ISOLATED="1", HOME=tmp, USERPROFILE=tmp, PYTHONSAFEPATH="1")
    sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
    names = ["test_work_session_lineage", "test_work_mcp_tools", "test_work_http_controller", "test_work_breakdown_statusline", "test_statusline", "test_compact_stop_liveness"]
    with (OUT / "integrated-tests.txt").open("w", encoding="utf-8") as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(names))
    record = {"tests": result.testsRun, "success": result.wasSuccessful(), "modules": names}
    (OUT / "integrated-tests.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(record, indent=2))
    raise SystemExit(not result.wasSuccessful())
