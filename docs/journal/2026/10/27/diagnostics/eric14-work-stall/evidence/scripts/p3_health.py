# Read-only: router listening port for pid 20300, GET /health, package file mtimes. No writes.
import json, subprocess, urllib.request, datetime
from pathlib import Path
ps = "Get-NetTCPConnection -State Listen -OwningProcess 20300 | Select-Object LocalAddress,LocalPort | ConvertTo-Json -Compress"
out = subprocess.run(['powershell','-NoProfile','-Command',ps],capture_output=True,text=True).stdout
print('listen', out.strip())
rows = json.loads(out or '[]'); rows = [rows] if isinstance(rows, dict) else rows
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for r in rows:
    try:
        with op.open(f"http://127.0.0.1:{r['LocalPort']}/health", timeout=5) as h:
            d = json.load(h)
            print('health', r['LocalPort'], json.dumps({k: d.get(k) for k in ('version','source_fingerprint','pid','config_dir','workspace','router_port','instance_id','active_client_count','active_client_pids')}, ensure_ascii=False))
    except Exception as e: print('health ERR', r['LocalPort'], type(e).__name__, e)
PKG = Path(r'C:\ProgramData\aap-win\sandboxes\u21\home\ara-ai\.npm-global\node_modules\@one-ciel-ai\ciel-runtime')
for rel in ('package.json','ciel_runtime_support/claude_mods/ciel-work/hooks/register.js','ciel_runtime.py'):
    p = PKG/rel; st = p.stat()
    print('mtime', rel, datetime.datetime.fromtimestamp(st.st_mtime).isoformat(timespec='seconds'), 'ctime', datetime.datetime.fromtimestamp(st.st_ctime).isoformat(timespec='seconds'))
print('now', datetime.datetime.now().isoformat(timespec='seconds'))
