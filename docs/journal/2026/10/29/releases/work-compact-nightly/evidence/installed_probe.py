"""Run exact-commit Work statusline tests against one installed snapshot."""
import json
import os
from pathlib import Path
import sys
import unittest

snapshot, tests, out = [Path(x) for x in sys.argv[1:4]]
for key in list(os.environ):
    if key.startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI")):
        del os.environ[key]
sys.path[:0] = [str(snapshot), str(tests)]
from ciel_runtime_support import statusline_script, work_plan_tree
assert Path(statusline_script.__file__).is_relative_to(snapshot)
assert Path(work_plan_tree.__file__).is_relative_to(snapshot)
suite = unittest.defaultTestLoader.loadTestsFromNames(["test_work_breakdown_statusline", "test_statusline"])
with (out / "installed-tests.txt").open("w", encoding="utf-8") as stream:
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
record = {"tests": result.testsRun, "success": result.wasSuccessful(),
          "statusline_module": str(statusline_script.__file__), "tree_module": str(work_plan_tree.__file__)}
(out / "installed-tests.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
print(json.dumps(record, indent=2))
raise SystemExit(0 if result.wasSuccessful() else 1)
