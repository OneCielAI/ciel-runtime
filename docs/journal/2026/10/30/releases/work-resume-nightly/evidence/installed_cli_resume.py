"""Installed Work continuation contract probe; no native live-session restart."""
from pathlib import Path
import json
import os
import sys
import tempfile
snapshot = Path(sys.argv[1])
out = Path(sys.argv[2])
for key in list(os.environ):
    if key.startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI")):
        del os.environ[key]
with tempfile.TemporaryDirectory(prefix="installed-work-resume-") as tmp:
    sys.path[:0] = [str(snapshot)]
    from ciel_runtime_support.work_plan_store import WorkPlanStore, session_work_plan_store
    from ciel_runtime_support.work_plan_tree import add_node
    root = Path(tmp)
    parent = session_work_plan_store(root, "claude:parent")
    parent.transaction(lambda doc: add_node(doc, None, "preserved installed task"))
    # Native session fixtures are supplied by the exact focused tests; this call verifies
    # the installed snapshot's durable store behavior, with no heuristic latest fallback.
    child = session_work_plan_store(root, "claude:unrelated")
    result = {"snapshot": str(snapshot), "parent_nodes": len(parent.read_snapshot()["nodes"]), "unrelated_nodes": len(child.read_snapshot()["nodes"]), "version": parent.read_snapshot()["version"]}
(out / "installed-resume-contract.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
