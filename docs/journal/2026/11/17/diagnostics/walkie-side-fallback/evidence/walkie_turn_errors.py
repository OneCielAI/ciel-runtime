import collections, json, sys, time
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
inbox = Path(sys.argv[1]); since = time.time() - float(sys.argv[2]) * 3600
tally = collections.Counter(); samples = {}
for folder in ("receipts", "pending"):
    for path in (inbox / folder).glob("*/*.json"):
        row = json.loads(path.read_text(encoding="utf-8"))
        f = row.get("side_failure") or {}
        if f.get("category") not in ("turn_error", "aborted", "legacy_main_fallback_reclaimed"):
            continue
        if (f.get("failed_at") or row.get("received_at") or 0) < since:
            continue
        immediate = f.get("retry_not_before") == f.get("failed_at")
        key = (folder, f.get("category"), f.get("use_parent_model"), "parent_since" in str(f.keys()) and bool(f.get("parent_model_since")),
               immediate, f.get("attempt_count"), (row.get("result") or {}).get("delivery"))
        tally[key] += 1
        samples.setdefault(key, (path.stem[:12], time.strftime("%m-%d %H:%M:%S", time.localtime(f.get("failed_at") or 0)), sorted(f)))
for key, value in sorted(tally.items(), key=lambda kv: -kv[1]):
    print(value, "folder=%s category=%s use_parent_model=%s parent_since=%s immediate_retry=%s attempts=%s delivery=%s" % key, samples[key][:2])
print("sample failure keys:", next(iter(samples.values()))[2] if samples else None)
