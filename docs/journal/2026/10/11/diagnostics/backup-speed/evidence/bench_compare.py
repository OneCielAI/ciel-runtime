"""Backup speed, old vs new code, on files Windows has never opened before.

usage: python bench_compare.py CODE_DIR WORK_BASE LABEL
Creates WORK_BASE/<LABEL>/work (10,000 x ~20 KB files in 100 folders + 2 x 200 MB files),
then times: first backup, the same again, and again after 100 small files changed.
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

code, work_base, label = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
sys.path.insert(0, str(code))
from ciel_runtime_support.session_backup_collect import CollectOptions, default_roots  # noqa: E402
from ciel_runtime_support.session_backup_ops import create_snapshot  # noqa: E402
from ciel_runtime_support.session_backup_store import LocalTarget  # noqa: E402

base = work_base / label
shutil.rmtree(base, ignore_errors=True)
cwd = base / "work"
for d in range(100):
    (cwd / f"d{d}").mkdir(parents=True)
    for i in range(100):
        (cwd / f"d{d}" / f"f{i}.js").write_bytes(os.urandom(2000) + (f"line {label} {d} {i}\n" * 1000).encode())
for i in range(2):
    (cwd / f"big{i}.bin").write_bytes(os.urandom(200 * 1024 * 1024))
past = time.time() - 600
for path in cwd.rglob("*"):
    if path.is_file():
        os.utime(path, (past, past))
roots = default_roots(cwd, environ={}, home=base / "home", asset_home=base / "home", config_dir=base / "ciel")
target = LocalTarget(base / "target")
rows = []
for step in ("first", "unchanged", "100 changed"):
    if step == "100 changed":
        for d in range(100):
            path = cwd / f"d{d}" / "f0.js"
            path.write_bytes(path.read_bytes() + b"// changed\n")
    started = time.perf_counter()
    result = create_snapshot(roots, [target], CollectOptions(), key=None, versions={}, host="h", user="u", jobs=8)
    seconds = time.perf_counter() - started
    stats = result.manifest["stats"]
    rows.append({"code": str(code), "step": step, "seconds": round(seconds, 1), "files": len(result.manifest["entries"]),
                 "mb_total": round(stats["bytes_total"] / 1048576), "uploaded_chunks": stats["uploaded_chunks"],
                 "reused_files": stats.get("reused_files", "-")})
    print(json.dumps(rows[-1]), flush=True)
shutil.rmtree(base, ignore_errors=True)
