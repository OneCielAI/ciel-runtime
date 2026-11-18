"""Tally synthetic API-error messages in a session's fork transcripts (read-only).

usage: python -I side_api_errors.py <session subagents folder>
Counts (UTC day, error kind, first 110 chars of the error text). No prompts or replies are printed.
"""
import collections, json, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
tally = collections.Counter()
for path in Path(sys.argv[1]).glob("*.jsonl"):
    with path.open("rb") as stream:
        for raw in stream:
            if b'"isApiErrorMessage":true' not in raw:
                continue
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            text = " ".join(b.get("text", "") for b in row["message"].get("content", []) if isinstance(b, dict))[:110]
            tally[(str(row.get("timestamp", ""))[:10], row.get("error"), text)] += 1
for key, value in sorted(tally.items()):
    print(value, key)
