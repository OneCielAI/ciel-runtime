"""Installed-beta real console: /work-mode settings shows the agent-view rows.

Owned, isolated launch: a new empty workspace, a copied ciel config dir (config.json and
model-registry.json only), a free router port, menu and self-update off, every inherited
CIEL_RUNTIME_*/CLAUDE*/ANTHROPIC*/CODEX*/OPENAI* variable stripped and only User-scope values
restored (so CIEL_RUNTIME_HOME is the installed snapshot). No prompt is submitted. Exits with
/exit, kills only its own process tree and deletes the copied config.

usage: py -3 .local/owned_agent_view_probe.py
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import winreg
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / 'docs/journal/2026/11/03/features/work-parallel/evidence/menu-ui'
BASE = REPO / '.local' / ('owned-agent-view-' + time.strftime('%Y%m%d-%H%M%S'))
BASE.mkdir(parents=True)
for name in ('console_driver.py', 'shot_hwnd.ps1'):
    shutil.copy2(TOOLS / name, BASE / name)
sys.path.insert(0, str(BASE))
from console_driver import Console  # noqa: E402

PREFIXES = ('CIEL_RUNTIME_', 'CLAUDE', 'ANTHROPIC', 'CODEX', 'OPENAI')
env = {k: v for k, v in os.environ.items() if not k.upper().startswith(PREFIXES)}
with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
    index = 0
    while True:
        try:
            name, value, _ = winreg.EnumValue(key, index)
        except OSError:
            break
        index += 1
        if name.upper().startswith(PREFIXES):
            env[name] = str(value)
port = next(p for p in range(9611, 9700) if socket.socket().connect_ex(('127.0.0.1', p)) != 0)
scratch = Path(tempfile.mkdtemp(prefix='ciel-owned-agent-view-'))
workspace = scratch / 'workspace'
config = scratch / 'config'
workspace.mkdir()
config.mkdir()
real = Path(os.environ['APPDATA']) / 'ciel-runtime'
for name in ('config.json', 'model-registry.json'):
    shutil.copy2(real / name, config / name)
# The machine-wide default provider has no key here; the Walkie workspace runs on cielairouter.
copied = json.loads((config / 'config.json').read_text(encoding='utf8'))
assert (copied.get('providers', {}).get('cielairouter') or {}).get('api_key'), 'cielairouter key missing'
copied['current_provider'] = 'cielairouter'
(config / 'config.json').write_text(json.dumps(copied, indent=2), encoding='utf8')
env.update(CIEL_RUNTIME_CONFIG_DIR=str(config), CIEL_RUNTIME_ROUTER_PORT=str(port), CIEL_RUNTIME_SKIP_MENU='1',
           CIEL_RUNTIME_SKIP_SELF_UPDATE='1')
launch = BASE / 'launch.cmd'
launch.write_text('@echo off\r\nchcp 65001 >nul\r\ncd /d ' + str(workspace) + '\r\ncall ciel-runtime --version\r\ncall ciel-runtime\r\n'
                  + 'echo [probe] ciel-runtime exited with %ERRORLEVEL%\r\n', encoding='utf-8')
proc = subprocess.Popen(['conhost.exe', 'cmd', '/k', str(launch)], cwd=str(workspace), env=env)


def tree():
    ps = (f'$all=Get-CimInstance Win32_Process; $ids=@({proc.pid}); do {{$new=@($all | Where-Object '
          '{$_.ParentProcessId -in $ids -and $_.ProcessId -notin $ids} | Select-Object -ExpandProperty ProcessId); '
          '$ids += $new} while ($new.Count); $all | Where-Object {$_.ProcessId -in $ids} | '
          'Select-Object ProcessId,ParentProcessId,Name | ConvertTo-Json')
    for attempt in range(5):
        try:
            raw = subprocess.check_output(['powershell', '-NoProfile', '-Command', ps], text=True).strip()
            break
        except subprocess.CalledProcessError:
            if attempt == 4:
                raise
            time.sleep(2)
    rows = json.loads(raw) if raw else []
    return rows if isinstance(rows, list) else [rows]


def capture(console, label):
    text = console.screen()
    (BASE / (label + '.txt')).write_text(text, encoding='utf8')
    subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(BASE / 'shot_hwnd.ps1'),
                    str(int(console.hwnd)), str(BASE / (label + '.png'))],
                   capture_output=True, text=True, creationflags=0x08000000)
    return text


def ctrl_x(console):
    down = console._record(0x58, '\x18', True)
    down.Event.KeyEvent.dwControlKeyState = 0x0008
    up = console._record(0x58, '\x18', False)
    up.Event.KeyEvent.dwControlKeyState = 0x0008
    console._write([down, up])
    time.sleep(.5)


result = {'base': str(BASE), 'port': port, 'outcome': 'timeout'}
try:
    time.sleep(3)
    console = Console(int(next(r['ProcessId'] for r in tree() if r['Name'] == 'cmd.exe')))
    deadline = time.time() + 180
    trusted = False
    while time.time() < deadline:
        time.sleep(1)
        text = console.screen()
        if '[probe] ciel-runtime exited' in text or 'Traceback (most recent call last)' in text:
            capture(console, 'error')
            result['outcome'] = 'error'
            break
        if not trusted and 'trust' in text.lower() and 'Yes' in text:
            capture(console, '00-trust')
            console.key('enter', pause=3)
            trusted = True
            continue
        if '? for shortcuts' in text or 'bypass permissions' in text or 'auto mode' in text:
            time.sleep(4)
            capture(console, '01-started')
            console.text('/work-mode settings')
            console.key('enter', pause=1)
            # The pane renders after the mod's router reads (several hook-wrapped MCP calls).
            end = time.time() + 45
            while time.time() < end and 'All Work settings' not in console.screen():
                time.sleep(1)
            time.sleep(2)
            text = capture(console, '02-work-settings')
            ctrl_x(console)
            time.sleep(1.5)
            capture(console, '03-work-settings-focused')
            rows = [line for line in text.splitlines() if 'agent' in line.lower() or '에이전트' in line]
            result.update(outcome='shown' if any('Left arrow' in r or '왼쪽 화살표' in r for r in rows) else 'rows-missing',
                          agent_lines=[r.strip()[-110:] for r in rows])
            console.key('esc', pause=.5)
            console.text('/exit')
            console.key('enter', pause=3)
            break
    else:
        capture(console, 'timeout')
finally:
    result['tree_before_kill'] = [r['Name'] for r in tree()]
    subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    # The owned router may outlive the console; stop only the listener on this probe's port.
    owner = subprocess.run(['powershell', '-NoProfile', '-Command',
                            f'(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue).OwningProcess'],
                           capture_output=True, text=True).stdout.split()
    result['owned_router_pids'] = owner
    for pid in owner:
        subprocess.run(['taskkill', '/PID', pid, '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    shutil.rmtree(scratch, ignore_errors=True)
    result['scratch_removed'] = not scratch.exists()
    (BASE / 'result.json').write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(result, indent=2, ensure_ascii=False))
