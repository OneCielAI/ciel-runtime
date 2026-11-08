"""Real console: an SSE side fork appears in the graphical Work & SSE Observatory.

Owned, isolated launch of the installed snapshot (PROBE_SNAPSHOT): new empty workspace, copied
ciel config (config.json and model-registry.json only, remote/receiver sections removed), a free
router port, every inherited CIEL_RUNTIME_*/CLAUDE*/ANTHROPIC*/CODEX*/OPENAI* variable stripped.
A local SSE source registered as the owned router's receiver delivers one CloudEvent through the
production receiver -> side inbox -> mod side fork path. No prompt is submitted to the main session.
The probe exits with /exit, kills only its own process tree and router, and deletes the copied config.

usage: py -3 .local/owned_sse_fork_probe.py
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import winreg
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent / ('owned-sse-fork-' + time.strftime('%Y%m%d-%H%M%S'))
BASE.mkdir(parents=True)
SNAP = os.environ['PROBE_SNAPSHOT']
sys.path.insert(0, str(Path(__file__).resolve().parent))
from console_driver import Console  # noqa: E402

SESSION = '33333333-3333-4333-8333-333333333333'
EVENT = json.dumps({'specversion': '1.0', 'id': 'obs-fork-1', 'source': 'urn:owned:observatory',
                    'type': 'message.created', 'time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                    'data': {'priority': 1, 'text': 'Side question: how many Work items are running now? '
                             'Answer in one short sentence.'}})
fire = threading.Event()
served = threading.Event()
stop = threading.Event()


class Source(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if not self.path.startswith('/events'):
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        try:
            self.wfile.write(b': connected\n\n')
            self.wfile.flush()
            while not stop.is_set():
                if fire.wait(5) and not served.is_set():
                    self.wfile.write(('id: obs-fork-1\nevent: message\ndata: ' + EVENT + '\n\n').encode())
                    self.wfile.flush()
                    served.set()
                self.wfile.write(b': keepalive\n\n')
                self.wfile.flush()
        except OSError:
            pass


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
        if name.upper().startswith(PREFIXES):
            env[name] = str(value)
port = free_port(9611)
source = ThreadingHTTPServer(('127.0.0.1', free_port(9711)), Source)
threading.Thread(target=source.serve_forever, daemon=True).start()
scratch = Path(tempfile.mkdtemp(prefix='ciel-owned-sse-fork-'))
workspace = scratch / 'workspace'
config = scratch / 'config'
workspace.mkdir()
config.mkdir()
(scratch / 'profile/AppData/Roaming').mkdir(parents=True)
(scratch / 'profile/AppData/Local').mkdir(parents=True)
claude_cfg = scratch / 'claude-config'
claude_cfg.mkdir()
(claude_cfg / 'settings.json').write_text(json.dumps({'theme': 'dark'}), encoding='utf8')
project_key = re.sub(r'[^A-Za-z0-9_-]', '-', str(workspace)).rstrip('-')
(claude_cfg / '.claude.json').write_text(json.dumps({'hasCompletedOnboarding': True, 'projects': {project_key: {'hasTrustDialogAccepted': True, 'allowedTools': [], 'mcpContextUris': [], 'mcpServers': {}, 'enabledMcpjsonServers': [], 'disabledMcpjsonServers': [], 'hasClaudeMdExternalIncludesApproved': False, 'hasClaudeMdExternalIncludesWarningShown': False}}}), encoding='utf8')
real = Path(os.environ['BETTY_REAL_CONFIG'])
for name in ('config.json', 'model-registry.json'):
    shutil.copy2(real / name, config / name)
copied = json.loads((config / 'config.json').read_text(encoding='utf8'))
assert copied.get('providers', {}).get('cielairouter'), 'configured fixture provider missing'
for section in ('external_event_receivers', 'remote_bridge', 'remote_instructions', 'remote_memory', 'remote_provision'):
    copied.pop(section, None)
copied.update(current_provider='cielairouter', work_mode=True, work_auto_continue=False,
              work_side_intake={'enabled': True, 'type': 'btw_with_mcp', 'model': 'inherit'})
(config / 'config.json').write_text(json.dumps(copied, indent=2), encoding='utf8')
env['PATH'] = os.environ['BETTY_NPM_BIN'] + os.pathsep + env.get('PATH', '')
env.update(HOME=str(scratch / 'profile'), USERPROFILE=str(scratch / 'profile'), APPDATA=str(scratch / 'profile/AppData/Roaming'), LOCALAPPDATA=str(scratch / 'profile/AppData/Local'), CLAUDE_CONFIG_DIR=str(scratch / 'claude-config'), CIEL_RUNTIME_SKIP_CLAUDE_UPDATE='1', CIEL_RUNTIME_SKIP_CLAUDE_INSTALL='1', CIEL_RUNTIME_HOME_OVERRIDE=SNAP, CIEL_RUNTIME_CONFIG_DIR=str(config), CIEL_RUNTIME_LAUNCH_CWD=str(workspace),
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
    (BASE / (label + '.txt')).write_text(text, encoding='utf8')
    subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(Path(__file__).resolve().parent / 'shot_hwnd.ps1'),
                    str(int(console.hwnd)), str(BASE / (label + '.png'))],
                   capture_output=True, text=True, creationflags=0x08000000)
    return text


def router(path, body):
    request = urllib.request.Request(f'http://127.0.0.1:{port}{path}', data=json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


result = {'host': 'Betty .12 u42', 'runtime_snapshot': SNAP, 'base': str(BASE), 'port': port, 'source_port': source.server_address[1], 'outcome': 'timeout', 'frames': []}
try:
    time.sleep(12)
    console = Console(int(next(r['ProcessId'] for r in tree() if r['Name'] == 'cmd.exe')))
    deadline = time.time() + 90
    trusted = False
    onboarding_at = 0
    while time.time() < deadline:
        time.sleep(1)
        text = console.screen()
        if 'Traceback (most recent call last)' in text:
            capture(console, 'error')
            result['outcome'] = 'error'
            break
        if '[probe] ciel-runtime exited' in text and time.time() - onboarding_at > 8:
            capture(console, 'error')
            result['outcome'] = 'error'
            break
        if 'Choose the text style' in text:
            console.key('enter', pause=3)
            continue
        if 'Security notes:' in text and 'Press Enter to continue' in text:
            console.key('enter', pause=3)
            continue
        if 'Bypass Permissions mode' in text and 'Yes, I accept' in text:
            onboarding_at = time.time()
            console.key('down', pause=.5)
            console.key('enter', pause=4)
            continue
        if not trusted and ('Yes' in text or 'I trust this folder' in text) and ('trust' in text.lower() or 'Quick safety check' in text):
            if 'I trust this folder' in text: console.key('down', pause=.5)
            console.key('enter', pause=4)
            trusted = True
            continue
        if '? for shortcuts' in text or 'bypass permissions' in text or 'auto mode' in text:
            time.sleep(4)
            capture(console, '01-started')
            sys.path.insert(0, SNAP)
            from ciel_runtime_support.workspace_router_selection import workspace_digest, workspace_identity
            from ciel_runtime_support.work_plan_store import session_work_plan_store
            from ciel_runtime_support import work_plan_tree as work_tree
            state_dir = config / 'workspaces' / workspace_digest(workspace_identity(workspace))
            state_dir.mkdir(parents=True, exist_ok=True)
            store = session_work_plan_store(state_dir, 'claude:' + SESSION)

            def seed(doc):
                work_tree.set_plan(doc, {'title': 'Graphical Work Observatory', 'priority': 'P1', 'children': [
                    {'title': 'Release graphical pane', 'children': [{'title': 'Render Work hierarchy'}, {'title': 'Verify Betty .12'}]},
                    {'title': 'Process external SSE events', 'children': [{'title': 'ACK incoming message'}, {'title': 'Queue P1 work'}]}]})
                work_tree.set_phase(doc, work_tree.PHASE_EXECUTING)
                by = {n['title']: n for n in doc['nodes']}
                by['Render Work hierarchy']['status'] = 'done'
                by['Verify Betty .12']['status'] = 'in_progress'
            store.transaction(seed)
            result['receiver'] = router('/ca/events/receivers/default', {
                'enabled': True, 'transport': 'sse', 'url': f'http://127.0.0.1:{source.server_address[1]}/events'})
            console.text('/work-mode view')
            console.key('enter', pause=1)
            end = time.time() + 45
            while time.time() < end and 'WORK / SESSION' not in console.screen():
                time.sleep(1)
            time.sleep(2)
            capture(console, '02-dashboard-before-sse')
            fire.set()
            result['event_served'] = served.wait(30)
            last = ''
            end = time.time() + 180
            index = 0
            seen_actor = seen_settled = False
            while time.time() < end:
                time.sleep(1.5)
                text = console.screen()
                if text == last:
                    continue
                last = text
                index += 1
                label = f'03-frame-{index:02d}'
                capture(console, label)
                actor = 'ACTIVE SSE FORKS' in text
                seen_actor = seen_actor or actor
                settled = seen_actor and 'No active side fork' in text
                # Only the SSE column (right of the work tree's border) is recorded.
                result['frames'].append({'label': label, 'active_fork': actor,
                                         'sse_lines': [l.strip()[-120:] for l in text.splitlines()
                                                       if any(k in l for k in ('SSE FLOW', 'IN ', 'ACTIVE SSE FORKS', 'fork ', 'model ', 'No active side fork', 'processing', 'pending', 'answered'))][:12]})
                if settled and not seen_settled:
                    seen_settled = True
                    # Keep observing the settled receipt (pending -> answered) for 20 s.
                    end = min(end, time.time() + 20)
                if index >= 40:
                    break
            result['outcome'] = 'fork-shown' if seen_actor else 'fork-not-shown'
            result['fork_settled'] = seen_settled
            capture(console, '04-final')
            console.key('esc', pause=.5)
            console.text('/exit')
            console.key('enter', pause=3)
            break
    else:
        capture(console, 'timeout')
finally:
    stop.set()
    result['tree_before_kill'] = [r['Name'] for r in tree()]
    subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    owner = subprocess.run(['powershell', '-NoProfile', '-Command',
                            f'(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue).OwningProcess'],
                           capture_output=True, text=True).stdout.split()
    result['owned_router_pids'] = owner
    for pid in owner:
        subprocess.run(['taskkill', '/PID', pid, '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    source.shutdown()
    time.sleep(1)
    side = config / 'workspaces'
    result['side_inbox_files'] = sorted(p.name for p in side.rglob('*') if p.is_file() and 'side' in p.name)[:10] if side.exists() else []
    for log in config.glob('router-instances/*/router.log'):
        shutil.copy2(log, BASE / 'router.log')
    inbox = sorted(str(q.relative_to(config)) for q in (config / 'workspaces').rglob('*') if q.is_file() and 'side-inboxes' in str(q)) if (config / 'workspaces').exists() else []
    result['side_inbox_paths'] = inbox[:20]
    shutil.rmtree(scratch, ignore_errors=True)
    result['scratch_removed'] = not scratch.exists()
    (BASE / 'result.json').write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(result, indent=2, ensure_ascii=False))
