import cProfile, os, pstats, sys, time, shutil, io
from pathlib import Path
sys.path.insert(0, r"C:\Users\djlov\ciel-runtime")
from ciel_runtime_support.session_backup_collect import CollectOptions, default_roots
from ciel_runtime_support.session_backup_ops import create_snapshot
from ciel_runtime_support.session_backup_store import LocalTarget
base = Path(sys.argv[1]); cwd = base / "work"
if not cwd.exists():
    for d in range(100):
        (cwd / f"d{d}").mkdir(parents=True)
        for i in range(100):
            (cwd / f"d{d}" / f"f{i}.js").write_bytes(os.urandom(2000) + (f"line {d} {i}\n" * 1500).encode())
roots = default_roots(cwd, environ={}, home=base / "home", asset_home=base / "home", config_dir=base / "ciel")
for label in ("first", "again"):
    if label == "first": shutil.rmtree(base / "target", ignore_errors=True)
    prof = cProfile.Profile(); t = time.perf_counter(); prof.enable()
    r = create_snapshot(roots, [LocalTarget(base / "target")], CollectOptions(), key=None, versions={}, host="h", user="u", jobs=8)
    prof.disable(); el = time.perf_counter() - t
    st = r.manifest["stats"]
    print(f"{label}: {el:.1f}s files={len(r.manifest['entries'])} MB={st.get('bytes_total',0)/1e6:.0f} uploaded={st.get('uploaded_chunks')}")
    s = io.StringIO(); pstats.Stats(prof, stream=s).sort_stats("tottime").print_stats(12); print("\n".join(s.getvalue().splitlines()[6:24]))
