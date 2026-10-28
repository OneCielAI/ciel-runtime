# Read-only: list ciel/claude/node processes with owner and command line; no writes.
import json, subprocess
ps = r"""Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'ciel|claude|codex' } | ForEach-Object { $o = Invoke-CimMethod -InputObject $_ -MethodName GetOwner; [pscustomobject]@{pid=$_.ProcessId; ppid=$_.ParentProcessId; name=$_.Name; user=$o.User; created=$_.CreationDate.ToString('s'); cmd=$_.CommandLine} } | ConvertTo-Json -Depth 3 -Compress"""
out = subprocess.run(['powershell','-NoProfile','-Command',ps],capture_output=True,text=True,encoding='utf-8',errors='replace').stdout
rows = json.loads(out or '[]')
if isinstance(rows, dict): rows=[rows]
for r in rows:
    cmd=(r.get('cmd') or '')
    if 'eric' in cmd.lower() or (r.get('user') or '') == 'aapw-a21u21':
        print(json.dumps({k:(v[:400] if isinstance(v,str) else v) for k,v in r.items()}, ensure_ascii=False))
print('users:', sorted({(r.get('user') or '?') for r in rows}))
