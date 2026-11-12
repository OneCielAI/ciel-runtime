"""Fixture stdio MCP server shaped like Walkie, for the owned MCP size probe.

Tools `walkie_read_messages` and `walkie_call_tool` return one JSON text
block of about `size` code points (synthetic rows only, no real data). The
text statistics and SHA-256 are written to `server-out-<tool>-<size>.json` in
the working directory so the probe can compare what `$.mcp.call` received.
With PROBE_ADVERTISE_META=<n>, each tool advertises
`_meta["anthropic/maxResultSizeChars"] = n`. With PROBE_UNICODE=1 the rows mix
Hangul, BMP symbols and astral emoji, sent as raw UTF-8 (ensure_ascii=False).
"""
import io, json, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from walkie_unicode_payload import payload, stats

META = os.environ.get('PROBE_ADVERTISE_META')
UNICODE = os.environ.get('PROBE_UNICODE') == '1'
# Raw UTF-8 JSON-RPC lines on stdin/stdout regardless of the Windows code page.
stdin = io.TextIOWrapper(sys.stdin.buffer, encoding='utf-8')
stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', newline='\n')


def reply(message_id, result):
    stdout.write(json.dumps({'jsonrpc': '2.0', 'id': message_id, 'result': result}, ensure_ascii=not UNICODE) + '\n')
    stdout.flush()


def tool(name):
    row = {'name': name, 'description': 'Walkie-shaped size probe ' + name,
           'inputSchema': {'type': 'object', 'properties': {'size': {'type': 'integer'}}}}
    if META:
        row['_meta'] = {'anthropic/maxResultSizeChars': int(META)}
    return row


for raw in stdin:
    raw = raw.strip()
    if not raw:
        continue
    message = json.loads(raw)
    method, message_id = message.get('method'), message.get('id')
    if message_id is None:
        continue
    if method == 'initialize':
        reply(message_id, {'protocolVersion': (message.get('params') or {}).get('protocolVersion', '2024-11-05'),
                           'capabilities': {'tools': {}}, 'serverInfo': {'name': 'walkie', 'version': '0.1.0'}})
    elif method == 'tools/list':
        reply(message_id, {'tools': [tool('walkie_read_messages'), tool('walkie_call_tool')]})
    elif method == 'tools/call':
        params = message.get('params') or {}
        name = params.get('name')
        size = int((params.get('arguments') or {}).get('size') or 1000)
        text = payload(name, size, ascii_only=not UNICODE)
        with open('server-out-%s-%d.json' % (name, size), 'w', encoding='utf-8') as out:
            json.dump(stats(text), out)
        reply(message_id, {'content': [{'type': 'text', 'text': text}]})
    else:
        reply(message_id, {})
