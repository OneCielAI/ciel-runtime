"""Where an unchanged backup spends its time (10,000 small files, second backup).

usage: python profile_unchanged.py WORK_BASE
"""
import cProfile
import io
import os
import pstats
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[8]))
from ciel_runtime_support.session_backup_collect import CollectOptions, default_roots  # noqa: E402
from ciel_runtime_support.session_backup_ops import create_snapshot  # noqa: E402
from ciel_runtime_support.session_backup_store import LocalTarget  # noqa: E402

base = Path(sys.argv[1]) / "profile"
shutil.rmtree(base, ignore_errors=True)
cwd = base / "work"
for d in range(100):
    (cwd / f"d{d}").mkdir(parents=True)
    for i in range(100):
        (cwd / f"d{d}" / f"f{i}.js").write_bytes(os.urandom(2000) + (f"line {d} {i}\n" * 1000).encode())
past = time.time() - 600
for path in cwd.rglob("*"):
    os.utime(path, (past, past))
roots = default_roots(cwd, environ={}, home=base / "home", asset_home=base / "home", config_dir=base / "ciel")
target = LocalTarget(base / "target")
create_snapshot(roots, [target], CollectOptions(), key=None, versions={}, host="h", user="u", jobs=8)
profile = cProfile.Profile()
started = time.perf_counter()
profile.enable()
result = create_snapshot(roots, [target], CollectOptions(), key=None, versions={}, host="h", user="u", jobs=8)
profile.disable()
print(f"unchanged: {time.perf_counter() - started:.2f} s, reused {result.manifest['stats']['reused_files']}")
out = io.StringIO()
pstats.Stats(profile, stream=out).sort_stats("tottime").print_stats(15)
print("\n".join(out.getvalue().splitlines()[4:30]))
shutil.rmtree(base, ignore_errors=True)
