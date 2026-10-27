# Read-only: raw context around selection refusals and Stop continuations in Kobe's transcript tail. No writes.
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from pathlib import Path
F = sorted(Path(r'C:\Users\aapw-a24u24\.claude\projects').glob('*/*.jsonl'), key=lambda f: f.stat().st_mtime, reverse=True)[0]
size = F.stat().st_size
with F.open('rb') as h:
    h.seek(max(0, size - 12_000_000)); data = h.read().decode('utf-8', 'replace')
import re
for m in re.finditer(r'requires an explicit user request', data):
    s = data[max(0, m.start()-500): m.end()+200]
    ts = re.findall(r'"timestamp":"([^"]+)"', data[max(0, m.start()-4000): m.start()])
    print('=== at', ts[-1] if ts else '?'); print(s.replace('\n', ' ')[:700])
idx = [m.start() for m in re.finditer(r'Ciel Work has \d+ actionable', data)]
print('continuations', len(idx))
for i in idx[-4:]:
    ts = re.findall(r'"timestamp":"([^"]+)"', data[max(0, i-4000): i])
    print('--- cont at', ts[-1] if ts else '?', data[i:i+520].replace('\n', ' '))
