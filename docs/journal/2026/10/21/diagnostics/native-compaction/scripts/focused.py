import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[8]
FILES = (
    "test_runtime_compaction_binding.py", "test_runtime_compaction_identity.py",
    "test_runtime_compaction_rejection.py", "test_runtime_compaction_control.py",
    "test_context_compaction.py", "test_native_responses_semantic_checkpoint.py",
    "test_responses_prompt_compaction.py", "test_provider_request_semantic_compaction.py",
)
for key in tuple(os.environ):
    if key.startswith(("CIEL_RUNTIME_", "ANTHROPIC", "CLAUDE")) or key in {"CODEX_HOME", "CODEX_CONFIG_DIR"}:
        os.environ.pop(key, None)
with tempfile.TemporaryDirectory(prefix="ciel-compaction-focused-") as state:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    os.environ.update(CIEL_RUNTIME_CONFIG_DIR=state, CIEL_RUNTIME_ROUTER_PORT=str(port),
                      CIEL_RUNTIME_TEST_ISOLATED="1")
    sys.path.insert(0, str(ROOT))
    print("Exact-file isolated run:", ", ".join(FILES), flush=True)
    suite = unittest.TestSuite()
    for name in FILES:
        suite.addTests(unittest.defaultTestLoader.discover(str(ROOT/"tests"), pattern=name))
    raise SystemExit(0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1)
