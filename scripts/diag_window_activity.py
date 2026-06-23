#!/usr/bin/env python3
"""One-shot diagnostic: list all opus/deepseek/limit events in a UTC window
across every Claude Code session log under ~/.claude/projects. Read-only.
"""
import json
import pathlib
from collections import Counter, defaultdict

WINDOW_START = "2026-05-22T16:30:00"
WINDOW_END   = "2026-05-22T21:30:00"

projects_dir = pathlib.Path.home() / ".claude" / "projects"
files = sorted(projects_dir.glob("**/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)

per_session = defaultdict(lambda: Counter())
per_session_first_last = defaultdict(lambda: [None, None])
per_session_proj = {}
limit_events = []

for f in files:
    try:
        st = f.stat()
        if st.st_mtime < 1779400000:
            continue
    except Exception:
        continue
    sid = f.stem
    proj = f.parent.name
    per_session_proj[sid] = proj
    try:
        fh = f.open()
    except Exception:
        continue
    try:
        for line in fh:
            if ("claude-opus" not in line
                    and "deepseek" not in line
                    and "hit your" not in line
                    and "temporarily limiting" not in line
                    and "kimi" not in line):
                continue
            try:
                ev = json.loads(line.strip())
            except Exception:
                continue
            ts = ev.get("timestamp", "")
            if not ts or ts < WINDOW_START or ts > WINDOW_END:
                continue
            msg = ev.get("message", {}) if isinstance(ev, dict) else {}
            m = msg.get("model", "") if isinstance(msg, dict) else ""
            per_session[sid][m] += 1
            fl = per_session_first_last[sid]
            if fl[0] is None or ts < fl[0]:
                fl[0] = ts
            if fl[1] is None or ts > fl[1]:
                fl[1] = ts
            if isinstance(msg, dict):
                c = msg.get("content")
                if isinstance(c, list):
                    for part in c:
                        if isinstance(part, dict) and part.get("type") == "text":
                            t = part.get("text", "")
                            if "hit your" in t or "temporarily limiting" in t:
                                limit_events.append((ts, sid, proj, t[:160]))
    finally:
        fh.close()

print("=== Activity in 2026-05-22 16:30-21:30 UTC, per session ===")
print("(only sessions with opus/deepseek/kimi/limit events in window)")
for sid, c in sorted(per_session.items(), key=lambda x: per_session_first_last[x[0]][0] or ""):
    fl = per_session_first_last[sid]
    proj = per_session_proj.get(sid, "?")
    models = dict(c)
    print(f"  sid={sid[:8]}  proj={proj}")
    print(f"    models={models}")
    print(f"    span={fl[0]} -> {fl[1]}")

print()
print("=== Limit-text events in window ===")
for ts, sid, proj, txt in sorted(limit_events):
    print(f"  {ts}  sid={sid[:8]}  proj={proj}")
    print(f"    text={txt!r}")
