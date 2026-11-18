"""Read-only scan of one side inbox's pending rows: failure, fallback, quarantine, main forward.

usage: python -I pending_scan.py <side-inbox folder> [hours]

Prints the key sets, counts per failure state, and one line per row received or failed in the
last [hours] (default 24). Event bodies, senders and secrets are not printed; the failure
reason is printed with model names kept (they are provider aliases, not secrets).
"""
import collections
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
inbox = Path(sys.argv[1])
hours = float(sys.argv[2]) if len(sys.argv) > 2 else 24.0
since = time.time() - hours * 3600


def priority(text):
    try:
        body = json.loads(text)
    except (TypeError, ValueError):
        return "-"
    data = body.get("data", body) if isinstance(body, dict) else {}
    if not isinstance(data, dict):
        return "-"
    if data.get("emergency") is True:
        return "P0"
    context = data.get("message_context")
    task = context.get("thread_task") if isinstance(context, dict) else None
    if isinstance(task, dict):
        if task.get("emergency") is True:
            return "P0"
        value = task.get("priority")
        return f"P{value}" if isinstance(value, int) else str(value or "-")
    return "-"


def stamp(value):
    return time.strftime("%m-%d %H:%M:%S", time.localtime(value)) if isinstance(value, (int, float)) and value else "-"


rows = []
keysets = collections.Counter()
failure_keys = collections.Counter()
for path in sorted((inbox / "pending").glob("*/*.json")):
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        continue
    keysets[",".join(sorted(row))] += 1
    failure = row.get("side_failure") if isinstance(row.get("side_failure"), dict) else None
    if failure:
        failure_keys[",".join(sorted(failure))] += 1
    rows.append((path.stem, row, failure))

print("pending rows:", len(rows))
print("row key sets:")
for key, value in keysets.most_common():
    print(f"  {value}: {key}")
print("side_failure key sets:")
for key, value in failure_keys.most_common():
    print(f"  {value}: {key}")
counts = collections.Counter()
for _key, row, failure in rows:
    counts["stage=" + str(row.get("stage"))] += 1
    counts["main_forward=" + str(row.get("main_forward"))] += 1
    notice = row.get("release_notice") if isinstance(row.get("release_notice"), dict) else None
    counts["release_notice=" + (str(notice.get("state")) if notice else "none")] += 1
    for name in ("side_fallback", "main_fallback", "fallback"):
        if name in row:
            counts[name + "=" + str((row[name] or {}).get("state") if isinstance(row[name], dict) else row[name])] += 1
    if failure:
        for name in ("category", "quarantined", "retryable", "typed", "use_parent_model", "main_fallback_eligible"):
            counts[f"failure.{name}={failure.get(name)}"] += 1
        counts["failure.model=" + str(failure.get("model"))] += 1
    else:
        counts["no side_failure"] += 1
print("counts:")
for key, value in sorted(counts.items()):
    print(f"  {key}: {value}")
recent = [entry for entry in rows if max(entry[1].get("received_at") or 0, (entry[2] or {}).get("failed_at") or 0) >= since]
print(f"recent (last {hours} h): {len(recent)}")
for key, row, failure in sorted(recent, key=lambda entry: entry[1].get("received_at") or 0):
    notice = row.get("release_notice") if isinstance(row.get("release_notice"), dict) else {}
    extra = {name: row.get(name) for name in ("side_fallback", "main_fallback", "fallback") if name in row}
    print(stamp(row.get("received_at")), key[:12], row.get("stage"), priority(row.get("text")),
          "main_forward=" + str(row.get("main_forward")),
          "notice=" + str(notice.get("state") or "-") + "@" + stamp(notice.get("updated_at")),
          json.dumps({k: failure.get(k) for k in ("category", "model", "attempt_count", "quarantined", "retryable", "typed",
                                                  "use_parent_model", "main_fallback_eligible")} if failure else {}, ensure_ascii=False),
          "failed_at=" + stamp((failure or {}).get("failed_at")),
          json.dumps(extra, ensure_ascii=False)[:200] if extra else "")
    if failure and failure.get("reason"):
        print("     reason:", str(failure.get("reason"))[:200])
