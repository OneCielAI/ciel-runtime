"""Observe installed admission policy using only an owned temporary state directory."""
from pathlib import Path
import json
import os
import sys
import tempfile
import unittest
snapshot, tests, out = map(Path, sys.argv[1:4])
for key in list(os.environ):
    if key.startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI")):
        del os.environ[key]
with tempfile.TemporaryDirectory(prefix="ciel-owned-installed-gate-") as scratch:
    os.environ.update(CIEL_RUNTIME_CONFIG_DIR=scratch, CIEL_RUNTIME_TEST_ISOLATED="1", HOME=scratch, USERPROFILE=scratch)
    sys.path[:0] = [str(snapshot), str(tests)]
    from test_sse_gate import SseGateBindingTests
    case = SseGateBindingTests("test_real_cycle_off_does_not_forge_shrink_or_clear_request")
    result = unittest.TestResult()
    case.run(result)
    assert result.wasSuccessful(), (result.errors, result.failures)
    print(json.dumps({"installed_snapshot": str(snapshot), "real_cycle_off_retains_request_and_unverified_phase": True, "re_enable_restores_hold": True}, indent=2))
