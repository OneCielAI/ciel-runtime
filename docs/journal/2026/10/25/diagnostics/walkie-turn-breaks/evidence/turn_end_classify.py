"""Read-only: classify each Claude Code turn in a transcript region by its trigger,
upstream model and tool-call count, without copying conversation text.

Usage: python turn_end_classify.py <transcript.jsonl> <start-offset> <out.json>
"""
import collections
import json
import sys

path, start, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]


def trigger_of(record, blocks):
    if record.get("isCompactSummary"):
        return "compaction-summary"
    text = next((str(b.get("text", "")) for b in blocks if isinstance(b, dict) and b.get("type") == "text"), "")
    if not isinstance(record.get("message", {}).get("content"), list):
        text = str(record.get("message", {}).get("content") or "")
    text = " ".join(text.split())
    if text.startswith("Another Claude session sent a message: [ciel-runtime external channel"):
        return "external-channel"
    if text.startswith("Another Claude session sent a message: <teammate-message"):
        return "teammate-message"
    if text.lstrip().startswith("<task-notification>"):
        return "task-notification"
    if text.startswith("[Image:"):
        return "image-meta"
    return "human-meta" if record.get("isMeta") else "human"


turns = []
current = None
messages = {}
with open(path, "rb") as handle:
    handle.seek(start)
    if start:
        handle.readline()
    for line in handle:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get("isSidechain"):
            continue
        message = record.get("message") if isinstance(record.get("message"), dict) else {}
        content = message.get("content")
        blocks = content if isinstance(content, list) else []
        if record.get("type") == "user":
            if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks):
                continue
            trigger = trigger_of(record, blocks)
            if trigger == "image-meta" and current is not None:
                continue
            if current is not None and not current.get("ended"):
                current["superseded_by"] = trigger
            current = {"start": record.get("timestamp"), "trigger": trigger, "tool_calls": 0,
                       "models": collections.Counter(), "ended": None}
            turns.append(current)
        elif record.get("type") == "assistant" and current is not None:
            model = message.get("model")
            if model == "<synthetic>":
                current["synthetic"] = True
                continue
            # Claude Code writes one record per content block, so a message's thinking
            # and tool_use blocks arrive as separate records with the same id.
            key = message.get("id")
            if key not in messages:
                messages[key] = False
                current["models"][model] += 1
            if not messages[key] and any(isinstance(b, dict) and b.get("type") == "tool_use" for b in blocks):
                messages[key] = True
                current["tool_calls"] += 1
            if message.get("stop_reason") == "end_turn":
                current["ended"] = {"at": record.get("timestamp"), "model": model,
                                    "has_text": any(isinstance(b, dict) and b.get("type") == "text" for b in blocks)}

for turn in turns:
    turn["models"] = dict(turn["models"])
summary = collections.defaultdict(collections.Counter)
for turn in turns:
    if turn["ended"]:
        summary[turn["ended"]["model"]][turn["trigger"]] += 1
with open(out, "w", encoding="utf-8") as output:
    json.dump({"turns": turns, "ended_by_model_and_trigger": {k: dict(v) for k, v in summary.items()}},
              output, indent=1, ensure_ascii=False)
print(json.dumps({k: dict(v) for k, v in summary.items()}, ensure_ascii=False))
