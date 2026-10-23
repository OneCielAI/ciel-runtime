"""Real 300s socket timeout using the default production retry scope."""
import os
import sys
import tempfile
import time
from pathlib import Path

root = Path(__file__).resolve().parents[8]
for key in list(os.environ):
    if key.startswith(("CIEL_RUNTIME_", "CLAUDE", "CODEX", "ANTHROPIC")):
        os.environ.pop(key)
with tempfile.TemporaryDirectory(prefix="ciel-long-timeout-") as temp:
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME"):
        os.environ[key] = temp
    os.environ["CIEL_RUNTIME_CONFIG_DIR"] = temp
    os.environ["CIEL_RUNTIME_TEST_ISOLATED"] = "1"
    sys.path[:0] = [str(root), str(root / "tests")]
    from test_cielairouter_timeout_route import TimeoutRouteTests
    case = TimeoutRouteTests("test_header_timeout_then_success_direct_and_wrapped")
    case.setUp()
    try:
        state = case.fixture(["timeout", "complete"], 1, open_timeout=300)
        started = time.monotonic()
        case.forward(state)
        elapsed = time.monotonic() - started
        case.check(state, 2)
        case.assertGreaterEqual(elapsed, 299)
        case.assertEqual(case.complete, state.handler.wfile.getvalue())
        state.handler.send_response.assert_called_once_with(200)
        print("REAL_LONG_TIMEOUT", {"open_timeout": 300, "elapsed": elapsed,
            "opens": len(state.hits), "retries": state.budgets[0].attempts,
            "deadline": state.budgets[0].deadline_seconds,
            "downstream_status": 200, "status_blocks": state.handler.send_response.call_count}, flush=True)
    finally:
        case.doCleanups()
