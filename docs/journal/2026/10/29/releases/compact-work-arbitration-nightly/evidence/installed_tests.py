"""Run exact commit tests against installed snapshot modules only."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
snapshot, tests, out = map(Path, sys.argv[1:4])
for key in list(os.environ):
    if key.startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI")):
        del os.environ[key]
scratch = tempfile.TemporaryDirectory(prefix="ciel-installed-compact-tests-")
os.environ["CIEL_RUNTIME_CONFIG_DIR"] = scratch.name
os.environ["CIEL_RUNTIME_TEST_ISOLATED"] = "1"
sys.path[:0] = [str(snapshot), str(tests)]
from ciel_runtime_support import channel_transcript, channel_compact_poll, claude_stream_recovery_runtime, work_mcp_tools  # noqa: E402
modules = (channel_transcript, channel_compact_poll, claude_stream_recovery_runtime, work_mcp_tools)
for module in modules:
    assert Path(module.__file__).is_relative_to(snapshot), module.__file__
names = ["test_compact_stop_liveness", "test_compact_failure_release", "test_compact_idle_retry",
         "test_channel_compact_poll", "test_channel_compact_injection", "test_channel_transcript",
         "test_claude_compact_continuation", "test_claude_stream_recovery_runtime", "test_work_mcp_tools"]
with (out / "installed-tests.txt").open("w", encoding="utf-8") as stream:
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(names))
record = {"tests": result.testsRun, "success": result.wasSuccessful(), "modules": [str(m.__file__) for m in modules]}
(out / "installed-tests.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
print(json.dumps(record, indent=2))
raise SystemExit(0 if result.wasSuccessful() else 1)
