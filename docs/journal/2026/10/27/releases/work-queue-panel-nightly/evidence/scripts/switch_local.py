"""Switch the local install to a verified snapshot: User CIEL_RUNTIME_HOME and the generated statusline.

Usage: switch_local.py <snapshot_dir> <short_sha> <out_json>
The statusline is replaced atomically with the snapshot's own template (backup kept) only when it differs.
Live routers and sessions are not touched; they keep the code they were launched with.
"""
import datetime, hashlib, json, os, subprocess, sys, winreg
from pathlib import Path

snap, short, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
assert (snap / 'ciel_runtime.py').is_file(), snap
sha = lambda b: hashlib.sha256(b).hexdigest()
with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as k:
    previous_home = winreg.QueryValueEx(k, 'CIEL_RUNTIME_HOME')[0]
sys.path.insert(0, str(snap))
from ciel_runtime_support.statusline_script import STATUSLINE_SCRIPT  # noqa: E402
line = Path(os.environ['LOCALAPPDATA']) / 'ciel-runtime/bin/ciel-runtime-statusline.py'
before = line.read_bytes()
record = {'at': datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
          'ciel_runtime_home_previous': previous_home, 'statusline_previous_sha256': sha(before)}
if line.read_bytes() != STATUSLINE_SCRIPT.encode('utf-8'):
    backup = line.with_name(line.name + f'.before-{short}-20261027')
    if backup.exists():  # an interrupted earlier attempt already kept the original
        before = backup.read_bytes(); record['statusline_previous_sha256'] = sha(before)
    else:
        backup.write_bytes(before)
    tmp = line.with_name(line.name + '.tmp-switch')
    tmp.write_bytes(STATUSLINE_SCRIPT.encode('utf-8'))  # exact template bytes (LF), as the previous switch left it
    os.replace(tmp, line)
    record['statusline_backup'] = str(backup)
after = line.read_bytes()
record['statusline_new_sha256'] = sha(after)
record['statusline_matches_snapshot_template'] = after == STATUSLINE_SCRIPT.encode('utf-8')
assert "'" not in str(snap), snap
ps = f"[Environment]::SetEnvironmentVariable('CIEL_RUNTIME_HOME', '{snap}', 'User'); [Environment]::GetEnvironmentVariable('CIEL_RUNTIME_HOME', 'User')"
now = subprocess.run(['powershell', '-NoProfile', '-Command', ps], capture_output=True, text=True, check=True).stdout.strip()
record['ciel_runtime_home_now'] = now
assert now == str(snap), now
out.write_text(json.dumps(record, indent=2), encoding='utf-8')
print(json.dumps(record, indent=2))
