import json, sys, time, statistics
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
inbox = Path(sys.argv[1]); now = time.time()
lat = []; recent_fail = []; unclaimed = []
for path in (inbox / "pending").glob("*/*.json"):
    row = json.loads(path.read_text(encoding="utf-8"))
    f = row.get("side_failure") or {}; n = row.get("release_notice") or {}
    if f.get("quarantined") and "main context budget" in str(f.get("reason")) and n.get("updated_at"):
        lat.append((n["updated_at"] - row["received_at"]) / 60)
    if f and now - (f.get("failed_at") or 0) < 3600:
        recent_fail.append((f["failed_at"], path.stem[:12], f.get("category"), f.get("compaction_waits"), f.get("attempt_count"),
                            time.strftime("%H:%M:%S", time.localtime(row["received_at"]))))
    if not f and not row.get("processing"):
        unclaimed.append(row["received_at"])
print("budget-quarantine forward latency minutes: n=%d min=%.0f median=%.0f max=%.0f" % (len(lat), min(lat), statistics.median(lat), max(lat)) if lat else "none")
print("unclaimed rows (no failure, no processing): %d, oldest received %s, newest %s" % (
    len(unclaimed), time.strftime("%m-%d %H:%M:%S", time.localtime(min(unclaimed))), time.strftime("%m-%d %H:%M:%S", time.localtime(max(unclaimed)))))
print("rows failed in the last hour:", len(recent_fail))
for r in sorted(recent_fail):
    print(" ", time.strftime("%H:%M:%S", time.localtime(r[0])), r[1], r[2], "waits=%s attempts=%s received=%s" % r[3:])
