import json
import sys
path = sys.argv[1]
rows = []
with open(path, encoding="utf-8") as fh:
    for i, line in enumerate(fh):
        try:
            rows.append((i, json.loads(line)))
        except Exception:
            pass
print("total", len(rows))
last_compact = None
for i, r in rows:
    if r.get("type") == "system" and r.get("subtype") == "compact_boundary":
        last_compact = i
print("last compact_boundary line", last_compact)
events = []
for i, r in rows:
    t = r.get("type")
    ts = r.get("timestamp", "")
    pm = r.get("permissionMode")
    att = r.get("attachment") if isinstance(r.get("attachment"), dict) else None
    if att and "plan" in str(att.get("type", "")):
        events.append((i, ts, "attachment", att.get("type"), pm))
    msg = r.get("message") if isinstance(r.get("message"), dict) else {}
    content = msg.get("content")
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in ("EnterPlanMode", "ExitPlanMode"):
                events.append((i, ts, "tool_use", b.get("name"), pm))
    if t == "user" and pm:
        events.append((i, ts, "user.permissionMode", pm, None))
# compress consecutive identical permissionMode entries
prev = None
for e in events:
    key = (e[2], e[3])
    if e[2] == "user.permissionMode" and prev == key:
        continue
    prev = key
    print(e)
