import json, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
rows = []
with open(sys.argv[1], "rb") as stream:
    for number, raw in enumerate(stream, 1):
        if b'"compact_boundary"' not in raw:
            continue
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if row.get("subtype") != "compact_boundary":
            continue
        meta = row.get("compactMetadata") or {}
        rows.append((row.get("timestamp"), meta.get("trigger"), meta.get("preTokens"), number))
for row in rows:
    if row[0] and row[0] >= sys.argv[2]:
        print(*row)
print("total", len(rows), "manual", sum(1 for r in rows if r[1] == "manual"))
