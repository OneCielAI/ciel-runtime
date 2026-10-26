"""Exact-file isolated tests for the CielAiRouter transient retry change."""
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
parser.add_argument("files", nargs="*")
args = parser.parse_args()
runtime = Path(args.runtime).resolve()
PURGE = ("CIEL_RUNTIME_", "ANTHROPIC", "CLAUDE", "CODEX_", "OPENAI_", "OPENROUTER_",
         "GEMINI_", "GOOGLE_", "AZURE_", "MOONSHOT_", "KIMI_", "XAI_", "ZAI_",
         "DEEPSEEK_", "OLLAMA_")
for key in tuple(os.environ):
    upper = key.upper()
    if upper.startswith(PURGE) or any(w in upper for w in ("API_KEY", "TOKEN", "SECRET", "PASSWORD")):
        os.environ.pop(key, None)
files = tuple(args.files) or (
    "test_transient_retry.py",
    "test_upstream_retry.py",
    "test_upstream_error_policy.py",
    "test_upstream_failure_model.py",
    "test_initial_stream_retry.py",
    "test_codex_no_output_http_retry.py",
    "test_provider_responses_gateway_replay_guard.py",
    "test_native_responses_semantic_checkpoint.py",
    "test_openai_responses_upstream_stream_error.py",
    "test_provider_responses_completion_gate.py",
    "test_anthropic_responses_bridge.py",
    "test_responses_anthropic_stream.py",
    "test_anthropic_stream_integrity.py",
    "test_remote_bridge_http.py",
    "test_cielairouter_gateway_transport.py",
    "test_cielairouter_provider.py",
    "test_meta_provider.py",
    "test_provider_option_panel.py",
    "test_provider_gateway_options.py",
    "test_ollama_provider_options.py",
)
with tempfile.TemporaryDirectory(prefix="ciel-transient-retry-tests-") as state:
    home = Path(state) / "home"
    home.mkdir()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    os.environ.update(CIEL_RUNTIME_CONFIG_DIR=state, CIEL_RUNTIME_ROUTER_PORT=str(port),
                      CIEL_RUNTIME_TEST_ISOLATED="1", HOME=str(home), USERPROFILE=str(home),
                      CLAUDE_CONFIG_DIR=str(home / ".claude"))
    sys.path.insert(0, str(ROOT / "tests"))
    sys.path.insert(0, str(runtime))
    import ciel_runtime_support.transient_retry as module
    assert Path(module.__file__).resolve().is_relative_to(runtime)
    print("Selected runtime:", runtime, flush=True)
    print("Exact files:", ", ".join(files), flush=True)
    suite = unittest.TestSuite()
    for name in files:
        suite.addTests(unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=name))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
