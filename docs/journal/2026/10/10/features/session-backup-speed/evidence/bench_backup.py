"""Profile a session backup: where does the time go? (G: scratch, local target)"""
import cProfile
import io
import json
import os
import pstats
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, r"C:\Users\djlov\ciel-runtime")
from ciel_runtime_support.session_backup_collect import CollectOptions, default_roots  # noqa: E402
from ciel_runtime_support.session_backup_ops import create_snapshot  # noqa: E402
from ciel_runtime_support.session_backup_store import LocalTarget  # noqa: E402

JOBS = int(os.environ.get("BENCH_JOBS") or 1)
BASE = Path(r"G:\ciel-e2e\bench")
if "--keep" not in sys.argv:
    shutil.rmtree(BASE, ignore_errors=True)
WS, CFG, HOME, OUT = BASE / "ws", BASE / "cfg", BASE / "home", BASE / "target"
for d in (WS, CFG, HOME):
    d.mkdir(parents=True, exist_ok=True)
if not (WS / "bin0.bin").exists():
    for i in range(3):
        with open(WS / f"bin{i}.bin", "wb") as f:
            for _ in range(256):
                f.write(os.urandom(1 << 20))
    line = json.dumps({"type": "message", "text": "lorem ipsum dolor sit amet " * 20}) + "\n"
    with open(WS / "log.jsonl", "w", encoding="utf-8") as f:
        while f.tell() < 256 << 20:
            f.write(line * 100)
    small = WS / "small"
    small.mkdir()
    for i in range(3000):
        (small / f"f{i}.txt").write_bytes((f"file {i} " * 2500).encode()[:20000])
total = sum(p.stat().st_size for p in WS.rglob("*") if p.is_file())
print(f"work folder {total / 1048576:.0f} MB")


def run(label: str) -> None:
    roots = default_roots(WS, environ={}, home=HOME, asset_home=HOME, config_dir=CFG)
    target = LocalTarget(OUT, "local")
    prof = cProfile.Profile()
    start = time.perf_counter()
    prof.enable()
    result = create_snapshot(roots, [target], CollectOptions(), key=None, versions={}, host="h", user="u", jobs=JOBS)
    prof.disable()
    elapsed = time.perf_counter() - start
    print(f"{label}: {elapsed:.1f} s, {total / 1048576 / elapsed:.0f} MB/s, stats {result.stats}")
    stream = io.StringIO()
    pstats.Stats(prof, stream=stream).sort_stats("tottime").print_stats(12)
    print("\n".join(stream.getvalue().splitlines()[:30]))


run("first backup")
run("second backup (nothing changed)")
