"""Minimal stdio MCP server for the restart E2E: three tools; every start appends its pid to a log."""
from __future__ import annotations

import json
import os
import sys
import time

LOG = sys.argv[1]
with open(LOG, "a", encoding="utf-8") as f:
    f.write(json.dumps({"pid": os.getpid(), "started": time.time()}) + "\n")

TOOLS = [
    {"name": name, "description": f"E2E tool {name}", "inputSchema": {"type": "object", "properties": {}}}
    for name in ("e2e_echo", "e2e_clock", "e2e_pid")
]

for raw in sys.stdin:
    try:
        message = json.loads(raw)
    except ValueError:
        continue
    if "id" not in message:
        continue
    method = message.get("method")
    if method == "initialize":
        result = {"protocolVersion": (message.get("params") or {}).get("protocolVersion") or "2025-06-18",
                  "capabilities": {"tools": {}}, "serverInfo": {"name": "e2e-tools", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": f"pid={os.getpid()}"}]}
    else:
        result = {}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}) + "\n")
    sys.stdout.flush()
