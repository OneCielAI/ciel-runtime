"""Real Claude CLI with the Ciel status line, isolated environment: waiting/blocked Work items in the status line.
Run: python -I <absolute path>. Set WORK_PROOF_REPO to test an installed snapshot instead of the worktree."""
import json, os, shlex, socket, subprocess, sys, threading, time, uuid, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = next(p for p in HERE.parents if (p / 'ciel_runtime.py').exists())
REPO = Path(os.environ.get('WORK_PROOF_REPO') or SOURCE)
NAV = SOURCE / 'docs/journal/2026/10/26/features/work-mode/evidence/navigation'
HELP = SOURCE / 'docs/journal/2026/10/16/features/cielairouter-direct-path/evidence/e2e'
RUN = Path('C:/Users/djlov/AppData/Local/Temp') / ('work-focus-ui-' + uuid.uuid4().hex[:8]); RUN.mkdir()
PREFIXES = ('CIEL_RUNTIME_', 'CLAUDE', 'ANTHROPIC', 'CODEX', 'OPENAI')
STRIPPED = sorted(k for k in os.environ if k.upper().startswith(PREFIXES))
for k in STRIPPED: os.environ.pop(k)
HOME, CFG, CCFG, WS = [RUN / x for x in ('home', 'cfg', 'ccfg', 'ws')]
for d in (HOME / 'AppData/Roaming', HOME / 'AppData/Local/Temp', CFG, CCFG, WS): d.mkdir(parents=True, exist_ok=True)
CLI = Path('C:/Users/djlov/AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe')
ROWS, EGRESS, OWNED, RESULT = [], [], [], {'repo': str(REPO), 'stripped_env': STRIPPED, 'run': str(RUN)}
TITLE = 'CIELE2E-STATUS-' + RUN.name
SESSION = str(uuid.uuid4())


def dump(n, o): (RUN / n).write_text(json.dumps(o, ensure_ascii=False, indent=1), encoding='utf-8')
def log(s):
    print(s, flush=True)
    with (RUN / 'driver.txt').open('a', encoding='utf-8') as f: f.write(s + '\n')
def port():
    with socket.socket() as s: s.bind(('127.0.0.1', 0)); return s.getsockname()[1]
def http(url, body=None):
    try:
        req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None, headers={'Content-Type': 'application/json'})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=3) as r: return json.loads(r.read())
    except Exception as e: return {'error': str(e)}
def health():
    out = {}
    for p in (9505, 9524):
        h = http(f'http://127.0.0.1:{p}/health'); out[str(p)] = {k: h.get(k) for k in ('pid', 'version', 'source_fingerprint', 'error')}
    return out
def procs():
    try: return json.loads(subprocess.run(['powershell', '-NoProfile', '-Command', 'Get-CimInstance Win32_Process | Select ProcessId,Name,CommandLine | ConvertTo-Json -Compress'], capture_output=True, text=True).stdout)
    except Exception: return []
def wait(pred, secs, label):
    end = time.time() + secs
    while time.time() < end:
        if pred(): log(label + ': yes'); return True
        time.sleep(.25)
    log(label + ': NO'); return False
# The owned window is kept minimized so keystrokes meant for other windows cannot land in it;
# it is shown without activation only for the moment a screenshot is taken.
import ctypes
from ctypes import wintypes
USER32 = ctypes.windll.user32
def owned_window():
    found = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(h, _):
        buf = ctypes.create_unicode_buffer(512); USER32.GetWindowTextW(h, buf, 512)
        if buf.value.startswith(TITLE): found.append(h); return False
        return True
    USER32.EnumWindows(proc(visit), 0)
    return found[0] if found else None
def stop(pid):
    if pid not in OWNED: raise RuntimeError('Unowned termination refused ' + str(pid))
    r = subprocess.run(['taskkill', '/T', '/F', '/PID', str(pid)], capture_output=True, text=True); log('owned stop ' + str(pid) + ' ' + r.stdout.strip())


class Sentinel(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def deny(self):
        EGRESS.append({'method': self.command, 'path': self.path}); self.send_response(403); self.send_header('content-length', '0'); self.end_headers()
    do_CONNECT = do_GET = do_POST = do_HEAD = deny


class Fake(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *a): pass
    def sendj(self, o):
        b = json.dumps(o).encode(); self.send_response(200); self.send_header('content-type', 'application/json'); self.send_header('content-length', str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self): return self.sendj({'object': 'list', 'data': [{'id': 'fake-work', 'object': 'model', 'context_window': 128000, 'max_tokens': 4096}]})
    def do_POST(self):
        b = json.loads(self.rfile.read(int(self.headers.get('content-length') or 0)) or '{}')
        if 'count_tokens' in self.path: return self.sendj({'input_tokens': 100})
        ROWS.append({'n': len(ROWS) + 1, 'tools': len(b.get('tools') or [])}); dump('requests-summary.json', ROWS)
        content = [{'type': 'text', 'text': 'STATUSLINE-FIXTURE-IDLE'}]
        msg = {'id': 'msg_' + uuid.uuid4().hex[:10], 'type': 'message', 'role': 'assistant', 'model': b.get('model'), 'content': content, 'stop_reason': 'end_turn', 'stop_sequence': None, 'usage': {'input_tokens': 100, 'output_tokens': 10}}
        if not b.get('stream'): return self.sendj(msg)
        events = [('message_start', {'type': 'message_start', 'message': {**msg, 'content': [], 'stop_reason': None}}),
                  ('content_block_start', {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}}),
                  ('content_block_delta', {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': content[0]['text']}}),
                  ('content_block_stop', {'type': 'content_block_stop', 'index': 0}),
                  ('message_delta', {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None}, 'usage': {'output_tokens': 10}}),
                  ('message_stop', {'type': 'message_stop'})]
        self.send_response(200); self.send_header('content-type', 'text/event-stream'); self.send_header('connection', 'close'); self.end_headers()
        try:
            for k, v in events: self.wfile.write(('event: ' + k + '\ndata: ' + json.dumps(v) + '\n\n').encode()); self.wfile.flush(); time.sleep(.025)
        except OSError: pass
        self.close_connection = True


GATE, SENT, ROUTER = port(), port(), port()
SERVERS = []
for number, handler in ((GATE, Fake), (SENT, Sentinel)):
    server = ThreadingHTTPServer(('127.0.0.1', number), handler); threading.Thread(target=server.serve_forever, daemon=True).start(); SERVERS.append(server)
BASE = os.environ.copy()
BASE.update({'HOME': str(HOME), 'USERPROFILE': str(HOME), 'APPDATA': str(HOME / 'AppData/Roaming'), 'LOCALAPPDATA': str(HOME / 'AppData/Local'), 'TEMP': str(HOME / 'AppData/Local/Temp'), 'TMP': str(HOME / 'AppData/Local/Temp'), 'XDG_CONFIG_HOME': str(HOME / '.config'), 'CIEL_RUNTIME_CONFIG_DIR': str(CFG), 'CIEL_RUNTIME_TEST_ISOLATED': '1', 'CIEL_RUNTIME_ROUTER_PORT': str(ROUTER), 'CLAUDE_CONFIG_DIR': str(CCFG), 'CODEX_HOME': str(RUN / 'codex'), 'HTTP_PROXY': f'http://127.0.0.1:{SENT}', 'HTTPS_PROXY': f'http://127.0.0.1:{SENT}', 'ALL_PROXY': f'http://127.0.0.1:{SENT}', 'NO_PROXY': '127.0.0.1,localhost,::1', 'DISABLE_AUTOUPDATER': '1', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1', 'DISABLE_TELEMETRY': '1', 'ENABLE_TOOL_SEARCH': 'false'})
pcfg = {'base_url': f'http://127.0.0.1:{GATE}', 'api_key': 'e2e-key', 'current_model': 'fake-work', 'context_window': 128000, 'max_output_tokens': 4096}
(CFG / 'config.json').write_text(json.dumps({'current_provider': 'cielairouter', 'language': 'en', 'work_mode': True, 'work_auto_continue': False, 'providers': {'cielairouter': pcfg}}), encoding='utf-8')
ENV = {**BASE, 'CIEL_RUNTIME_WORKSPACE_STATE_DIR': str(CFG), 'ANTHROPIC_BASE_URL': f'http://127.0.0.1:{GATE}', 'ANTHROPIC_API_KEY': '', 'ANTHROPIC_AUTH_TOKEN': 'e2e-key', 'ANTHROPIC_MODEL': 'fake-work', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'fake-work', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'fake-work', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'fake-work'}
MCP = RUN / 'mcp.json'; MCP.write_text(json.dumps({'mcpServers': {'ciel-runtime-router': {'type': 'http', 'url': f'http://127.0.0.1:{ROUTER}/ca/mcp'}}}), encoding='utf-8')

# Status line exactly as the runtime installs it (statusline_settings.install_statusline_settings), from REPO.
sys.path.insert(0, str(REPO))
from ciel_runtime_support.statusline_script import STATUSLINE_SCRIPT
from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.work_plan_store import session_work_plan_store
SCRIPT = RUN / 'statusline.py'; SCRIPT.write_text(STATUSLINE_SCRIPT, encoding='utf-8')
STATUS_CMD = f"{shlex.quote(Path(sys.executable).as_posix())} {shlex.quote(SCRIPT.as_posix())}"
(CCFG / 'settings.json').write_text(json.dumps({'statusLine': {'type': 'command', 'command': STATUS_CMD, 'padding': 0, 'refreshInterval': 1000}}), encoding='utf-8')
(CCFG / '.claude.json').write_text(json.dumps({'hasCompletedOnboarding': True, 'lastOnboardingVersion': '2.1.310', 'numStartups': 3, 'bypassPermissionsModeAccepted': True, 'projects': {k: {'hasTrustDialogAccepted': True, 'allowedTools': []} for k in (str(WS), WS.as_posix(), str(WS).lower(), WS.as_posix().lower())}}), encoding='utf-8')

MOD = REPO / 'ciel_runtime_support/claude_mods/ciel-work'
RESULT['mod'] = str(MOD); RESULT['cli_version'] = subprocess.run([str(CLI), '--version'], env=ENV, capture_output=True, text=True).stdout.strip()
RESULT['statusline_command'] = STATUS_CMD


def fixture_plan():
    doc = tree.empty_document()
    tree.set_plan(doc, {'title': '1. STATUS-ATTENTION', 'children': [
        {'title': 'Deploy', 'children': [
            {'title': 'Reserve UI save', 'waiting_on': 'n201 source contract, incremental run and Eric workspace routing verification'},
            {'title': 'Admission check'},
            {'title': 'Run smoke'}]},
        {'title': 'Rollback', 'children': [
            {'title': 'Drain path', 'children': [
                {'title': 'Signal forwarding', 'waiting_on': 'external ingress control'},
                {'title': 'Scheduler drain'}]}]},
        {'title': 'Docs', 'children': [{'title': 'Write notes'}]}]})
    ids = {n['title']: n['id'] for n in doc['nodes']}
    tree.update_node(doc, ids['Admission check'], status='blocked', waiting_on='deploy approval from Daniel')
    tree.update_node(doc, ids['Scheduler drain'], status='blocked')
    tree.set_phase(doc, 'executing')
    tree.update_node(doc, ids['Run smoke'], status='in_progress')
    tree.add_subtree(doc, None, {'title': '2. QUEUED-WAIT', 'waiting_on': 'Daniel 검수 응답', 'children': [{'title': 'Phase', 'children': [{'title': 'Step'}]}]})
    return doc


BEFORE = health(); dump('live-before.json', BEFORE)
router = None; hwnd = None
try:
    router = subprocess.Popen([sys.executable, '-I', '-c', "import sys,runpy;sys.path.insert(0,sys.argv[1]);sys.argv=[sys.argv[1]+'/ciel_runtime.py','serve'];runpy.run_path(sys.argv[0],run_name='__main__')", str(REPO)], env=BASE, cwd=WS, stdout=(RUN / 'router-out.txt').open('wb'), stderr=subprocess.STDOUT); OWNED.append(router.pid)
    if not wait(lambda: http(f'http://127.0.0.1:{ROUTER}/health').get('ok'), 40, 'owned router'): raise RuntimeError('router unavailable')
    store = session_work_plan_store(CFG, 'claude:' + SESSION)
    doc = fixture_plan()
    store.transaction(lambda d: d.update(doc))
    dump('plan-before.json', store.read_snapshot())
    # Direct render of the same script with the same session payload, plain and ANSI.
    for label, ansi in (('direct-plain', '0'), ('direct-ansi', '1')):
        env = {**ENV, 'CIEL_RUNTIME_STATUSLINE_ANSI': ansi}
        r = subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps({'session_id': SESSION}), env=env, capture_output=True, text=True, encoding='utf-8', timeout=20)
        (RUN / (label + '.txt')).write_text(r.stdout + ('\n[stderr]\n' + r.stderr if r.stderr else ''), encoding='utf-8')
    common = [str(CLI), '--debug-file', str(RUN / 'native-debug.log'), '--dangerously-skip-permissions', '--plugin-dir', str(MOD), '--mcp-config', str(MCP), '--strict-mcp-config']
    launch = RUN / 'interactive.cmd'
    lines = ['@echo off']
    for prefix in PREFIXES: lines.append('for /f "delims==" %%v in (' + chr(39) + 'set ' + prefix + ' 2^>nul' + chr(39) + ') do set "%%v="')
    lines += ['set "' + k + '=' + str(v) + '"' for k, v in ENV.items() if k.upper().startswith(PREFIXES) or k in ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP', 'XDG_CONFIG_HOME', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'DISABLE_AUTOUPDATER', 'DISABLE_TELEMETRY', 'ENABLE_TOOL_SEARCH')]
    lines += ['cd /d "' + str(WS) + '"', subprocess.list2cmdline([*common, '--session-id', SESSION])]
    launch.write_text(chr(10).join(lines) + chr(10), encoding='utf-8')
    subprocess.Popen(['wt.exe', '-w', 'new', '--size', '150,48', '--title', TITLE, '--suppressApplicationTitle', 'cmd', '/k', str(launch)])
    cmdpid = 0
    def findcmd():
        global cmdpid
        for x in procs():
            if str(x.get('Name', '')).lower() == 'cmd.exe' and str(launch) in (x.get('CommandLine') or ''): cmdpid = x['ProcessId']; return True
        return False
    if not wait(findcmd, 25, 'owned cmd'): raise RuntimeError('no owned console')
    OWNED.append(cmdpid)
    def window():
        global hwnd
        hwnd = owned_window(); return bool(hwnd)
    if wait(window, 15, 'owned window'): USER32.ShowWindow(hwnd, 6)  # SW_MINIMIZE: focus returns to the previous window
    agent = subprocess.Popen([sys.executable, '-I', str(HERE / 'console_agent.py'), str(cmdpid), str(RUN)], creationflags=8); OWNED.append(agent.pid)
    def screen():
        try: return (RUN / 'screen-latest.txt').read_text(encoding='utf-8')
        except OSError: return ''
    def command(text=None, key=None):
        q = ({'hotkey': key} if key in ('f','c') else {'key': key}) if key else {'text': text}
        (RUN / 'command-sent.json').unlink(missing_ok=True)
        (RUN / 'console-command.tmp').write_text(json.dumps(q), encoding='utf-8'); (RUN / 'console-command.tmp').replace(RUN / 'console-command.json')
        if not wait(lambda: (RUN / 'command-sent.json').exists(), 10, 'console command'): raise RuntimeError('console not responding')
    def screenshot(label):
        (RUN / ('screen-' + label + '.txt')).write_text(screen(), encoding='utf-8')
        if hwnd: USER32.ShowWindow(hwnd, 4); time.sleep(1.2)  # SW_SHOWNOACTIVATE
        shot = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(HELP / 'shot_title.ps1'), TITLE, str(RUN / (label + '.png'))], capture_output=True, text=True, timeout=30)
        with (RUN / 'screenshot-log.txt').open('a', encoding='utf-8') as f: f.write(label + chr(10) + shot.stdout + shot.stderr)
        if hwnd: USER32.ShowWindow(hwnd, 7)  # SW_SHOWMINNOACTIVE
    if not wait(lambda: 'shift+tab' in screen() or '? for shortcuts' in screen(), 45, 'interactive ready'): raise RuntimeError('interactive not ready')
    command('STATUSLINE-MAIN show the Work status line and end the turn.')
    idle = wait(lambda: 'STATUSLINE-FIXTURE-IDLE' in screen(), 60, 'fixture idle')
    wait(lambda: 'STATUS-ATTENTION' in screen() and 'in_progress' in screen(), 20, 'status line rendered')
    time.sleep(2.5)
    screenshot('01-statusline')
    command('/work-mode view')
    checks = {'inspector':wait(lambda:'Focus only this item' in screen(),15,'focus UI visible')}
    screenshot('02-focus-control')
    command(key='f')
    checks['focus_via_ui'] = wait(lambda:bool(http(f'http://127.0.0.1:{ROUTER}/ca/work?session_id=claude:' + SESSION).get('focus',{}).get('node_ids')),15,'focus button activated')
    screenshot('03-focused')
    if checks['focus_via_ui']:
        command(key='esc');command(key='esc');command('/work-mode view')
        wait(lambda:'Clear focus / resume all' in screen(),10,'clear control visible')
        command(key='c')
        checks['clear_via_ui'] = wait(lambda:not http(f'http://127.0.0.1:{ROUTER}/ca/work?session_id=claude:' + SESSION).get('focus',{}).get('node_ids'),15,'clear button activated')
        screenshot('04-cleared')
    RESULT['checks'] = checks
    dump('plan-after.json', store.read_snapshot())
    checks['node_data_preserved'] = sorted(store.read_snapshot()['nodes'],key=lambda n:n['id']) == sorted(doc['nodes'],key=lambda n:n['id'])
    if not all(checks.values()): RESULT['verification_failed'] = [name for name, ok in checks.items() if not ok]
    (RUN / 'stop-console').write_text('stop'); time.sleep(.5)
    stop(cmdpid)
    if agent.poll() is None: stop(agent.pid)
    RESULT['rows'] = ROWS; RESULT['egress'] = EGRESS
except Exception as e:
    RESULT['error'] = type(e).__name__ + ': ' + str(e)
    for pid in [x for x in OWNED if not router or x != router.pid]:
        try: stop(pid)
        except Exception: pass
finally:
    if router and router.poll() is None: stop(router.pid)
    if hwnd: time.sleep(1); USER32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE the owned window
    for server in SERVERS: server.shutdown()
    AFTER = health(); dump('live-after.json', AFTER); RESULT['live_before'] = BEFORE; RESULT['live_after'] = AFTER
    dump('result.json', RESULT); log('evidence ' + str(RUN))
    print(json.dumps({k: v for k, v in RESULT.items() if k not in ('rows',)}, ensure_ascii=False, indent=1))

if RESULT.get("error") or RESULT.get("verification_failed"): raise SystemExit(1)
