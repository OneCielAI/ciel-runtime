"""Real console: the side actor releases a recorded wait and wakes main at once (idle and busy).

Owned, isolated launch of a source snapshot (PROBE_SNAPSHOT) in conhost: new workspace, copied ciel
config (config.json + model-registry.json only, receiver/remote sections removed), free router port,
inherited CIEL_RUNTIME_*/CLAUDE*/ANTHROPIC*/CODEX*/OPENAI* stripped. A loopback SSE source feeds
events through the production receiver -> side inbox -> side actor path. Phases:

  1. side model A, event 'model-a' (status question);
  2. live switch to model B with the same work_live_settings call the Observatory picker makes,
     then event 'model-b' (status question);
  3. hostile code-change event asking to edit code, run tests and restart the session;
  4. approval for a seeded item waiting on 'Daniel approval 118269' while main is idle.

No prompt is typed into main. The probe exits with /exit and kills only its own process tree and router.
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

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / 'docs/journal/2026/11/03/features/work-parallel/evidence/menu-ui'
SNAP = os.environ['PROBE_SNAPSHOT']
SIDE_MODEL = os.environ.get('PROBE_SIDE_MODEL', 'inherit')
MAIN_MODEL = os.environ.get('PROBE_MAIN_MODEL', '_Walkie_Opus55-Sol61-Fast')
BASE = REPO / '.local' / ('owned-side-release-' + time.strftime('%Y%m%d-%H%M%S'))
BASE.mkdir(parents=True)
shutil.copy2(TOOLS / 'console_driver.py', BASE / 'console_driver.py')
shutil.copy2(REPO / '.local' / 'shot_hwnd_restore.ps1', BASE / 'shot_hwnd.ps1')
sys.path.insert(0, str(BASE))
from console_driver import Console  # noqa: E402

SESSION = '55555555-5555-4555-8555-555555555555'
IDENTITY = 'claude:' + SESSION
queue = []
queue_lock = threading.Lock()
stop = threading.Event()


def cloud(event_id, text, priority=3):
    return json.dumps({'specversion': '1.0', 'id': event_id, 'source': 'urn:owned:side-release',
                       'type': 'message.created', 'time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                       'data': {'priority': priority, 'text': text}}, ensure_ascii=False, separators=(',', ':'))


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
                with queue_lock:
                    batch = list(queue)
                    queue.clear()
                for event_id, event in batch:
                    self.wfile.write(('id: ' + event_id + '\nevent: message\ndata: ' + event + '\n\n').encode())
                    self.wfile.flush()
                self.wfile.write(b': keepalive\n\n')
                self.wfile.flush()
                time.sleep(1)
        except OSError:
            pass


def send(event_id, text, priority=3):
    with queue_lock:
        queue.append((event_id + '-t', cloud(event_id, text, priority)))


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
scratch = Path(tempfile.mkdtemp(prefix='ciel-owned-side-release-'))
workspace = scratch / 'workspace'
config = scratch / 'config'
workspace.mkdir()
config.mkdir()
real = Path(os.environ['APPDATA']) / 'ciel-runtime'
for name in ('config.json', 'model-registry.json'):
    shutil.copy2(real / name, config / name)
copied = json.loads((config / 'config.json').read_text(encoding='utf8'))
assert copied.get('providers', {}).get('cielairouter'), 'configured fixture provider missing'
for section in ('external_event_receivers', 'remote_bridge', 'remote_instructions', 'remote_memory', 'remote_provision'):
    copied.pop(section, None)
copied['providers']['cielairouter']['current_model'] = MAIN_MODEL
copied.update(current_provider='cielairouter', work_mode=True, work_auto_continue=False,
              work_side_intake={'enabled': True, 'type': 'btw_with_mcp', 'model': SIDE_MODEL, 'context_mode': 'isolated'})
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
    (BASE / (label + '.txt')).write_text(text, encoding='utf8')
    subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(BASE / 'shot_hwnd.ps1'),
                    str(int(console.hwnd)), str(BASE / (label + '.png'))],
                   capture_output=True, text=True, creationflags=0x08000000)
    return text


def post(path, body, headers=None):
    request = urllib.request.Request(f'http://127.0.0.1:{port}{path}', data=json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json', **(headers or {})})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def host_tool(name, arguments):
    """The authenticated host call the ciel-work mod makes, with this owned launch's own token."""
    token = (config / 'work-parallel-host.token').read_text(encoding='ascii').strip()

    def call(tool, args):
        value = post('/ca/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
            'name': tool, 'arguments': {**args, 'session_id': IDENTITY, '_host': {'token': token}}}},
            {'Accept': 'application/json, text/event-stream'})
        result = value.get('result') or {}
        text = ''.join(c.get('text', '') for c in result.get('content') or [] if c.get('type') == 'text')
        if result.get('isError'):
            return {'error': text[:400]}
        return json.loads(text)
    value = call(name, arguments)
    if isinstance(value, dict) and isinstance(value.get('paged'), dict):
        # Large host results are paged exactly as the mod reassembles them.
        pages = value['paged']
        value = json.loads(''.join(call('work_host_page', {'id': pages['id'], 'part': part})['text']
                                   for part in range(pages['parts'])))
    return value


def claude_project():
    return Path.home() / '.claude' / 'projects' / re.sub(r'[^A-Za-z0-9]', '-', str(workspace))


def actors():
    """Every side actor transcript of the owned session: models and tools offered/called."""
    rows = []
    for meta in (claude_project() / SESSION / 'subagents').glob('*.meta.json'):
        try:
            info = json.loads(meta.read_text(encoding='utf8'))
        except (OSError, ValueError):
            continue
        if not str(info.get('description', '')).startswith('Side SSE'):
            continue
        body = meta.with_name(meta.name.replace('.meta.json', '.jsonl'))
        requested, native, called, offered, final = set(), set(), [], None, ''
        try:
            lines = body.read_text(encoding='utf8', errors='replace').splitlines()
        except OSError:
            lines = []
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get('type') == 'api-request' and offered is None:
                params = row.get('params') or {}
                if isinstance(params.get('tools'), list):
                    offered = sorted(str(t.get('name')) for t in params['tools'] if isinstance(t, dict))
            if row.get('type') == 'assistant':
                requested.add(row.get('requestedModel'))
                message = row.get('message') or {}
                native.add(message.get('model'))
                for block in message.get('content') or []:
                    if isinstance(block, dict) and block.get('type') == 'tool_use':
                        called.append(block.get('name'))
                    if (isinstance(block, dict) and block.get('type') == 'text'
                            and message.get('stop_reason') == 'end_turn'):
                        final = block.get('text', '')
        rows.append({'description': info.get('description'), 'agent_type': info.get('agentType'),
                     'mtime': round(meta.stat().st_mtime, 1), 'requested_models': sorted(m for m in requested if m),
                     'native_models': sorted(m for m in native if m), 'tools_called': called,
                     'tools_offered': offered, 'final_tail': final[-700:]})
    return sorted(rows, key=lambda r: r['mtime'])


def receipts():
    rows = []
    for path in config.rglob('side-inboxes/**/receipts/**/*.json'):
        try:
            value = json.loads(path.read_text(encoding='utf8'))
        except (OSError, ValueError):
            continue
        result = value.get('result') or {}
        rows.append({'event_key': value.get('event_key'), 'stage': value.get('stage'),
                     'received_at': value.get('received_at'), 'claimed_at': (value.get('processing') or {}).get('started_at'),
                     'consumed_at': value.get('consumed_at'), 'released_waits': result.get('released_waits'),
                     'unreleased_waits': result.get('unreleased_waits'),
                     'action': result.get('action'), 'handoff': result.get('handoff'),
                     'item_id': result.get('item_id'), 'reply': str(result.get('reply', ''))[:400],
                     'reported_sends': result.get('reported_sends'),
                     'failure': (value.get('side_failure') or {}).get('category')})
    return rows


def main_rows():
    """Main transcript user/assistant rows after launch: who submitted, and whether a turn ran."""
    rows = []
    for path in [claude_project() / (SESSION + '.jsonl')]:
        if not path.exists():
            continue
        for line in path.read_text(encoding='utf8', errors='replace').splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get('type') not in ('user', 'assistant') or row.get('isSidechain'):
                continue
            message = row.get('message') or {}
            content = message.get('content')
            text = content if isinstance(content, str) else ' '.join(
                str(b.get('text', '')) for b in content or [] if isinstance(b, dict) and b.get('type') == 'text')
            tools = [b.get('name') for b in content or [] if isinstance(b, dict) and b.get('type') == 'tool_use'] \
                if isinstance(content, list) else []
            rows.append({'type': row.get('type'), 'timestamp': row.get('timestamp'), 'is_meta': bool(row.get('isMeta')),
                         'origin': row.get('origin'), 'tools': tools, 'text': text[:500]})
    return rows


def wait_for(keys, seconds, console, prefix):
    end = time.time() + seconds
    index = 0
    last = ''
    while time.time() < end:
        time.sleep(2)
        done = [r for r in receipts() if r['stage'] in ('processed', 'failed')]
        if all(any(k in str(r['event_key']) for r in done) for k in keys):
            return True
        text = console.screen()
        if text != last:
            last = text
            index += 1
            if index <= 4:
                capture(console, f'{prefix}-{index:02d}')
    return False




def released_rows(rows):
    return [r for r in rows if r['type'] == 'user' and 'Ciel side released recorded wait' in r['text']]


def stamp(value):
    from datetime import datetime
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() if value else None


def receipt(key):
    return next((r for r in receipts() if key in str(r['event_key']) and r['stage'] in ('processed', 'failed')), None)


def timing(row, woke):
    if not row:
        return None
    return {'received_to_claim_s': round(row['claimed_at'] - row['received_at'], 3) if row.get('claimed_at') else None,
            'received_to_resolved_s': round(row['consumed_at'] - row['received_at'], 3) if row.get('consumed_at') else None,
            'resolved_to_main_row_s': round(stamp(woke['timestamp']) - row['consumed_at'], 3) if woke and row.get('consumed_at') else None}


def idle(console):
    text = console.screen()
    return '? for shortcuts' in text and 'esc to interrupt' not in text


def sleeping_python():
    return any(r['Name'] == 'python.exe' and 'time.sleep(75)' in str(r.get('CommandLine')) for r in tree())


result = {'base': str(BASE), 'snapshot': SNAP, 'port': port, 'side_model': SIDE_MODEL, 'main_model': MAIN_MODEL,
          'outcome': 'timeout'}
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
            work_tree.set_plan(doc, {'title': 'P1 118160 fail-first fix', 'priority': 'P1', 'children': [
                {'title': 'Daniel design approval 118269', 'waiting_on': 'Daniel approval 118269 via Walkie'},
                {'title': 'Wait for CI run 4312', 'waiting_on': 'CI run 4312 result'}]})
            work_tree.set_phase(doc, work_tree.PHASE_EXECUTING)
        store.transaction(seed)
        board = store.read()
        gates = {n['waiting_on']: n['id'] for n in board['nodes'] if n.get('waiting_on')}
        result['seed'] = {'version': board['version'], 'gates': gates, 'phase': board['phase']}
        result['receiver'] = post('/ca/events/receivers/default', {
            'enabled': True, 'transport': 'sse', 'url': f'http://127.0.0.1:{source.server_address[1]}/events',
            'cursor_query_parameter': 'after'})
        time.sleep(3)
        capture(console, '02-seeded-idle')

        # Phase A: main idle; the approval main waits for arrives.
        before = len(main_rows())
        send('approval', 'Daniel: I approve design 118269 for P1 118160. Go ahead with the fail-first implementation.', 1)
        result['a_processed'] = wait_for(['approval'], 300, console, '03-approval')
        row_a = receipt('approval')
        end = time.time() + 120
        woke_a = None
        while time.time() < end and not woke_a:
            time.sleep(1)
            woke_a = next(iter(released_rows(main_rows()[before:])), None)
        capture(console, '04-main-woken-idle')
        doc = store.read()
        result['a'] = {'receipt': row_a, 'timing': timing(row_a, woke_a), 'main_row': woke_a,
                       'gate_after': next((n.get('waiting_on', '') for n in doc['nodes']
                                           if n['id'] == gates['Daniel approval 118269 via Walkie']), None),
                       'handoffs': len(doc.get('side_handoffs', {}))}
        # Let main's woken turn settle before the busy phase.
        end = time.time() + 300
        while time.time() < end and not idle(console):
            time.sleep(2)
        time.sleep(3)
        capture(console, '05-after-idle-wake')

        # Phase B: main is busy inside a long foreground command when CI reports.
        before = len(main_rows())
        console.text('Run exactly this with the Bash tool in the foreground and wait for it to finish: '
                     'python -c "import time; time.sleep(75); print(75)" . Then reply only with the word FINISHED.')
        console.key('enter', pause=2)
        end = time.time() + 150
        busy_seen = False
        while time.time() < end and not busy_seen:
            time.sleep(1)
            busy_seen = sleeping_python()
        result['b_busy_seen'] = busy_seen
        capture(console, '06-main-busy')
        result['b_sent_epoch'] = time.time()
        send('ci', 'CI run 4312 finished: all checks passed on the fail-first branch.', 2)
        result['b_processed'] = wait_for(['ci'], 300, console, '07-ci')
        row_b = receipt('ci')
        result['b_resolved_while_busy'] = bool(row_b and row_b.get('consumed_at')) and sleeping_python()
        end = time.time() + 240
        woke_b = None
        while time.time() < end and not woke_b:
            time.sleep(1)
            woke_b = next(iter(released_rows(main_rows()[before:])), None)
        capture(console, '08-main-after-busy-release')
        end = time.time() + 300
        while time.time() < end and not idle(console):
            time.sleep(2)
        time.sleep(3)
        capture(console, '09-final')
        doc = store.read()
        rows_b = main_rows()[before:]
        result['b'] = {'receipt': row_b, 'timing': timing(row_b, woke_b), 'main_row': woke_b,
                       'gate_after': next((n.get('waiting_on', '') for n in doc['nodes']
                                           if n['id'] == gates['CI run 4312 result']), None),
                       'handoffs': len(doc.get('side_handoffs', {})),
                       'main_rows': [dict({k: r[k] for k in ('type', 'timestamp', 'is_meta', 'tools')}, text=r['text'][:160])
                                     for r in rows_b][:20]}
        result['board_after'] = [{'id': n['id'], 'title': n['title'], 'status': n['status'],
                                  'waiting_on': n.get('waiting_on', ''), 'note_tail': n.get('note', '')[-200:]}
                                 for n in doc['nodes']]
        result['actors'] = actors()
        result['receipts'] = receipts()
        result['outcome'] = 'completed'
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
    result['preserved_config'] = str(config)
    (BASE / 'result.json').write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({k: result.get(k) for k in ('base', 'outcome', 'a', 'b_busy_seen', 'b_resolved_while_busy', 'b')},
                     indent=2, ensure_ascii=True)[:7000])
