"""Real ciel-runtime launcher taking the Windows self-update restart path in an owned minimized Windows Terminal.
It starts the way an npm install does (`node <package>/npm-bin/ciel-runtime.js --ca-runtime claude`, then
`py -3`, then Python) with isolated HOME/config/ports, an egress sentinel and a stand-in npm that reports a newer
release. The updater restarts the launcher as a waiting child; the check is whether that child's managed router and
CLI start. --mode auto uses the automatic port selection, --mode explicit CIEL_RUNTIME_ROUTER_PORT.
SELF_UPDATE_PACKAGE selects an installed npm package directory (read-only); otherwise the source tree is copied into
an isolated node_modules/@one-ciel-ai/ciel-runtime layout."""
import argparse, json, os, shutil, socket, subprocess, sys, threading, time, uuid, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import ctypes
from ctypes import wintypes

A = argparse.ArgumentParser(); A.add_argument('--cli', default='C:/Users/djlov/.local/bin/claude.exe'); A.add_argument('--mode', choices=('auto', 'explicit'), default='auto'); A.add_argument('--runtime', choices=('claude', 'codex'), default='claude'); args = A.parse_args()
HERE = Path(__file__).resolve().parent
SOURCE = next(p for p in HERE.parents if (p / 'ciel_runtime.py').exists())
INSTALLED = os.environ.get('SELF_UPDATE_PACKAGE')
HELP = SOURCE / 'docs/journal/2026/10/16/features/cielairouter-direct-path/evidence/e2e'
RUN = HERE.parent / ('update-' + ('installed-' if INSTALLED else 'source-') + args.mode + ('-codex' if args.runtime == 'codex' else '') + '-' + time.strftime('%H%M%S')); RUN.mkdir()
PREFIXES = ('CIEL_RUNTIME_', 'CLAUDE', 'ANTHROPIC', 'CODEX', 'OPENAI')
STRIPPED = sorted(k for k in os.environ if k.upper().startswith(PREFIXES))
for k in STRIPPED: os.environ.pop(k)
SCR = Path('C:/Users/djlov/AppData/Local/Temp') / ('selfupd-' + uuid.uuid4().hex[:8])
HOME, CFG, CCFG, WS, NPM = [SCR / x for x in ('home', 'cfg', 'ccfg', 'ws', 'npm')]
for d in (HOME / 'AppData/Roaming', HOME / 'AppData/Local/Temp', CFG, CCFG, WS, NPM): d.mkdir(parents=True, exist_ok=True)
if INSTALLED:
    REPO = Path(INSTALLED)
else:
    # package_root_from_installed_path only recognises a real (not junctioned) node_modules path.
    REPO = SCR / 'prefix/node_modules/@one-ciel-ai/ciel-runtime'
    skip = shutil.ignore_patterns('__pycache__', '*.pyc')
    for name in ('ciel_runtime_support', 'npm-bin', 'scripts'):
        shutil.copytree(SOURCE / name, REPO / name, ignore=skip)
    for name in ('ciel_runtime.py', 'ciel-runtime-menu.py', 'ciel-runtime-tool-guard.py', 'package.json'):
        shutil.copy2(SOURCE / name, REPO / name)
NPM_LOG = RUN / 'npm-calls.jsonl'
(NPM / 'npm.cmd').write_text('@echo off' + chr(10) + subprocess.list2cmdline([sys.executable, '-I', str(HERE / 'fake_npm.py')]) + ' %*' + chr(10), encoding='utf-8')
CLI = Path(args.cli)
EGRESS, OWNED = [], []
RESULT = {'repo': str(REPO), 'mode': args.mode, 'runtime': args.runtime, 'stripped_env': STRIPPED, 'scratch': str(SCR), 'cli': str(CLI)}
TITLE = 'CIELE2E-UPDATE-' + RUN.name
USER32 = ctypes.windll.user32


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
    try: return json.loads(subprocess.run(['powershell', '-NoProfile', '-Command', 'Get-CimInstance Win32_Process | Select ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress'], capture_output=True, text=True).stdout)
    except Exception: return []
def tree(root):
    rows = procs(); kids = {}
    for x in rows: kids.setdefault(x.get('ParentProcessId'), []).append(x)
    out, todo = [], [(root, 0)]
    while todo:
        pid, depth = todo.pop()
        for x in kids.get(pid, []):
            out.append({'depth': depth + 1, 'pid': x['ProcessId'], 'ppid': pid, 'name': x.get('Name'), 'cmd': (x.get('CommandLine') or '')[:300]})
            todo.append((x['ProcessId'], depth + 1))
    return out
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
    def do_GET(self): return self.sendj({'object': 'list', 'data': [{'id': 'owned-send', 'object': 'model', 'owned_by': 'stub', 'context_length': 200000, 'max_output_tokens': 4096, 'max_model_len': 200000, 'capabilities': {'tool_calling': True, 'reasoning': True, 'thinking': True, 'supportsThinking': True}}]})
    def do_POST(self):
        b = json.loads(self.rfile.read(int(self.headers.get('content-length') or 0)) or b'{}')
        if 'count_tokens' in self.path: return self.sendj({'input_tokens': 100})
        msg = {'id': 'msg_' + uuid.uuid4().hex[:10], 'type': 'message', 'role': 'assistant', 'model': 'owned-send', 'content': [{'type': 'text', 'text': 'UPDATE-OK'}], 'stop_reason': 'end_turn', 'stop_sequence': None, 'usage': {'input_tokens': 100, 'output_tokens': 10}}
        if not b.get('stream'): return self.sendj(msg)
        events = [('message_start', {'type': 'message_start', 'message': {**msg, 'content': [], 'stop_reason': None}}), ('content_block_start', {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}}),
                  ('content_block_delta', {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': 'UPDATE-OK'}}), ('content_block_stop', {'type': 'content_block_stop', 'index': 0}),
                  ('message_delta', {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None}, 'usage': {'output_tokens': 10}}), ('message_stop', {'type': 'message_stop'})]
        self.send_response(200); self.send_header('content-type', 'text/event-stream'); self.send_header('connection', 'close'); self.end_headers()
        for k, v in events: self.wfile.write(('event: ' + k + chr(10) + 'data: ' + json.dumps(v) + chr(10) * 2).encode()); self.wfile.flush()
        self.close_connection = True
GATE, SENT, ROUTER = port(), port(), port()
SERVERS = []
for number, handler in ((GATE, Fake), (SENT, Sentinel)):
    server = ThreadingHTTPServer(('127.0.0.1', number), handler); threading.Thread(target=server.serve_forever, daemon=True).start(); SERVERS.append(server)
BASE = os.environ.copy()
BASE.update({'HOME': str(HOME), 'USERPROFILE': str(HOME), 'APPDATA': str(HOME / 'AppData/Roaming'), 'LOCALAPPDATA': str(HOME / 'AppData/Local'), 'TEMP': str(HOME / 'AppData/Local/Temp'), 'TMP': str(HOME / 'AppData/Local/Temp'), 'XDG_CONFIG_HOME': str(HOME / '.config'), 'CIEL_RUNTIME_CONFIG_DIR': str(CFG), 'CIEL_RUNTIME_TEST_ISOLATED': '1', 'CIEL_RUNTIME_NPM_MODE': 'cli', 'CIEL_RUNTIME_LOG_LEVEL': 'INFO', 'CLAUDE_CONFIG_DIR': str(CCFG), 'CODEX_HOME': str(SCR / 'codex'), 'HTTP_PROXY': f'http://127.0.0.1:{SENT}', 'HTTPS_PROXY': f'http://127.0.0.1:{SENT}', 'ALL_PROXY': f'http://127.0.0.1:{SENT}', 'NO_PROXY': '127.0.0.1,localhost,::1', 'DISABLE_AUTOUPDATER': '1', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1', 'DISABLE_TELEMETRY': '1', 'ENABLE_TOOL_SEARCH': 'false', 'FAKE_NPM_LOG': str(NPM_LOG), 'PYTHONDONTWRITEBYTECODE': '1'})
config = {'current_provider': 'cielairouter', 'language': 'en', 'work_mode': True, 'providers': {'cielairouter': {'base_url': f'http://127.0.0.1:{GATE}', 'api_key': 'owned-dummy', 'current_model': 'owned-send', 'native_compat': True, 'gateway_selection': 'off', 'context_window': 200000, 'max_output_tokens': 4096}}}
if args.mode == 'explicit':
    BASE['CIEL_RUNTIME_ROUTER_PORT'] = str(ROUTER)
else:
    # Automatic selection scans from this base, away from the live routers' range.
    config['web_backend'] = {'port': ROUTER, 'enabled': False}
(CFG / 'config.json').write_text(json.dumps(config), encoding='utf-8')
(CCFG / '.claude.json').write_text(json.dumps({'hasCompletedOnboarding': True, 'lastOnboardingVersion': '2.1.310', 'numStartups': 3, 'bypassPermissionsModeAccepted': True, 'projects': {k: {'hasTrustDialogAccepted': True, 'allowedTools': []} for k in (str(WS), WS.as_posix(), str(WS).lower(), WS.as_posix().lower())}}), encoding='utf-8')
RESULT['cli_version'] = subprocess.run([str(CLI), '--version'], env=BASE, capture_output=True, text=True).stdout.strip()

BEFORE = health(); dump('live-before.json', BEFORE)
hwnd = None
try:
    launch = SCR / 'interactive.cmd'
    lines = ['@echo off']
    for prefix in PREFIXES: lines.append('for /f "delims==" %%v in (' + chr(39) + 'set ' + prefix + ' 2^>nul' + chr(39) + ') do set "%%v="')
    keep = ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP', 'XDG_CONFIG_HOME', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'DISABLE_AUTOUPDATER', 'DISABLE_TELEMETRY', 'ENABLE_TOOL_SEARCH', 'FAKE_NPM_LOG', 'PYTHONDONTWRITEBYTECODE')
    lines += ['set "' + k + '=' + str(v) + '"' for k, v in BASE.items() if k.upper().startswith(PREFIXES) or k in keep]
    lines += ['set "PATH=' + str(NPM) + ';%PATH%"', 'set "CIEL_RUNTIME_STARTUP_AUTO_ANSWER=1"', 'set CLAUDECODE=', 'cd /d "' + str(WS) + '"',
              # The npm bin shim; no --ca-no-self-update-check, so this launch takes the self-update path.
              subprocess.list2cmdline(['node', str(REPO / 'npm-bin' / 'ciel-runtime.js'), '--ca-runtime', args.runtime, '--ca-no-update-check']), 'echo LAUNCHER-EXIT=%ERRORLEVEL%']
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
    agent = subprocess.Popen([sys.executable, '-I', str(SOURCE / 'docs/journal/2026/10/27/diagnostics/subagent-powershell/evidence/scripts/console_agent.py'), str(cmdpid), str(RUN)], creationflags=8); OWNED.append(agent.pid)
    def screen():
        try: return (RUN / 'screen-latest.txt').read_text(encoding='utf-8')
        except OSError: return ''
    def command(text=None, key=None):
        (RUN / 'command-sent.json').unlink(missing_ok=True)
        (RUN / 'console-command.tmp').write_text(json.dumps({'key': key} if key else {'text': text}), encoding='utf-8'); (RUN / 'console-command.tmp').replace(RUN / 'console-command.json')
        if not wait(lambda: (RUN / 'command-sent.json').exists(), 10, 'console command'): raise RuntimeError('console not responding')
    def screenshot(label):
        (RUN / ('screen-' + label + '.txt')).write_text(screen(), encoding='utf-8')
        if hwnd: USER32.ShowWindow(hwnd, 4); time.sleep(1.2)  # SW_SHOWNOACTIVATE
        shot = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(HELP / 'shot_title.ps1'), TITLE, str(RUN / (label + '.png'))], capture_output=True, text=True, timeout=30)
        with (RUN / 'screenshot-log.txt').open('a', encoding='utf-8') as f: f.write(label + chr(10) + shot.stdout + shot.stderr)
        if hwnd: USER32.ShowWindow(hwnd, 7)  # SW_SHOWMINNOACTIVE
    def ready():
        text = screen()
        if args.runtime == 'claude': return 'shift+tab' in text or '? for shortcuts' in text
        return not any(t in text for t in ('esc close', 'esc back', 'esc skip')) and ('context left' in text or '? for shortcuts' in text)
    def codex_ready():
        time.sleep(6)
        for _ in range(6):
            if not any(t in screen() for t in ('esc close', 'esc back', 'esc skip')): break
            command(key='esc'); time.sleep(1)
        return wait(ready, 30, 'Codex main prompt ready')
    def router_port():
        try:
            records = json.loads((CFG / 'workspace-router-ports.json').read_text(encoding='utf-8'))['workspaces']
            return next(iter(records.values()))
        except Exception: return None
    checks = {}
    checks['restart_announced'] = wait(lambda: 'Restarting with the new version' in screen(), 60, 'self-update restart announced')
    settled = wait(lambda: ready() or (args.runtime == 'codex' and 'OpenAI Codex' in screen()) or 'LAUNCHER-EXIT=' in screen() or 'already reserved' in screen(), 90, 'launch settled')
    if args.runtime == 'codex' and 'OpenAI Codex' in screen(): codex_ready()
    time.sleep(2)
    record = router_port() or {}
    RESULT['registry_record'] = record
    RESULT['process_tree'] = tree(cmdpid)
    dump('process-tree.json', RESULT['process_tree'])
    port_used = int(record.get('port') or 0)
    RESULT['router_port'] = port_used
    checks['cli_ready'] = ready()
    checks['router_up'] = bool(port_used and http(f'http://127.0.0.1:{port_used}/health').get('ok'))
    launchers = [x for x in RESULT['process_tree'] if str(x['name']).lower().startswith('python') and 'ciel_runtime.py' in x['cmd'] and ' cli ' in x['cmd'] + ' ']
    pids = {x['pid'] for x in launchers}
    updater = next((x for x in launchers if x['ppid'] not in pids), None)
    restarted = next((x for x in launchers if updater and x['ppid'] == updater['pid']), None)
    RESULT['updater_pid'] = updater and updater['pid']; RESULT['restarted_pid'] = restarted and restarted['pid']
    checks['restarted_is_child_of_updater'] = bool(updater and restarted)
    checks['lease_moved_to_restarted_launcher'] = bool(restarted and (record.get('launch_lease') or {}).get('pid') == restarted['pid'])
    logs = {str(p.relative_to(CFG)): p.read_text(encoding='utf-8', errors='replace') for p in CFG.rglob('*.log')}
    reserved = [name for name, text in logs.items() if 'launch already reserved' in text]
    RESULT['logs_with_reserved_error'] = reserved
    checks['no_reserved_error'] = 'already reserved' not in screen() and not reserved
    screenshot('01-after-self-update')
    if checks['cli_ready']:
        command('/exit')
        checks['launcher_exit'] = wait(lambda: 'LAUNCHER-EXIT=' in screen(), 45, 'launcher exited')
        def port_closed():
            try:
                with socket.create_connection(('127.0.0.1', port_used), timeout=.25): return False
            except OSError: return True
        checks['router_down_after_exit'] = wait(port_closed, 25, 'router down after exit')
    else:
        checks['launcher_exit'] = wait(lambda: 'LAUNCHER-EXIT=' in screen(), 60, 'launcher exited')
    screenshot('02-exit')
    try: RESULT['npm_calls'] = [json.loads(l) for l in NPM_LOG.read_text(encoding='utf-8').splitlines()]
    except OSError: RESULT['npm_calls'] = []
    checks['npm_view_and_install'] = any(c[:1] == ['view'] for c in RESULT['npm_calls']) and any('install' in c for c in RESULT['npm_calls'])
    RESULT['checks'] = checks
    if not all(checks.values()): RESULT['verification_failed'] = [k for k, v in checks.items() if not v]
    (RUN / 'stop-console').write_text('stop'); time.sleep(.5)
    stop(cmdpid)
    if agent.poll() is None: stop(agent.pid)
    RESULT['egress'] = EGRESS
except Exception as e:
    RESULT['error'] = type(e).__name__ + ': ' + str(e)
    for pid in list(OWNED):
        try: stop(pid)
        except Exception: pass
finally:
    if hwnd: time.sleep(1); USER32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE the owned window
    for server in SERVERS: server.shutdown()
    for path in CFG.rglob('router.log'):
        (RUN / ('router-log-' + path.parent.name + '.txt')).write_text(path.read_text(encoding='utf-8', errors='replace')[-20000:], encoding='utf-8')
    AFTER = health(); dump('live-after.json', AFTER); RESULT['live_before'] = BEFORE; RESULT['live_after'] = AFTER
    dump('result.json', RESULT); log('evidence ' + str(RUN))
    print(json.dumps({k: v for k, v in RESULT.items() if k not in ('stripped_env', 'process_tree')}, ensure_ascii=False, indent=1))
if RESULT.get('error') or RESULT.get('verification_failed'): raise SystemExit(1)
