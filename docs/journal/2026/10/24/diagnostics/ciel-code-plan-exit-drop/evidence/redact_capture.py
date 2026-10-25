"""Redact a captured Claude Code request (messages) for the journal.

python redact_capture.py <captured-main-messages.json>   (rewrites the file in place)

Claude Code adds local context to every request (CLAUDE.md files found from the
workspace upward, git status, environment, skill list). Only the structure and
Claude Code's own plan-mode reminder are evidence here. Each text block keeps
its leading "<system-reminder>" tag (when present) and up to 700 characters from
each plan-mode reminder line; everything else becomes "[redacted: N chars]".
"""
import json
import re
import sys
from pathlib import Path

MARKER = re.compile(r"^(?:Plan mode is active\.|Plan mode still active|## Re-entering Plan Mode|## Exited Plan Mode)",
                    re.MULTILINE)


def redact(text: str) -> str:
    prefix = "<system-reminder>\n" if text.startswith("<system-reminder>") else ""
    kept, cursor = [], len(prefix)
    for match in MARKER.finditer(text):
        if match.start() > cursor:
            kept.append(f"[redacted: {match.start() - cursor} chars]\n")
        end = min(len(text), match.start() + 700)
        kept.append(text[match.start():end])
        cursor = end
    if cursor < len(text):
        kept.append(f"\n[redacted: {len(text) - cursor} chars]")
    return prefix + "".join(kept)


path = Path(sys.argv[1])
messages = json.loads(path.read_text(encoding="utf-8"))
for message in messages:
    content = message.get("content")
    if isinstance(content, str):
        message["content"] = redact(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                block["text"] = redact(block["text"])
path.write_text(json.dumps(messages, indent=2, ensure_ascii=False), encoding="utf-8")
