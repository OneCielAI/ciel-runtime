"""Run the focused SSE socket tests against an exported source tree (e.g. HEAD).

usage: run_against_tree.py <source_root> <label>
Purges inherited runtime/credential variables; uses an owned temporary HOME/config.
"""
import os
import sys
PURGE = ("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC_", "CODEX_", "OPENAI_", "OPENROUTER_", "GEMINI_", "GOOGLE_", "AZURE_")
for name in list(os.environ):
    if name.upper().startswith(PURGE) or any(w in name.upper() for w in ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")):
        del os.environ[name]
import tempfile
import unittest
from pathlib import Path
source = Path(sys.argv[1]).resolve()
evidence = Path(__file__).resolve().parent.parent / sys.argv[2]
evidence.mkdir(exist_ok=True)
os.environ["E2E_EVIDENCE_DIR"] = str(evidence)
with tempfile.TemporaryDirectory(prefix="ciel-sse-tree-tests-") as raw:
    root = Path(raw)
    for name in ("home", "config", "tmp", "claude"):
        (root / name).mkdir()
    os.environ.update(HOME=str(root / "home"), USERPROFILE=str(root / "home"), TEMP=str(root / "tmp"),
                      TMP=str(root / "tmp"), CLAUDE_CONFIG_DIR=str(root / "claude"),
                      CIEL_RUNTIME_CONFIG_DIR=str(root / "config"), CIEL_RUNTIME_TEST_ISOLATED="1")
    previous = os.getcwd()
    os.chdir(root)
    try:
        sys.path.insert(0, str(source))
        suite = unittest.defaultTestLoader.discover(str(source / "tests"), pattern="test_channel_direct_socket_e2e.py",
                                                    top_level_dir=str(source))
        outcome = unittest.TextTestRunner(verbosity=2).run(suite)
    finally:
        os.chdir(previous)
raise SystemExit(0 if outcome.wasSuccessful() else 1)
