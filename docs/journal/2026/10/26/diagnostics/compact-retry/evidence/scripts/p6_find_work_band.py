# Read-only: find the Work board that matches the screenshot band
# (an item mentioning 81915 and Robert/Ara). Prints matching node titles only.
import os, json, time, glob, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
def T(x): return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(x))
print("REMOTE_START", T(time.time()), "gmtoff", time.localtime().tm_gmtoff)
for f in glob.glob(r"C:\Users\aapw-*\AppData\Roaming\ciel-runtime\workspaces\*\work-sessions\*\work-plan.json"):
    try:
        raw = open(f, "rb").read()
        if b"81915" not in raw and b"Robert" not in raw: continue
        d = json.loads(raw)
        nodes = d.get("nodes", {}); items = list(nodes.values() if isinstance(nodes, dict) else nodes)
        print("PLAN", f, "mtime", T(os.path.getmtime(f)), "active", d.get("active_root"), "version", d.get("version"))
        for n in items:
            t = n.get("title", "")
            if "81915" in t or "Robert" in t or n.get("id") == d.get("active_root"):
                print("   ", n.get("id"), n.get("status"), n.get("parent"), t[:160])
    except Exception as e:
        print("ERR", f, type(e).__name__)
print("REMOTE_END", T(time.time()))
