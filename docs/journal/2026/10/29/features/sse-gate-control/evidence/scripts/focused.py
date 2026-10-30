"""Owned focused gate tests; never inherit live routing or provider state."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

repo = Path(sys.argv[1])
out = Path(sys.argv[2])
for key in list(os.environ):
    if key.startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI")):
        del os.environ[key]
with tempfile.TemporaryDirectory(prefix="owned-sse-gate-tests-") as tmp:
    home = Path(tmp)
    os.environ.update(CIEL_RUNTIME_CONFIG_DIR=str(home / "config"), CIEL_RUNTIME_TEST_ISOLATED="1",
                      HOME=tmp, USERPROFILE=tmp, CLAUDE_CONFIG_DIR=str(home / ".claude"))
    sys.path[:0] = [str(repo), str(repo / "tests")]
    names = ["test_sse_gate", "test_router_shortcuts", "test_runtime_compaction_binding",
             "test_runtime_compaction_identity", "test_runtime_compaction_checkpoint_evidence",
             "test_compact_stop_liveness", "test_provider_option_panel", "test_work_mode_option", "test_statusline"]
    with (out / "focused-tests.txt").open("w", encoding="utf-8") as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(names))
    record = {"tests": result.testsRun, "success": result.wasSuccessful(), "modules": names}
    (out / "focused-tests.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(record, indent=2))
    raise SystemExit(not result.wasSuccessful())
