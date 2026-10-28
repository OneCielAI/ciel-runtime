# Read-only: find the newest Claude transcript for the eric sandbox; list sizes and mtimes. No writes.
from pathlib import Path
import datetime
cands = [Path(r'C:\ProgramData\aap-win\sandboxes\u21\home\ara-ai\.claude\projects'), Path(r'C:\Users\aapw-a21u21\.claude\projects')]
for c in cands:
    try:
        files = sorted(c.glob('*/*.jsonl'), key=lambda f: f.stat().st_mtime, reverse=True)[:5]
        print('dir', c, 'ok')
        for f in files: print('  ', f, f.stat().st_size, datetime.datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec='seconds'))
    except Exception as e: print('dir', c, 'ERR', type(e).__name__, e)
R = Path(r'C:\Users\aapw-a21u21\AppData\Roaming\ciel-runtime\router-instances\9179-fe924fced90d')
try:
    print('instance files', sorted((f.name, f.stat().st_size, datetime.datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec='seconds')) for f in R.iterdir()))
except Exception as e: print('instance ERR', type(e).__name__, e)
