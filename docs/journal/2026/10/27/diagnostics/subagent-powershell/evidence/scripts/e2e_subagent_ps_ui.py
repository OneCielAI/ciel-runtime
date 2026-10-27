"""Interactive Claude Code through an owned Ciel router (isolated env, minimized owned window): the main
agent runs PowerShell, then a general-purpose subagent runs PowerShell; records offered tools and results.
Run: python -I <absolute path> [--cli PATH] [--ps-env V]. SUBAGENT_PS_REPO selects an installed snapshot."""
import argparse, json, os, socket, subprocess, sys, threading, time, uuid, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import ctypes
from ctypes import wintypes

A = argparse.ArgumentParser(); A.add_argument('--cli', default='C:/Users/djlov/.local/bin/claude.exe'); A.add_argument('--ps-env', default=None); args = A.parse_args()
HERE = Path(__file__).resolve().parent
SOURCE = next(p for p in HERE.parents if (p / 'ciel_runtime.py').exists())
REPO = Path(os.environ.get('SUBAGENT_PS_REPO') or SOURCE)
HELP = SOURCE / 'docs/journal/2026/10/16/features/cielairouter-direct-path/evidence/e2e'
RUN = HERE / ('ui-' + ('installed-' if os.environ.get('SUBAGENT_PS_REPO') else 'source-') + ('ps' + args.ps_env + '-' if args.ps_env is not None else '') + time.strftime('%H%M%S')); RUN.mkdir()
PREFIXES = ('CIEL_RUNTIME_', 'CLAUDE', 'ANTHROPIC', 'CODEX', 'OPENAI')
STRIPPED = sorted(k for k in os.environ if k.upper().startswith(PREFIXES))
for k in STRIPPED: os.environ.pop(k)
SCR = Path('C:/Users/djlov/AppData/Local/Temp') / ('subps-ui-' + uuid.uuid4().hex[:8])
HOME, CFG, CCFG, WS = [SCR / x for x in ('home', 'cfg', 'ccfg', 'ws')]
for d in (HOME / 'AppData/Roaming', HOME / 'AppData/Local/Temp', CFG, CCFG, WS): d.mkdir(parents=True, exist_ok=True)
CLI = Path(args.cli)
ROWS, EGRESS, OWNED = [], [], []
RESULT = {'repo': str(REPO), 'stripped_env': STRIPPED, 'scratch': str(SCR), 'cli': str(CLI), 'ps_env': args.ps_env}
TITLE = 'CIELE2E-SUBPS-' + RUN.name
USER32 = ctypes.windll.user32
ERROR_TEXT = 'codex/gpt-6.1-sol-medium: provider \u2014 Model codex/gpt-6.1-sol-medium timed out (HTTP 504); claude/claude-opus-5-5: provider \u2014 Model claude/claude-opus-5-5 timed out (HTTP 504)'


def dump(n, o): (RUN / n).write_text(json.dumps(o, ensure_ascii=False, indent=1), encoding='utf-8')
def log(s):
    print(s, flush=True)
    with (RUN / 'driver.txt').open('a', encoding='utf-8') as f: f.write(s + chr(10))
def port():
    with socket.socket() as s: s.bind(('127.0.0.1', 0)); return s.getsockname()[1]
def http(url):
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=3) as r: return json.loads(r.read())
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


def results(body):
    out = []
    for m in body.get('messages') or []:
        for b in (m.get('content') if isinstance(m.get('content'), list) else []):
            if b.get('type') == 'tool_result':
                c = b.get('content'); out.append({'error': bool(b.get('is_error')), 'text': (c if isinstance(c, str) else json.dumps(c))[:400]})
    return out
class Fake(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *a): pass
    def sendj(self, o):
        b = json.dumps(o).encode(); self.send_response(200); self.send_header('content-type', 'application/json'); self.send_header('content-length', str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self): return self.sendj({'object': 'list', 'data': [{'id': 'owned-send', 'object': 'model', 'context_length': 200000, 'max_output_tokens': 4096}]})
    def do_POST(self):
        b = json.loads(self.rfile.read(int(self.headers.get('content-length') or 0)) or b'{}')
        if 'count_tokens' in self.path: return self.sendj({'input_tokens': 100})
        text = json.dumps(b); names = [t.get('name') for t in b.get('tools') or []]; done = results(b)
        role = 'main' if 'SUBAGENT-PS-MAIN' in text else ('child' if 'SUBAGENT-PS-CHILD' in text else 'aux')
        row = {'n': len(ROWS) + 1, 'role': role, 'tools_count': len(names), 'has_powershell': 'PowerShell' in names, 'has_bash': 'Bash' in names,
               'agent_tool': next((x for x in ('Agent', 'Task') if x in names), None), 'results': done}
        ROWS.append(row); dump('requests.json', ROWS)
        if role == 'main' and len(done) == 0:
            content = [{'type': 'tool_use', 'id': 'toolu_mainps', 'name': 'PowerShell', 'input': {'command': 'Write-Output ("PS-MAIN-OK-" + (6*7))', 'description': 'main powershell'}}]
        elif role == 'main' and len(done) == 1:
            content = [{'type': 'tool_use', 'id': 'toolu_task', 'name': row['agent_tool'] or 'Agent', 'input': {'subagent_type': 'general-purpose', 'description': 'run powershell', 'prompt': 'SUBAGENT-PS-CHILD: run one PowerShell command.'}}]
        elif role == 'child' and len(done) == 0:
            content = [{'type': 'tool_use', 'id': 'toolu_childps', 'name': 'PowerShell', 'input': {'command': 'Write-Output ("PS-CHILD-OK-" + (6*7))', 'description': 'child powershell'}}]
        else:
            content = [{'type': 'text', 'text': role.upper() + '-DONE'}]
        reason = 'tool_use' if content[0]['type'] == 'tool_use' else 'end_turn'
        msg = {'id': 'msg_' + uuid.uuid4().hex[:10], 'type': 'message', 'role': 'assistant', 'model': 'owned-send', 'content': content, 'stop_reason': reason, 'stop_sequence': None, 'usage': {'input_tokens': 100, 'output_tokens': 10}}
        if not b.get('stream'): return self.sendj(msg)
        c = content[0]
        start = {'type': 'tool_use', 'id': c['id'], 'name': c['name'], 'input': {}} if c['type'] == 'tool_use' else {'type': 'text', 'text': ''}
        delta = {'type': 'input_json_delta', 'partial_json': json.dumps(c['input'])} if c['type'] == 'tool_use' else {'type': 'text_delta', 'text': c['text']}
        events = [('message_start', {'type': 'message_start', 'message': {**msg, 'content': [], 'stop_reason': None}}), ('content_block_start', {'type': 'content_block_start', 'index': 0, 'content_block': start}),
                  ('content_block_delta', {'type': 'content_block_delta', 'index': 0, 'delta': delta}), ('content_block_stop', {'type': 'content_block_stop', 'index': 0}),
                  ('message_delta', {'type': 'message_delta', 'delta': {'stop_reason': reason, 'stop_sequence': None}, 'usage': {'output_tokens': 10}}), ('message_stop', {'type': 'message_stop'})]
        self.send_response(200); self.send_header('content-type', 'text/event-stream'); self.send_header('connection', 'close'); self.end_headers()
        for k, v in events: self.wfile.write(('event: ' + k + chr(10) + 'data: ' + json.dumps(v) + chr(10) * 2).encode()); self.wfile.flush()
        self.close_connection = True
GATE, SENT, ROUTER = port(), port(), port()
SERVERS = []
for number, handler in ((GATE, Fake), (SENT, Sentinel)):
    server = ThreadingHTTPServer(('127.0.0.1', number), handler); threading.Thread(target=server.serve_forever, daemon=True).start(); SERVERS.append(server)
BASE = os.environ.copy()
BASE.update({'HOME': str(HOME), 'USERPROFILE': str(HOME), 'APPDATA': str(HOME / 'AppData/Roaming'), 'LOCALAPPDATA': str(HOME / 'AppData/Local'), 'TEMP': str(HOME / 'AppData/Local/Temp'), 'TMP': str(HOME / 'AppData/Local/Temp'), 'XDG_CONFIG_HOME': str(HOME / '.config'), 'CIEL_RUNTIME_CONFIG_DIR': str(CFG), 'CIEL_RUNTIME_TEST_ISOLATED': '1', 'CIEL_RUNTIME_ROUTER_PORT': str(ROUTER), 'CIEL_RUNTIME_LOG_LEVEL': 'INFO', 'CLAUDE_CONFIG_DIR': str(CCFG), 'CODEX_HOME': str(SCR / 'codex'), 'HTTP_PROXY': f'http://127.0.0.1:{SENT}', 'HTTPS_PROXY': f'http://127.0.0.1:{SENT}', 'ALL_PROXY': f'http://127.0.0.1:{SENT}', 'NO_PROXY': '127.0.0.1,localhost,::1', 'DISABLE_AUTOUPDATER': '1', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1', 'DISABLE_TELEMETRY': '1', 'ENABLE_TOOL_SEARCH': 'false'})
pcfg = {'base_url': f'http://127.0.0.1:{GATE}', 'api_key': 'owned-dummy', 'current_model': 'owned-send', 'native_compat': True, 'gateway_selection': 'off', 'context_window': 200000, 'max_output_tokens': 4096}
(CFG / 'config.json').write_text(json.dumps({'current_provider': 'cielairouter', 'language': 'en', 'work_mode': False, 'providers': {'cielairouter': pcfg}}), encoding='utf-8')
# Routed cielairouter launch policy (claude_environment.py): the router is the only retry owner.
POLICY = {'CLAUDE_CODE_MAX_RETRIES': '0', 'CLAUDE_CODE_RETRY_WATCHDOG': '0', 'CLAUDE_CODE_NONSTREAMING_TIMEOUT_RETRIES': '0', 'CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK': '1'}
_policy = (REPO / 'ciel_runtime_support' / 'claude_environment.py').read_text(encoding='utf-8')
assert all(f'"{k}": "{v}"' in _policy for k, v in POLICY.items() if k != 'CLAUDE_CODE_MAX_RETRIES'), 'launch policy changed'
ENV = {**BASE, **POLICY, **({'CLAUDE_CODE_USE_POWERSHELL_TOOL': args.ps_env} if args.ps_env is not None else {}), 'CIEL_RUNTIME_BYPASS_PERMISSIONS': '1', 'CLAUDE_CODE_SUBAGENT_MODEL': 'owned-send', 'ANTHROPIC_BASE_URL': f'http://127.0.0.1:{ROUTER}', 'ANTHROPIC_API_KEY': '', 'ANTHROPIC_AUTH_TOKEN': 'owned-dummy', 'ANTHROPIC_MODEL': 'owned-send', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'owned-send', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'owned-send', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'owned-send'}
(CCFG / '.claude.json').write_text(json.dumps({'hasCompletedOnboarding': True, 'lastOnboardingVersion': '2.1.310', 'numStartups': 3, 'bypassPermissionsModeAccepted': True, 'projects': {k: {'hasTrustDialogAccepted': True, 'allowedTools': []} for k in (str(WS), WS.as_posix(), str(WS).lower(), WS.as_posix().lower())}}), encoding='utf-8')
RESULT['cli_version'] = subprocess.run([str(CLI), '--version'], env=ENV, capture_output=True, text=True).stdout.strip()

BEFORE = health(); dump('live-before.json', BEFORE)
router = None; hwnd = None
try:
    router = subprocess.Popen([sys.executable, '-I', '-c', "import sys,runpy;sys.path.insert(0,sys.argv[1]);sys.argv=[sys.argv[1]+'/ciel_runtime.py','serve'];runpy.run_path(sys.argv[0],run_name='__main__')", str(REPO)], env=BASE, cwd=WS, stdout=(RUN / 'router-out.txt').open('wb'), stderr=subprocess.STDOUT); OWNED.append(router.pid)
    if not wait(lambda: http(f'http://127.0.0.1:{ROUTER}/health').get('ok'), 40, 'owned router'): raise RuntimeError('router unavailable')
    launch = SCR / 'interactive.cmd'
    lines = ['@echo off']
    for prefix in PREFIXES: lines.append('for /f "delims==" %%v in (' + chr(39) + 'set ' + prefix + ' 2^>nul' + chr(39) + ') do set "%%v="')
    keep = ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP', 'XDG_CONFIG_HOME', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'DISABLE_AUTOUPDATER', 'DISABLE_TELEMETRY', 'ENABLE_TOOL_SEARCH')
    lines += ['set "' + k + '=' + str(v) + '"' for k, v in ENV.items() if k.upper().startswith(PREFIXES) or k in keep]
    lines += ['cd /d "' + str(WS) + '"', subprocess.list2cmdline([str(CLI), '--debug-file', str(RUN / 'native-debug.log'), '--dangerously-skip-permissions'])]
    launch.write_text(chr(10).join(lines) + chr(10), encoding='utf-8')
    subprocess.Popen(['wt.exe', '-w', 'new', '--size', '150,40', '--title', TITLE, '--suppressApplicationTitle', 'cmd', '/k', str(launch)])
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
    if wait(window, 15, 'owned window'): USER32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
    agent = subprocess.Popen([sys.executable, '-I', str(HERE / 'console_agent.py'), str(cmdpid), str(RUN)], creationflags=8); OWNED.append(agent.pid)
    def screen():
        try: return (RUN / 'screen-latest.txt').read_text(encoding='utf-8')
        except OSError: return ''
    def command(text):
        (RUN / 'command-sent.json').unlink(missing_ok=True)
        (RUN / 'console-command.tmp').write_text(json.dumps({'text': text}), encoding='utf-8'); (RUN / 'console-command.tmp').replace(RUN / 'console-command.json')
        if not wait(lambda: (RUN / 'command-sent.json').exists(), 10, 'console command'): raise RuntimeError('console not responding')
    def screenshot(label):
        (RUN / ('screen-' + label + '.txt')).write_text(screen(), encoding='utf-8')
        if hwnd: USER32.ShowWindow(hwnd, 4); time.sleep(1.2)  # SW_SHOWNOACTIVATE
        shot = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(HELP / 'shot_title.ps1'), TITLE, str(RUN / (label + '.png'))], capture_output=True, text=True, timeout=30)
        with (RUN / 'screenshot-log.txt').open('a', encoding='utf-8') as f: f.write(label + chr(10) + shot.stdout + shot.stderr)
        if hwnd: USER32.ShowWindow(hwnd, 7)  # SW_SHOWMINNOACTIVE
    if not wait(lambda: 'shift+tab' in screen() or '? for shortcuts' in screen(), 45, 'interactive ready'): raise RuntimeError('interactive not ready')
    command('SUBAGENT-PS-MAIN: run PowerShell, then delegate to a subagent.')
    outcome = wait(lambda: 'MAIN-DONE' in screen(), 120, 'turn outcome')
    time.sleep(2)
    screenshot('01-outcome')
    mains = [r for r in ROWS if r['role'] == 'main']; kids = [r for r in ROWS if r['role'] == 'child']
    RESULT['observed'] = {'turn_done': outcome, 'main_offered_powershell': bool(mains) and mains[0]['has_powershell'], 'child_requests': len(kids),
                          'child_offered_powershell': bool(kids) and kids[0]['has_powershell'], 'child_offered_bash': bool(kids) and kids[0]['has_bash'],
                          'main_ps_result': mains[1]['results'][0] if len(mains) > 1 and mains[1]['results'] else None,
                          'child_ps_result': kids[1]['results'][0] if len(kids) > 1 and kids[1]['results'] else None}
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
    print(json.dumps({k: v for k, v in RESULT.items() if k not in ('rows', 'stripped_env')}, ensure_ascii=False, indent=1))
if RESULT.get('error'): raise SystemExit(1)
