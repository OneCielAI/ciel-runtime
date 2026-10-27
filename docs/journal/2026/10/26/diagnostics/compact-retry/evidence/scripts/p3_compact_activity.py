# Read-only: per router instance on .14, print compact activity, admission
# phase and the last semantic-compaction log rows of the past 24 hours.
import os, json, time, glob
def T(x): return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(x))
t0 = time.time(); print("REMOTE_START", T(t0), "gmtoff", time.localtime(t0).tm_gmtoff)
KEYS = ("context_compact_", "router_client_disconnected", "semantic_compaction", "native_compact", "runtime_compaction_control event=")
for inst in sorted(glob.glob(r"C:\Users\aapw-*\AppData\Roaming\ciel-runtime\router-instances\*")):
    try:
        mt = os.path.getmtime(inst)
        if t0 - mt > 86400: continue
        print("== INSTANCE", inst, "mtime", T(mt))
        for name in ("context-compact-activity.json", "compaction-admission.json"):
            p = os.path.join(inst, name)
            if os.path.exists(p):
                try: print("  ", name, json.dumps(json.load(open(p, encoding="utf-8")), ensure_ascii=False)[:600])
                except Exception as e: print("  ", name, "ERR", type(e).__name__)
        p = os.path.join(inst, "context-compact-progress.json")
        if os.path.exists(p):
            try:
                d = json.load(open(p, encoding="utf-8"))
                runs = d.get("runs", {})
                print("   progress runs", len(runs), [(k[:12], T(v.get("at", 0)), len(v.get("steps", []))) for k, v in runs.items()])
            except Exception as e: print("   progress ERR", type(e).__name__)
        log = os.path.join(inst, "router.log")
        if os.path.exists(log):
            rows = []
            with open(log, "rb") as fh:
                fh.seek(max(0, os.path.getsize(log) - 40 * 1024 * 1024))
                for raw in fh:
                    line = raw.decode("utf-8", "replace")
                    if any(k in line for k in KEYS): rows.append(line.rstrip()[:300])
            for r in rows[-40:]: print("   LOG", r)
    except Exception as e:
        print("ERR", inst, type(e).__name__)
print("REMOTE_END", T(time.time()))
