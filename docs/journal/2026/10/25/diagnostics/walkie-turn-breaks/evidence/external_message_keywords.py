"""Read-only: for each injected external channel or teammate message in a transcript
region, list which reminder keywords it contains (no message text is copied).

Usage: python external_message_keywords.py <transcript.jsonl> <start-offset> <out.txt>
"""
import json
import sys

path, start, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
KEYWORDS = ["turn_ended", "follow_up_due", "priority_idle", "stop_eta_passed", "idle_notification",
            "delivery_stalled", "delivery_recovered", "mention", "message.created"]
rows = []
with open(path, "rb") as handle:
    handle.seek(start)
    if start:
        handle.readline()
    for line in handle:
        if b"external channel" not in line and b"teammate-message" not in line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get("type") != "user":
            continue
        content = (record.get("message") or {}).get("content")
        text = content if isinstance(content, str) else " ".join(
            b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")
        if "external channel" not in text and "teammate-message" not in text:
            continue
        rows.append(f"{record.get('timestamp', '')[:19]}\t{','.join(k for k in KEYWORDS if k in text)}")
with open(out, "w", encoding="utf-8") as output:
    output.write("timestamp\tkeywords\n" + "\n".join(rows) + "\n")
print(len(rows), "messages;", sum("turn_ended" in r for r in rows), "contain turn_ended")
