# Read-only probe: find Claude transcripts on .14 that contain the manual
# /compact idle-timeout error. Opens files read-only; writes nothing remotely.
import os, json, time, glob
def T(x): return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(x))
t0 = time.time(); print("REMOTE_START", T(t0), "gmtoff", time.localtime(t0).tm_gmtoff)
NEEDLE = b"no chunks received"
since = t0 - 2 * 86400
roots = glob.glob(r"C:\Users\aapw-*\.claude\projects") + glob.glob(r"C:\ProgramData\aap-win\sandboxes\u*\home\*\.claude\projects")
print("ROOTS", roots)
for root in roots:
    for dirpath, dirs, files in os.walk(root):
        if "subagents" in dirpath or "tool-results" in dirpath: continue
        for f in files:
            if not f.endswith(".jsonl"): continue
            p = os.path.join(dirpath, f)
            try:
                st = os.stat(p)
                if st.st_mtime < since: continue
                hits = []
                with open(p, "rb") as fh:
                    for i, line in enumerate(fh):
                        if NEEDLE in line and b"Error during compaction" in line:
                            try: r = json.loads(line)
                            except Exception: continue
                            if r.get("type") == "system":
                                hits.append((i, r.get("subtype"), r.get("timestamp"), str(r.get("content"))[:200]))
                if hits:
                    print("FILE", p, "size", st.st_size, "mtime", T(st.st_mtime))
                    for h in hits: print("  HIT", h)
            except Exception as e:
                print("ERR", p, type(e).__name__)
print("REMOTE_END", T(time.time()))
