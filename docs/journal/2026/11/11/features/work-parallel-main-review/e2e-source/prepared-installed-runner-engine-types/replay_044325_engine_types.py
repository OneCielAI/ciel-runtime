"""Offline replay of the fixed engine-types classification on run 044325.
Uses the runner's own engine_generated/tree_manifest (ast). Read-only on evidence
and on the run's SNAP. Usage: <runner> <run dir> <snap dir>."""
import ast
import hashlib
import json
import sys
from pathlib import Path

runner, run, snap = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
mod = ast.parse(runner.read_text(encoding="utf-8"))
body = [n for n in mod.body if (isinstance(n, ast.FunctionDef) and n.name in ("engine_generated", "tree_manifest", "sha"))
        or (isinstance(n, ast.Assign) and any(getattr(t, "id", None) in ("ENGINE_TYPES", "IGNORED") for t in n.targets))]
result = json.loads((run / "result.json").read_text(encoding="utf-8"))
ns = {"hashlib": hashlib, "Path": Path, "SNAP_DIRS": tuple(result["installed_source"]["snap_dirs"])}
exec(compile(ast.Module(body=body, type_ignores=[]), str(runner), "exec"), ns)
eg = ns["engine_generated"]
installed = json.loads((run / "installed-manifest.json").read_text(encoding="utf-8"))
before = json.loads((run / "snapshot-manifest.json").read_text(encoding="utf-8"))
recorded = result["snapshot_changes"]
paths = sorted(set(recorded["engine_generated_excluded"]) | set(recorded["product"]))
a = {"engine_generated_excluded": [k for k in paths if eg(k, installed)],
     "product": [k for k in paths if not eg(k, installed)]}
print("A. recorded 044325 changes re-classified:", json.dumps(a, indent=1))
after = ns["tree_manifest"](snap)
changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
b = {"changed": len(changed), "engine": [k for k in changed if eg(k, installed)],
     "product": [k for k in changed if not eg(k, installed)]}
print("B. recomputed from the run's SNAP now:", json.dumps(b, indent=1))
types = ns["ENGINE_TYPES"]
synthetic = {"present_in_installed_and_changed_is_product": not eg(types + "tsconfig.json", {types + "tsconfig.json": "x"}),
             "absent_gitignore_is_engine": eg(types + ".gitignore", {}),
             "non_types_path_is_product": not eg("ciel_runtime_support/work_parallel_mcp.py", {}),
             "legacy_no_manifest_unchanged": eg(types + "claude-code/index.d.ts") and not eg(types + "tsconfig.json")}
print("C. synthetic:", json.dumps(synthetic, indent=1))
ok = a["product"] == [] and b["product"] == [] and all(synthetic.values())
print("all", ok)
sys.exit(0 if ok else 1)
