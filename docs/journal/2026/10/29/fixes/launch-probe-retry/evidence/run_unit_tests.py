"""Run only the launch probe tests with isolated configuration and CLI environment."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile


root = Path(__file__).resolve().parents[8]
with tempfile.TemporaryDirectory(prefix="ciel-probe-test-") as home:
    env = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI"))
    }
    env.update(
        HOME=home, USERPROFILE=home,
        APPDATA=home + "/AppData/Roaming", LOCALAPPDATA=home + "/AppData/Local",
        CIEL_RUNTIME_CONFIG_DIR=home + "/config", PYTHONDONTWRITEBYTECODE="1",
    )
    code = (
        "import sys,unittest; sys.path[:0]=[sys.argv[1],sys.argv[1]+'/tests']; "
        "suite=unittest.defaultTestLoader.loadTestsFromNames("
        "['test_provider_status_retry',"
        "'test_cielairouter_provider.CielAiRouterProviderTests.test_adapter_contract_defaults']); "
        "result=unittest.TextTestRunner(verbosity=2).run(suite); "
        "sys.exit(not result.wasSuccessful())"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-X", "utf8", "-c", code, str(root)],
        cwd=home, env=env, capture_output=True, text=True, encoding="utf-8",
    )
    text = (
        "Scoped source tests; isolated temporary HOME/config; purged inherited provider/CLI environment."
        + chr(10) + result.stdout + result.stderr + chr(10)
        + "Exit code: " + str(result.returncode) + chr(10)
    )
    Path(__file__).with_name("unit-tests.txt").write_text(text, encoding="utf-8")
    print(text)
    sys.exit(result.returncode)
