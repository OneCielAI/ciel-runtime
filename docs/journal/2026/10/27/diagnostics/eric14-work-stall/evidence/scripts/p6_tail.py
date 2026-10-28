# Read-only: summarize the last transcript entries (types, tool names, stop reasons, hook/system text). No writes.
import sys; sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import json, os
from pathlib import Path
F = Path(r'C:\Users\aapw-a21u21\.claude\projects\C--ProgramData-aap-win-sandboxes-u21-home-ara-ai\ad4b04e9-84bb-496f-b441-9e79b55530cb.jsonl')
size = F.stat().st_size
with F.open('rb') as f:
    f.seek(max(0, size - 6_000_000)); data = f.read()
lines = data.split(b'\n')[1:]
rows = []
for l in lines:
    try: rows.append(json.loads(l))
    except Exception: pass
print('entries in tail', len(rows))
def short(x, n=220):
    s = x if isinstance(x, str) else json.dumps(x, ensure_ascii=False)
    return s.replace('\n', ' ')[:n]
for r in rows[-60:]:
    t = r.get('type'); ts = r.get('timestamp', '')[11:19]; sub = r.get('subtype') or ''
    msg = r.get('message') or {}
    content = msg.get('content') if isinstance(msg, dict) else None
    desc = ''
    if isinstance(content, list):
        parts = []
        for c in content:
            ct = c.get('type')
            if ct == 'tool_use': parts.append('tool_use:' + c.get('name', '') + ' ' + short(c.get('input', {}), 120))
            elif ct == 'tool_result':
                parts.append('tool_result:' + short(c.get('content'), 160))
            elif ct == 'text': parts.append('text:' + short(c.get('text', ''), 160))
            else: parts.append(ct)
        desc = ' | '.join(parts)
    elif isinstance(content, str): desc = 'str:' + short(content, 200)
    extra = {k: short(r.get(k), 200) for k in ('content', 'level', 'hookEvent', 'stopReason', 'isMeta') if r.get(k) not in (None, '', False)}
    sr = msg.get('stop_reason') if isinstance(msg, dict) else None
    print(ts, t, sub, ('stop=' + str(sr)) if sr else '', desc[:400], json.dumps(extra, ensure_ascii=False)[:300] if extra else '')
# Latest Work board text returned by any work_* tool result
last = None
for r in rows:
    msg = r.get('message') or {}
    c = msg.get('content') if isinstance(msg, dict) else None
    if isinstance(c, list):
        for x in c:
            if x.get('type') == 'tool_result':
                txt = x.get('content')
                txt = ' '.join(i.get('text', '') for i in txt if isinstance(i, dict)) if isinstance(txt, list) else str(txt)
                if 'Work phase:' in txt: last = (r.get('timestamp'), txt)
if last:
    print('=== latest board at', last[0]); print(last[1][:9000])
