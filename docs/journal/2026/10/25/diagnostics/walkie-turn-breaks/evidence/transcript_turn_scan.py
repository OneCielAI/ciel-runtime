"""Read-only scan of a Claude Code transcript region: one compact row per record.

Usage: python transcript_turn_scan.py <transcript.jsonl> <start-offset> <out.tsv>

Starts at the first full line after <start-offset>. Text is cut to short snippets
so the output shows turn structure, not conversation content.
"""
import json
import sys

path, start, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]


def snippet(text, limit=140):
    return " ".join(str(text).split())[:limit]


rows = []
with open(path, "rb") as handle:
    handle.seek(start)
    if start:
        handle.readline()
    while True:
        offset = handle.tell()
        line = handle.readline()
        if not line:
            break
        try:
            record = json.loads(line)
        except ValueError:
            rows.append([str(offset), str(len(line)), "", "unparsed", "", "", "", "", ""])
            continue
        message = record.get("message") if isinstance(record.get("message"), dict) else {}
        content = message.get("content")
        blocks = content if isinstance(content, list) else ([{"type": "text", "text": content}] if content else [])
        kinds = ",".join(str(b.get("type")) for b in blocks if isinstance(b, dict))
        note = ""
        if record.get("type") == "assistant":
            texts = [b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
            tools = [b.get("name", "") for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use"]
            note = ("tools=" + ",".join(tools) + " " if tools else "") + (snippet(texts[-1]) if texts else "")
        elif record.get("type") == "user":
            results = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
            if results:
                note = "tool_result" + (" is_error" if any(b.get("is_error") for b in results) else "")
            else:
                texts = [b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
                note = "human " + snippet(texts[0] if texts else "", 80)
        elif record.get("type") == "system":
            note = snippet(record.get("content") or "", 120)
        flags = []
        for key in ("isApiErrorMessage", "isMeta", "isCompactSummary", "isSidechain"):
            if record.get(key):
                flags.append(key)
        if record.get("error"):
            flags.append("error=" + snippet(record.get("error"), 60))
        rows.append([
            str(offset), str(len(line)), str(record.get("timestamp") or ""),
            str(record.get("type") or ""), str(record.get("subtype") or ""),
            str(message.get("stop_reason") or ""), kinds, "|".join(flags), note,
        ])

with open(out, "w", encoding="utf-8") as output:
    output.write("offset\tbytes\ttimestamp\ttype\tsubtype\tstop_reason\tblocks\tflags\tnote\n")
    for row in rows:
        output.write("\t".join(value.replace("\t", " ") for value in row) + "\n")
print(len(rows), "records")
