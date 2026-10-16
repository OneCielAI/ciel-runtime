"""E2E: two gateway nodes advertise the same loopback URL; the router must pick the node actually running here.

Usage: python e2e_loopback.py <label> <repo-dir>

Runs `<repo-dir>/ciel_runtime.py serve` as an isolated router (scratch CIEL_RUNTIME_CONFIG_DIR on G:, unique
port, CIEL_RUNTIME_TEST_ISOLATED=1, inherited CIEL_RUNTIME_/ANTHROPIC_/CLAUDE_/CODEX_/OPENAI_ variables cleared).
Fake servers on loopback, following the discovery contract:

  central   GET /api/gateway/discovery -> [node-remote @ http://127.0.0.1:G/v1, node-local @ same URL]
            (node-remote listed first, as a remote node that advertises the loopback alternate)
            POST /v1/messages answers STUB-REPLY-central (only reached on fallback)
  gateway G GET /healthz -> nodeId node-local; POST /v1/messages answers STUB-REPLY-gateway

One non-streaming /v1/messages request goes through the router. The result records which server got it and
the router's /health cielairouter_gateway block. Only routing facts are kept; no request text.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
LABEL, REPO = sys.argv[1], Path(sys.argv[2]).resolve()
RUN = HERE / f"run-{LABEL}"
shutil.rmtree(RUN, ignore_errors=True)
RUN.mkdir(parents=True)
SCRATCH = Path(r"G:\ciel-e2e\gateway-loopback") / LABEL
shutil.rmtree(SCRATCH, ignore_errors=True)
CFG, WS = SCRATCH / "cfg", SCRATCH / "ws"
for d in (CFG, WS):
    d.mkdir(parents=True, exist_ok=True)
KEY = "sk-e2e-loopback-00000000000000000"
LOCK = threading.Lock()
EVENTS: list[dict] = []


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


PORTS = {"central": free_port(), "gateway": free_port()}
ROUTER = free_port()


def make_handler(name: str):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code, data: bytes):
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _record(self, path: str, **extra):
            with LOCK:
                EVENTS.append({"n": len(EVENTS) + 1, "server": name, "path": path, **extra})

        def _authorized(self) -> bool:
            return self.headers.get("Authorization") == f"Bearer {KEY}" or self.headers.get("x-api-key") == KEY

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/healthz" and name == "gateway":
                self._record(path, auth=bool(self.headers.get("Authorization")))
                body = {"status": "ok", "role": "gateway", "nodeId": "node-local", "ready": True, "online": True}
                return self._send(200, json.dumps(body).encode())
            if path == "/api/gateway/discovery" and name == "central":
                self._record(path, auth_ok=self._authorized())
                now = time.time()
                endpoint = f"http://127.0.0.1:{PORTS['gateway']}/v1"
                rows = [{
                    "id": node, "name": node, "endpoint": endpoint, "endpoints": [endpoint],
                    "status": "online", "lastSeenAt": iso(now - 2), "availableUntil": iso(now + 600),
                    "backlog": 0, "health": "healthy", "healthUrl": f"http://127.0.0.1:{PORTS['gateway']}/healthz",
                    "clientProbe": {"path": "/healthz", "method": "GET", "timeoutMs": 2000},
                    "congestion": {"utilization": 0.1, "backlog": 0, "observedAt": iso(now - 2)},
                } for node in ("node-remote", "node-local")]
                body = {"object": "list", "protocol_version": 1, "generated_at": iso(now), "data": rows}
                return self._send(200, json.dumps(body).encode())
            if path.endswith("/v1/models"):
                self._record(path)
                rows = [{"id": "stub-model", "owned_by": "stub", "context_length": 128000, "max_output_tokens": 8192}]
                return self._send(200, json.dumps({"object": "list", "data": rows}).encode())
            self._send(404, b"{}")

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("content-length") or 0))
            path = self.path.split("?")[0]
            self._record(path, auth_ok=self._authorized())
            body = json.loads(raw or b"{}")
            if path.endswith("/v1/messages"):
                message = {"id": "msg_1", "type": "message", "role": "assistant",
                           "model": body.get("model") or "stub-model",
                           "content": [{"type": "text", "text": f"STUB-REPLY-{name}"}],
                           "stop_reason": "end_turn", "stop_sequence": None,
                           "usage": {"input_tokens": 10, "output_tokens": 2}}
                return self._send(200, json.dumps(message).encode())
            self._send(404, b"{}")

    return Handler


servers = []
for name, port in PORTS.items():
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(name))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    servers.append(srv)

(CFG / "config.json").write_text(json.dumps({
    "current_provider": "cielairouter",
    "language": "en",
    "providers": {"cielairouter": {"base_url": f"http://127.0.0.1:{PORTS['central']}", "api_key": KEY,
                                   "current_model": "stub-model", "context_window": 128000,
                                   "max_output_tokens": 4096, "request_timeout_ms": 60000}},
}, indent=2), encoding="utf-8")
(CFG / "log-level").write_text("INFO")

env = {k: v for k, v in os.environ.items()
       if not k.startswith(("CIEL_RUNTIME_", "ANTHROPIC_", "CLAUDE_", "CODEX_", "OPENAI_"))}
env.update(CIEL_RUNTIME_CONFIG_DIR=str(CFG), CIEL_RUNTIME_ROUTER_PORT=str(ROUTER), CIEL_RUNTIME_TEST_ISOLATED="1",
           PYTHONIOENCODING="utf-8")
rev = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
out = open(RUN / "router-stdout.txt", "w", encoding="utf-8")
proc = subprocess.Popen([sys.executable, str(REPO / "ciel_runtime.py"), "serve"], cwd=str(WS), env=env,
                        stdout=out, stderr=subprocess.STDOUT)


def http(method: str, path: str, body: dict | None = None, timeout: float = 30) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{ROUTER}{path}", method=method, data=data,
                                 headers={"content-type": "application/json", "accept": "application/json",
                                          "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
    except Exception as e:
        return {"raw": f"{type(e).__name__}: {e}"}
    try:
        return json.loads(raw)
    except ValueError:
        return {"raw": raw[:400]}


def router_lines(*needles: str) -> list[str]:
    lines = []
    for f in CFG.rglob("*.log*"):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines += [line[-400:] for line in text.splitlines() if any(n in line for n in needles)]
    return lines


try:
    deadline = time.time() + 60
    while "raw" in (h := http("GET", "/health", timeout=2)) and time.time() < deadline:
        time.sleep(0.5)
    reply = http("POST", "/v1/messages", {"model": "stub-model", "max_tokens": 16,
                                          "messages": [{"role": "user", "content": "E2E-LOOPBACK"}]})
    text = "".join(b.get("text", "") for b in reply.get("content", []) if isinstance(b, dict)) or reply.get("raw", "")
    health = http("GET", "/health")
    posts = [e for e in EVENTS if e["path"].endswith("/v1/messages")]
    result = {
        "label": LABEL, "repo_head": rev, "ports": PORTS,
        "reply_text": text,
        "generation_request_went_to": [e["server"] for e in posts],
        "healthz_with_auth": [e for e in EVENTS if e["path"] == "/healthz" and e.get("auth")],
        "discovery_auth_ok": [e.get("auth_ok") for e in EVENTS if e["path"] == "/api/gateway/discovery"],
        "health_gateway": health.get("cielairouter_gateway"),
        "router_log": router_lines("cielairouter_gateway")[-20:],
        "events": EVENTS,
    }
    (RUN / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("label", "repo_head", "reply_text", "generation_request_went_to",
                                              "healthz_with_auth", "discovery_auth_ok")}, ensure_ascii=False))
    print(json.dumps(result["health_gateway"], ensure_ascii=False)[:1500])
    for line in result["router_log"]:
        print(line)
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    out.close()
    for srv in servers:
        srv.shutdown()
