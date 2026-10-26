"""Read-only: per UTC hour, count unique main-thread assistant messages by the
upstream model Claude Code recorded and by stop_reason.

Usage: python model_stop_by_day.py <transcript.jsonl> <out.json>
"""
import collections
import json
import sys

path, out = sys.argv[1], sys.argv[2]
seen = {}
with open(path, "rb") as handle:
    for line in handle:
        if b'"type":"assistant"' not in line and b'"type": "assistant"' not in line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get("type") != "assistant" or record.get("isSidechain"):
            continue
        message = record.get("message") if isinstance(record.get("message"), dict) else {}
        key = message.get("id")
        if not key or message.get("model") == "<synthetic>":
            continue
        entry = seen.setdefault(key, {"hour": str(record.get("timestamp") or "")[:13], "model": message.get("model"), "stop": None})
        if message.get("stop_reason"):
            entry["stop"] = message.get("stop_reason")
table = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
for entry in seen.values():
    table[entry["hour"]][entry["model"]][entry["stop"]] += 1
result = {hour: {model: dict(stops) for model, stops in models.items()} for hour, models in sorted(table.items())}
with open(out, "w", encoding="utf-8") as output:
    json.dump(result, output, indent=1)
print(len(seen), "messages,", len(result), "hours")
