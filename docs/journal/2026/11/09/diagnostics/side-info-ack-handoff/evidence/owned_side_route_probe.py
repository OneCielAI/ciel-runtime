"""Real console proof: Walkie side answers reach the source without a configured reply route (KOBE 2026-11-09).

Derived from owned_suggestion_capture_probe.py. PROBE_NO_REPLY_CONNECTOR=1 removes
work_side_intake.reply_connectors, the KOBE condition. Events are a status and an info question.

Original description:

Owned, isolated launch of PROBE_SNAPSHOT in conhost: new workspace holding a copy of the snapshot's
ciel_runtime_support (so code reads have real files), copied ciel config with receivers/remote
sections removed, free router port, inherited CIEL_RUNTIME_*/CLAUDE*/ANTHROPIC*/CODEX*/OPENAI*
stripped. A loopback SSE source sends Walkie-shaped CloudEvents (data.payload {}, data.text empty,
only conversation + message_id name the message) through the production receiver -> side inbox ->
side actor path. An owned Walkie MCP (HTTP, same response shape as the real walkie_read_messages,
observed on Prody 2026-11-09) holds the messages and records every send; the operator's Walkie
reply preset names it. Main reads 16 source files (a Prody-sized parent context) and then runs two
foreground sleeps, so every event arrives while main is busy. Events:

  1. status question          -> answered at once, nothing queued
  2. info question (code read) -> answered from guarded code tools, nothing queued
  3. new request              -> ACK + recap, queued, committed position
  4. same request restated     -> the existing item is updated, no new item
  5. "move X to 2nd"          -> queue reordered
  6. P1 request (thread_task)  -> queued first behind active
  7. redelivery of event 3     -> no second receipt, no second send

The only prompt typed into main is the read+sleep request. The probe exits with /exit and kills
only its own process tree and router.
"""
import calendar
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
import uuid
import winreg
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / 'docs/journal/2026/11/03/features/work-parallel/evidence/menu-ui'
SNAP = os.environ['PROBE_SNAPSHOT']
SIDE_MODEL = os.environ.get('PROBE_SIDE_MODEL', '_API_KEYS-SSE-1')
BASE = REPO / '.local' / ('owned-side-route-' + os.environ.get('PROBE_LABEL', 'run') + '-' + time.strftime('%Y%m%d-%H%M%S'))
BASE.mkdir(parents=True)
shutil.copy2(TOOLS / 'console_driver.py', BASE / 'console_driver.py')
shutil.copy2(REPO / '.local' / 'shot_hwnd_restore.ps1', BASE / 'shot_hwnd.ps1')
sys.path.insert(0, str(BASE))
from console_driver import Console  # noqa: E402

SESSION = os.environ.get('PROBE_SESSION', '66666666-6666-4666-8666-666666666666')
IDENTITY = 'claude:' + SESSION
SOURCE = 'urn:owned:walkie'
CONVERSATION = '7b0c6f1e-2d4a-4c11-9e55-0a1b2c3d4e5f'
OWNER = '1f2e3d4c-5b6a-4789-8abc-def012345678'
READS = ['prelaunch.py', 'external_event_receiver.py', 'runtime_compaction_control.py', 'work_plan_tree.py',
         'claude_router.py', 'responses_anthropic_stream.py', 'codex_desktop_injection.py',
         'protocols/conversation_turn_policy.py', 'transient_retry.py', 'runtime_compaction_binding.py',
         'usage_service.py', 'channel_terminal_proxy.py', 'windows_conpty.py', 'upstream_retry.py',
         'speech_http_controller.py', 'statusline_script.py']
queue = []
queue_lock = threading.Lock()
stop = threading.Event()
walkie = {'messages': [], 'sends': [], 'reads': [], 'next': 9001}
walkie_lock = threading.Lock()


def walkie_message(text, sender='Owner Kim', sender_id=OWNER, reply_to=None, kind='user'):
    with walkie_lock:
        message_id = str(walkie['next'])
        walkie['next'] += 1
        walkie['messages'].append({'id': message_id, 'conversation_id': CONVERSATION, 'sender_id': sender_id,
                                   'sender': sender, 'text': text, 'sender_kind': kind,
                                   'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                                   'reply_to_message_id': reply_to, 'files': []})
        return message_id


WALKIE_TOOLS = [
    {'name': 'walkie_read_messages', 'description': 'Read messages of a Walkie conversation after a message id',
     'inputSchema': {'type': 'object', 'properties': {'conversation_id': {'type': 'string'}, 'after': {'type': 'string'}},
                     'required': ['conversation_id']}},
    {'name': 'walkie_send_message', 'description': 'Send a message to a Walkie conversation',
     'inputSchema': {'type': 'object', 'properties': {'conversation_id': {'type': 'string'}, 'client_id': {'type': 'string'},
                                                      'reply_to_message_id': {'type': 'string'}, 'text': {'type': 'string'},
                                                      'mention_ids': {'type': 'array', 'items': {'type': 'string'}}},
                     'required': ['conversation_id', 'client_id', 'text']}},
]


class Source(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, obj, status=200):
        raw = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_DELETE(self):
        self.reply({})

    def do_POST(self):
        if not self.path.startswith('/mcp-walkie'):
            self.send_error(404)
            return
        body = json.loads(self.rfile.read(int(self.headers.get('content-length', 0))) or b'{}')
        method, params, ident = body.get('method'), body.get('params') or {}, body.get('id')
        if ident is None:
            self.send_response(202)
            self.end_headers()
            return
        if method == 'initialize':
            result = {'protocolVersion': params.get('protocolVersion', '2025-03-26'), 'capabilities': {'tools': {}},
                      'serverInfo': {'name': 'owned-walkie', 'version': '1'}}
        elif method == 'tools/list':
            result = {'tools': WALKIE_TOOLS}
        elif method == 'tools/call':
            name, args = params.get('name'), params.get('arguments') or {}
            with walkie_lock:
                if name == 'walkie_read_messages':
                    after = int(args.get('after') or 0)
                    rows = [m for m in walkie['messages'] if m['conversation_id'] == args.get('conversation_id')
                            and int(m['id']) > after]
                    walkie['reads'].append({'at': time.time(), 'after': args.get('after'), 'returned': [m['id'] for m in rows]})
                    text = json.dumps({'messages': rows}, ensure_ascii=False)
                elif name == 'walkie_send_message':
                    duplicate = any(s['args'].get('client_id') == args.get('client_id') for s in walkie['sends'])
                    walkie['sends'].append({'at': time.time(), 'args': args, 'duplicate_client_id': duplicate})
                    text = json.dumps({'message': {'id': 'sent-' + str(len(walkie['sends'])), 'conversation_id': args.get('conversation_id')}})
                else:
                    text = json.dumps({'error': 'unknown tool'})
            result = {'content': [{'type': 'text', 'text': text}], 'isError': False}
        elif method == 'ping':
            result = {}
        else:
            self.reply({'jsonrpc': '2.0', 'id': ident, 'error': {'code': -32601, 'message': 'method not found'}})
            return
        self.reply({'jsonrpc': '2.0', 'id': ident, 'result': result})

    def do_GET(self):
        if not self.path.startswith('/events'):
            self.send_response(405)
            self.end_headers()
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
                for transport_id, event in batch:
                    self.wfile.write(('id: ' + transport_id + '\nevent: message\ndata: ' + event + '\n\n').encode())
                    self.wfile.flush()
                self.wfile.write(b': keepalive\n\n')
                self.wfile.flush()
                time.sleep(1)
        except OSError:
            pass


def cloud(message_id, thread_priority=None):
    data = {'conversation': CONVERSATION, 'message_id': message_id, 'event_type': 'message.created',
            'sender': 'Owner Kim', 'sender_id': OWNER, 'stream_id': '1', 'payload': {}, 'text': '',
            'own': False, 'trust': 'member'}
    if thread_priority:
        data['message_context'] = {'thread_task': {'priority': thread_priority}}
    return json.dumps({'specversion': '1.0', 'id': 'walkie-' + message_id, 'source': SOURCE,
                       'type': 'com.ciel.walkie.support.message',
                       'time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'data': data},
                      ensure_ascii=False, separators=(',', ':'))


def send_event(message_id, thread_priority=None, transport_suffix='t'):
    with queue_lock:
        queue.append(('walkie-' + message_id + '-' + transport_suffix, cloud(message_id, thread_priority)))


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
port = free_port(9621)
source = ThreadingHTTPServer(('127.0.0.1', free_port(9721)), Source)
threading.Thread(target=source.serve_forever, daemon=True).start()
source_port = source.server_address[1]
scratch = Path(tempfile.mkdtemp(prefix='ciel-owned-side-contract-'))
workspace = scratch / 'workspace'
config = scratch / 'config'
config.mkdir()
shutil.copytree(Path(SNAP) / 'ciel_runtime_support', workspace / 'ciel_runtime_support',
                ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
real = Path(os.environ['APPDATA']) / 'ciel-runtime'
for name in ('config.json', 'model-registry.json'):
    shutil.copy2(real / name, config / name)
copied = json.loads((config / 'config.json').read_text(encoding='utf8'))
assert copied.get('providers', {}).get('cielairouter'), 'configured fixture provider missing'
for section in ('external_event_receivers', 'remote_bridge', 'remote_instructions', 'remote_memory', 'remote_provision'):
    copied.pop(section, None)
# Main runs the model the live Walkie sessions run; the copied default (ASTRA) reported
# no Read tool and declined the foreground wait (owned run 2026-11-09 04:33Z).
copied['providers']['cielairouter']['current_model'] = os.environ.get('PROBE_MAIN_MODEL', '_Walkie_Opus55-Sol61-Fast')
copied.update(current_provider='cielairouter', work_mode=True, work_auto_continue=False,
              work_side_intake={'enabled': True, 'type': 'btw_with_mcp', 'model': SIDE_MODEL, 'context_mode': 'full',
                                'reply_connectors': [{'source': SOURCE, 'receiver_id': 'default', 'preset': 'walkie',
                                                      'server': 'walkie'}]},
              workspace_mcp={'servers': {'walkie': {'transport': 'streamable_http', 'runtimes': ['claude'],
                                                    'url': f'http://127.0.0.1:{source_port}/mcp-walkie'}}})
if os.environ.get('PROBE_NO_REPLY_CONNECTOR') == '1':
    copied['work_side_intake'].pop('reply_connectors', None)
(config / 'config.json').write_text(json.dumps(copied, indent=2), encoding='utf8')
env['CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION'] = '1'
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


def claude_project():
    return Path.home() / '.claude' / 'projects' / re.sub(r'[^A-Za-z0-9]', '-', str(workspace))


def stamp(value):
    """ISO transcript timestamp -> epoch seconds."""
    if not value:
        return None
    return calendar.timegm(time.strptime(value[:19], '%Y-%m-%dT%H:%M:%S')) + float('0' + value[19:23].rstrip('Z'))


def actors():
    """Every side actor transcript: model requests, tokens, cache reads and tools called."""
    rows = []
    for meta in (claude_project() / SESSION / 'subagents').glob('*.meta.json'):
        try:
            info = json.loads(meta.read_text(encoding='utf8'))
        except (OSError, ValueError):
            continue
        if not str(info.get('description', '')).startswith('Side SSE'):
            continue
        body = meta.with_name(meta.name.replace('.meta.json', '.jsonl'))
        requests, native, requested, called, final, first_at, last_at, started = {}, set(), set(), [], '', None, None, None
        try:
            lines = body.read_text(encoding='utf8', errors='replace').splitlines()
        except OSError:
            lines = []
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            started = started or row.get('timestamp')
            if row.get('type') != 'assistant':
                continue
            message = row.get('message') or {}
            first_at = first_at or row.get('timestamp')
            last_at = row.get('timestamp') or last_at
            requested.add(row.get('requestedModel'))
            native.add(message.get('model'))
            usage = message.get('usage') or {}
            requests[message.get('id') or row.get('uuid')] = {
                'input': usage.get('input_tokens', 0) + usage.get('cache_read_input_tokens', 0) + usage.get('cache_creation_input_tokens', 0),
                'cache_read': usage.get('cache_read_input_tokens', 0), 'cache_write': usage.get('cache_creation_input_tokens', 0),
                'output': usage.get('output_tokens', 0)}
            for block in message.get('content') or []:
                if isinstance(block, dict) and block.get('type') == 'tool_use':
                    called.append(block.get('name'))
                if isinstance(block, dict) and block.get('type') == 'text':
                    final = block.get('text', '')
        rows.append({'receipt_prefix': str(info.get('description', ''))[9:], 'started_at': stamp(started),
                     'first_response_at': stamp(first_at), 'last_response_at': stamp(last_at),
                     'requested_models': sorted(m for m in requested if m), 'native_models': sorted(m for m in native if m),
                     'model_requests': len(requests), 'usage': list(requests.values()), 'tools_called': called,
                     'final_tail': final[-900:]})
    return sorted(rows, key=lambda r: r['started_at'] or 0)


def receipts():
    rows = []
    for folder in ('receipts', 'pending'):
        for path in config.rglob(f'side-inboxes/**/{folder}/**/*.json'):
            try:
                value = json.loads(path.read_text(encoding='utf8'))
            except (OSError, ValueError):
                continue
            result = value.get('result') or {}
            rows.append({'receipt_id': path.stem, 'folder': folder, 'event_key': value.get('event_key'),
                         'stage': value.get('stage'), 'received_at': value.get('received_at'),
                         'claimed_at': (value.get('processing') or {}).get('started_at'), 'consumed_at': value.get('consumed_at'),
                         'outbox': value.get('outbox'), 'action': result.get('action'), 'kind': result.get('kind'),
                         'item_id': result.get('item_id'), 'queue': result.get('queue'), 'duplicate': result.get('duplicate'),
                         'delivery': result.get('delivery'), 'final_text': result.get('final_text'),
                         'rechecks': value.get('rechecks'), 'failure': (value.get('side_failure') or {}).get('category')})
    return rows


def main_rows():
    rows = []
    path = claude_project() / (SESSION + '.jsonl')
    if not path.exists():
        return rows
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
        rows.append({'type': row.get('type'), 'timestamp': row.get('timestamp'), 'tools': tools, 'text': text[:400],
                     'usage': (message.get('usage') or {}) if row.get('type') == 'assistant' else None})
    return rows


def main_raw():
    path = claude_project() / (SESSION + '.jsonl')
    return path.read_text(encoding='utf8', errors='replace') if path.exists() else ''


def wait_event(message_id, seconds, console, label):
    """Until the receipt for this message is committed and its one host send is recorded."""
    end = time.time() + seconds
    while time.time() < end:
        time.sleep(1.5)
        row = next((r for r in receipts() if r['event_key'] and ('walkie-' + message_id) in r['event_key']
                    and (r['folder'] == 'receipts' or r.get('failure'))), None)
        if row and ((row['outbox'] or {}).get('state') in ('submitted', 'failed', 'uncertain')
                    or row['delivery'] in ('route_unavailable', 'side_mcp')):
            time.sleep(2)
            capture(console, label)
            return row
        if row and row.get('failure'):
            capture(console, label + '-failure')
            return row
    capture(console, label + '-timeout')
    return None


def board():
    doc = store.read()
    queued = [n['id'] for n in work_tree.queued_roots(doc)]
    return {'version': doc['version'], 'active_root': doc.get('active_root'), 'queued': queued,
            'roots': [{'id': n['id'], 'title': n['title'], 'priority': n.get('priority'), 'status': n['status'],
                       'note': n.get('note', '')[:300]} for n in doc['nodes'] if n['parent_id'] is None],
            'render': work_tree.render(doc)}


def main_finished():
    return any(r['type'] == 'assistant' and 'ALL DONE' in r['text'] for r in main_rows())


def show_thread(label):
    """Render the owned Walkie conversation in its own console window and screenshot it."""
    with walkie_lock:
        lines = []
        for m in walkie['messages']:
            lines.append(f"[{m['id']}] {m['sender']}: {m['text']}")
        for s in walkie['sends']:
            a = s['args']
            lines.append(f"[reply to {a.get('reply_to_message_id')}] side (host send, client_id {a.get('client_id', '')[:8]}): ")
            lines.extend('    ' + part for part in str(a.get('text', '')).splitlines())
    path = BASE / (label + '.txt')
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    viewer = subprocess.Popen(['conhost.exe', 'cmd', '/k', f'chcp 65001 >nul & mode con cols=160 lines=60 & type "{path}"'],
                              creationflags=0)
    time.sleep(4)
    try:
        # Attaching to a second console detaches the probe from main's console
        # (WriteConsoleInputW error 6 in the first run): use the window handle only.
        hwnd = subprocess.check_output(['powershell', '-NoProfile', '-Command',
                                        f'(Get-Process -Id {viewer.pid}).MainWindowHandle'], text=True).strip()
        subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(BASE / 'shot_hwnd.ps1'),
                        hwnd, str(BASE / (label + '.png'))], capture_output=True, text=True, creationflags=0x08000000)
    finally:
        subprocess.run(['taskkill', '/PID', str(viewer.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


EVENTS = [
    ('status', '지금 작업 상태 어때? 진행 중인 것과 대기열만 알려줘.', None),
    ('info', '이 워크스페이스 ciel_runtime_support/work_plan_tree.py 에 정의된 MAX_DEPTH 값이 몇이야? 파일을 직접 확인해서 알려줘.', None),
]

result = {'base': str(BASE), 'snapshot': SNAP, 'port': port, 'side_model': SIDE_MODEL, 'outcome': 'timeout',
          'events': []}
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
            work_tree.set_plan(doc, {'title': '결제 모듈 리팩터링', 'priority': 'P2', 'children': [
                {'title': '결제 어댑터 인터페이스 분리'}, {'title': '회귀 테스트 보강'}]})
            work_tree.set_phase(doc, work_tree.PHASE_EXECUTING)
            for title in ('문서 번역 업데이트', 'CI 캐시 정리', '로그 보존 정책 변경'):
                work_tree.add_subtree(doc, None, {'title': title, 'priority': 'P3'})
        store.transaction(seed)
        result['board_before'] = board()
        result['receiver'] = post('/ca/events/receivers/default', {
            'enabled': True, 'transport': 'sse', 'url': f'http://127.0.0.1:{source_port}/events',
            'cursor_query_parameter': 'after'})
        # Finish two real turns and wait for Claude Code's prompt-suggestion auxiliary
        # request after each. Prody's corrupt capture was one of these requests.
        turn_markers = []
        for number in (1, 2):
            marker = f'TURN-{number}-DONE'
            console.text(f'Run Bash `echo probe-{number}` and then reply exactly: {marker}')
            console.key('enter', pause=2)
            turn_end = time.time() + 240
            while time.time() < turn_end and not any(marker in r['text'] for r in main_rows() if r['type'] == 'assistant'):
                time.sleep(2)
            time.sleep(25)
            turn_markers.append({'marker': marker, 'done': any(marker in r['text'] for r in main_rows() if r['type'] == 'assistant')})
        result['turn_markers'] = turn_markers
        # Wait until an auxiliary suggestion request actually replaced the parent capture.
        from ciel_runtime_support.side_context_store import SideContextStore
        context_store = SideContextStore(state_dir, IDENTITY)
        expected_capture = os.environ.get('PROBE_EXPECT_CAPTURE', 'suggestion')
        capture_wait_end = time.time() + (180 if expected_capture == 'suggestion' else 45)
        capture_kind = None
        while time.time() < capture_wait_end:
            value = context_store.read(context_store.directory / 'latest.json') or {}
            messages = (value.get('parent') or {}).get('messages') or []
            content = messages[-1].get('content') if messages else ''
            blocks = [{'type': 'text', 'text': content}] if isinstance(content, str) else (content or [])
            tail = next((b.get('text') for b in blocks if isinstance(b, dict) and b.get('type') == 'text'), '') or ''
            capture_kind = 'suggestion' if tail.lstrip().startswith('[SUGGESTION MODE:') else 'main'
            if capture_kind == expected_capture:
                break
            time.sleep(2)
        result['capture_before_events'] = {'kind': capture_kind, 'expected': expected_capture}
        capture(console, '02-main-idle-after-suggestions')
        ids = {}
        wanted = [x for x in os.environ.get('PROBE_EVENTS', '').split(',') if x]
        for index, (label, text_, priority) in enumerate(EVENTS, start=3):
            if wanted and label not in wanted:
                continue
            message_id = walkie_message(text_)
            ids[label] = message_id
            sent_at = time.time()
            send_event(message_id, priority)
            row = wait_event(message_id, 300, console, f'{index:02d}-{label}')
            result['events'].append({'label': label, 'message_id': message_id, 'text': text_, 'sent_at': sent_at,
                                     'receipt': row, 'main_finished_at_commit': main_finished(),
                                     'board_after': board()})
            with walkie_lock:
                (BASE / 'walkie.json').write_text(json.dumps(walkie, indent=2, ensure_ascii=False), encoding='utf-8')
            (BASE / 'partial.json').write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding='utf-8')
        result['redelivery'] = None
        result['board_after'] = board()
        result['walkie'] = {'messages': walkie['messages'], 'sends': walkie['sends'], 'reads': walkie['reads']}
        result['actors'] = actors()
        result['receipts'] = receipts()
        raw = main_raw()
        result['main_transcript_has_raw_event'] = {
            'cloudevent_type': 'com.ciel.walkie.support.message' in raw,
            'event_texts': [label for label, text_, _ in EVENTS if text_ in raw]}
        result['main_finished_before_end'] = main_finished()
        result['main_rows'] = [r for r in main_rows() if r['type'] == 'user' or r['tools'] or 'DONE' in r['text']][:40]
        show_thread('10-walkie-thread')
        capture(console, '11-final')
        result['outcome'] = 'completed'
        console.key('esc', pause=1)
        console.key('esc', pause=1)
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
    print(json.dumps({'base': str(BASE), 'outcome': result['outcome'], 'turn_markers': result.get('turn_markers'),
                      'events': [{'label': e['label'], 'action': (e['receipt'] or {}).get('action'),
                                  'kind': (e['receipt'] or {}).get('kind'),
                                  'outbox': ((e['receipt'] or {}).get('outbox') or {}).get('state'),
                                  'sent_to_commit': round(((e['receipt'] or {}).get('consumed_at') or 0) - e['sent_at'], 1)}
                                 for e in result['events']],
                      'redelivery': result.get('redelivery'), 'owned_router_pids': owner}, indent=1, ensure_ascii=True))
