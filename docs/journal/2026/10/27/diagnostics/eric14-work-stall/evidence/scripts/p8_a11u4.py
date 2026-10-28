# Read-only: full command lines for aapw-a11u4 ciel/claude processes; eric-ai (u23) sandbox recent files. No writes.
import sys, json, subprocess, datetime
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from pathlib import Path
ps = r"""Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'ciel|claude' } | ForEach-Object { $o = Invoke-CimMethod -InputObject $_ -MethodName GetOwner; if ($o.User -eq 'aapw-a11u4') { [pscustomobject]@{pid=$_.ProcessId; ppid=$_.ParentProcessId; name=$_.Name; created=$_.CreationDate.ToString('s'); cmd=$_.CommandLine} } } | ConvertTo-Json -Compress"""
rows = json.loads(subprocess.run(['powershell','-NoProfile','-Command',ps],capture_output=True,text=True,encoding='utf-8',errors='replace').stdout or '[]')
rows = [rows] if isinstance(rows, dict) else rows
for r in rows: print(r['pid'], r['ppid'], r['name'], r['created'], (r.get('cmd') or '')[:330])
E = Path(r'C:\ProgramData\aap-win\sandboxes\u23\home\eric-ai')
for rel in ('.npm-global/node_modules/@one-ciel-ai/ciel-runtime/package.json', '.claude'):
    p = E/rel
    try: print('u23', rel, p.exists(), datetime.datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec='seconds') if p.exists() else '')
    except Exception as e: print('u23 ERR', rel, type(e).__name__, e)
for c in (Path(r'C:\Users\aapw-a11u4\.claude\projects'), Path(r'C:\Users\aapw-a23u23\.claude\projects'), E/'.claude/projects'):
    try:
        fs = sorted(c.glob('*/*.jsonl'), key=lambda f: f.stat().st_mtime, reverse=True)[:3]
        print('projects', c, [(str(f.relative_to(c)), f.stat().st_size, datetime.datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec='seconds')) for f in fs])
    except Exception as e: print('projects ERR', c, type(e).__name__, e)
