"""Exact-file runner with no inherited live-router state."""
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path

root = Path(__file__).resolve().parents[8]
for key in list(os.environ):
    if key.startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX")):
        os.environ.pop(key)
with tempfile.TemporaryDirectory(prefix="ciel-timeout-tests-") as temp:
    home = Path(temp)
    for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME"):
        os.environ[name] = str(home)
    os.environ["CIEL_RUNTIME_CONFIG_DIR"] = str(home / "runtime")
    os.environ["CLAUDE_CONFIG_DIR"] = str(home / "claude")
    os.environ["CODEX_HOME"] = str(home / "codex")
    os.environ["CIEL_RUNTIME_TEST_ISOLATED"] = "1"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        os.environ["CIEL_RUNTIME_ROUTER_PORT"] = str(sock.getsockname()[1])
    sys.path[:0] = [str(root), str(root / "tests")]
    suite = unittest.defaultTestLoader.loadTestsFromNames(sys.argv[1:])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
