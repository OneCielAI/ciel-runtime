import os, sys, time, shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
base = Path(sys.argv[1])
def make(name, n=4000):
    d = base / name; shutil.rmtree(d, ignore_errors=True); d.mkdir(parents=True)
    for i in range(n):
        (d / f"f{i}.js").write_bytes(os.urandom(2000) + (f"line {i}\n" * 1500).encode())
    return sorted(d.iterdir())
def read(p):
    with open(p, "rb") as f: return len(f.read())
for workers in (1, 8, 16):
    files = make(f"fresh{workers}")
    t = time.perf_counter()
    if workers == 1:
        for p in files: read(p)
    else:
        with ThreadPoolExecutor(workers) as pool: list(pool.map(read, files))
    el = time.perf_counter() - t
    print(f"fresh files, {workers} reader(s): {el:.1f}s = {el/len(files)*1000:.2f} ms/file")
