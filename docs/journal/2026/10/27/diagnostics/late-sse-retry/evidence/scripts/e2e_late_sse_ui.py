"""Interactive Claude Code through an owned Ciel router: a CielAiRouter-style HTTP 200 stream whose
first meaningful event is a delayed aggregate-timeout SSE error. Baseline shows the API error; the fix
retries before any output. Isolated HOME/config/ports, egress sentinel, minimized owned window.
Run: python -I <absolute path> [--delay S]. Set LATE_SSE_PROOF_REPO to test an installed snapshot."""
import argparse, json, os, socket, subprocess, sys, threading, time, uuid, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import ctypes
from ctypes import wintypes

A = argparse.ArgumentParser(); A.add_argument('--delay', type=float, default=20.0); args = A.parse_args()
HERE = Path(__file__).resolve().parent
SOURCE = next(p for p in HERE.parents if (p / 'ciel_runtime.py').exists())
REPO = Path(os.environ.get('LATE_SSE_PROOF_REPO') or SOURCE)
HELP = SOURCE / 'docs/journal/2026/10/16/features/cielairouter-direct-path/evidence/e2e'
RUN = HERE / ('ui-' + ('installed-' if os.environ.get('LATE_SSE_PROOF_REPO') else 'source-') + time.strftime('%H%M%S')); RUN.mkdir()
PREFIXES = ('CIEL_RUNTIME_', 'CLAUDE', 'ANTHROPIC', 'CODEX', 'OPENAI')
STRIPPED = sorted(k for k in os.environ if k.upper().startswith(PREFIXES))
for k in STRIPPED: os.environ.pop(k)
SCR = Path('C:/Users/djlov/AppData/Local/Temp') / ('late-sse-ui-' + uuid.uuid4().hex[:8])
HOME, CFG, CCFG, WS = [SCR / x for x in ('home', 'cfg', 'ccfg', 'ws')]
for d in (HOME / 'AppData/Roaming', HOME / 'AppData/Local/Temp', CFG, CCFG, WS): d.mkdir(parents=True, exist_ok=True)
CLI = Path('C:/Users/djlov/AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe')
ROWS, EGRESS, OWNED = [], [], []
RESULT = {'repo': str(REPO), 'stripped_env': STRIPPED, 'scratch': str(SCR), 'delay': args.delay}
TITLE = 'CIELE2E-LATESSE-' + RUN.name
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


class Fake(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *a): pass
    def sendj(self, o):
        b = json.dumps(o).encode(); self.send_response(200); self.send_header('content-type', 'application/json'); self.send_header('content-length', str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self): return self.sendj({'object': 'list', 'data': [{'id': 'owned-send', 'object': 'model', 'context_length': 200000, 'max_output_tokens': 4096}]})
    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get('content-length') or 0)) or b'{}'
        b = json.loads(raw)
        if 'count_tokens' in self.path: return self.sendj({'input_tokens': 100})
        main = 'LATE-SSE-MAIN' in json.dumps(b) and bool(b.get('tools'))  # the conversation turn, not the title request
        ROWS.append({'n': len(ROWS) + 1, 'main': main, 'stream': bool(b.get('stream')), 'bytes': len(raw), 'at': time.time()}); dump('requests-summary.json', ROWS)
        attempt = sum(1 for r in ROWS if r['main'])
        if main and attempt == 1:
            self.send_response(200); self.send_header('content-type', 'text/event-stream'); self.send_header('connection', 'close'); self.end_headers()
            self.wfile.write(b': upstream accepted' + bytes([10, 10])); self.wfile.flush()
            end = time.time() + args.delay
            while time.time() < end:
                time.sleep(min(10, max(0, end - time.time())))
                if time.time() < end: self.wfile.write(b'event: ping' + bytes([10]) + b'data: {"type":"ping"}' + bytes([10, 10])); self.wfile.flush()
            payload = {'type': 'error', 'error': {'type': 'api_error', 'message': ERROR_TEXT}}
            self.wfile.write(b'event: error' + bytes([10]) + b'data: ' + json.dumps(payload).encode() + bytes([10, 10])); self.wfile.flush()
            self.close_connection = True; return
        text = 'LATE-SSE-RETRY-SUCCEEDED' if main else 'AUX-OK'
        msg = {'id': 'msg_' + uuid.uuid4().hex[:10], 'type': 'message', 'role': 'assistant', 'model': 'owned-send', 'content': [{'type': 'text', 'text': text}], 'stop_reason': 'end_turn', 'stop_sequence': None, 'usage': {'input_tokens': 100, 'output_tokens': 10}}
        if not b.get('stream'): return self.sendj(msg)
        events = [('message_start', {'type': 'message_start', 'message': {**msg, 'content': [], 'stop_reason': None}}),
                  ('content_block_start', {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}}),
                  ('content_block_delta', {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': text}}),
                  ('content_block_stop', {'type': 'content_block_stop', 'index': 0}),
                  ('message_delta', {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None}, 'usage': {'output_tokens': 10}}),
                  ('message_stop', {'type': 'message_stop'})]
        self.send_response(200); self.send_header('content-type', 'text/event-stream'); self.send_header('connection', 'close'); self.end_headers()
        try:
            for k, v in events: self.wfile.write(('event: ' + k + chr(10) + 'data: ' + json.dumps(v) + chr(10) * 2).encode()); self.wfile.flush(); time.sleep(.025)
        except OSError: pass
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
ENV = {**BASE, **POLICY, 'ANTHROPIC_BASE_URL': f'http://127.0.0.1:{ROUTER}', 'ANTHROPIC_API_KEY': '', 'ANTHROPIC_AUTH_TOKEN': 'owned-dummy', 'ANTHROPIC_MODEL': 'owned-send', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'owned-send', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'owned-send', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'owned-send'}
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
    command('LATE-SSE-MAIN answer once.')
    outcome = wait(lambda: 'LATE-SSE-RETRY-SUCCEEDED' in screen() or 'API Error' in screen() or 'timed out (HTTP 504)' in screen(), 60 + 3 * args.delay, 'turn outcome')
    time.sleep(2)
    screenshot('01-outcome')
    s = screen()
    mains = [r for r in ROWS if r['main']]
    rlog = (CFG / 'router.log').read_text(encoding='utf-8', errors='replace') if (CFG / 'router.log').exists() else ''
    keys = ('late_sse_', 'anthropic_sse_upstream_error_event', 'upstream_direct_request')
    (RUN / 'router-retry-lines.txt').write_text(chr(10).join(l for l in rlog.splitlines() if any(k in l for k in keys)), encoding='utf-8')
    native = (RUN / 'native-debug.log').read_text(encoding='utf-8', errors='replace') if (RUN / 'native-debug.log').exists() else ''
    RESULT['observed'] = {'turn_outcome_seen': outcome, 'success_on_screen': 'LATE-SSE-RETRY-SUCCEEDED' in s,
                          'api_error_on_screen': 'API Error' in s or 'timed out (HTTP 504)' in s,
                          'main_upstream_requests': len(mains), 'same_payload_size': len({r['bytes'] for r in mains}) == 1,
                          'retry_logged': 'cielairouter_late_sse_retry' in rlog, 'cli_nonstreaming_fallback': 'falling back to non-streaming' in native}
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
