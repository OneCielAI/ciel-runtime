import collections, json, sys, time
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
inbox = Path(sys.argv[1])
since = time.time() - float(sys.argv[2]) * 3600
tally = collections.Counter()
for path in (inbox / "pending").glob("*/*.json"):
    row = json.loads(path.read_text(encoding="utf-8"))
    f = row.get("side_failure") or {}
    if not f or (f.get("failed_at") or 0) < since:
        continue
    reason = str(f.get("reason") or "")
    kind = "budget" if "main context budget" in reason else ("persisted" if "persisted result" in reason else reason[:50])
    tally[(f.get("category"), kind, f.get("compaction_waits"), f.get("attempt_count"), f.get("quarantined"), f.get("use_parent_model"))] += 1
for key, value in sorted(tally.items(), key=lambda kv: -kv[1]):
    print(value, "category=%s reason=%s compaction_waits=%s attempts=%s quarantined=%s parent_model=%s" % key)
