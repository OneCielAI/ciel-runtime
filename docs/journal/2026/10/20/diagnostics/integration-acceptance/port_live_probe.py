"""Real loopback listeners and scratch registry only; no production state."""
import os
for key in list(os.environ):
    if key.startswith("CIEL_RUNTIME_"):
        os.environ.pop(key, None)
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
ROOT = next(p for p in Path(__file__).resolve().parents if (p / "ciel_runtime.py").exists())
sys.path.insert(0, str(ROOT))
from ciel_runtime_support.workspace_router_selection import select_workspace_router_port, workspace_digest, workspace_identity

class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        data = json.dumps({"ok": True, "workspace": self.server.workspace}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def log_message(self, *_args):
        pass

def free_range():
    for base in range(33000, 48000, 51):
        sockets = []
        try:
            for port in range(base, base + 50):
                s = socket.socket()
                sockets.append(s)
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                s.bind(("127.0.0.1", port))
            return base
        except OSError:
            pass
        finally:
            for s in sockets:
                s.close()
    raise RuntimeError("No owned probe range")

servers = []
with tempfile.TemporaryDirectory(prefix="ciel-port-live-") as temp:
    home = Path(temp)
    base = free_range()
    workspaces = [home / str(i) for i in range(50)]
    records = {workspace_digest(w): {"workspace": workspace_identity(w), "port": base + i} for i, w in enumerate(workspaces)}
    registry = home / "workspace-router-ports.json"
    registry.write_text(json.dumps({"version": 1, "workspaces": records}))
    try:
        for i in (0, 20, 49):
            server = ThreadingHTTPServer(("127.0.0.1", base + i), Health)
            server.workspace = workspace_identity(workspaces[i])
            servers.append(server)
            threading.Thread(target=server.serve_forever, daemon=True).start()
        selected = select_workspace_router_port(base, home / "new", {}, registry_path=registry)
        assert selected == base + 1, (base, selected)
        updated = json.loads(registry.read_text())["workspaces"]
        assert all(updated[workspace_digest(workspaces[i])]["port"] == base + i for i in (0, 20, 49))
        assert workspace_digest(workspaces[1]) not in updated
        assert len(updated) == 50
        reused = select_workspace_router_port(base, home / "new", {}, registry_path=registry)
        assert reused == selected
        with socket.socket() as s:
            s.bind(("127.0.0.1", selected))
            s.listen()
        print(json.dumps({"passed": True, "base": base, "selected": selected, "registry_entries": len(updated), "live_routers_preserved": 3, "same_workspace_reuses": reused == selected}))
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
for server in servers:
    with socket.socket() as s:
        s.settimeout(0.2)
        assert s.connect_ex(server.server_address) != 0
print("Owned listeners closed; real registry untouched.")
