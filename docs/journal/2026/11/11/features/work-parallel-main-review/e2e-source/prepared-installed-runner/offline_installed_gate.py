"""Offline: run the installed runner's own build_snapshot (key-hash gate, copy,
file-by-file verification against the installed manifest) on a given tree.
Only the runner's functions/constants are executed (ast); nothing launches."""
import ast
import hashlib
import json
import shutil
import stat
import sys
import tempfile
from pathlib import Path

runner, tree_root = Path(sys.argv[1]), Path(sys.argv[2]).resolve()
mod = ast.parse(runner.read_text(encoding="utf-8"))
want_funcs = {"sha", "tree_manifest", "build_snapshot"}
want_names = {"KEY_HASHES", "IGNORED", "SNAP_DIRS_FULL", "TESTS_DROPPED", "SNAP_DIRS"}
body = [n for n in mod.body if (isinstance(n, ast.FunctionDef) and n.name in want_funcs)
        or (isinstance(n, ast.Assign) and any(getattr(t, "id", None) in want_names for t in n.targets))]
work = Path(tempfile.mkdtemp(prefix="installed-gate-"))
ns = {"hashlib": hashlib, "json": json, "shutil": shutil, "stat": stat, "Path": Path,
      "INSTALLED": tree_root, "SNAP": work / "snap", "OUT": work / "out"}
(work / "out").mkdir()
exec(compile(ast.Module(body=body, type_ignores=[]), str(runner), "exec"), ns)
print("tree", tree_root)
print("snap_dirs", ns["SNAP_DIRS"], "tests_dropped", ns["TESTS_DROPPED"])
try:
    proof = ns["build_snapshot"]()
except RuntimeError as error:
    print("GATE/COPY FAILED:", error)
    sys.exit(1)
print(json.dumps({k: proof[k] for k in ("files", "installed_files", "mismatch", "tests_dropped", "snap_dirs",
                                        "manifest_sha256", "installed_manifest_sha256")}))
print("key_hashes_equal", proof["key_hashes"] == ns["KEY_HASHES"])
print("installed-manifest.json written", (work / "out" / "installed-manifest.json").exists())
