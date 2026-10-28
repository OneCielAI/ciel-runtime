"""Real Claude Code through an owned Ciel router (Work mod, isolated env): the model sends Bash heredoc commands
of increasing length; records what the shell actually received (byte count, last line).
Run: python -I <absolute path> [--mode bypass|default]. SUBAGENT_PS_REPO selects an installed snapshot."""
import argparse, json, os, socket, subprocess, sys, threading, time, uuid, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
A = argparse.ArgumentParser(); A.add_argument('--mode', default='bypass'); A.add_argument('--ps-env', default=None, help='CLAUDE_CODE_USE_POWERSHELL_TOOL value'); A.add_argument('--cli', default='C:/Users/djlov/AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe'); A.add_argument('--live-gates', action='store_true', help='copy only the live cached feature-flag maps (not credentials) into the scratch config'); args = A.parse_args()
HERE = Path(__file__).resolve().parent
SOURCE = next(p for p in HERE.parents if (p / 'ciel_runtime.py').exists())
REPO = Path(os.environ.get('SUBAGENT_PS_REPO') or SOURCE)
PREFIXES = ('CIEL_RUNTIME_', 'CLAUDE', 'ANTHROPIC', 'CODEX', 'OPENAI')
STRIPPED = sorted(k for k in os.environ if k.upper().startswith(PREFIXES))
for k in STRIPPED: os.environ.pop(k)
RUN = HERE / ('bashlen-' + ('installed-' if os.environ.get('SUBAGENT_PS_REPO') else 'source-') + args.mode + ('-ps' + args.ps_env if args.ps_env is not None else '') + ('-livegates' if args.live_gates else '') + '-' + time.strftime('%H%M%S')); RUN.mkdir()
SCR = Path('C:/Users/djlov/AppData/Local/Temp') / ('subps-' + uuid.uuid4().hex[:8])
HOME, CFG, CCFG, WS = [SCR / x for x in ('home', 'cfg', 'ccfg', 'ws')]
for d in (HOME / 'AppData/Roaming', HOME / 'AppData/Local/Temp', CFG, CCFG, WS): d.mkdir(parents=True, exist_ok=True)
CLI = Path(args.cli)
ROWS, EGRESS = [], []
SIZES = [2000, 4000, 6000, 7000, 8000, 9000, 12000, 20000]
def dump(n, o): (RUN / n).write_text(json.dumps(o, ensure_ascii=False, indent=1), encoding='utf-8')
def port():
    with socket.socket() as s: s.bind(('127.0.0.1', 0)); return s.getsockname()[1]
def http(url):
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=3) as r: return json.loads(r.read())
    except Exception as e: return {'error': str(e)}
def health():
    return {str(p): {k: http(f'http://127.0.0.1:{p}/health').get(k) for k in ('pid', 'version', 'source_fingerprint')} for p in (9505, 9524)}
def results(body):
    out = []
    for m in body.get('messages') or []:
        for b in (m.get('content') if isinstance(m.get('content'), list) else []):
            if b.get('type') == 'tool_result':
                c = b.get('content'); out.append({'error': bool(b.get('is_error')), 'text': (c if isinstance(c, str) else json.dumps(c))[:400]})
    return out
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
        b = json.loads(self.rfile.read(int(self.headers.get('content-length') or 0)) or b'{}')
        if 'count_tokens' in self.path: return self.sendj({'input_tokens': 100})
        text = json.dumps(b); names = [t.get('name') for t in b.get('tools') or []]; done = results(b)
        role = 'main' if 'SUBAGENT-PS-MAIN' in text else ('child' if 'SUBAGENT-PS-CHILD' in text else 'aux')
        row = {'n': len(ROWS) + 1, 'role': role, 'tools_count': len(names), 'has_powershell': 'PowerShell' in names, 'has_bash': 'Bash' in names,
               'agent_tool': next((x for x in ('Agent', 'Task') if x in names), None), 'results': done}
        ROWS.append(row); dump('requests.json', ROWS)
        if role == 'main' and len(done) < len(SIZES):
            n = SIZES[len(done)]; body = ''.join('L%05d %s' % (i, "x'y" * 20) + chr(10) for i in range(n // 70 + 1))[:n].rsplit(chr(10), 1)[0]
            cmd = 'cat > probe.txt <<' + chr(39) + 'EOF' + chr(39) + chr(10) + body + chr(10) + 'EOF' + chr(10) + 'echo BYTES=$(wc -c < probe.txt) LAST=$(tail -n 1 probe.txt | cut -c1-6) EXPECTED_LAST=' + body.rsplit(chr(10), 1)[-1][:6]
            row['sent_command_chars'] = len(cmd)
            content = [{'type': 'tool_use', 'id': 'toolu_len%d' % n, 'name': 'Bash', 'input': {'command': cmd, 'description': 'length probe %d' % n}}]
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
    s = ThreadingHTTPServer(('127.0.0.1', number), handler); threading.Thread(target=s.serve_forever, daemon=True).start(); SERVERS.append(s)
BASE = os.environ.copy()
BASE.update({'HOME': str(HOME), 'USERPROFILE': str(HOME), 'APPDATA': str(HOME / 'AppData/Roaming'), 'LOCALAPPDATA': str(HOME / 'AppData/Local'), 'TEMP': str(HOME / 'AppData/Local/Temp'), 'TMP': str(HOME / 'AppData/Local/Temp'),
             'CIEL_RUNTIME_CONFIG_DIR': str(CFG), 'CIEL_RUNTIME_WORKSPACE_STATE_DIR': str(CFG), 'CIEL_RUNTIME_TEST_ISOLATED': '1', 'CIEL_RUNTIME_ROUTER_PORT': str(ROUTER), 'CIEL_RUNTIME_LOG_LEVEL': 'INFO', 'CLAUDE_CONFIG_DIR': str(CCFG),
             'HTTP_PROXY': f'http://127.0.0.1:{SENT}', 'HTTPS_PROXY': f'http://127.0.0.1:{SENT}', 'ALL_PROXY': f'http://127.0.0.1:{SENT}', 'NO_PROXY': '127.0.0.1,localhost,::1',
             'DISABLE_AUTOUPDATER': '1', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1', 'DISABLE_TELEMETRY': '1', 'ENABLE_TOOL_SEARCH': 'false'})
pcfg = {'base_url': f'http://127.0.0.1:{GATE}', 'api_key': 'owned-dummy', 'current_model': 'owned-send', 'native_compat': True, 'gateway_selection': 'off', 'context_window': 200000, 'max_output_tokens': 4096}
(CFG / 'config.json').write_text(json.dumps({'current_provider': 'cielairouter', 'language': 'en', 'work_mode': True, 'providers': {'cielairouter': pcfg}}), encoding='utf-8')
ENV = {**BASE, 'CLAUDE_CODE_MAX_RETRIES': '0', 'CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK': '1', 'ANTHROPIC_BASE_URL': f'http://127.0.0.1:{ROUTER}', 'ANTHROPIC_API_KEY': '', 'ANTHROPIC_AUTH_TOKEN': 'owned-dummy',
       'ANTHROPIC_MODEL': 'owned-send', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'owned-send', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'owned-send', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'owned-send', 'CLAUDE_CODE_SUBAGENT_MODEL': 'owned-send'}
if args.mode == 'bypass': ENV['CIEL_RUNTIME_BYPASS_PERMISSIONS'] = '1'
if args.ps_env is not None: ENV['CLAUDE_CODE_USE_POWERSHELL_TOOL'] = args.ps_env
CC_JSON = {'hasCompletedOnboarding': True, 'bypassPermissionsModeAccepted': True, 'projects': {str(WS): {'hasTrustDialogAccepted': True}}}
if args.live_gates:
    _live = json.loads((Path.home() / '.claude.json').read_text(encoding='utf-8'))
    for key in ('cachedGrowthBookFeatures', 'cachedStatsigGates', 'cachedDynamicConfigs', 'cachedExperimentFeatures'):
        if key in _live: CC_JSON[key] = _live[key]
(CCFG / '.claude.json').write_text(json.dumps(CC_JSON), encoding='utf-8')
MCP = SCR / 'mcp.json'; MCP.write_text(json.dumps({'mcpServers': {'ciel-runtime-router': {'type': 'http', 'url': f'http://127.0.0.1:{ROUTER}/ca/mcp'}}}), encoding='utf-8')
MOD = REPO / 'ciel_runtime_support/claude_mods/ciel-work'
RESULT = {'repo': str(REPO), 'mode': args.mode, 'ps_env': args.ps_env, 'live_gates': args.live_gates, 'cli': str(CLI), 'stripped_env': STRIPPED, 'live_before': health()}
router = None
try:
    router = subprocess.Popen([sys.executable, '-I', '-c', "import sys,runpy;sys.path.insert(0,sys.argv[1]);sys.argv=[sys.argv[1]+'/ciel_runtime.py','serve'];runpy.run_path(sys.argv[0],run_name='__main__')", str(REPO)], env=BASE, cwd=WS, stdout=(RUN / 'router-out.txt').open('wb'), stderr=subprocess.STDOUT)
    for _ in range(150):
        if http(f'http://127.0.0.1:{ROUTER}/health').get('ok'): break
        time.sleep(.2)
    perm = ['--dangerously-skip-permissions'] if args.mode == 'bypass' else ['--permission-mode', 'default']
    cmd = [str(CLI), '-p', 'SUBAGENT-PS-MAIN: run the Bash length probes.', '--output-format', 'stream-json', '--verbose', *perm, '--plugin-dir', str(MOD), '--mcp-config', str(MCP), '--strict-mcp-config', '--debug-file', str(RUN / 'native-debug.log')]
    RESULT['cli_version'] = subprocess.run([str(CLI), '--version'], env=ENV, capture_output=True, text=True).stdout.strip()
    cli = subprocess.run(cmd, env=ENV, cwd=WS, capture_output=True, text=True, encoding='utf-8', timeout=180)
    (RUN / 'stdout.jsonl').write_text(cli.stdout, encoding='utf-8'); (RUN / 'stderr.txt').write_text(cli.stderr, encoding='utf-8')
    RESULT['exit'] = cli.returncode
    mains = [r for r in ROWS if r['role'] == 'main']
    RESULT['observed'] = [{'sent_command_chars': mains[i].get('sent_command_chars'), 'result': (mains[i + 1]['results'][-1] if i + 1 < len(mains) and mains[i + 1]['results'] else None)} for i in range(len(SIZES))]
except Exception as e:
    RESULT['error'] = type(e).__name__ + ': ' + str(e)
finally:
    if router: subprocess.run(['taskkill', '/T', '/F', '/PID', str(router.pid)], capture_output=True)
    for s in SERVERS: s.shutdown()
    RESULT['live_after'] = health(); RESULT['egress'] = EGRESS; dump('result.json', RESULT)
    print(json.dumps({k: v for k, v in RESULT.items() if k != 'stripped_env'}, ensure_ascii=False, indent=1))
