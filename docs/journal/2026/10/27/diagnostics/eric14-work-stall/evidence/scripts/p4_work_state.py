# Read-only: newest Work plan for the eric session, router log tail filtered. No writes.
import json, datetime
from pathlib import Path
R = Path(r'C:\Users\aapw-a21u21\AppData\Roaming\ciel-runtime')
plans = sorted(R.rglob('work-sessions/*/*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
for p in plans[:4]:
    print('plan', p, datetime.datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec='seconds'), p.stat().st_size)
doc = json.loads(plans[0].read_text(encoding='utf-8'))
nodes = doc.get('nodes'); nodes = list(nodes.values()) if isinstance(nodes, dict) else nodes
print('doc', {k: doc.get(k) for k in ('schema','version','phase','active_root','updated_at')})
by = {n['id']: n for n in nodes}; kids = {}
for n in nodes: kids.setdefault(n.get('parent') or n.get('parent_id'), []).append(n)
def walk(i, d=0):
    n = by[i]
    if n.get('status') == 'done' and d > 0: return
    extra = {k: n.get(k) for k in ('phase','waiting_on','held','urgent','approval','ref') if n.get(k) not in (None, '', False)}
    print('  '*d + f"[{n.get('status')}] {i} {str(n.get('title'))[:120]} {json.dumps(extra, ensure_ascii=False) if extra else ''}")
    for c in kids.get(i, []): walk(c['id'], d+1)
for r in kids.get(None, []) + kids.get('', []):
    if r.get('status') != 'done': walk(r['id'])
print('root order', [r['id'] for r in kids.get(None, [])])
