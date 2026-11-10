"""Minimal stdio MCP server for the owned clock/MCP latency probe.

It answers one tool, `now`, with the server's wall clock, so the probe can
tell how long a plugin's `$.mcp.call` takes while main is idle or busy.
"""
import json, sys, time


def reply(message_id, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": message_id, "result": result}) + "\n")
    sys.stdout.flush()


for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue
    message = json.loads(raw)
    method, message_id = message.get("method"), message.get("id")
    if message_id is None:
        continue
    if method == "initialize":
        reply(message_id, {"protocolVersion": (message.get("params") or {}).get("protocolVersion", "2024-11-05"),
                           "capabilities": {"tools": {}}, "serverInfo": {"name": "probe", "version": "0.1.0"}})
    elif method == "tools/list":
        reply(message_id, {"tools": [{"name": "now", "description": "Server wall clock",
                                      "inputSchema": {"type": "object", "properties": {}}}]})
    elif method == "tools/call":
        reply(message_id, {"content": [{"type": "text", "text": json.dumps({"server_at": time.time()})}]})
    else:
        reply(message_id, {})
