"""Copy a verified published package into a NEW snapshot home (no install.ps1).

Usage: install_snapshot.py <package_dir> <target_snapshot_dir>
Refuses an existing target, copies ciel_runtime.py, ciel_runtime_support/ and
scripts/ exactly as install.ps1 lays them out, then compares every file.
Bin shims and CIEL_RUNTIME_HOME are handled separately after verification.
"""
import filecmp
import hashlib
import json
import shutil
import sys
from pathlib import Path

package, target = Path(sys.argv[1]), Path(sys.argv[2])
if target.exists():
    raise SystemExit(f"refusing to overwrite existing snapshot {target}")
target.mkdir(parents=True)
shutil.copy2(package / "ciel_runtime.py", target / "ciel_runtime.py")
ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
shutil.copytree(package / "ciel_runtime_support", target / "ciel_runtime_support", ignore=ignore)
shutil.copytree(package / "scripts", target / "scripts", ignore=ignore)
mismatch = []
count = 0
for rel in ["ciel_runtime.py", "ciel_runtime_support", "scripts"]:
    src = package / rel
    files = [src] if src.is_file() else [p for p in src.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    for f in files:
        r = f.relative_to(package)
        count += 1
        if not filecmp.cmp(f, target / r, shallow=False):
            mismatch.append(str(r))
extra = [str(p.relative_to(target)) for p in target.rglob("*") if p.is_file() and not (package / p.relative_to(target)).exists()]
mod = target / "ciel_runtime_support/claude_mods/ciel-work/.claude-plugin/plugin.json"
result = {"target": str(target), "files_compared": count, "mismatch": mismatch, "extra": extra,
          "mod_manifest_present": mod.is_file(),
          "ciel_runtime_sha256": hashlib.sha256((target / "ciel_runtime.py").read_bytes()).hexdigest()}
print(json.dumps(result, indent=2))
raise SystemExit(1 if mismatch or extra or not result["mod_manifest_present"] else 0)
