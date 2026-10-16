"""E2E: plain Codex TUI with gateway_data_path=direct keeps the router data path, at launch and after a restart.

Usage: python e2e_plain_codex.py

Real Codex CLI through ciel_runtime.py (--ca-runtime codex) in an isolated config: scratch CIEL_RUNTIME_CONFIG_DIR and
CODEX_HOME on G:, unique router port, CIEL_RUNTIME_TEST_ISOLATED=1, inherited CIEL_RUNTIME_/ANTHROPIC_/CLAUDE_/CODEX_/
OPENAI_ variables cleared in the launch script. Loopback stubs only (no live traffic): a fake CielAiRouter central
(discovery, /v1/models) and two fake gateways A and B. The provider is configured with gateway_data_path=direct.

Checks
  launch   the launcher prints the direct fallback and records codex active=router with the channel reason;
           no gateway discovery happens before the first model request (no launch-time selection)
  turn 1   a channel message is typed as a [ciel-wake] marker; the gateway request comes from the router with the
           message body injected (E2E-TURN-1 present, marker removed)
  restart  restart_session through the router MCP endpoint relaunches Codex; the relaunched command still names the
           router provider block, and turn 2 again reaches the gateway through the router with its body injected
The stub keeps only routing facts and non-secret header values (user-agent, originator).
"""
from __future__ import annotations

import json
import os
import re
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
# E2E_REPO selects another checkout (e.g. an extracted HEAD tree for a baseline run).
REPO = Path(os.environ.get("E2E_REPO") or r"C:\Users\djlov\ciel-runtime")
# argv[1]: "direct" (default) configures gateway_data_path=direct; "router" leaves it unset (control run).
MODE = sys.argv[1] if len(sys.argv) > 1 else "direct"
RUN = HERE / ("e2e-plain-codex" if MODE == "direct" else f"e2e-plain-codex-{MODE}")
shutil.rmtree(RUN, ignore_errors=True)
SCRATCH = Path(r"G:\ciel-e2e\direct-plain") / ("codex" if MODE == "direct" else f"codex-{MODE}")
shutil.rmtree(SCRATCH, ignore_errors=True)
CFG, WS, CODEX_HOME, SHOTS = SCRATCH / "cfg", SCRATCH / "ws", SCRATCH / "codex-home", RUN / "shots"
for d in (CFG, WS, CODEX_HOME, SHOTS):
    d.mkdir(parents=True, exist_ok=True)
LOG = open(RUN / "driver.log", "w", encoding="utf-8")
KEY = "sk-e2e-direct-plain-000000000000"
LOCK = threading.Lock()
EVENTS: list[dict] = []


def log(m: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {m}"
    print(line, flush=True)
    LOG.write(line + "\n")
    LOG.flush()


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def record(server: str, path: str, **extra) -> dict:
    with LOCK:
        item = {"n": len(EVENTS) + 1, "at": time.strftime("%H:%M:%S"), "t": time.time(), "server": server, "path": path, **extra}
        EVENTS.append(item)
    with open(RUN / "requests.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(item) + "\n")
    return item


def last_user_text(body: dict) -> str:
    """Text of the last user input item of a Responses request (routing fact only)."""
    items = body.get("input") if isinstance(body.get("input"), list) else body.get("messages") or []
    for item in reversed(items):
        if not isinstance(item, dict) or item.get("role") != "user":
            continue
        content = item.get("content")
        if isinstance(content, str):
            return content[:80]
        texts = [c.get("text", "") for c in content or [] if isinstance(c, dict)]
        return " ".join(texts)[:80]
    return ""


def make_handler(name: str, ports: dict):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code, data: bytes, ctype="application/json"):
            self.send_response(code)
            self.send_header("content-type", ctype)
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self) -> bool:
            auth = self.headers.get("Authorization") or ""
            return auth == f"Bearer {KEY}" or self.headers.get("x-api-key") == KEY

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/healthz" and name != "central":
                record(name, path, auth=bool(self.headers.get("Authorization")))
                body = {"status": "ok", "role": "gateway", "nodeId": f"node-{name}", "ready": True, "online": True}
                return self._send(200, json.dumps(body).encode())
            if path == "/api/gateway/discovery" and name == "central":
                ok = self._authorized()
                record(name, path, auth_ok=ok, ua=self.headers.get("User-Agent") or "")
                if not ok:
                    return self._send(401, b'{"error":{"message":"Valid API key required"}}')
                now = time.time()
                rows = []
                for node, util in (("A", 0.6), ("B", 0.1)):
                    endpoint = f"http://127.0.0.1:{ports[node]}/v1"
                    rows.append({
                        "id": f"node-{node}", "name": f"gateway-{node}", "endpoint": endpoint, "endpoints": [endpoint],
                        "status": "online", "lastSeenAt": iso(now - 2), "availableUntil": iso(now + 600),
                        "backlog": 0, "health": "healthy",
                        "healthUrl": f"http://127.0.0.1:{ports[node]}/healthz",
                        "clientProbe": {"path": "/healthz", "method": "GET", "timeoutMs": 2000},
                        "congestion": {"activeRequests": int(util * 128), "maxInFlight": 128, "utilization": util,
                                       "backlog": 0, "eventLoopLagMs": 1, "observedAt": iso(now - 2)},
                    })
                body = {"object": "list", "protocol_version": 1, "generated_at": iso(now), "data": rows}
                return self._send(200, json.dumps(body).encode())
            if path.endswith("/v1/models"):
                record(name, path)
                rows = [{"id": "stub-model", "owned_by": "stub", "context_length": 128000, "max_output_tokens": 8192,
                         "capabilities": {"tool_calling": True, "reasoning": True, "thinking": True,
                                          "supportsThinking": True}}]
                return self._send(200, json.dumps({"object": "list", "data": rows}).encode())
            self._send(404, b"{}")

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("content-length") or 0))
            path = self.path.split("?")[0]
            text = raw.decode("utf-8", "replace")
            body = json.loads(raw or b"{}")
            marker = (re.findall(r"E2E-TURN-(\d+)", text) or [""])[-1]
            last = last_user_text(body)
            record(name, path, turn=marker, auth_ok=self._authorized(), model=body.get("model"),
                   ua=self.headers.get("User-Agent") or "", originator=self.headers.get("originator") or "",
                   wake_marker_in_body="[ciel-wake]" in text, last_user_prefix=last[:60])
            if path.endswith("/v1/responses"):
                return self._responses(body)
            self._send(404, b"{}")

        def _stream(self, events):
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("connection", "close")
            self.end_headers()
            for kind, data in events:
                self.wfile.write(f"event: {kind}\ndata: {json.dumps(data)}\n\n".encode())
                self.wfile.flush()
            self.close_connection = True

        def _responses(self, body):
            reply = f"STUB-REPLY-{name}"
            rid = f"resp_{name}_{int(time.time() * 1000)}"
            item = {"id": f"msg_{rid}", "type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": reply, "annotations": []}]}
            response = {"id": rid, "object": "response", "created_at": int(time.time()), "status": "in_progress",
                        "model": body.get("model") or "stub-model", "output": []}
            done = {**response, "status": "completed", "output": [item],
                    "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}}
            self._stream([
                ("response.created", {"type": "response.created", "response": response}),
                ("response.output_item.added", {"type": "response.output_item.added", "output_index": 0,
                                                "item": {**item, "status": "in_progress", "content": []}}),
                ("response.output_text.delta", {"type": "response.output_text.delta", "item_id": item["id"],
                                                "output_index": 0, "content_index": 0, "delta": reply}),
                ("response.output_item.done", {"type": "response.output_item.done", "output_index": 0, "item": item}),
                ("response.completed", {"type": "response.completed", "response": done}),
            ])

    return Handler


PORTS = {"central": free_port(), "A": free_port(), "B": free_port()}
SERVERS = []
for name, port in PORTS.items():
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(name, PORTS))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    SERVERS.append(server)
ROUTER, APPSRV = free_port(), free_port()
(CFG / "config.json").write_text(json.dumps({
    "current_provider": "cielairouter",
    "language": "en",
    "providers": {"cielairouter": {"base_url": f"http://127.0.0.1:{PORTS['central']}", "api_key": KEY,
                                   "current_model": "stub-model", "context_window": 128000,
                                   "max_output_tokens": 4096, "request_timeout_ms": 120000,
                                   **({"gateway_data_path": "direct"} if MODE == "direct" else {})}},
}, indent=2), encoding="utf-8")
(CFG / "log-level").write_text("INFO")
(CODEX_HOME / "config.toml").write_text(f"[projects.'{WS}']\ntrust_level = \"trusted\"\n", encoding="utf-8")
log(f"central={PORTS['central']} A={PORTS['A']} B={PORTS['B']} router={ROUTER} scratch={SCRATCH}")

CLEAR_PREFIXES = ("CIEL_RUNTIME_", "ANTHROPIC_", "CLAUDE_", "CODEX_", "OPENAI_")
clear = "".join(f'for /f "delims==" %%v in (\'set {p} 2^>nul\') do set "%%v="\n' for p in CLEAR_PREFIXES)
TITLE = f"CIELE2E-DIRECT-PLAIN-{int(time.time())}"
SCRIPT = RUN / "launch.cmd"
SCRIPT.write_text(
    "@echo off\n" + clear + "set CLAUDECODE=\n"
    f'set "CIEL_RUNTIME_CONFIG_DIR={CFG}"\nset "CIEL_RUNTIME_ROUTER_PORT={ROUTER}"\nset "CIEL_RUNTIME_TEST_ISOLATED=1"\n'
    f'set "CODEX_HOME={CODEX_HOME}"\nset "CIEL_RUNTIME_CODEX_APP_SERVER_LISTEN=ws://127.0.0.1:{APPSRV}"\n'
    'set "CIEL_RUNTIME_STARTUP_AUTO_ANSWER=1"\n'
    f'cd /d "{WS}"\npython "{REPO / "ciel_runtime.py"}" cli --ca-runtime codex --ca-no-update-check --ca-no-self-update-check\n'
    "echo LAUNCHER-EXIT=%ERRORLEVEL%\n", encoding="utf-8")


def powershell(script: str) -> str:
    return subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True).stdout


def launcher_pid() -> int:
    out = powershell("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ? { $_.CommandLine -like '*ciel_runtime.py*' -and "
                     "$_.ParentProcessId -in @(Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | ? { $_.CommandLine -like '*"
                     + str(SCRIPT) + "*' } | % { $_.ProcessId }) } | % { $_.ProcessId }").split()
    return int(out[0]) if out else 0


PROVIDER_ARG = re.compile(r"model_providers\.ciel-runtime[\w.-]*\.(?:base_url|env_key)=\S+|model_provider=\S+|-m \S+")


def codex_provider_args() -> list[str]:
    """Provider block / model arguments on this run's codex command lines (the key travels only in env)."""
    out = powershell("Get-CimInstance Win32_Process | ? { $_.CommandLine -like '*model_providers.ciel-runtime*' } | "
                     "% { $_.CommandLine }")
    found = []
    for line in out.splitlines():
        if str(ROUTER) not in line and "ciel-runtime-gateway" not in line:
            continue
        found.append(" ".join(PROVIDER_ARG.findall(line)))
    return found


def http(method: str, path: str, body: dict | None = None, timeout: float = 10) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{ROUTER}{path}", method=method, data=data,
                                 headers={"content-type": "application/json", "accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
    except Exception as e:
        return {"raw": f"{type(e).__name__}: {e}"}
    try:
        return json.loads(raw)
    except Exception:
        return {"raw": raw[:500]}


def shot(label: str) -> None:
    out = SHOTS / f"{len(list(SHOTS.iterdir())) + 1:02d}-{label}.png"
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HERE / "shot_title.ps1"), TITLE, str(out)],
                       capture_output=True, text=True)
    log(f"shot {label}: {r.stdout.strip() or r.stderr.strip()[:200]}")


def log_lines(*needles: str) -> list[str]:
    out = []
    for f in CFG.rglob("*.log*"):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        out += [line[-400:] for line in text.splitlines() if any(n in line for n in needles)]
    return out


def records() -> dict:
    for f in CFG.rglob("launch-data-path.json"):
        try:
            return {"path": str(f.relative_to(SCRATCH)), **json.loads(f.read_text(encoding="utf-8"))}
        except (OSError, ValueError):
            pass
    return {}


def rollout_user_prefixes() -> list[str]:
    """First 60 characters of every user message Codex recorded (what the TUI submitted)."""
    out = []
    for f in sorted((CODEX_HOME / "sessions").rglob("*.jsonl")):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            payload = row.get("payload") or {}
            if row.get("type") == "event_msg" and payload.get("type") == "user_message":
                out.append(f"{f.name[-45:]}: {str(payload.get('message') or '')[:60]}")
    return out


def wait(pred, secs: float, label: str) -> bool:
    end = time.time() + secs
    while time.time() < end:
        try:
            if pred():
                log(f"wait {label}: True")
                return True
        except Exception:
            pass
        time.sleep(0.5)
    log(f"wait {label}: False")
    return False


def notify(text: str) -> None:
    r = http("POST", "/ca/chat/notify", {"message": text, "channel": "e2e", "sender_id": "e2e-driver"})
    log(f"notify {text!r} -> ok={r.get('ok')}")


def turn_events(turn: str) -> list[dict]:
    with LOCK:
        return [dict(e) for e in EVENTS if e.get("turn") == turn]


def kill_all() -> None:
    (RUN / "stop-main").write_text("1")
    ps = ("$ports = @(" + str(ROUTER) + "," + str(APPSRV) + "); "
          "$pids = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | ? { $ports -contains $_.LocalPort } | % { $_.OwningProcess }); "
          "$pids += @(Get-CimInstance Win32_Process | ? { $_.ProcessId -ne $PID -and $_.ProcessId -ne " + str(os.getpid()) + " -and $_.Name -ne 'powershell.exe' -and "
          "-not ($_.CommandLine -like '*e2e_plain_codex.py*') -and "
          "($_.CommandLine -like '*" + str(RUN) + "*' -or $_.CommandLine -like '*" + str(SCRATCH) + "*') } | % { $_.ProcessId }); "
          "$pids | Sort-Object -Unique | % { taskkill /T /F /PID $_ | Out-Null; 'killed ' + $_ }")
    log("kill: " + powershell(ps).strip().replace("\n", " "))


result: dict = {"mode": MODE, "ports": PORTS, "router_port": ROUTER}
try:
    subprocess.Popen(["wt.exe", "-w", "new", "--title", TITLE, "--suppressApplicationTitle", "cmd", "/k", str(SCRIPT)])
    pid = 0
    end = time.time() + 60
    while time.time() < end and not pid:
        pid = launcher_pid()
        time.sleep(0.5)
    log(f"launcher pid {pid}")
    if pid:
        subprocess.Popen([sys.executable, str(HERE / "e2e_console.py"), str(pid), str(RUN), "main"], creationflags=0x00000008)
    wait(lambda: "raw" not in http("GET", "/health", timeout=2), 90, "router up")
    wait(lambda: bool(records()), 60, "launch data path recorded")
    result["record_at_launch"] = records()
    time.sleep(15)
    result["launch_data_path_log_at_launch"] = log_lines("launch_data_path")
    with LOCK:
        result["events_before_turn1"] = [dict(e) for e in EVENTS]
    result["codex_provider_args_launch"] = codex_provider_args()
    shot("launch")
    notify("E2E-TURN-1 please reply")
    wait(lambda: bool(turn_events("1")), 120, "turn 1 reached a gateway")
    time.sleep(8)
    shot("turn1")
    result["restart_response"] = http("POST", "/ca/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                          "params": {"name": "restart_session",
                                                                     "arguments": {"reason": "direct-plain E2E restart"}}})
    log(f"restart response: {json.dumps(result['restart_response'])[:300]}")
    wait(lambda: bool(log_lines("codex_session_restart")), 120, "codex session restarted")
    time.sleep(20)
    result["codex_provider_args_after_restart"] = codex_provider_args()
    shot("after-restart")
    notify("E2E-TURN-2 please reply")
    wait(lambda: bool(turn_events("2")), 120, "turn 2 reached a gateway")
    time.sleep(8)
    shot("turn2")
    with LOCK:
        events = [dict(e) for e in EVENTS]
    result.update({
        "turn1": turn_events("1"),
        "turn2": turn_events("2"),
        "discovery_requests": [e for e in events if e["path"] == "/api/gateway/discovery"],
        "healthz_with_auth": [e for e in events if e["path"] == "/healthz" and e.get("auth")],
        "central_generation_requests": [e for e in events if e["server"] == "central" and e["path"].startswith("/v1/") and e["path"] != "/v1/models"],
        "events": events,
        "record_at_end": records(),
        "launch_data_path_log": log_lines("launch_data_path"),
        "restart_log": log_lines("codex_session_restart"),
        "wake_log": log_lines("channel_stdin_proxy_injected", "channel_llm_injected"),
        "codex_rollout_user_prefixes": rollout_user_prefixes(),
    })
finally:
    (RUN / "result.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    kill_all()
    for server in SERVERS:
        server.shutdown()
    log("done")
