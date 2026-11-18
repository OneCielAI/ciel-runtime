"""Which 'Ciel side 처리 실패 · 원문 전달' notices appear in the main transcript, and how (user text vs attachment)."""
import json, re, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
NEEDLE = "Ciel side 처리 실패 · 원문 전달 · event ".encode()
EVENT = re.compile(r"Ciel side 처리 실패 · 원문 전달 · event ([0-9a-f]{12})")
seen = {}
with open(sys.argv[1], "rb") as stream:
    for number, raw in enumerate(stream, 1):
        if NEEDLE not in raw:
            continue
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        kind = row.get("type")
        sub = (row.get("attachment") or {}).get("type") if isinstance(row.get("attachment"), dict) else None
        role = (row.get("message") or {}).get("role") if isinstance(row.get("message"), dict) else None
        text = raw.decode("utf-8", "replace")
        for event in EVENT.findall(text):
            seen.setdefault(event, []).append((row.get("timestamp"), kind, sub, role, number, bool(row.get("isSidechain"))))
wanted = sys.argv[2:]
for event in wanted:
    rows = seen.get(event, [])
    first = [r for r in rows if not r[5]]
    print(event, "main-occurrences", len(first), "first", first[0][:5] if first else None)
print("distinct events forwarded in transcript:", len(seen))
