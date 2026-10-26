"""Purge inherited runtime/credential state before isolated E2E imports."""
import os
import sys
PURGE = ("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC_", "CODEX_", "OPENAI_", "OPENROUTER_", "GEMINI_", "GOOGLE_", "AZURE_", "MOONSHOT_", "KIMI_", "XAI_", "GROK_", "ZAI_", "DEEPSEEK_", "NVIDIA_", "OLLAMA_", "FIREWORKS_", "OPENCODE_")
for name in list(os.environ):
    if name.upper().startswith(PURGE) or any(word in name.upper() for word in ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")):
        del os.environ[name]
import tempfile
import socket
import unittest
from pathlib import Path
REPO = Path(__file__).resolve().parents[8]
assert (REPO / "ciel_runtime.py").is_file(), REPO
label = sys.argv[1] if len(sys.argv) > 1 else "after"
evidence = Path(__file__).resolve().parent.parent / label
evidence.mkdir(exist_ok=True)
os.environ["E2E_EVIDENCE_DIR"] = str(evidence)
with tempfile.TemporaryDirectory(prefix="ciel-sse-admission-tests-") as raw:
    root = Path(raw)
    for name in ("home", "config", "tmp", "claude"):
        (root / name).mkdir()
    port = socket.socket()
    port.bind(("127.0.0.1", 0))
    own_port = port.getsockname()[1]
    port.close()
    os.environ.update(HOME=str(root / "home"), USERPROFILE=str(root / "home"),
                      TEMP=str(root / "tmp"), TMP=str(root / "tmp"),
                      CLAUDE_CONFIG_DIR=str(root / "claude"),
                      CIEL_RUNTIME_CONFIG_DIR=str(root / "config"), CIEL_RUNTIME_TEST_ISOLATED="1",
                      CIEL_RUNTIME_ROUTER_PORT=str(own_port))
    previous = os.getcwd()
    os.chdir(root)
    try:
        sys.path.insert(0, str(REPO))
        suite = unittest.defaultTestLoader.discover(str(REPO / "tests"), pattern="test_channel_direct_socket_e2e.py")
        outcome = unittest.TextTestRunner(verbosity=2).run(suite)
    finally:
        os.chdir(previous)
raise SystemExit(0 if outcome.wasSuccessful() else 1)
