import os, socket, sys, tempfile, unittest
from pathlib import Path
ROOT = next(p for p in Path(__file__).resolve().parents if (p / "ciel_runtime.py").exists())
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
for key in list(os.environ):
    if key.upper().startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI")):
        os.environ.pop(key)
with tempfile.TemporaryDirectory() as directory:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    os.environ.update(CIEL_RUNTIME_CONFIG_DIR=directory, CIEL_RUNTIME_ROUTER_PORT=str(port), CIEL_RUNTIME_TEST_ISOLATED="1")
    names = ("test_work_plan_tree", "test_work_plan_store", "test_work_mcp_tools", "test_work_runtime_status", "test_work_runtime_observer", "test_work_http_controller", "test_architecture_contracts")
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in names)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
