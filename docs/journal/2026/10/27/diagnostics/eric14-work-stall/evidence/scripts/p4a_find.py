# Read-only: locate work-sessions under the eric Roaming ciel-runtime tree (bounded). No writes.
from pathlib import Path
import datetime
R = Path(r'C:\Users\aapw-a21u21\AppData\Roaming\ciel-runtime')
print('top', sorted(p.name for p in R.iterdir()))
for ws in sorted((R/'workspaces').iterdir()) if (R/'workspaces').is_dir() else []:
    w = ws/'work-sessions'
    if w.is_dir():
        files = sorted(w.glob('*/work-plan.json'), key=lambda f: f.stat().st_mtime, reverse=True)[:3]
        for f in files: print('plan', f, f.stat().st_size, datetime.datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec='seconds'))
w = R/'work-sessions'
if w.is_dir():
    for f in sorted(w.glob('*/work-plan.json'), key=lambda f: f.stat().st_mtime, reverse=True)[:3]:
        print('plan', f, f.stat().st_size, datetime.datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec='seconds'))
