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
parser.add_argument("files", nargs="*")
args = parser.parse_args()
runtime = Path(args.runtime).resolve()
PURGE = ("CIEL_RUNTIME_", "ANTHROPIC", "CLAUDE", "CODEX_", "OPENAI_", "OPENROUTER_", "GEMINI_",
         "GOOGLE_", "AZURE_", "MOONSHOT_", "KIMI_", "XAI_", "ZAI_", "DEEPSEEK_", "OLLAMA_")
for key in tuple(os.environ):
    upper = key.upper()
    if upper.startswith(PURGE) or any(word in upper for word in ("API_KEY", "TOKEN", "SECRET", "PASSWORD")):
        os.environ.pop(key, None)
files = tuple(args.files) or (
    "test_runtime_input_status.py", "test_channel_interrupted_attempts.py",
    "test_channel_pending_injection.py", "test_channel_pending_poll.py",
    "test_channel_direct_socket_e2e.py", "test_channel_message_repository.py",
    "test_runtime_input_delivery_health.py", "test_tui_observation.py",
    "test_runtime_input_gateway.py", "test_channel_wake_delivery_repository.py",
    "test_channel_wake_claim_repository.py", "test_channel_walkie_delivery.py",
    "test_channel_terminal_dispatch.py", "test_channel_compact_poll.py",
    "test_channel_bridge.py", "test_channel_llm_context.py", "test_channel_transcript.py",
    "test_event_delivery_ack.py", "test_external_event_menu.py",
    "test_external_event_receiver.py", "test_external_event_control_http.py",
    "test_prelaunch_wiring.py", "test_prelaunch_panel_projection.py",
    "test_prelaunch_default_action.py", "test_prelaunch_launch_preference.py",
    "test_runtime_session_restart.py", "test_claude_session_socket_launch.py",
    "test_architecture_contracts.py", "test_architecture_budget.py",
    "test_unisolated_test_guard.py",
)
with tempfile.TemporaryDirectory(prefix="ciel-sse-tolerance-tests-") as state:
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
    import ciel_runtime_support.runtime_input_status as status
    assert Path(status.__file__).resolve().is_relative_to(runtime)
    print("Selected runtime:", runtime, flush=True)
    print("Exact files:", ", ".join(files), flush=True)
    suite = unittest.TestSuite()
    for name in files:
        suite.addTests(unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=name))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
