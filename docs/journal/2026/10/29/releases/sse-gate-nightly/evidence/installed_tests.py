"""Run exact commit tests against installed snapshot modules only."""
import json
import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
snapshot, tests, out = map(Path, sys.argv[1:4])
for key in list(os.environ):
    if key.startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI")):
        del os.environ[key]
scratch = tempfile.TemporaryDirectory(prefix="ciel-installed-compact-tests-")
os.environ["CIEL_RUNTIME_CONFIG_DIR"] = scratch.name
os.environ["CIEL_RUNTIME_TEST_ISOLATED"] = "1"
sys.path[:0] = [str(snapshot), str(tests)]
# Exact test fixtures spawn python -c from their exported ROOT. Pin their import path too.
os.environ["PYTHONPATH"] = str(snapshot)
os.environ["PYTHONSAFEPATH"] = "1"
names = json.loads((out / "test-modules.json").read_text(encoding="utf-8"))
modules = [importlib.import_module("ciel_runtime_support." + name) for name in
           ("runtime_compaction_binding", "statusline_script", "router_shortcuts", "provider_option_panel")]
for module in modules:
    assert Path(module.__file__).is_relative_to(snapshot), module.__file__
with (out / "installed-tests.txt").open("w", encoding="utf-8") as stream:
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(names))
record = {"tests": result.testsRun, "success": result.wasSuccessful(), "modules": [str(m.__file__) for m in modules]}
(out / "installed-tests.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
print(json.dumps(record, indent=2))
raise SystemExit(0 if result.wasSuccessful() else 1)
