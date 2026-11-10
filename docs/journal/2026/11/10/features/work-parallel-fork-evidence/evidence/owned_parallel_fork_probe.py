"""Real console proof: two independent native forks process two Work items at once.

Owned, isolated launch of a snapshot (PROBE_SNAPSHOT) in conhost: new workspace,
copied ciel config (config.json + model-registry.json only, remote/receiver
sections removed), a free router port, every inherited CIEL_RUNTIME_*/CLAUDE*/
ANTHROPIC*/CODEX*/OPENAI* variable stripped. Nothing live is touched.

Proof, read back from four independent sources:
  1. native evidence file (work-parallel-native.json): two distinct agent ids
     recorded pending/running for the two Work leases;
  2. GET /ca/work/api/v1/parallel: forks.confirmed_count == 2 with both items;
  3. marker files written by each fork: their write intervals overlap, and main
     ran an unrelated foreground command inside that overlap;
  4. transcripts: each fork's unique canary is in its own subagent transcript
     and in NO main-transcript row (main context was not contaminated).
Terminal and web screenshots are captured while both forks run. The probe ends
with /exit and kills only its own process tree and router port.
"""
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import winreg
from pathlib import Path

REPO = Path(__file__).resolve().parents[8]
TOOLS = REPO / 'docs/journal/2026/11/03/features/work-parallel/evidence/menu-ui'
SNAP = os.environ['PROBE_SNAPSHOT']
MAIN_MODEL = os.environ.get('PROBE_MAIN_MODEL', '_Walkie_Opus55-Sol61-Fast')
LABEL = os.environ.get('PROBE_LABEL', '')
OUT = Path(__file__).resolve().parent / ('run' + LABEL)
OUT.mkdir(parents=True, exist_ok=True)
BASE = REPO / '.local' / ('owned-parallel-fork-' + time.strftime('%Y%m%d-%H%M%S'))
BASE.mkdir(parents=True)
shutil.copy2(TOOLS / 'console_driver.py', BASE / 'console_driver.py')
shutil.copy2(REPO / '.local' / 'shot_hwnd_restore.ps1', BASE / 'shot_hwnd.ps1')
sys.path.insert(0, str(BASE))
from console_driver import Console  # noqa: E402

SESSION = '66666666-6666-4666-8666-666666666666'
IDENTITY = 'claude:' + SESSION
CANARY = {'a': 'FORKCANARY-A-' + secrets.token_hex(6), 'b': 'FORKCANARY-B-' + secrets.token_hex(6)}
FORK_SECONDS = 70


def free_port(start):
    return next(p for p in range(start, start + 90) if socket.socket().connect_ex(('127.0.0.1', p)) != 0)


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
        if name.upper().startswith(PREFIXES) and name.upper() not in ('CIEL_RUNTIME_HOME',):
            env[name] = str(value)
port = free_port(9631)
scratch = Path(tempfile.mkdtemp(prefix='ciel-owned-parallel-fork-'))
workspace = scratch / 'workspace'
config = scratch / 'config'
workspace.mkdir()
config.mkdir()
markers = workspace / 'markers'
markers.mkdir()
# Forks run in a git worktree of the workspace repo; it must be its own repo
# root (C:/ itself is a git repo on this machine, run 1 refused to dispatch).
subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
(workspace / 'README.md').write_text('owned parallel fork probe\n', encoding='utf8')
(workspace / '.gitignore').write_text('markers/\n', encoding='utf8')
subprocess.run(['git', '-C', str(workspace), 'add', '-A'], check=True)
subprocess.run(['git', '-C', str(workspace), '-c', 'user.name=probe', '-c', 'user.email=probe@localhost',
                'commit', '-q', '-m', 'probe workspace'], check=True)
real = Path(os.environ['APPDATA']) / 'ciel-runtime'
for name in ('config.json', 'model-registry.json'):
    shutil.copy2(real / name, config / name)
copied = json.loads((config / 'config.json').read_text(encoding='utf8'))
assert copied.get('providers', {}).get('cielairouter'), 'configured fixture provider missing'
for section in ('external_event_receivers', 'remote_bridge', 'remote_instructions', 'remote_memory', 'remote_provision',
                'work_parallel'):
    copied.pop(section, None)
copied['providers']['cielairouter']['current_model'] = MAIN_MODEL
copied.update(current_provider='cielairouter', work_mode=True, work_auto_continue=False,
              work_side_intake={'enabled': False})
(config / 'config.json').write_text(json.dumps(copied, indent=2), encoding='utf8')
env.update(CIEL_RUNTIME_HOME_OVERRIDE=SNAP, CIEL_RUNTIME_CONFIG_DIR=str(config), CIEL_RUNTIME_LAUNCH_CWD=str(workspace),
           CIEL_RUNTIME_ROUTER_PORT=str(port), CIEL_RUNTIME_SKIP_MENU='1', CIEL_RUNTIME_SKIP_SELF_UPDATE='1')
launch = BASE / 'launch.cmd'
launch.write_text('@echo off\r\nchcp 65001 >nul\r\ncd /d ' + str(workspace) + '\r\npy -3 "' + SNAP + '\\ciel_runtime.py" version\r\npy -3 "'
                  + SNAP + '\\ciel_runtime.py" cli -- --session-id ' + SESSION + ' --debug-file "' + str(BASE / 'claude-debug.txt') + '"\r\n'
                  + 'echo [probe] ciel-runtime exited with %ERRORLEVEL%\r\n', encoding='utf-8')
proc = subprocess.Popen(['conhost.exe', 'cmd', '/k', str(launch)], cwd=str(workspace), env=env)


def tree():
    ps = (f'$all=Get-CimInstance Win32_Process; $ids=@({proc.pid}); do {{$new=@($all | Where-Object '
          '{$_.ParentProcessId -in $ids -and $_.ProcessId -notin $ids} | Select-Object -ExpandProperty ProcessId); '
          '$ids += $new} while ($new.Count); $all | Where-Object {$_.ProcessId -in $ids} | '
          'Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json')
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
    (OUT / (label + '.txt')).write_text(text, encoding='utf8')
    subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(BASE / 'shot_hwnd.ps1'),
                    str(int(console.hwnd)), str(OUT / (label + '.png'))],
                   capture_output=True, text=True, creationflags=0x08000000)
    return text


def admin_token():
    path = config / 'router-external.token'
    return path.read_text(encoding='ascii').strip() if path.exists() else ''


def get(path):
    headers = {'Accept': 'application/json'}
    token = admin_token()
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = urllib.request.Request(f'http://127.0.0.1:{port}{path}', headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def browser_shot(label, *, mobile=False):
    """Open the real router UI with its admin bearer and capture Parallel."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        return {'ok': False, 'error': repr(error)}
    # Loopback needs no bearer (run 4: the API read succeeded without one);
    # send it only when this owned profile has one.
    token = admin_token()
    chrome = Path.home() / 'AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe'
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=str(chrome))
            page = browser.new_page(viewport={'width': 390 if mobile else 1440, 'height': 844 if mobile else 1000},
                                    extra_http_headers={'Authorization': 'Bearer ' + token} if token else {})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{port}/ca/work/ui', wait_until='domcontentloaded')
            page.get_by_role('tab', name='Parallel').click()
            page.locator("#panel-parallel .parallel-section[data-group='confirmed'] .fork-card").nth(1).wait_for(timeout=15000)
            text = page.locator('#panel-parallel').inner_text()
            page.screenshot(path=str(OUT / (label + '.png')), full_page=True)
            browser.close()
        return {'ok': True, 'confirmed_cards': text.count('confirmed by native agent list'),
                'has_a': 'Fork item A' in text, 'has_b': 'Fork item B' in text,
                'page_errors': errors, 'text': text}
    except Exception as error:
        return {'ok': False, 'error': repr(error)}


def claude_project():
    return Path.home() / '.claude' / 'projects' / re.sub(r'[^A-Za-z0-9]', '-', str(workspace))


def main_text():
    """Every main-transcript row (not sidechain), serialized, for the canary check."""
    path = claude_project() / (SESSION + '.jsonl')
    if not path.exists():
        return ''
    kept = []
    for line in path.read_text(encoding='utf8', errors='replace').splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not row.get('isSidechain'):
            kept.append(line)
    return '\n'.join(kept)


def fork_transcripts():
    rows = []
    for meta in (claude_project() / SESSION / 'subagents').glob('*.meta.json'):
        try:
            info = json.loads(meta.read_text(encoding='utf8'))
        except ValueError:
            continue
        body = meta.with_name(meta.name.replace('.meta.json', '.jsonl'))
        text = body.read_text(encoding='utf8', errors='replace') if body.exists() else ''
        rows.append({'file': body.name, 'type': info.get('agentType'), 'description': str(info.get('description'))[:120],
                     'canary_a': CANARY['a'] in text, 'canary_b': CANARY['b'] in text})
    return rows


def marker_window(name):
    path = markers / (name + '.txt')
    if not path.exists():
        return None
    stamps = []
    for line in path.read_text(encoding='utf8', errors='replace').splitlines():
        try:
            stamps.append(float(line.split()[0]))
        except (ValueError, IndexError):
            continue
    return (min(stamps), max(stamps), len(stamps)) if stamps else None


def native_state():
    path = config / 'work-parallel-native.json'
    return json.loads(path.read_text(encoding='utf8')) if path.exists() else None


def idle(console):
    text = console.screen()
    return '? for shortcuts' in text and 'esc to interrupt' not in text


def item_note(key):
    return ('Run exactly this Python program with the Bash tool in the foreground, then report the result through '
            'work_parallel_report: python -c "import time; [(open(r\'' + str(markers / (key + '.txt')).replace('\\', '/')
            + '\', \'a\').write(str(time.time()) + \' ' + CANARY[key] + '\\n\'), time.sleep(2)) for _ in range('
            + str(FORK_SECONDS // 2) + ')]"')


result = {'out': str(OUT), 'base': str(BASE), 'snapshot': SNAP, 'port': port, 'main_model': MAIN_MODEL,
          'canary': CANARY, 'outcome': 'timeout'}
try:
    time.sleep(12)
    console = Console(int(next(r['ProcessId'] for r in tree() if r['Name'] == 'cmd.exe')))
    deadline = time.time() + 150
    trusted = False
    while time.time() < deadline:
        time.sleep(1)
        text = console.screen()
        if '[probe] ciel-runtime exited' in text or 'Traceback (most recent call last)' in text:
            capture(console, 'error')
            result['outcome'] = 'error'
            break
        if not trusted and 'trust' in text.lower() and 'Yes' in text:
            # In a git repo the cursor starts on "No, exit" (run 2 exited here).
            if '❯ No' in text or '> No' in text:
                console.key('down', pause=1)
            capture(console, '00-trust')
            console.key('enter', pause=3)
            trusted = True
            continue
        if not ('? for shortcuts' in text or 'bypass permissions' in text or 'auto mode' in text):
            continue
        time.sleep(4)
        capture(console, '01-started')
        sys.path.insert(0, SNAP)
        from ciel_runtime_support.workspace_router_selection import workspace_digest, workspace_identity
        from ciel_runtime_support.work_plan_store import session_work_plan_store
        from ciel_runtime_support import work_plan_tree as work_tree
        state_dir = config / 'workspaces' / workspace_digest(workspace_identity(workspace))
        state_dir.mkdir(parents=True, exist_ok=True)
        store = session_work_plan_store(state_dir, IDENTITY)

        def seed(doc):
            work_tree.set_plan(doc, {'title': 'Parallel fork proof', 'priority': 'P2', 'children': [
                {'title': 'Fork item A', 'note': item_note('a')},
                {'title': 'Fork item B', 'note': item_note('b')}]})
            work_tree.set_phase(doc, work_tree.PHASE_EXECUTING)
        store.transaction(seed)
        board = store.read()
        items = {n['title']: n['id'] for n in board['nodes'] if n['title'].startswith('Fork item')}
        result['seed'] = {'version': board['version'], 'items': items}
        capture(console, '02-seeded')

        # Main dispatches both items, then does unrelated foreground work while
        # the forks run. Main is told nothing about the canaries.
        console.text('Call work_parallel_run for Work item ' + items['Fork item A'] + ' and then for Work item '
                     + items['Fork item B'] + ' (priority P2, role worker, expected_version from work_parallel_status). '
                     'Right after both are dispatched, run exactly this with the Bash tool in the foreground: '
                     'python -c "import time; p=r\'' + str(markers / 'main.txt').replace('\\', '/')
                     + '\'; open(p, \'w\').write(str(time.time())); time.sleep(40); open(p, \'a\').write(\' \' + str(time.time())); print(40)" .'
                     ' Then reply only with the word FINISHED.')
        console.key('enter', pause=2)
        main_busy = None
        both_seen = None
        end = time.time() + 240
        shots = 0
        while time.time() < end:
            time.sleep(2)
            busy = any(r['Name'] == 'python.exe' and 'time.sleep(40)' in str(r.get('CommandLine')) for r in tree())
            main_marker = markers / 'main.txt'
            if main_busy is None and main_marker.exists():
                try:
                    main_busy = float(main_marker.read_text(encoding='ascii').split()[0])
                except (ValueError, IndexError):
                    pass
            if busy and main_busy is None:
                main_busy = time.time()
            native = native_state()
            confirmed = [r for r in (native or {}).get('records', {}).values()
                         if r['scope'] == IDENTITY and r['status'] in ('pending', 'running')]
            if len(confirmed) >= 2 and both_seen is None:
                both_seen = time.time()
                capture(console, '03-two-forks-terminal')
                result['native_while_running'] = native
                try:
                    result['api_while_running'] = get('/ca/work/api/v1/parallel?session_id=' + urllib.parse.quote(IDENTITY))
                except Exception as error:  # recorded, not hidden
                    result['api_while_running_error'] = repr(error)
                result['web_while_running'] = browser_shot('05-web-parallel')
                result['web_mobile_while_running'] = browser_shot('05-web-parallel-mobile', mobile=True)
                console.text('/work-mode parallel')
                console.key('enter', pause=3)
                capture(console, '04-work-mode-parallel')
                # No Esc here: in run 4 it interrupted main's foreground Bash.
            if both_seen and main_busy and shots < 1:
                shots += 1
                capture(console, '05-main-busy-forks-running')
            if both_seen and main_busy and time.time() - both_seen > FORK_SECONDS + 30:
                break
        result['main_busy_epoch'] = main_busy
        result['both_confirmed_epoch'] = both_seen
        end = time.time() + 300
        while time.time() < end and not idle(console):
            time.sleep(2)
        time.sleep(3)
        capture(console, '06-final')
        windows = {k: marker_window(k) for k in ('a', 'b')}
        result['marker_windows'] = windows
        # Main's real foreground interval: start and end stamps it wrote itself.
        main_window = None
        try:
            stamps = [float(v) for v in (markers / 'main.txt').read_text(encoding='ascii').split()]
            main_window = (stamps[0], stamps[1]) if len(stamps) >= 2 else (stamps[0], None)
        except (OSError, ValueError, IndexError):
            pass
        result['main_window'] = main_window
        if windows['a'] and windows['b']:
            overlap = (max(windows['a'][0], windows['b'][0]), min(windows['a'][1], windows['b'][1]))
            result['fork_overlap_seconds'] = round(max(0, overlap[1] - overlap[0]), 1)
            if main_window and main_window[1] is not None:
                shared = min(overlap[1], main_window[1]) - max(overlap[0], main_window[0])
                result['main_overlap_with_both_forks_seconds'] = round(max(0, shared), 1)
                result['main_busy_inside_overlap'] = shared > 0
            else:
                result['main_busy_inside_overlap'] = False
                result['main_overlap_with_both_forks_seconds'] = None
        main = main_text()
        # Main-visible status and dispatch results must not contain any fork-only
        # assignment canary; the assignment body goes host -> fork directly.
        result['main_contains_canary'] = {k: v in main for k, v in CANARY.items()}
        result['fork_transcripts'] = fork_transcripts()
        primary = {}
        confirmed_rows = ((result.get('api_while_running') or {}).get('forks') or {}).get('confirmed') or []
        for row in confirmed_rows:
            agent_id = str(row.get('agent_id') or '')
            transcript = next((item for item in result['fork_transcripts']
                               if item['file'] == 'agent-' + agent_id + '.jsonl'), None)
            if transcript is not None:
                primary[row['item_id']] = transcript
        result['primary_fork_transcripts'] = primary
        result['primary_forks_canary_isolated'] = (
            primary.get(items['Fork item A'], {}).get('canary_a') is True
            and primary.get(items['Fork item A'], {}).get('canary_b') is False
            and primary.get(items['Fork item B'], {}).get('canary_a') is False
            and primary.get(items['Fork item B'], {}).get('canary_b') is True)
        result['native_final'] = native_state()
        try:
            result['api_final'] = get('/ca/work/api/v1/parallel?session_id=' + urllib.parse.quote(IDENTITY))
        except Exception as error:
            result['api_final_error'] = repr(error)
        result['outcome'] = 'completed'
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
    owner = subprocess.run(['powershell', '-NoProfile', '-Command',
                            f'(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue).OwningProcess'],
                           capture_output=True, text=True).stdout.split()
    result['owned_router_pids'] = owner
    for pid in owner:
        subprocess.run(['taskkill', '/PID', pid, '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    result['preserved_config'] = str(config)
    (OUT / 'result.json').write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({k: result.get(k) for k in ('out', 'outcome', 'both_confirmed_epoch', 'main_busy_epoch',
                                                  'main_window', 'fork_overlap_seconds', 'main_busy_inside_overlap',
                                                  'main_overlap_with_both_forks_seconds',
                                                  'main_contains_canary', 'primary_forks_canary_isolated')}, indent=2))
    for key in ('web_while_running', 'web_mobile_while_running'):
        web = result.get(key) or {}
        print(key, json.dumps({k: web.get(k) for k in ('ok', 'error', 'confirmed_cards', 'has_a', 'has_b',
                                                        'page_errors')}))
