"""Interactive Claude Code + Ciel Work mod through an owned router: the model itself calls
work_mode_set(false), then edits without a plan, then work_mode_set(true), then is gated again.
Isolated HOME/config/ports, egress sentinel, minimized owned window, no live router touched.
Run: python -I <absolute path>. Set WORK_SWITCH_PROOF_REPO to test an installed snapshot."""
import json, os, re, socket, subprocess, sys, threading, time, uuid, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import ctypes
from ctypes import wintypes

HERE = Path(__file__).resolve().parent
SOURCE = next(p for p in HERE.parents if (p / 'ciel_runtime.py').exists())
REPO = Path(os.environ.get('WORK_SWITCH_PROOF_REPO') or SOURCE)
HELP = SOURCE / 'docs/journal/2026/10/16/features/cielairouter-direct-path/evidence/e2e'
AGENT = SOURCE / 'docs/journal/2026/10/25/fixes/teammate-model-inheritance/evidence/console_agent.py'
RUN = HERE.parent / ('run-' + ('installed-' if os.environ.get('WORK_SWITCH_PROOF_REPO') else 'source-') + time.strftime('%H%M%S')); RUN.mkdir()
PREFIXES = ('CIEL_RUNTIME_', 'CLAUDE', 'ANTHROPIC', 'CODEX', 'OPENAI')
STRIPPED = sorted(k for k in os.environ if k.upper().startswith(PREFIXES))
for k in STRIPPED: os.environ.pop(k)
SCR = Path('C:/Users/djlov/AppData/Local/Temp') / ('work-switch-' + uuid.uuid4().hex[:8])
HOME, CFG, CCFG, WS = [SCR / x for x in ('home', 'cfg', 'ccfg', 'ws')]
for d in (HOME / 'AppData/Roaming', HOME / 'AppData/Local/Temp', CFG, CCFG, WS): d.mkdir(parents=True, exist_ok=True)
SUBJECT = WS / 'subject.txt'; SUBJECT.write_text('BEFORE\n', encoding='utf-8')
CLI = Path('C:/Users/djlov/AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe')
MOD = REPO / 'ciel_runtime_support/claude_mods/ciel-work'
ROWS, EGRESS, OWNED = [], [], []
RESULT = {'repo': str(REPO), 'stripped_env': STRIPPED, 'scratch': str(SCR)}
TITLE = 'CIELE2E-WORKSWITCH-' + RUN.name
USER32 = ctypes.windll.user32
TAG = re.compile(r'E2EWMS-(PROBE-OFF|PROBE-ON|OFF|ON)\b')
INSTRUCTION_MARK = 'Ciel Work mode is hybrid per-item planning and execution'


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
    try: return json.loads(subprocess.run(['powershell', '-NoProfile', '-Command', '[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); Get-CimInstance Win32_Process | Select ProcessId,Name,CommandLine | ConvertTo-Json -Compress'], capture_output=True, text=True, encoding='utf-8', errors='replace').stdout)
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
def router_tool_names():
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {}}).encode()
    request = urllib.request.Request(f'http://127.0.0.1:{ROUTER}/ca/mcp', data=body, headers={'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=5) as r: text = r.read().decode('utf-8', 'replace')
        payload = json.loads(text[text.index('{'):]) if text.lstrip().startswith(('{', 'event', 'data')) else {}
        return sorted(t['name'] for t in payload.get('result', {}).get('tools', []) if t['name'].startswith('work_'))
    except Exception as e: return ['error: ' + type(e).__name__]
def work_mode():
    try: return json.loads((CFG / 'config.json').read_text(encoding='utf-8')).get('work_mode', '<unset>')
    except Exception as e: return 'unreadable: ' + type(e).__name__


class Sentinel(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def deny(self):
        EGRESS.append({'method': self.command, 'path': self.path}); self.send_response(403); self.send_header('content-length', '0'); self.end_headers()
    do_CONNECT = do_GET = do_POST = do_HEAD = deny


def blocks(message):
    content = message.get('content')
    return [{'type': 'text', 'text': content}] if isinstance(content, str) else [x for x in content or [] if isinstance(x, dict)]


class Fake(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *a): pass
    def sendj(self, o):
        b = json.dumps(o).encode(); self.send_response(200); self.send_header('content-type', 'application/json'); self.send_header('content-length', str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self): return self.sendj({'object': 'list', 'data': [{'id': 'owned-switch', 'object': 'model', 'context_length': 200000, 'max_output_tokens': 4096}]})
    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get('content-length') or 0)) or b'{}'
        b = json.loads(raw)
        if 'count_tokens' in self.path: return self.sendj({'input_tokens': 100})
        tools = [t.get('name') for t in b.get('tools') or [] if isinstance(t, dict)]
        users = [m for m in b.get('messages', []) if m.get('role') == 'user']
        tagged = [i for i, m in enumerate(users) if any(TAG.search(x.get('text', '')) for x in blocks(m) if x.get('type') == 'text')]
        tag = None
        if tools and tagged:
            last = users[tagged[-1]]
            tag = [TAG.search(x['text']).group(1) for x in blocks(last) if x.get('type') == 'text' and TAG.search(x.get('text', ''))][-1]
        results = [x for m in users[tagged[-1]:] for x in blocks(m) if x.get('type') == 'tool_result'] if tag else []
        stage = len(results)
        # The hook context arrives as a later message of the same turn, so read from the turn's prompt onwards.
        messages = b.get('messages', [])
        turn = messages[next(i for i, m in enumerate(messages) if m is users[tagged[-1]]):] if tag else []
        row = {'n': len(ROWS) + 1, 'tag': tag, 'stage': stage, 'tools_offered': [t for t in tools if 'work_' in t],
               'work_instructions_in_turn_prompt': bool(tag) and INSTRUCTION_MARK in json.dumps(turn),
               'last_tool_result': results[-1] if results else None, 'config_work_mode': work_mode()}
        content = [{'type': 'text', 'text': 'AUX-OK'}]; reason = 'end_turn'
        def tool(name, inp):
            nonlocal content, reason
            matches = [t for t in tools if t == name or t.endswith('__' + name)]
            if len(matches) != 1:
                row['schema_error'] = {'requested': name, 'matches': matches}; content = [{'type': 'text', 'text': 'E2EWMS-SCHEMA-MISSING-' + name}]; return
            row['served_tool'] = {'name': matches[0], 'input': inp}
            content = [{'type': 'tool_use', 'id': 'toolu_s' + str(row['n']), 'name': matches[0], 'input': inp}]; reason = 'tool_use'
        def done(): content[0] = {'type': 'text', 'text': 'E2EWMS-' + tag + '-DONE'}
        if row['n'] > 30: content = [{'type': 'text', 'text': 'E2EWMS-CALL-BOUND'}]
        elif tag == 'OFF': tool('work_mode_set', {'enabled': False}) if stage == 0 else done()
        elif tag == 'ON': tool('work_mode_set', {'enabled': True}) if stage == 0 else done()
        elif tag == 'PROBE-OFF':
            if stage == 0: tool('Read', {'file_path': str(SUBJECT)})
            elif stage == 1: tool('Edit', {'file_path': str(SUBJECT), 'old_string': 'BEFORE', 'new_string': 'AFTER-OFF'})
            else: done()
        elif tag == 'PROBE-ON':
            if stage == 0: tool('Edit', {'file_path': str(SUBJECT), 'old_string': 'AFTER-OFF', 'new_string': 'AFTER-ON'})
            else: done()
        # Raw requests are not kept: they carried the user's private global CLAUDE.md text.
        row['response'] = content; ROWS.append(row); dump('requests-summary.json', ROWS)
        msg = {'id': 'msg_' + uuid.uuid4().hex[:10], 'type': 'message', 'role': 'assistant', 'model': 'owned-switch', 'content': content, 'stop_reason': reason, 'stop_sequence': None, 'usage': {'input_tokens': 100, 'output_tokens': 10}}
        if not b.get('stream'): return self.sendj(msg)
        events = [('message_start', {'type': 'message_start', 'message': {**msg, 'content': [], 'stop_reason': None}})]
        for i, c in enumerate(content):
            empty = {'type': 'tool_use', 'id': c['id'], 'name': c['name'], 'input': {}} if c['type'] == 'tool_use' else {'type': 'text', 'text': ''}
            delta = {'type': 'input_json_delta', 'partial_json': json.dumps(c['input'])} if c['type'] == 'tool_use' else {'type': 'text_delta', 'text': c['text']}
            events += [('content_block_start', {'type': 'content_block_start', 'index': i, 'content_block': empty}),
                       ('content_block_delta', {'type': 'content_block_delta', 'index': i, 'delta': delta}),
                       ('content_block_stop', {'type': 'content_block_stop', 'index': i})]
        events += [('message_delta', {'type': 'message_delta', 'delta': {'stop_reason': reason, 'stop_sequence': None}, 'usage': {'output_tokens': 10}}), ('message_stop', {'type': 'message_stop'})]
        self.send_response(200); self.send_header('content-type', 'text/event-stream'); self.send_header('connection', 'close'); self.end_headers()
        try:
            for k, v in events: self.wfile.write(('event: ' + k + chr(10) + 'data: ' + json.dumps(v) + chr(10) * 2).encode()); self.wfile.flush(); time.sleep(.02)
        except OSError: pass
        self.close_connection = True


GATE, SENT, ROUTER = port(), port(), port()
SERVERS = []
for number, handler in ((GATE, Fake), (SENT, Sentinel)):
    server = ThreadingHTTPServer(('127.0.0.1', number), handler); threading.Thread(target=server.serve_forever, daemon=True).start(); SERVERS.append(server)
BASE = os.environ.copy()
BASE.update({'HOME': str(HOME), 'USERPROFILE': str(HOME), 'APPDATA': str(HOME / 'AppData/Roaming'), 'LOCALAPPDATA': str(HOME / 'AppData/Local'), 'TEMP': str(HOME / 'AppData/Local/Temp'), 'TMP': str(HOME / 'AppData/Local/Temp'), 'XDG_CONFIG_HOME': str(HOME / '.config'), 'CIEL_RUNTIME_CONFIG_DIR': str(CFG), 'CIEL_RUNTIME_TEST_ISOLATED': '1', 'CIEL_RUNTIME_ROUTER_PORT': str(ROUTER), 'CIEL_RUNTIME_LOG_LEVEL': 'INFO', 'CLAUDE_CONFIG_DIR': str(CCFG), 'CODEX_HOME': str(SCR / 'codex'), 'HTTP_PROXY': f'http://127.0.0.1:{SENT}', 'HTTPS_PROXY': f'http://127.0.0.1:{SENT}', 'ALL_PROXY': f'http://127.0.0.1:{SENT}', 'NO_PROXY': '127.0.0.1,localhost,::1', 'DISABLE_AUTOUPDATER': '1', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1', 'DISABLE_TELEMETRY': '1', 'ENABLE_TOOL_SEARCH': 'false'})
pcfg = {'base_url': f'http://127.0.0.1:{GATE}', 'api_key': 'owned-dummy', 'current_model': 'owned-switch', 'native_compat': True, 'gateway_selection': 'off', 'context_window': 200000, 'max_output_tokens': 4096}
(CFG / 'config.json').write_text(json.dumps({'current_provider': 'cielairouter', 'language': 'en', 'work_mode': True, 'providers': {'cielairouter': pcfg}}), encoding='utf-8')
ENV = {**BASE, 'ANTHROPIC_BASE_URL': f'http://127.0.0.1:{GATE}', 'ANTHROPIC_API_KEY': '', 'ANTHROPIC_AUTH_TOKEN': 'owned-dummy', 'ANTHROPIC_MODEL': 'owned-switch', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'owned-switch', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'owned-switch', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'owned-switch'}
(CCFG / '.claude.json').write_text(json.dumps({'hasCompletedOnboarding': True, 'lastOnboardingVersion': '2.1.310', 'numStartups': 3, 'bypassPermissionsModeAccepted': True, 'projects': {k: {'hasTrustDialogAccepted': True, 'allowedTools': []} for k in (str(WS), WS.as_posix(), str(WS).lower(), WS.as_posix().lower())}}), encoding='utf-8')
MCP = RUN / 'mcp.json'; MCP.write_text(json.dumps({'mcpServers': {'ciel-runtime-router': {'type': 'http', 'url': f'http://127.0.0.1:{ROUTER}/ca/mcp'}}}), encoding='utf-8')
sys.path.insert(0, str(REPO))
from ciel_runtime_support.statusline_script import STATUSLINE_SCRIPT  # noqa: E402
STATUS = RUN / 'statusline.py'; STATUS.write_text(STATUSLINE_SCRIPT, encoding='utf-8')
SETTINGS = RUN / 'settings.json'; SETTINGS.write_text(json.dumps({'statusLine': {'type': 'command', 'command': subprocess.list2cmdline([sys.executable, str(STATUS)])}}), encoding='utf-8')
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
    lines += ['cd /d "' + str(WS) + '"', subprocess.list2cmdline([str(CLI), '--settings', str(SETTINGS), '--plugin-dir', str(MOD), '--mcp-config', str(MCP), '--strict-mcp-config', '--dangerously-skip-permissions'])]
    launch.write_text(chr(10).join(lines) + chr(10), encoding='utf-8')
    subprocess.Popen(['wt.exe', '-w', 'new', '--size', '150,42', '--title', TITLE, '--suppressApplicationTitle', 'cmd', '/k', str(launch)])
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
    agent = subprocess.Popen([sys.executable, '-I', str(AGENT), str(cmdpid), str(RUN)], creationflags=8); OWNED.append(agent.pid)
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
    def turn(tag, prompt, label):
        command(prompt)
        ok = wait(lambda: any(r['tag'] == tag and r['response'][0].get('text') == 'E2EWMS-' + tag + '-DONE' for r in ROWS), 60, tag + ' turn')
        time.sleep(2.5); screenshot(label)
        return ok
    if not wait(lambda: 'shift+tab' in screen() or '? for shortcuts' in screen(), 45, 'interactive ready'): raise RuntimeError('interactive not ready')
    time.sleep(2); screenshot('01-work-on-start')
    RESULT['config_start'] = work_mode()
    RESULT['turns'] = {'off': turn('OFF', 'E2EWMS-OFF turn Work mode off yourself.', '02-agent-turned-off')}
    RESULT['config_after_off'] = work_mode()
    RESULT['router_work_tools_while_off'] = router_tool_names()
    RESULT['turns']['probe_off'] = turn('PROBE-OFF', 'E2EWMS-PROBE-OFF edit subject.txt without a plan.', '03-edit-allowed-while-off')
    RESULT['subject_after_probe_off'] = SUBJECT.read_text(encoding='utf-8').strip()
    RESULT['turns']['on'] = turn('ON', 'E2EWMS-ON turn Work mode back on yourself.', '04-agent-turned-on')
    RESULT['config_after_on'] = work_mode()
    RESULT['router_work_tools_while_on'] = router_tool_names()
    RESULT['turns']['probe_on'] = turn('PROBE-ON', 'E2EWMS-PROBE-ON edit subject.txt again without a plan.', '05-edit-gated-again')
    RESULT['subject_after_probe_on'] = SUBJECT.read_text(encoding='utf-8').strip()
    (RUN / 'stop-console').write_text('stop'); time.sleep(.5)
    stop(cmdpid)
    if agent.poll() is None: stop(agent.pid)
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
    RESULT['egress'] = EGRESS


def first(tag, stage):
    return next((r for r in ROWS if r['tag'] == tag and r['stage'] == stage), {})
def result_text(row):
    value = (row.get('last_tool_result') or {}).get('content')
    return json.dumps(value, ensure_ascii=False) if value is not None else ''
off0, off1, poff2, on0, on1, pon0, pon1 = (first('OFF', 0), first('OFF', 1), first('PROBE-OFF', 2), first('ON', 0), first('ON', 1),
                                           first('PROBE-ON', 0), first('PROBE-ON', 1))
RESULT['checks'] = {
    'no_error': 'error' not in RESULT,
    'work_on_before_off': RESULT.get('config_start') is True and off0.get('work_instructions_in_turn_prompt') is True,
    'agent_called_work_mode_set_off': (off0.get('served_tool') or {}).get('input') == {'enabled': False},
    'off_result_reported': 'now off' in result_text(off1),
    'config_off': RESULT.get('config_after_off') is False,
    'no_work_prompt_while_off': first('PROBE-OFF', 0).get('work_instructions_in_turn_prompt') is False,
    'edit_allowed_while_off': RESULT.get('subject_after_probe_off') == 'AFTER-OFF' and bool(poff2),
    'router_lists_only_switch_while_off': RESULT.get('router_work_tools_while_off') == ['work_mode_set'],
    'router_lists_all_work_tools_when_on': 'work_plan_set' in RESULT.get('router_work_tools_while_on', []),
    'agent_called_work_mode_set_on': (on0.get('served_tool') or {}).get('input') == {'enabled': True},
    'on_result_reported': 'now on' in result_text(on1),
    'config_on': RESULT.get('config_after_on') is True,
    'work_prompt_back_when_on': pon0.get('work_instructions_in_turn_prompt') is True,
    'edit_gated_again': RESULT.get('subject_after_probe_on') == 'AFTER-OFF' and 'planning' in result_text(pon1),
    # Claude Code's own background CONNECTs may appear; the sentinel refuses every one (403).
    'egress_refused_cli_hosts_only': all(e['method'] == 'CONNECT' and e['path'] in ('downloads.claude.ai:443', 'github.com:443') for e in EGRESS),
    'live_unchanged': BEFORE == AFTER,
}
RESULT['rows'] = ROWS
dump('result.json', RESULT); log('evidence ' + str(RUN))
print(json.dumps({k: v for k, v in RESULT.items() if k not in ('rows', 'stripped_env')}, ensure_ascii=False, indent=1))
raise SystemExit(0 if all(RESULT['checks'].values()) else 1)
