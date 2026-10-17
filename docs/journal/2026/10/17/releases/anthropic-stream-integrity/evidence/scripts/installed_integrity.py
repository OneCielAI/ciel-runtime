"""Drive the installed composition root with the committed synthetic stream tests."""
import os
import sys
import tempfile
import socket
import types
import unittest
from pathlib import Path

root = Path(sys.argv[1]).resolve()
fixture = Path(sys.argv[2]).resolve()
for key in tuple(os.environ):
    if key.startswith(("CIEL_RUNTIME_", "ANTHROPIC_", "CLAUDE_")) or key == "CLAUDECODE":
        os.environ.pop(key)
with tempfile.TemporaryDirectory(prefix="ciel-installed-integrity-") as scratch:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    os.environ.update(CIEL_RUNTIME_CONFIG_DIR=scratch, CIEL_RUNTIME_TEST_ISOLATED="1", CIEL_RUNTIME_ROUTER_PORT=str(port))
    sys.path.insert(0, str(root))
    import ciel_runtime
    import ciel_runtime_support.streaming_anthropic as relay
    assert Path(ciel_runtime.__file__).resolve() == root / "ciel_runtime.py"
    assert Path(relay.__file__).resolve() == root / "ciel_runtime_support/streaming_anthropic.py"
    print("installed_root=" + str(root), flush=True)
    print("relay=" + str(relay.__file__), flush=True)
    module = types.ModuleType("installed_stream_integrity")
    module.__file__ = str(fixture)
    exec(compile(fixture.read_text(encoding="utf-8"), str(fixture), "exec"), module.__dict__)
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    assert suite.countTestCases() == 20
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
