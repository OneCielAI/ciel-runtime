"""Owned interactive Claude + real loopback empty SSE recovery, no live sessions."""
import importlib.util
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from http.server import ThreadingHTTPServer

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent/'ciel_runtime_support').is_dir())
sys.path.insert(0, str(ROOT))
from ciel_runtime_support.windows_conpty import WindowsConPtySession
source = ROOT / 'docs/journal/2026/10/21/diagnostics/cielairouter-transient-retry/harness/direct_claude_retry.py'
spec = importlib.util.spec_from_file_location('fixture', source)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
run = ROOT / '.local' / ('interactive-network-' + uuid.uuid4().hex[:8])
run.mkdir()
cfg = run / 'config'
cfg.mkdir()
ws = run / 'workspace'
ws.mkdir()
(cfg / '.claude.json').write_text(json.dumps({'hasCompletedOnboarding': True, 'theme': 'dark',
    'projects': {str(ws): {'hasTrustDialogAccepted': True}, str(ws).replace('\\', '/'): {'hasTrustDialogAccepted': True}}}))
fixture = m.Fixture(1, 'empty')
base = fixture.handler()
wire = []
class Handler(base):
    def do_POST(self):
        body = self._body()
        if 'count_tokens' in self.path:
            self._send(200, 'application/json', b'{"input_tokens":1}')
            return
        failed = fixture.next_verdict()
        payload = b'' if failed else m._stream_body()
        wire.append({'at': time.time(), 'path': self.path, 'stream': body.get('stream'), 'status': 200, 'bytes': len(payload)})
        self._send(200, 'text/event-stream', payload)
server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
env = {k:v for k,v in os.environ.items() if not k.upper().startswith(('CIEL_RUNTIME_', 'CLAUDE', 'ANTHROPIC', 'CODEX', 'OPENAI'))}
env.update(CLAUDE_CONFIG_DIR=str(cfg), ANTHROPIC_BASE_URL='http://127.0.0.1:'+str(server.server_port),
    ANTHROPIC_AUTH_TOKEN='fixture', CLAUDE_CODE_MAX_RETRIES='0', CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK='1',
    CLAUDE_CODE_RETRY_WATCHDOG='0', CLAUDE_CODE_NONSTREAMING_TIMEOUT_RETRIES='0',
    CIEL_RUNTIME_DIRECT_GATEWAY_ACTIVE='1', CIEL_RUNTIME_DIRECT_EMPTY_STREAM_RETRIES='3',
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1', DISABLE_AUTOUPDATER='1')
sock = r'\\.\pipe\LOCAL\network-' + uuid.uuid4().hex[:12]
os.chdir(ws)
session = WindowsConPtySession([str(Path.home()/'.local/bin/claude.exe'), '--model', 'claude-sonnet-5-5',
    '--messaging-socket-path', sock, '--plugin-dir', str(ROOT/'ciel_runtime_support/claude_mods/ciel-network-retry'),
    '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}'], env,
    log=lambda *_:None, mirror_output=False, forward_stdin=False)
try:
    deadline = time.time()+40
    while time.time()<deadline and not list((cfg/'sessions').glob('*.key')):
        time.sleep(.1)
    keys = list((cfg/'sessions').glob('*.key'))
    assert keys, 'No owned interactive peer key'
    token = json.loads(keys[0].read_text())['peerToken']
    frames = json.dumps({'type':'auth','token':token})+'\n'+json.dumps({'type':'user','uuid':str(uuid.uuid4()),
        'message':{'role':'user','content':'Reply ok'},'priority':'next'})+'\n'
    with open(sock,'wb',buffering=0) as pipe:
        pipe.write(frames.encode())
    deadline = time.time()+35
    transcript = ''
    while time.time()<deadline:
        paths = list((cfg/'projects').glob('**/*.jsonl'))
        transcript = '\n'.join(p.read_text(encoding='utf8') for p in paths)
        if fixture.hits>=2 and '"text":"ok"' in transcript.replace(' ', ''):
            break
        time.sleep(.2)
    screen = session.output_tail().decode('utf8', errors='replace')
    (run/'screen.txt').write_text(screen, encoding='utf8')
    (run/'transcript.jsonl').write_text(transcript, encoding='utf8')
    result = {'run':str(run),'hits':fixture.hits,'wire':wire,'screen':screen,
        'recovered':fixture.hits==2 and '"text":"ok"' in transcript.replace(' ', '')}
    (run/'result.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps(result,indent=2))
    assert result['recovered'], 'Interactive empty recovery did not complete'
finally:
    session.close()
    server.shutdown()
    server.server_close()
