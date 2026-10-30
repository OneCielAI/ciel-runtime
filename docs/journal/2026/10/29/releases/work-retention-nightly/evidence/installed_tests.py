"""Pin all module imports to installed snapshot and exact-commit test sources."""
import json
import os
from pathlib import Path
import sys
import unittest
snapshot, tests, out = map(Path, sys.argv[1:4])
for key in list(os.environ):
    if key.startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI")):
        del os.environ[key]
sys.path[:0] = [str(snapshot), str(tests)]
from ciel_runtime_support import work_retention, work_plan_tree, work_plan_store
for module in (work_retention, work_plan_tree, work_plan_store):
    assert Path(module.__file__).is_relative_to(snapshot)
names = ["test_work_retention", "test_work_plan_tree", "test_work_mcp_tools",
         "test_work_breakdown_statusline", "test_statusline", "test_provider_option_panel"]
with (out / "installed-tests.txt").open("w", encoding="utf-8") as stream:
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(names))
record = {"tests": result.testsRun, "success": result.wasSuccessful(),
          "modules": [str(m.__file__) for m in (work_retention, work_plan_tree, work_plan_store)]}
(out / "installed-tests.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
print(json.dumps(record, indent=2))
raise SystemExit(0 if result.wasSuccessful() else 1)
