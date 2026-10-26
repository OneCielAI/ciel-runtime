import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path

# docs/journal/2026/10/21/diagnostics/<run>/reproduction.okf/evidence/scripts
ROOT = Path(__file__).resolve().parents[10]
FILES = (
    "test_runtime_compaction_checkpoint_evidence.py",
    "test_runtime_compaction_control.py",
)
for key in tuple(os.environ):
    if key.startswith(("CIEL_RUNTIME_", "ANTHROPIC", "CLAUDE")) or key in {"CODEX_HOME", "CODEX_CONFIG_DIR"}:
        os.environ.pop(key, None)
with tempfile.TemporaryDirectory(prefix="ciel-checkpoint-evidence-") as state:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    os.environ.update(CIEL_RUNTIME_CONFIG_DIR=state, CIEL_RUNTIME_ROUTER_PORT=str(port),
                      CIEL_RUNTIME_TEST_ISOLATED="1")
    sys.path.insert(0, str(ROOT))
    print("Exact-file isolated run:", ", ".join(FILES), flush=True)
    suite = unittest.TestSuite()
    for name in FILES:
        suite.addTests(unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=name))
    raise SystemExit(0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1)
