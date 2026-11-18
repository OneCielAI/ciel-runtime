"""Read-only scan of one side inbox: failure, fallback and quarantine states per receipt.

usage: python -I receipt_scan.py <side-inbox folder> [hours]

Prints counts and, for receipts touched in the last [hours] (default 12), one line each:
received time, stage, source priority, side_failure category/attempts/quarantined,
fallback state, result delivery/action. Event bodies, sources and secrets are not printed.
"""
import collections
import json
import re
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
inbox = Path(sys.argv[1])
hours = float(sys.argv[2]) if len(sys.argv) > 2 else 12.0
since = time.time() - hours * 3600
FIELD = re.compile(r'"priority"\s*:\s*(\d|"P[0-5]")')


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
    task = (data.get("message_context") or {}).get("thread_task") if isinstance(data.get("message_context"), dict) else None
    if isinstance(task, dict):
        if task.get("emergency") is True:
            return "P0"
        value = task.get("priority")
        return f"P{value}" if isinstance(value, int) else str(value or "-")
    return "-"


def event_type(text):
    try:
        body = json.loads(text)
    except (TypeError, ValueError):
        return "-"
    return str(body.get("type") or body.get("event") or "-")[:40] if isinstance(body, dict) else "-"


counts = collections.Counter()
recent = []
for path in sorted((inbox / "receipts").glob("*/*.json")):
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        counts["unreadable"] += 1
        continue
    failure = row.get("side_failure") if isinstance(row.get("side_failure"), dict) else None
    fallback = row.get("side_fallback") if isinstance(row.get("side_fallback"), dict) else None
    result = row.get("result") if isinstance(row.get("result"), dict) else {}
    stage = row.get("stage")
    counts["stage=" + str(stage)] += 1
    if failure:
        counts["has_side_failure"] += 1
        counts["failure_category=" + str(failure.get("category"))] += 1
        if failure.get("quarantined"):
            counts["quarantined"] += 1
    if fallback:
        counts["fallback_state=" + str(fallback.get("state"))] += 1
    touched = max([float(v) for v in (row.get("received_at"), (failure or {}).get("at"), (failure or {}).get("updated_at"),
                                      (fallback or {}).get("at"), (fallback or {}).get("updated_at")) if isinstance(v, (int, float))] or [0])
    if touched >= since:
        recent.append((row.get("received_at") or 0, path.stem[:12], stage, priority(row.get("text")), event_type(row.get("text")),
                       failure, fallback, result.get("delivery"), result.get("action"), sorted(row.keys())))
print("receipts:", sum(v for k, v in counts.items() if k.startswith("stage=")))
for key, value in sorted(counts.items()):
    print(f"  {key}: {value}")
print(f"recent (last {hours} h): {len(recent)}")
for received, key, stage, prio, kind, failure, fallback, delivery, action, keys in sorted(recent):
    stamp = time.strftime("%m-%d %H:%M:%S", time.localtime(received)) if received else "-"
    fail = ("fail=" + json.dumps({k: failure.get(k) for k in ("category", "attempts", "quarantined", "model", "reason",
                                                            "retry_after", "fallback")}, ensure_ascii=False)) if failure else ""
    back = ("fallback=" + json.dumps(fallback, ensure_ascii=False)[:300]) if fallback else ""
    print(stamp, key, stage, prio, kind, f"delivery={delivery} action={action}", fail, back)
    if failure or fallback:
        print("     keys:", ",".join(keys))
