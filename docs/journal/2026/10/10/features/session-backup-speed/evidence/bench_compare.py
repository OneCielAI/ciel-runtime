import os, shutil, sys, time
from pathlib import Path
code, jobs = sys.argv[1], int(sys.argv[2])
sys.path.insert(0, code)
from ciel_runtime_support.session_backup_collect import CollectOptions, default_roots
from ciel_runtime_support.session_backup_ops import create_snapshot
from ciel_runtime_support.session_backup_store import LocalTarget
BASE = Path(r"G:\ciel-e2e\bench")
out = BASE / "cmp-target"
shutil.rmtree(out, ignore_errors=True)
roots = default_roots(BASE / "ws", environ={}, home=BASE / "home", asset_home=BASE / "home", config_dir=BASE / "cfg")
kw = {"jobs": jobs} if jobs else {}
for label in ("first", "incremental"):
    t = time.perf_counter()
    r = create_snapshot(roots, [LocalTarget(out, "local")], CollectOptions(), key=None, versions={}, host="h", user="u", **kw)
    dt = time.perf_counter() - t
    print(f"{Path(code).name:16} jobs={jobs or 'n/a'} {label:11} {dt:6.1f} s  {1081 / dt:6.0f} MB/s  stored {r.stats['bytes_uploaded'] / 1048576:.0f} MB")
shutil.rmtree(out, ignore_errors=True)
