"""Exact-file tests with isolated state, optionally against an installed runtime."""
import argparse
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[8]
parser = argparse.ArgumentParser()
parser.add_argument("--runtime", default=str(ROOT))
args = parser.parse_args()
runtime = Path(args.runtime).resolve()
for key in tuple(os.environ):
    if key.startswith(("CIEL_RUNTIME_", "ANTHROPIC", "CLAUDE", "CODEX_", "OPENAI_")):
        os.environ.pop(key, None)
files = (
    "test_codex_no_output_http_retry.py", "test_codex_backend_item_repair.py",
    "test_codex_runtime.py", "test_upstream_failure_model.py",
    "test_runtime_input_delivery_health.py", "test_tui_observation.py",
    "test_architecture_contracts.py", "test_architecture_budget.py",
)
with tempfile.TemporaryDirectory(prefix="ciel-final-retry-tests-") as state:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    os.environ.update(CIEL_RUNTIME_CONFIG_DIR=state, CIEL_RUNTIME_ROUTER_PORT=str(port),
                      CIEL_RUNTIME_TEST_ISOLATED="1")
    sys.path.insert(0, str(ROOT / "tests"))
    sys.path.insert(0, str(runtime))
    import ciel_runtime_support.router_http as router_http
    assert Path(router_http.__file__).resolve().is_relative_to(runtime)
    print("Selected runtime:", runtime, flush=True)
    print("Exact files:", ", ".join(files), flush=True)
    suite = unittest.TestSuite()
    for name in files:
        suite.addTests(unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=name))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
