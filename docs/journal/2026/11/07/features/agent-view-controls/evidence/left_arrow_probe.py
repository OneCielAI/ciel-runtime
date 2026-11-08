"""Real-console check of Claude Code's empty-prompt left arrow against leftArrowOpensAgents.

Launches plain `claude` (no ciel-runtime) in a conhost window with every inherited
CIEL_RUNTIME_*/CLAUDE*/ANTHROPIC*/CODEX*/OPENAI* variable stripped and only the
User-scope values restored, then flips the setting through
ciel_runtime_support.claude_user_settings while the session runs:
  launch with it on: left once, left twice, Esc, flip off live, left twice;
  launch with it off: left twice.
No prompt is ever submitted. Exits with /exit and kills only its own process tree.
The setting is left off.

usage: py -3 .local/left_arrow_probe.py [ab|a|b]
"""
import json
import os
import shutil
import subprocess
import sys
import time
import winreg
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from ciel_runtime_support import claude_user_settings as cus  # noqa: E402

TOOLS = REPO / 'docs/journal/2026/11/03/features/work-parallel/evidence/menu-ui'
BASE = REPO / '.local' / ('left-arrow-' + time.strftime('%Y%m%d-%H%M%S'))
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
(BASE / 'env-keys.json').write_text(json.dumps(sorted(k for k in env if k.upper().startswith(PREFIXES))),
                                    encoding='utf-8')
# The binary ciel-runtime sessions run (not the npm `claude` that may come first on PATH).
CLAUDE = Path.home() / '.local/bin/claude.exe'
launch = BASE / 'launch.cmd'
launch.write_text('@echo off\r\nchcp 65001 >nul\r\ncd /d ' + str(REPO) + '\r\n"' + str(CLAUDE) + '"\r\n'
                  + 'echo [probe] claude exited with %ERRORLEVEL%\r\n', encoding='utf-8')
proc = None


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


def wait_screen(console, predicate, seconds):
    end = time.time() + seconds
    while time.time() < end:
        text = console.screen()
        if predicate(text):
            return text, time.time()
        time.sleep(0.5)
    return console.screen(), None


def footer_hint(text):
    return 'for agents' in text


def session(label, initial, plan):
    """One fresh Claude launch; plan(console) returns its step records."""
    global proc
    cus.set_value(cus.LEFT_ARROW, initial)
    proc = subprocess.Popen(['conhost.exe', 'cmd', '/k', str(launch)], cwd=str(REPO), env=env)
    try:
        time.sleep(3)
        cmd_pid = next(r['ProcessId'] for r in tree() if r['Name'] == 'cmd.exe')
        console = Console(int(cmd_pid))
        text, ready = wait_screen(console, lambda t: 'trust' in t.lower() or '? for shortcuts' in t or '❯' in t, 90)
        if ready is None:
            capture(console, label + '-timeout')
            return [{'step': 'start', 'ready': False}]
        time.sleep(5)
        steps = plan(console)
        console.key('esc', pause=0.5)
        console.text('/exit')
        console.key('enter', pause=3)
        return steps
    finally:
        subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def confirm(text):
    return 'again to open agents' in text


def plan_on(console):
    steps = []
    t = capture(console, 'a1-on-start')
    steps.append({'step': 'on-start', 'version_line': next((x.strip() for x in t.splitlines() if 'Claude Code v' in x), '')})
    console.key('left', pause=1.5)
    t = capture(console, 'a2-on-left-once')
    steps.append({'step': 'on-left-once', 'confirm_hint': confirm(t)})
    console.key('left', pause=3)
    t = capture(console, 'a3-on-left-twice')
    steps.append({'step': 'on-left-twice', 'prompt_still_shown': 'Try "' in t, 'confirm_hint': confirm(t)})
    console.key('esc', pause=2)
    t = capture(console, 'a4-after-esc')
    steps.append({'step': 'after-esc', 'prompt_shown': 'Try "' in t})
    flipped = time.time()
    cus.set_value(cus.LEFT_ARROW, 'off')
    time.sleep(15)
    console.key('left', pause=1.5)
    t = capture(console, 'a5-live-off-left-once')
    steps.append({'step': 'live-off-left-once', 'seconds_after_flip': round(time.time() - flipped, 1), 'confirm_hint': confirm(t)})
    console.key('left', pause=3)
    t = capture(console, 'a6-live-off-left-twice')
    steps.append({'step': 'live-off-left-twice', 'prompt_still_shown': 'Try "' in t, 'confirm_hint': confirm(t)})
    return steps


def plan_off(console):
    steps = []
    capture(console, 'b1-off-start')
    console.key('left', pause=1.5)
    t = capture(console, 'b2-off-left-once')
    steps.append({'step': 'off-left-once', 'confirm_hint': confirm(t)})
    console.key('left', pause=3)
    t = capture(console, 'b3-off-left-twice')
    steps.append({'step': 'off-left-twice', 'prompt_still_shown': 'Try "' in t, 'confirm_hint': confirm(t)})
    return steps


result = {'base': str(BASE), 'claude': str(CLAUDE)}
try:
    phases = sys.argv[1] if len(sys.argv) > 1 else 'ab'
    if 'a' in phases:
        result['launch_on'] = session('a', 'on', plan_on)
    if 'b' in phases:
        result['launch_off'] = session('b', 'off', plan_off)
finally:
    cus.set_value(cus.LEFT_ARROW, 'off')
    result['final_setting'] = cus.values()[cus.LEFT_ARROW]
    (BASE / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
