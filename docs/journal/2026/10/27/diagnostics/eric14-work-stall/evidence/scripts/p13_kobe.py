# Read-only: Kobe (u24) runtime version and transcript evidence of Stop continuations and queue selection. No writes.
import sys, json, re
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from pathlib import Path
PKG = Path(r'C:\ProgramData\aap-win\sandboxes\u24\home\kobe-ai\.npm-global\node_modules\@one-ciel-ai\ciel-runtime')
print('kobe version', re.findall(r'^VERSION = .*$', (PKG/'ciel_runtime_support/runtime_constants.py').read_text(encoding='utf-8'), re.M))
F = sorted(Path(r'C:\Users\aapw-a24u24\.claude\projects').glob('*/*.jsonl'), key=lambda f: f.stat().st_mtime, reverse=True)[0]
size = F.stat().st_size
with F.open('rb') as h:
    h.seek(max(0, size - 12_000_000)); data = h.read().decode('utf-8', 'replace')
print('file', F.name, size)
for n in ('90981', '83576', 'select it only under the user-priority rule', 'requires an explicit user request', 'Ciel Work has', 'none can run now', 'auto continue paused', 'work_select', 'work_reorder'):
    print('count', repr(n), data.count(n))
rows = []
for l in data.split('\n')[1:]:
    try: rows.append(json.loads(l))
    except Exception: pass
def txt(c):
    if isinstance(c, str): return c
    if isinstance(c, list): return ' '.join(txt(x.get('text') or x.get('content') or '') if isinstance(x, dict) else str(x) for x in c)
    return ''
shown = 0
for r in rows:
    msg = r.get('message') or {}
    c = msg.get('content') if isinstance(msg, dict) else r.get('content')
    s = txt(c) if c is not None else txt(r.get('content'))
    if any(k in s for k in ('select it only under the user-priority rule', 'requires an explicit user request', 'none can run now', 'auto continue paused')):
        print('---', r.get('timestamp'), r.get('type'), r.get('subtype') or '', s.replace('\n', ' ')[:700])
        shown += 1
    if r.get('type') == 'assistant' and isinstance(c, list):
        for x in c:
            if x.get('type') == 'tool_use' and x.get('name', '').endswith(('work_select', 'work_reorder')):
                print('+++', r.get('timestamp'), x.get('name'), json.dumps(x.get('input'), ensure_ascii=False)[:300])
print('shown', shown)
