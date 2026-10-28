# Read-only: which .14 session's newest transcript tail mentions the screenshot's active goal. No writes.
import sys, datetime
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from pathlib import Path
users = ['aapw-a11u4', 'aapw-a20u19', 'aapw-a21u21', 'aapw-a22u22', 'aapw-a23u23', 'aapw-a24u24']
needles = ['88742 P2', 'inv-processing-photos-88742', 'Photo storage']
for u in users:
    base = Path(rf'C:\Users\{u}\.claude\projects')
    try: files = sorted(base.glob('*/*.jsonl'), key=lambda f: f.stat().st_mtime, reverse=True)[:2]
    except Exception as e: print(u, 'ERR', type(e).__name__); continue
    for f in files:
        size = f.stat().st_size
        with f.open('rb') as h:
            h.seek(max(0, size - 4_000_000)); tail = h.read().decode('utf-8', 'replace')
        hits = {n: tail.count(n) for n in needles}
        print(u, f.name, size, datetime.datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec='seconds'), hits)
