# Read-only: map sandboxes to home names, owners and running claude/router processes. No writes.
import sys, json, subprocess
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from pathlib import Path
S = Path(r'C:\ProgramData\aap-win\sandboxes')
for u in sorted(S.iterdir()):
    homes = [h.name for h in (u/'home').iterdir()] if (u/'home').is_dir() else []
    print('sandbox', u.name, homes)
ps = r"""Get-CimInstance Win32_Process -Filter "Name='claude.exe' or Name='python.exe'" | ForEach-Object { $o = Invoke-CimMethod -InputObject $_ -MethodName GetOwner; [pscustomobject]@{pid=$_.ProcessId; name=$_.Name; user=$o.User; created=$_.CreationDate.ToString('s'); cmd=$_.CommandLine} } | ConvertTo-Json -Compress"""
rows = json.loads(subprocess.run(['powershell','-NoProfile','-Command',ps],capture_output=True,text=True,encoding='utf-8',errors='replace').stdout or '[]')
rows = [rows] if isinstance(rows, dict) else rows
for r in rows:
    c = r.get('cmd') or ''
    if 'claude.exe' in c or ' serve' in c:
        bs = chr(92); home = c.split('sandboxes' + bs)[1].split(bs + '.npm')[0] if ('sandboxes' + bs) in c else '?'
        print(r['pid'], r['name'], r['user'], r['created'], home, ('serve' if ' serve' in c else c[c.find('--model'):c.find('--model')+70]))
