# Read-only: locate any eric-ai home and aap-win agent mapping files on this host (bounded). No writes.
import sys, os, datetime
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from pathlib import Path
for base in (Path(r'C:\Data\Source'), Path(r'C:\ProgramData\aap-win')):
    try:
        for d1 in base.iterdir():
            for d2 in ([d1] + ([x for x in d1.iterdir() if x.is_dir()] if d1.is_dir() else [])):
                h = d2/'home'
                if h.is_dir():
                    print('home', h, [x.name for x in h.iterdir()])
    except Exception as e: print('ERR', base, type(e).__name__, e)
A = Path(r'C:\ProgramData\aap-win')
try:
    for f in sorted(A.iterdir()):
        if f.is_file(): print('aap file', f.name, f.stat().st_size, datetime.datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec='seconds'))
        else: print('aap dir', f.name)
except Exception as e: print('ERR aap', type(e).__name__, e)
print('hostname', os.environ.get('COMPUTERNAME'))
