"""Isolated focused test runner. Does not start routers or touch live sessions."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(sys.argv[1]).resolve()
with tempfile.TemporaryDirectory(prefix="ciel-codex-focused-") as td:
    base = Path(td)
    env = {k:v for k,v in os.environ.items() if not k.upper().startswith(
        ("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI"))}
    for key in ("HOME", "USERPROFILE", "XDG_CONFIG_HOME", "APPDATA", "LOCALAPPDATA", "CIEL_RUNTIME_CONFIG_DIR", "CODEX_HOME"):
        p = base / key
        p.mkdir()
        env[key] = str(p)
    args = sys.argv[2:] or ["test_codex_app_server", "test_codex_desktop_injection", "test_runtime_input_retry.CodexResendTests"]
    env["PYTHONPATH"] = str(root) + os.pathsep + str(root / "tests")
    result = subprocess.run([sys.executable, "-m", "unittest", "-v", *args], env=env, cwd=root, capture_output=True, text=True)
    print(result.stdout, end="")
    print(result.stderr, end="")
    raise SystemExit(result.returncode)
