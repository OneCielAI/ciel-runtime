"""Tally Celly side-fork decisions for events whose source_priority is P0 (read-only)."""
import collections
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
folder = Path(sys.argv[1])
# The JSON field only (the side prompt's own words "source_priority is P0" are not a value).
FIELD = re.compile(r'source_priority\\*"\s*:\s*(?:\\*"(P[0-5])|null)')
values = collections.Counter()
tally = collections.Counter()
samples = collections.defaultdict(list)
for path in sorted(folder.glob("*.jsonl"), key=lambda p: p.stat().st_mtime):
    raw = path.read_text(encoding="utf-8", errors="replace")
    found = {match.group(1) or "null" for match in FIELD.finditer(raw)}
    values[",".join(sorted(found)) or "(no field)"] += 1
    if "P0" not in found:
        continue
    final = None
    for line in raw.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        message = row.get("message") if isinstance(row, dict) else None
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for block in message.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text" and '"decision"' in block.get("text", ""):
                final = (row.get("timestamp"), block["text"])
    if final is None:
        tally[("no-decision-text", "-")] += 1
        samples[("no-decision-text", "-")].append(path.name)
        continue
    text = final[1]
    start = text.find("{")
    try:
        decision = json.loads(text[start:text.rfind("}") + 1]).get("decision") or {}
    except ValueError:
        tally[("unparsed", "-")] += 1
        samples[("unparsed", "-")].append(path.name)
        continue
    key = (str(decision.get("action")) + "/" + str(decision.get("kind")), str(decision.get("priority") or "(none)"))
    tally[key] += 1
    if len(samples[key]) < 3:
        samples[key].append(f"{final[0]} {path.name} item={decision.get('item_id')}")
print("source_priority values per side fork:", dict(values.most_common()))
for key, count in tally.most_common():
    print(count, key)
    for sample in samples[key][:3]:
        print("   ", sample)
