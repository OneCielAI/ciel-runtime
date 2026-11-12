"""Owned probes for the SSE native-fork contract on a real Claude Code.

  python owned_side_probe.py mcp-size none   # no env
  python owned_side_probe.py mcp-size env    # MAX_MCP_OUTPUT_TOKENS=500000
  python owned_side_probe.py mcp-size meta   # server advertises maxResultSizeChars
  python owned_side_probe.py mcp-size unicode-http   # streamable HTTP MCP, raw Hangul/BMP/emoji
  python owned_side_probe.py mcp-size unicode-http-sse  # same, each response SSE-framed (text/event-stream)
  python owned_side_probe.py mcp-size unicode-stdio  # stdio MCP, same raw UTF-8 text
  python owned_side_probe.py named-fork

mcp-size: a plugin calls `$.mcp.call` on a Walkie-shaped fixture MCP returning
60K, 200K and 600K characters, and records exactly what it received.
named-fork: a plugin spawns a named and an unnamed native fork, records the
agent.spawn input, the native agent list rows, TaskStop and the rows after.

Loopback model gateway, temp CLAUDE_CONFIG_DIR, no live router/provider/config;
every inherited CIEL_RUNTIME_/CLAUDE/ANTHROPIC/MAX_MCP variable is removed.
"""
import json, os, sys, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[8]))
from ciel_runtime_support.windows_conpty import WindowsConPtySession

MODE = sys.argv[1]
VARIANT = sys.argv[2] if len(sys.argv) > 2 else ''
ROOT = Path(__file__).resolve().parent
RUN = ROOT / (MODE + ('-' + VARIANT if VARIANT else '') + '-' + uuid.uuid4().hex[:8]); RUN.mkdir()
CFG = RUN / 'ccfg'; CFG.mkdir()
WS = RUN / 'workspace'; WS.mkdir()
(CFG / '.claude.json').write_text(json.dumps({
    'hasCompletedOnboarding': True, 'theme': 'dark',
    'projects': {str(WS): {'hasTrustDialogAccepted': True},
                 str(WS).replace('\\', '/'): {'hasTrustDialogAccepted': True}},
}), encoding='utf-8')
(CFG / 'settings.json').write_text(json.dumps({'permissions': {'allow': ['Bash']}}), encoding='utf-8')
CLI = 'C:/Users/djlov/AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe'
SOCKET = rf'\\.\pipe\LOCAL\ciel-side-probe-{uuid.uuid4().hex[:16]}'
HOOK_LOG = WS / 'hook-events.jsonl'
SERVER = (ROOT / 'walkie_size_mcp_server.py').as_posix()
server_env = ({'PROBE_ADVERTISE_META': '1000000'} if VARIANT == 'meta'
              else {'PROBE_UNICODE': '1'} if VARIANT == 'unicode-stdio' else {})
MCP_CONFIG = json.dumps({'mcpServers': {'walkie': {'command': sys.executable, 'args': [SERVER], 'env': server_env}}})
HTTP_OUT = {}
HTTP_FRAMING = []
# unicode-http-sse: every JSON-RPC response is one SSE event (`event: message`,
# `data: <json>`) on text/event-stream, sent with HTTP/1.1 chunked encoding in
# 4093-byte chunks so chunk boundaries fall inside multi-byte UTF-8 sequences.
SSE_FRAMED = VARIANT == 'unicode-http-sse'
SSE_CHUNK = 4093
if VARIANT in ('unicode-http', 'unicode-http-sse'):
    sys.path.insert(0, str(ROOT))
    from walkie_unicode_payload import payload as unicode_payload_shared, stats as payload_stats

    def unicode_payload(tool, size):
        return unicode_payload_shared(tool, size)

    class McpHttp(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1' if SSE_FRAMED else 'HTTP/1.0'

        def log_message(self, *args): pass

        def send_json(self, value):
            raw = json.dumps(value, ensure_ascii=False).encode('utf-8')
            if not SSE_FRAMED:
                self.send_response(200); self.send_header('content-type', 'application/json; charset=utf-8')
                self.send_header('content-length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
                return
            frame = b'event: message\ndata: ' + raw + b'\n\n'
            self.send_response(200); self.send_header('content-type', 'text/event-stream')
            self.send_header('cache-control', 'no-cache'); self.send_header('transfer-encoding', 'chunked')
            self.end_headers()
            chunks = splits = 0
            for start in range(0, len(frame), SSE_CHUNK):
                chunk = frame[start:start + SSE_CHUNK]
                end = start + len(chunk)
                # A chunk ends inside a UTF-8 sequence when the next byte is a continuation byte.
                if end < len(frame) and (frame[end] & 0xC0) == 0x80:
                    splits += 1
                self.wfile.write(b'%x\r\n' % len(chunk) + chunk + b'\r\n'); self.wfile.flush(); chunks += 1
            self.wfile.write(b'0\r\n\r\n'); self.wfile.flush()
            HTTP_FRAMING.append({'at': time.time(), 'id': value.get('id'), 'content_type': 'text/event-stream', 'event': 'message',
                                 'frame_bytes': len(frame), 'chunks': chunks, 'chunks_split_inside_utf8': splits,
                                 'text_sha256': payload_stats(((value.get('result') or {}).get('content') or [{}])[0].get('text', ''))['sha256']
                                 if (value.get('result') or {}).get('content') else None})

        def do_GET(self):
            self.send_response(405); self.send_header('content-length', '0'); self.end_headers()

        def do_DELETE(self):
            self.send_response(200); self.send_header('content-length', '0'); self.end_headers()

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get('content-length', 0))) or b'{}')
            method, params, ident = body.get('method'), body.get('params') or {}, body.get('id')
            if ident is None:
                self.send_response(202); self.send_header('content-length', '0'); self.end_headers(); return
            if method == 'initialize':
                result = {'protocolVersion': params.get('protocolVersion', '2025-03-26'), 'capabilities': {'tools': {}},
                          'serverInfo': {'name': 'walkie', 'version': '0.1.0'}}
            elif method == 'tools/list':
                result = {'tools': [{'name': name, 'description': 'Walkie-shaped unicode probe ' + name,
                                     'inputSchema': {'type': 'object', 'properties': {'size': {'type': 'integer'}}}}
                                    for name in ('walkie_read_messages', 'walkie_call_tool')]}
            elif method == 'tools/call':
                name = params.get('name')
                size = int((params.get('arguments') or {}).get('size') or 1000)
                text = unicode_payload(name, size)
                HTTP_OUT['%s-%d' % (name, size)] = payload_stats(text)
                result = {'content': [{'type': 'text', 'text': text}]}
            elif method == 'ping':
                result = {}
            else:
                return self.send_json({'jsonrpc': '2.0', 'id': ident, 'error': {'code': -32601, 'message': 'not found'}})
            self.send_json({'jsonrpc': '2.0', 'id': ident, 'result': result})

    class McpServer(ThreadingHTTPServer):
        # Record (instead of printing) any per-connection error with its time,
        # so it can be placed relative to the probe's calls and shutdown.
        def handle_error(self, request, client_address):
            HTTP_ERRORS.append({'at': time.time(), 'error': repr(sys.exc_info()[1])[:300]})

    HTTP_ERRORS = []
    MCP_SERVER = McpServer(('127.0.0.1', 0), McpHttp)
    threading.Thread(target=MCP_SERVER.serve_forever, daemon=True).start()
    MCP_CONFIG = json.dumps({'mcpServers': {'walkie': {'type': 'http',
                                                       'url': 'http://127.0.0.1:%d/mcp' % MCP_SERVER.server_port}}})
PLUGIN = RUN / 'plugin'; (PLUGIN / '.claude-plugin').mkdir(parents=True); (PLUGIN / 'hooks').mkdir()
(PLUGIN / '.claude-plugin/plugin.json').write_text(json.dumps({'name': 'side-probe', 'version': '0.1.0'}), encoding='utf-8')
(PLUGIN / 'hooks/hooks.json').write_text('{"modules":["./register.js"]}', encoding='utf-8')
COMMON = r'''
let chain = Promise.resolve()
function note($, row) {
  chain = chain.then(async () => {
    let prior = ''
    try { prior = await $.fs.read('hook-events.jsonl') } catch {}
    await $.fs.write('hook-events.jsonl', prior + JSON.stringify(row) + '\n')
  }).catch(() => {})
  return chain
}
async function sha(text) {
  const hash = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text)))
  return [...hash].map(v => v.toString(16).padStart(2, '0')).join('')
}
'''
MCP_PLUGIN = COMMON + r'''
let started = false
export function register(on) {
  on('session.start', async ($, e, next) => {
    const result = await next(e)
    if (started) return result
    started = true
    try {
      await $.mcp.connect('walkie')
      let configDir = null
      try { configDir = await $.env.get('CLAUDE_CONFIG_DIR') } catch (error) { configDir = 'threw: ' + String(error).slice(0, 200) }
      await note($, { event: 'connected', session_id: await $.session.id(), claude_config_dir: configDir })
    }
    catch (error) { await note($, { event: 'connect-failed', error: String(error).slice(0, 300) }); return result }
    // Run 1 showed the first call can precede the server's tool listing.
    for (let attempt = 0; attempt < 40; attempt++) {
      try { await $.mcp.call('walkie', 'walkie_read_messages', { size: 100 }); break }
      catch { await new Promise(resolve => setTimeout(resolve, 250)) }
    }
    for (const tool of ['walkie_read_messages', 'walkie_call_tool']) {
      for (const size of [60000, 200000, 600000]) {
        let row = { event: 'call', tool, size }
        try {
          const answer = await $.mcp.call('walkie', tool, { size })
          const content = Array.isArray(answer?.content) ? answer.content : []
          const text = content.filter(c => c?.type === 'text').map(c => c.text || '').join('')
          let parsed = false
          try { JSON.parse(text); parsed = true } catch {}
          // Can the plugin read the host-persisted full result named in a stub?
          const saved = /Output has been saved to (.+?\.txt)/.exec(text)?.[1] || null
          let savedRead = null
          if (saved) {
            try { const full = await $.fs.read(saved); savedRead = { chars: full.length, code_points: [...full].length,
              utf8_bytes: new TextEncoder().encode(full).length, sha256: await sha(full) } }
            catch (error) { savedRead = { threw: String(error).slice(0, 300) } }
          }
          const stated = /^Error: result \(([\d,]+) characters/.exec(text)?.[1] || null
          row = { ...row, saved_path: saved, saved_read: savedRead, stub_text: saved ? text : null,
            stub_count: stated ? Number(stated.replace(/,/g, '')) : null,
            received_code_points: [...text].length }
          row = { ...row, keys: Object.keys(answer || {}).sort(), isError: answer?.isError ?? null,
            content_types: content.map(c => c?.type), chars: text.length, sha256: await sha(text), parsed,
            head: text.slice(0, 400), tail: text.slice(-200),
            other_content: content.filter(c => c?.type !== 'text').map(c => JSON.stringify(c).slice(0, 400)),
            meta: answer?._meta ? JSON.stringify(answer._meta).slice(0, 400) : null }
        } catch (error) { row = { ...row, threw: String(error).slice(0, 600) } }
        await note($, row)
      }
    }
    await note($, { event: 'done' })
    return result
  })
}
'''
FORK_PLUGIN = COMMON + r'''
const NAMED = 'side-intake-0123456789abcdef'
const pending = new Set()
const row = a => ({ id: a.id, type: a.type, name: a.name ?? null, status: a.status, spawnedBy: a.spawnedBy ?? null,
  description: a.description ?? null, keys: Object.keys(a).sort() })
export function register(on) {
  on('agent.spawn', async ($, e, next) => {
    await note($, { event: 'agent.spawn', name: e.name ?? null, description: e.description ?? null,
      subagentType: e.subagentType ?? null, fork: e.fork ?? null, parentAgentId: e.parentAgentId ?? null,
      origin: next.origin?.plugin ?? null, keys: Object.keys(e).sort() })
    return next(e)
  })
  on('session.receive', async ($, e, next) => {
    if (String(e.text || '').trim() !== '[fork-probe] go' || e.agentId !== undefined) return next(e)
    void (async () => {
      for (const [label, spec] of [['named', { name: NAMED, description: 'Side SSE 0123456789ab' }],
                                   ['unnamed', { description: 'Side SSE abcdefabcdef' }]]) {
        try {
          const spawned = await $.agent.spawn({ subagentType: 'fork', ...spec,
            prompt: 'FORK-PROBE-' + label.toUpperCase() + ' reply with one word' })
          pending.add(spawned?.agentId)
          await note($, { event: 'spawned', label, agentId: spawned?.agentId ?? null, deny: spawned?.deny ?? null,
            keys: Object.keys(spawned || {}).sort() })
        } catch (error) { await note($, { event: 'spawn-threw', label, error: String(error).slice(0, 400) }) }
      }
    })()
    return { consumed: 'fork probe' }
  })
  on('turn.complete', async ($, e, next) => {
    const result = await next(e)
    if (!e.agentId || !pending.has(e.agentId)) return result
    pending.delete(e.agentId)
    void (async () => {
      const before = (await $.agent.list()).filter(a => a.id === e.agentId).map(row)
      await note($, { event: 'complete', agentId: e.agentId, reason: e.reason ?? null,
        answer: String(e.answer || '').slice(0, 200), before })
      let stop = null
      try { stop = await $.tool.call({ tool: 'TaskStop', task_id: e.agentId }) }
      catch (error) { stop = { threw: String(error).slice(0, 300) } }
      await new Promise(resolve => setTimeout(resolve, 3000))
      const after = (await $.agent.list()).filter(a => a.id === e.agentId).map(row)
      const all = (await $.agent.list()).map(row)
      await note($, { event: 'stopped', agentId: e.agentId, stop: JSON.stringify(stop).slice(0, 400), after, all })
    })()
    return result
  })
}
'''
(PLUGIN / 'hooks/register.js').write_text(MCP_PLUGIN if MODE == 'mcp-size' else FORK_PLUGIN, encoding='utf-8')

REQUESTS = []


def sse(handler, message, text):
    events = [('message_start', {'type': 'message_start', 'message': {**message, 'content': [], 'stop_reason': None}}),
              ('content_block_start', {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}}),
              ('content_block_delta', {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': text}}),
              ('content_block_stop', {'type': 'content_block_stop', 'index': 0}),
              ('message_delta', {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None}, 'usage': {'output_tokens': 5}}),
              ('message_stop', {'type': 'message_stop'})]
    handler.send_response(200); handler.send_header('content-type', 'text/event-stream'); handler.send_header('connection', 'close'); handler.end_headers()
    for name, data in events:
        handler.wfile.write(('event: ' + name + '\ndata: ' + json.dumps(data) + '\n\n').encode()); handler.wfile.flush()
    handler.close_connection = True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('content-length', 0))))
        if 'count_tokens' in self.path:
            raw = json.dumps({'input_tokens': 100}).encode()
            self.send_response(200); self.send_header('content-length', str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        text = json.dumps(body.get('messages', []), ensure_ascii=False)
        REQUESTS.append({'at': time.time(), 'agent': self.headers.get('x-claude-code-agent-id'),
                         'has_main_history': 'MAIN-HELLO' in text, 'fork_named': 'FORK-PROBE-NAMED' in text,
                         'fork_unnamed': 'FORK-PROBE-UNNAMED' in text, 'tools': len(body.get('tools') or []),
                         'model': body.get('model')})
        message = {'id': 'msg_' + uuid.uuid4().hex[:10], 'type': 'message', 'role': 'assistant',
                   'model': body.get('model'), 'usage': {'input_tokens': 100, 'output_tokens': 5}}
        sse(self, message, 'FORK-DONE' if 'FORK-PROBE' in text else 'PROBE-DONE')


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
env = {k: v for k, v in os.environ.items() if not k.upper().startswith(('CIEL_RUNTIME_', 'CLAUDE', 'ANTHROPIC', 'CODEX', 'OPENAI', 'MAX_MCP'))}
env.update({'CLAUDE_CONFIG_DIR': str(CFG), 'ANTHROPIC_BASE_URL': 'http://127.0.0.1:' + str(server.server_port),
            'ANTHROPIC_AUTH_TOKEN': 'fixture', 'ANTHROPIC_API_KEY': '', 'ANTHROPIC_MODEL': 'fixture',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1', 'DISABLE_AUTOUPDATER': '1', 'ENABLE_TOOL_SEARCH': 'false'})
if VARIANT == 'env':
    env['MAX_MCP_OUTPUT_TOKENS'] = '500000'
os.chdir(WS)
session = WindowsConPtySession(
    [CLI, '--permission-mode', 'default', '--messaging-socket-path', SOCKET,
     '--plugin-dir', str(PLUGIN), '--strict-mcp-config', '--mcp-config', MCP_CONFIG],
    env, log=lambda level, message: None, mirror_output=False, forward_stdin=False)
result = {'run': RUN.name, 'mode': MODE, 'variant': VARIANT, 'claude_code': '2.1.321',
          'env_MAX_MCP_OUTPUT_TOKENS': env.get('MAX_MCP_OUTPUT_TOKENS'), 'server_meta': server_env.get('PROBE_ADVERTISE_META')}


def rows():
    return [json.loads(line) for line in HOOK_LOG.read_text(encoding='utf-8').splitlines()] if HOOK_LOG.exists() else []


def wait_for(predicate, seconds):
    limit = time.time() + seconds
    while time.time() < limit and not predicate():
        time.sleep(0.25)
    return bool(predicate())


def send(token, content):
    frames = (json.dumps({'type': 'auth', 'token': token}) + '\n' + json.dumps({
        'type': 'user', 'uuid': str(uuid.uuid4()), 'message': {'role': 'user', 'content': content},
        'priority': 'next'}) + '\n').encode()
    with open(SOCKET, 'wb', buffering=0) as pipe:
        pipe.write(frames)


try:
    if MODE == 'mcp-size':
        result['finished'] = wait_for(lambda: any(r.get('event') == 'done' for r in rows()), 120)
    else:
        token, deadline = '', time.time() + 60
        while time.time() < deadline and session.poll() is None and not token:
            keys = list((CFG / 'sessions').glob('*.key')) if (CFG / 'sessions').exists() else []
            if keys:
                token = str(json.loads(keys[0].read_text(encoding='utf-8')).get('peerToken') or '')
            time.sleep(0.25)
        result['token_found'] = len(token) == 32
        if token:
            time.sleep(2)
            send(token, 'MAIN-HELLO establish main history')
            result['main_turn'] = wait_for(lambda: any(not r['agent'] and r['has_main_history'] for r in REQUESTS), 60)
            time.sleep(3)
            send(token, '[fork-probe] go')
            result['finished'] = wait_for(lambda: sum(r.get('event') == 'stopped' for r in rows()) >= 2, 120)
            time.sleep(1)
        result['gateway_requests'] = [{k: v for k, v in r.items() if k != 'at'} for r in REQUESTS]
        teams = CFG / 'teams'
        result['team_files'] = sorted(str(p.relative_to(CFG)) for p in teams.rglob('*')) if teams.exists() else []
    result['hook_rows'] = rows()
    server_files = {}
    for path in sorted(WS.glob('server-out-*.json')):
        server_files[path.stem] = json.loads(path.read_text(encoding='utf-8'))
    for key, value in HTTP_OUT.items():
        server_files['server-out-' + key] = value
    result['server_out'] = server_files
    if SSE_FRAMED:
        result['http_framing'] = HTTP_FRAMING
        result['probe_done_at'] = time.time()
    persisted = [str(p.relative_to(CFG)) for p in CFG.rglob('*') if p.is_file() and 'tool-results' in str(p)]
    result['persisted_tool_result_files'] = persisted
    (RUN / 'screen.txt').write_text(session.output_tail().decode('utf-8', errors='replace'), encoding='utf-8')
    result['exit'] = session.poll()
finally:
    try:
        result['cli_close_at'] = time.time()
        session.close()
    finally:
        server.shutdown()
if SSE_FRAMED:
    time.sleep(1)
    result['http_errors'] = HTTP_ERRORS
(RUN / 'result.json').write_text(json.dumps(result, ensure_ascii=True, indent=2), encoding='utf-8')
print(json.dumps({'run': RUN.name, 'finished': result.get('finished')}))
