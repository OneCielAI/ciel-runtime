"""Real Claude CLI against an owned loopback gateway and router, isolated environment: Work mode usage hints.
Run: python -I <absolute path>. Set WORK_PROOF_REPO to test an installed snapshot instead of the worktree."""
import json, os, re, socket, subprocess, sys, threading, time, uuid, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = next(p for p in HERE.parents if (p / 'ciel_runtime.py').exists())
REPO = Path(os.environ.get('WORK_PROOF_REPO') or SOURCE)
NAV = SOURCE / 'docs/journal/2026/10/26/features/work-mode/evidence/navigation'
HELP = SOURCE / 'docs/journal/2026/10/16/features/cielairouter-direct-path/evidence/e2e'
RUN = Path('C:/Users/djlov/AppData/Local/Temp') / ('work-shell-proof-' + uuid.uuid4().hex[:8]); RUN.mkdir()
PREFIXES = ('CIEL_RUNTIME_', 'CLAUDE', 'ANTHROPIC', 'CODEX', 'OPENAI')
STRIPPED = sorted(k for k in os.environ if k.upper().startswith(PREFIXES))
for k in STRIPPED: os.environ.pop(k)
HOME, CFG, CCFG, WS = [RUN / x for x in ('home', 'cfg', 'ccfg', 'ws')]
for d in (HOME / 'AppData/Roaming', HOME / 'AppData/Local/Temp', CFG, CCFG, WS): d.mkdir(parents=True, exist_ok=True)
TARGET = WS / 'subject.txt'; TARGET.write_text('BEFORE\n', encoding='utf-8')
CLI = Path('C:/Users/djlov/AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe')
ROWS, EGRESS, STATE, OWNED, RESULT = [], [], {}, [], {'repo': str(REPO), 'stripped_env': STRIPPED}
TITLE = 'CIELE2E-SHELL-' + RUN.name
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


PLAN = {'goal': '1. Hint check', 'ref': 'REQ-101', 'children': [
    {'title': 'Parser', 'ref': 'BR-1', 'children': [{'title': 'Tokenize input', 'ref': 'TEST-1'}, {'title': 'Verify tokens', 'waiting_on': 'CI gate'}]},
    {'title': 'Cache', 'children': [{'title': 'Verify expiry'}]}]}
HOLD = threading.Event()
URGENT_HOLD = threading.Event()
FIRST_HOLD = threading.Event()
SECOND = {'title': '2. Second request', 'children': [{'title': 'Phase', 'children': [{'title': 'Step'}]}]}
LEAVES = ('Tokenize input', 'Verify tokens', 'Verify expiry')
def leaf_id(title):
    for p in CFG.rglob('work-plan.json'):
        for n in json.loads(p.read_text(encoding='utf-8')).get('nodes', []):
            if n.get('title') == title: return n['id']
    return None


class Fake(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *a): pass
    def sendj(self, o):
        b = json.dumps(o).encode(); self.send_response(200); self.send_header('content-type', 'application/json'); self.send_header('content-length', str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self): return self.sendj({'object': 'list', 'data': [{'id': 'fake-work', 'object': 'model', 'context_window': 128000, 'max_tokens': 4096}]})
    def do_POST(self):
        b = json.loads(self.rfile.read(int(self.headers.get('content-length') or 0)) or '{}')
        if 'count_tokens' in self.path: return self.sendj({'input_tokens': 100})
        tools = {t.get('name'): t for t in b.get('tools') or [] if isinstance(t, dict)}
        users = [m for m in b.get('messages', []) if m.get('role') == 'user']
        texts = ' '.join(m['content'] if isinstance(m.get('content'), str) else ' '.join(x.get('text', '') for x in m.get('content', []) if isinstance(x, dict) and x.get('type') == 'text') for m in users)
        # Title and suggestion requests repeat the prompt without tools; only the main loop carries tools.
        tag = ('queue' if 'E2EHINT-QUEUE' in texts else 'main' if 'E2EHINT-MAIN' in texts else 'aux') if tools else 'aux'
        n = STATE.get(tag, 0); STATE[tag] = n + 1; number = len(ROWS) + 1
        body = json.dumps(b)
        plan_tool = next((t for k, t in tools.items() if k.endswith('work_plan_set')), None)
        row = {'n': number, 'tag': tag, 'stage': n, 'tool_names': list(tools),
               'context_number_hint': 'Triage every new user request' in body and 'its item number where possible' in body,
               'plan_tool_number_hint': bool(plan_tool and 'its item number' in plan_tool.get('description', '')
                                             and 'queued behind it' in plan_tool.get('description', '')),
               'notice_sent_to_model': 'Items show above the prompt' in body,
               'prior_results': [x for m in users if isinstance(m.get('content'), list) for x in m['content'] if isinstance(x, dict) and x.get('type') == 'tool_result'][-2:]}
        content = [{'type': 'text', 'text': 'E2EHINT-AUX-OK'}]; reason = 'end_turn'
        def tool(name, inp):
            nonlocal content, reason
            matches = [k for k in tools if k == name or k.endswith('__' + name)]
            if len(matches) != 1: row['schema_error'] = {'requested': name, 'matches': matches}; content = [{'type': 'text', 'text': 'E2EHINT-SCHEMA-MISSING'}]; return
            content = [{'type': 'tool_use', 'id': f'toolu_h{number}', 'name': matches[0], 'input': inp}]; reason = 'tool_use'; row['served_tool'] = matches[0]
        if tag == 'main':
            if n == 0: tool('Bash', {'command': 'printf WORK-BASH-OK > bash-marker.txt', 'description': 'Write disposable Bash proof marker'})
            elif n == 1: tool('Bash', {'command': "powershell.exe -NoProfile -Command \"[IO.File]::WriteAllText('powershell-marker.txt','WORK-POWERSHELL-OK')\"", 'description': 'Write disposable PowerShell proof marker'})
            elif n == 2: tool('Write', {'file_path': str(TARGET), 'content': 'ILLEGAL-DIRECT-WRITE'})
            else: content = [{'type': 'text', 'text': 'WORK-SHELL-PROOF-END'}]
        row['response'] = content; ROWS.append(row); dump('requests-summary.json', ROWS)
        msg = {'id': 'msg_' + uuid.uuid4().hex[:10], 'type': 'message', 'role': 'assistant', 'model': b.get('model'), 'content': content, 'stop_reason': reason, 'stop_sequence': None, 'usage': {'input_tokens': 100, 'output_tokens': 10}}
        if not b.get('stream'): return self.sendj(msg)
        events = [('message_start', {'type': 'message_start', 'message': {**msg, 'content': [], 'stop_reason': None}})]
        for i, c in enumerate(content):
            empty = {'type': 'tool_use', 'id': c['id'], 'name': c['name'], 'input': {}} if c['type'] == 'tool_use' else {'type': 'text', 'text': ''}
            delta = {'type': 'input_json_delta', 'partial_json': json.dumps(c['input'])} if c['type'] == 'tool_use' else {'type': 'text_delta', 'text': c['text']}
            events += [('content_block_start', {'type': 'content_block_start', 'index': i, 'content_block': empty}), ('content_block_delta', {'type': 'content_block_delta', 'index': i, 'delta': delta}), ('content_block_stop', {'type': 'content_block_stop', 'index': i})]
        events += [('message_delta', {'type': 'message_delta', 'delta': {'stop_reason': reason, 'stop_sequence': None}, 'usage': {'output_tokens': 10}}), ('message_stop', {'type': 'message_stop'})]
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
(CCFG / '.claude.json').write_text(json.dumps({'hasCompletedOnboarding': True, 'lastOnboardingVersion': '2.1.310', 'numStartups': 3, 'bypassPermissionsModeAccepted': True, 'projects': {k: {'hasTrustDialogAccepted': True, 'allowedTools': []} for k in (str(WS), WS.as_posix(), str(WS).lower(), WS.as_posix().lower())}}), encoding='utf-8')
ENV = {**BASE, 'CIEL_RUNTIME_WORKSPACE_STATE_DIR': str(CFG), 'ANTHROPIC_BASE_URL': f'http://127.0.0.1:{GATE}', 'ANTHROPIC_API_KEY': '', 'ANTHROPIC_AUTH_TOKEN': 'e2e-key', 'ANTHROPIC_MODEL': 'fake-work', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'fake-work', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'fake-work', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'fake-work'}
MCP = RUN / 'mcp.json'; MCP.write_text(json.dumps({'mcpServers': {'ciel-runtime-router': {'type': 'http', 'url': f'http://127.0.0.1:{ROUTER}/ca/mcp'}}}), encoding='utf-8')
MOD = REPO / 'ciel_runtime_support/claude_mods/ciel-work'
RESULT['mod'] = str(MOD); RESULT['cli_version'] = subprocess.run([str(CLI), '--version'], env=ENV, capture_output=True, text=True).stdout.strip()
BEFORE = health(); dump('live-before.json', BEFORE)
router = None; hwnd = None
try:
    router = subprocess.Popen([sys.executable, '-I', '-c', "import sys,runpy;sys.path.insert(0,sys.argv[1]);sys.argv=[sys.argv[1]+'/ciel_runtime.py','serve'];runpy.run_path(sys.argv[0],run_name='__main__')", str(REPO)], env=BASE, cwd=WS, stdout=(RUN / 'router-out.txt').open('wb'), stderr=subprocess.STDOUT); OWNED.append(router.pid)
    if not wait(lambda: http(f'http://127.0.0.1:{ROUTER}/health').get('ok'), 40, 'owned router'): raise RuntimeError('router unavailable')
    common = [str(CLI), '--debug-file', str(RUN / 'native-debug.log'), *(['--permission-mode', 'default', '--allowedTools', 'Bash', '--allowedTools', 'mcp__ciel-runtime-router__work_status'] if os.environ.get('SHELL_PROOF_MODE') == 'default' else ['--dangerously-skip-permissions']), '--plugin-dir', str(MOD), '--mcp-config', str(MCP), '--strict-mcp-config']
    launch = RUN / 'interactive.cmd'
    lines = ['@echo off']
    for prefix in PREFIXES: lines.append('for /f "delims==" %%v in (' + chr(39) + 'set ' + prefix + ' 2^>nul' + chr(39) + ') do set "%%v="')
    lines += ['set "' + k + '=' + str(v) + '"' for k, v in ENV.items() if k.upper().startswith(PREFIXES) or k in ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP', 'XDG_CONFIG_HOME', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'DISABLE_AUTOUPDATER', 'DISABLE_TELEMETRY', 'ENABLE_TOOL_SEARCH')]
    lines += ['cd /d "' + str(WS) + '"', subprocess.list2cmdline([*common, '--session-id', SESSION])]
    launch.write_text(chr(10).join(lines) + chr(10), encoding='utf-8')
    subprocess.Popen(['wt.exe', '-w', 'new', '--title', TITLE, '--suppressApplicationTitle', 'cmd', '/k', str(launch)])
    cmdpid = 0
    def findcmd():
        global cmdpid
        for x in procs():
            if str(x.get('Name', '')).lower() == 'cmd.exe' and str(launch) in (x.get('CommandLine') or ''): cmdpid = x['ProcessId']; return True
        return False
    if not wait(findcmd, 25, 'owned cmd'): raise RuntimeError('no owned console')
    OWNED.append(cmdpid)
    hwnd = None
    def window():
        global hwnd
        hwnd = owned_window(); return bool(hwnd)
    if wait(window, 15, 'owned window'): USER32.ShowWindow(hwnd, 6)  # SW_MINIMIZE: focus returns to the previous window
    agent = subprocess.Popen([sys.executable, '-I', str(NAV / 'console_agent.py'), str(cmdpid), str(RUN)], creationflags=8); OWNED.append(agent.pid)
    def screen():
        try: return (RUN / 'screen-latest.txt').read_text(encoding='utf-8')
        except OSError: return ''
    def prompt_line():
        rows = [r for r in screen().splitlines() if r.startswith('❯')]
        return rows[-1][1:].strip() if rows else ''
    def command(text=None, key=None):
        if text is not None and prompt_line():
            RESULT.setdefault('stray_input_before', []).append({'command': text, 'prompt': prompt_line()})
            raise RuntimeError('prompt not empty before typing; stray input recorded')
        q = {'key': key} if key else {'text': text}
        (RUN / 'command-sent.json').unlink(missing_ok=True)
        (RUN / 'console-command.tmp').write_text(json.dumps(q), encoding='utf-8'); (RUN / 'console-command.tmp').replace(RUN / 'console-command.json')
        if not wait(lambda: (RUN / 'command-sent.json').exists(), 10, 'console command'): raise RuntimeError('console not responding')
    def screenshot(label):
        (RUN / ('screen-' + label + '.txt')).write_text(screen(), encoding='utf-8')
        if hwnd: USER32.ShowWindow(hwnd, 4); time.sleep(1.2)  # SW_SHOWNOACTIVATE
        shot = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(HELP / 'shot_title.ps1'), TITLE, str(RUN / (label + '.png'))], capture_output=True, text=True, timeout=30)
        with (RUN / 'screenshot-log.txt').open('a', encoding='utf-8') as f: f.write(label + chr(10) + shot.stdout + shot.stderr)
        if hwnd: USER32.ShowWindow(hwnd, 7)  # SW_SHOWMINNOACTIVE
    checks = {}
    if not wait(lambda: 'shift+tab' in screen() or '? for shortcuts' in screen(), 45, 'interactive ready'): raise RuntimeError('interactive not ready')
    command('E2EHINT-MAIN execute disposable shell proof while Work is planning.')
    checks['turn_ended'] = wait(lambda: 'WORK-SHELL-PROOF-END' in screen(), 55, 'shell proof ended')
    time.sleep(2)
    board = http('http://127.0.0.1:' + str(ROUTER) + '/ca/work?session_id=claude:' + SESSION)
    dump('board.json', board)
    checks['work_still_planning'] = board.get('phase') == 'planning'
    checks['direct_write_blocked'] = TARGET.read_text(encoding='utf-8') == 'BEFORE' + chr(10)
    expected = os.environ.get('SHELL_PROOF_EXPECT') != 'blocked'
    for name, text in [('bash-marker.txt', 'WORK-BASH-OK'), ('powershell-marker.txt', 'WORK-POWERSHELL-OK')]:
        q = WS / name
        checks[name] = (q.exists() and q.read_text(encoding='utf-8') == text) if expected else not q.exists()
    checks['native_Bash_exposed'] = any('Bash' in r.get('tool_names', []) for r in ROWS)
    RESULT['native_PowerShell_exposed'] = any('PowerShell' in r.get('tool_names', []) for r in ROWS)
    RESULT['permission_mode'] = os.environ.get('SHELL_PROOF_MODE', 'bypass')
    RESULT['expected'] = 'executed' if expected else 'blocked'
    RESULT['checks'] = checks
    screenshot('01-shell-execution' if expected else '01-shell-blocked-baseline')
    if not all(checks.values()): RESULT['verification_failed'] = [name for name, ok in checks.items() if not ok]
    (RUN / 'stop-console').write_text('stop'); time.sleep(.5)
    stop(cmdpid)
    if agent.poll() is None: stop(agent.pid)
    RESULT['rows'] = ROWS; RESULT['egress'] = EGRESS
except Exception as e:
    HOLD.set(); URGENT_HOLD.set(); FIRST_HOLD.set()
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
    print(json.dumps({k: v for k, v in RESULT.items() if k not in ('rows',)}, indent=1))

if RESULT.get('error') or RESULT.get('verification_failed'): raise SystemExit(1)
