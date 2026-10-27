# Read-only: Emily (.14 aapw-a11u4, router 8966) compaction detail around the
# 2026-10-26 13:00-14:30 host-time failures. Prints activity JSON, progress
# run step kinds (no summary text), router log rows and transcript rows.
import os, json, time, glob
def T(x): return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(x))
t0 = time.time(); print("REMOTE_START", T(t0), "gmtoff", time.localtime(t0).tm_gmtoff)
inst = r"C:\Users\aapw-a11u4\AppData\Roaming\ciel-runtime\router-instances\8966-c27b0d656c05"
print("ACTIVITY", open(os.path.join(inst, "context-compact-activity.json"), encoding="utf-8").read())
print("ADMISSION", open(os.path.join(inst, "compaction-admission.json"), encoding="utf-8").read())
print("SCOPE", open(os.path.join(inst, "compaction-scope.json"), encoding="utf-8").read()[:1500])
d = json.load(open(os.path.join(inst, "context-compact-progress.json"), encoding="utf-8"))
for k, v in d.get("runs", {}).items():
    print("RUN", k[:16], T(v.get("at", 0)), [(s.get("kind"), s.get("prompt", "")[:10], len(s.get("text", ""))) for s in v.get("steps", [])])
KEYS = ("context_compact", "router_client_disconnected", "semantic", "native_compact", "runtime_compaction", "claude_compaction_launch_snapshot",
        "POST /v1/messages provider", "anthropic_sse", "upstream_direct_request", "claude_stream_recovery", "compact")
log = os.path.join(inst, "router.log")
with open(log, "rb") as fh:
    for raw in fh:
        line = raw.decode("utf-8", "replace")
        if line[:13] in ("2026-10-26T12", "2026-10-26T13", "2026-10-26T14") and any(k in line for k in KEYS):
            if "checkpoint_scan" in line: continue
            print("LOG", line.rstrip()[:330])
print("REMOTE_END", T(time.time()))
