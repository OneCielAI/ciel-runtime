"""Why unclaimed rows are not claimed: source-holder binding (read-only, uses the installed source_identity)."""
import collections, hashlib, json, sys, time
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, sys.argv[2])
from ciel_runtime_support.side_intake_inbox import source_identity  # noqa: E402
inbox = Path(sys.argv[1])
pending = {}
for path in (inbox / "pending").glob("*/*.json"):
    pending[path.stem] = json.loads(path.read_text(encoding="utf-8"))
def state(row):
    f = row.get("side_failure") or {}
    return "unclaimed" if not f else ("quarantined" if f.get("quarantined") else "waiting")
verdict = collections.Counter(); holders = collections.Counter()
for key, row in pending.items():
    if state(row) != "unclaimed":
        continue
    source = source_identity(row["text"])
    if not source:
        verdict["no source identity -> claimable"] += 1
        continue
    marker = inbox / "sources" / (hashlib.sha256(source.encode()).hexdigest() + ".json")
    try:
        holder = json.loads(marker.read_text(encoding="utf-8")).get("receipt_id")
    except (OSError, ValueError):
        holder = None
    if not holder or holder == key:
        verdict["holder none/self -> claimable"] += 1
        continue
    other = pending.get(holder)
    if other is None:
        verdict["holder processed or gone"] += 1
        continue
    verdict["holder pending: " + state(other)] += 1
    holders[(holder[:12], state(other), (other.get("side_failure") or {}).get("category"),
             (other.get("side_failure") or {}).get("compaction_waits"),
             time.strftime("%m-%d %H:%M", time.localtime(other.get("received_at") or 0)))] += 1
for k, v in verdict.most_common():
    print(v, k)
print("blocking holders:")
for k, v in holders.most_common(10):
    print(" ", v, "rows wait on", k)
