import sys, time
from pathlib import Path
code, jobs, kind = sys.argv[1], int(sys.argv[2]), sys.argv[3]
sys.path.insert(0, code)
from ciel_runtime_support.session_backup_collect import CollectOptions, default_roots
from ciel_runtime_support.session_backup_ops import create_snapshot
BASE = Path(r"G:\ciel-e2e\bench")
for p in (BASE / "ws").rglob("*"):  # warm the page cache
    if p.is_file():
        with open(p, "rb") as f:
            while f.read(8 << 20):
                pass

class Sink:
    """has() answers `exists`; put() keeps nothing; `delay` per request like a network round trip."""
    def __init__(self, exists=False, delay=0.0):
        self.name, self.exists, self.delay, self.puts = "sink", exists, delay, 0
    def has(self, key):
        time.sleep(self.delay); return self.exists
    def put(self, key, data):
        time.sleep(self.delay); self.puts += 1
    def get(self, key): raise KeyError(key)
    def list(self, prefix): return []
    def delete(self, key): pass

target = {"cpu-first": Sink(), "cpu-incremental": Sink(exists=True), "net30ms-first": Sink(delay=0.03)}[kind]
roots = default_roots(BASE / "ws", environ={}, home=BASE / "home", asset_home=BASE / "home", config_dir=BASE / "cfg")
kw = {"jobs": jobs} if jobs else {}
t = time.perf_counter()
create_snapshot(roots, [target], CollectOptions(), key=None, versions={}, host="h", user="u", **kw)
dt = time.perf_counter() - t
print(f"{'old' if 'old' in code else 'new':3} jobs={jobs or '-':<2} {kind:16} {dt:6.1f} s {1081 / dt:6.0f} MB/s")
