import collections, json, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
inbox = Path(sys.argv[1])
def thread(text):
    try: body = json.loads(text)
    except ValueError: return "-"
    data = body.get("data", body) if isinstance(body, dict) else {}
    msg = data.get("message") if isinstance(data.get("message"), dict) else data
    root = msg.get("thread_root_id") or data.get("thread_root_id") or (data.get("message_context") or {}).get("thread_root_id") if isinstance(msg, dict) else None
    return str(root or msg.get("id") if isinstance(msg, dict) else "-")
groups = collections.Counter()
for path in (inbox / "pending").glob("*/*.json"):
    row = json.loads(path.read_text(encoding="utf-8"))
    f = row.get("side_failure") or {}
    state = "unclaimed" if not f else ("quarantined" if f.get("quarantined") else "waiting")
    if state == "quarantined" and "main context budget" not in str(f.get("reason")):
        continue
    groups[(state, thread(row.get("text")))] += 1
for key, value in groups.most_common(15):
    print(value, key)
print("distinct threads among unclaimed:", len({k[1] for k in groups if k[0] == "unclaimed"}))
