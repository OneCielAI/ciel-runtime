"""Isolated actual CLI direct-data-path E2E (loopback synthetic gateways and SSE events).

python -I e2e_direct.py claude|codex-remote|codex
Fresh config/state/workspace/CODEX_HOME/CLAUDE_CONFIG_DIR/ports. Child batch clears inherited
CIEL_RUNTIME_, CLAUDE_, ANTHROPIC_, CODEX_, OPENAI_ and CLAUDECODE. No production code patched.
Central discovery lists A at launch, B after MCP restart_session. Runtime receives two synthetic
CloudEvents on /e2e/stream, injects each 951-character body, and keeps /ca/mcp serving.
Record only gateway path/auth-ok/model/CLI UA/length/head+tail marker booleans, never request bodies.
Assert externally from result.json: both direct flows have zero router model access lines, SSE1 at A,
restart notice + SSE2 at B. Plain Codex must remain router; this generic driver unnecessarily waits
for a second direct selection and B after its restart (those waits are expected to fail for plain).
Screenshots are PrintWindow captures of isolated actual Windows Terminal CLI windows.
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
import uuid
from urllib.parse import urlsplit, parse_qs
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = Path(r"C:\Users\djlov\ciel-runtime")
FLOW = sys.argv[1]
RUN = HERE / f"run-{FLOW}"
shutil.rmtree(RUN, ignore_errors=True)
SCRATCH = Path(r"G:\ciel-e2e\direct-path") / f"{FLOW}-{int(time.time())}"
shutil.rmtree(SCRATCH, ignore_errors=True)
CFG, WS, CODEX_HOME, SHOTS = SCRATCH / "cfg", SCRATCH / "ws", SCRATCH / "codex-home", RUN / "shots"
for d in (CFG, WS, CODEX_HOME, SHOTS):
    d.mkdir(parents=True, exist_ok=True)
LOG = open(RUN / "driver.log", "w", encoding="utf-8")
KEY = "sk-e2e-gateway-0000000000000000"
LOCK = threading.Lock()
SELECTED = ["A"]
ROWS = []
STREAM_GETS = []
UID = uuid.uuid4().hex[:10]
EVENTS: list[dict] = []          # every request any server got: server, path, turn marker, outcome
BREAK_ONCE = {"B": False}          # B truncates its first marked turn


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
        item = {"n": len(EVENTS) + 1, "at": time.strftime("%H:%M:%S"), "server": server, "path": path, **extra}
        EVENTS.append(item)
    with open(RUN / "requests.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(item) + "\n")
    return item


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
            if path == "/e2e/stream":
                return self.event_stream()
            if path == "/healthz" and name != "central":
                record(name, path, auth=bool(self.headers.get("Authorization")))
                body = {"status": "ok", "role": "gateway", "nodeId": f"node-{name}", "ready": True, "online": True}
                return self._send(200, json.dumps(body).encode())
            if path == "/api/gateway/discovery" and name == "central":
                ok = self._authorized()
                record(name, path, auth_ok=ok)
                if not ok:
                    return self._send(401, b'{"error":{"message":"Valid API key required"}}')
                now = time.time()
                rows = []
                for node, util in ((SELECTED[0], 0.1),):
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
                rows = [{"id": "stub-model", "owned_by": "stub", "context_length": 128000, "max_output_tokens": 8192, "max_model_len": 128000,
                         "capabilities": {"tool_calling": True, "reasoning": True, "thinking": True,
                                          "supportsThinking": True}}]
                return self._send(200, json.dumps({"object": "list", "data": rows}).encode())
            self._send(404, b"{}")

        def event_stream(self):
            after = int((parse_qs(urlsplit(self.path).query).get("after") or [self.headers.get("Last-Event-ID") or "0"])[-1])
            STREAM_GETS.append({"at": time.time(), "after": after})
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            start = time.time()
            try:
                while time.time() - start < 50:
                    for row in list(ROWS):
                        if row["seq"] <= after:
                            continue
                        event={"specversion":"1.0", "id":f"e2e-{UID}-{row['seq']}",
                               "type":"com.ciel.e2e.direct.message", "source":"http://127.0.0.1/e2e",
                               "data":{"stream_id":str(row["seq"]), "text":row["text"]}}
                        self.wfile.write(("id: " + str(row["seq"]) + "\ndata: " + json.dumps(event) + "\n\n").encode())
                        self.wfile.flush()
                        after=row["seq"]
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                    time.sleep(1)
                    self.wfile.flush()
                    time.sleep(1)
            except OSError:
                pass
            self.close_connection=True

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("content-length") or 0))
            path = self.path.split("?")[0]
            text = raw.decode("utf-8", "replace")
            body = json.loads(raw or b"{}")
            marker = (re.findall(r"E2E-TURN-(\d+)", text) or [""])[-1]
            truncate = False
            with LOCK:
                if name == "B" and marker == "2" and BREAK_ONCE["B"]:
                    BREAK_ONCE["B"] = False
                    truncate = True
            record(name, path, turn=marker, truncated=truncate, auth_ok=self._authorized(),
                   model=body.get("model"), bytes=len(raw), user_agent=self.headers.get("User-Agent"),
                   head1=f"E2E-DIRECT-1-HEAD-{UID}" in text, tail1=f"E2E-DIRECT-1-TAIL-{UID}" in text,
                   head2=f"E2E-DIRECT-2-HEAD-{UID}" in text, tail2=f"E2E-DIRECT-2-TAIL-{UID}" in text,
                   restart_notice="계속-세션재시작됨" in text)
            if path.endswith("/count_tokens"):
                return self._send(200, b'{"input_tokens":10}')
            if path.endswith("/v1/messages"):
                return self._anthropic(body, truncate)
            if path.endswith("/v1/responses"):
                return self._responses(body, truncate)
            if path.endswith("/v1/chat/completions"):
                return self._chat(body, truncate)
            self._send(404, b"{}")

        def _stream(self, events, truncate):
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("connection", "close")
            self.end_headers()
            for i, (kind, data) in enumerate(events):
                if truncate and i == len(events) - 2:
                    break  # a clean close before the completion event
                self.wfile.write(f"event: {kind}\ndata: {json.dumps(data)}\n\n".encode())
                self.wfile.flush()
            self.close_connection = True

        def _anthropic(self, body, truncate):
            reply = f"STUB-REPLY-{name}"
            message = {"id": "msg_1", "type": "message", "role": "assistant", "model": body.get("model") or "stub-model",
                       "content": [], "stop_reason": None, "stop_sequence": None,
                       "usage": {"input_tokens": 10, "output_tokens": 2}}
            if not body.get("stream"):
                return self._send(200, json.dumps({**message, "content": [{"type": "text", "text": reply}],
                                                   "stop_reason": "end_turn"}).encode())
            self._stream([
                ("message_start", {"type": "message_start", "message": message}),
                ("content_block_start", {"type": "content_block_start", "index": 0,
                                         "content_block": {"type": "text", "text": ""}}),
                ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                         "delta": {"type": "text_delta", "text": reply}}),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                                   "usage": {"output_tokens": 2}}),
                ("message_stop", {"type": "message_stop"}),
            ], truncate)

        def _responses(self, body, truncate):
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
            ], truncate)

        def _chat(self, body, truncate):
            reply = f"STUB-REPLY-{name}"
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("connection", "close")
            self.end_headers()
            base = {"id": "c1", "object": "chat.completion.chunk", "created": int(time.time()), "model": "stub-model"}
            self.wfile.write(f"data: {json.dumps({**base, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': reply}, 'finish_reason': None}]})}\n\n".encode())
            if not truncate:
                self.wfile.write(f"data: {json.dumps({**base, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            self.close_connection = True

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
                                   "max_output_tokens": 4096, "request_timeout_ms": 120000, "gateway_data_path": "direct"}},
}, indent=2), encoding="utf-8")
(CFG / "log-level").write_text("INFO")
(CODEX_HOME / "config.toml").write_text(f"[projects.'{WS}']\ntrust_level = \"trusted\"\n", encoding="utf-8")
ch=SCRATCH / "claude-config"
ch.mkdir(exist_ok=True)
(ch / ".claude.json").write_text(json.dumps({"hasCompletedOnboarding":True,"lastOnboardingVersion":"2.1.299",
    "numStartups":1,"projects":{str(WS):{"hasTrustDialogAccepted":True,"allowedTools":[]}}}), encoding="utf-8")
log(f"flow={FLOW} central={PORTS['central']} A={PORTS['A']} B={PORTS['B']} router={ROUTER} scratch={SCRATCH}")

clear = "".join(f'for /f "delims==" %%v in (\'set {p} 2^>nul\') do set "%%v="\n' for p in ("CIEL_RUNTIME_", "ANTHROPIC_", "CLAUDE_", "CODEX_", "OPENAI_"))
TITLE = f"CIELE2E-DIRECT-{FLOW}-{int(time.time())}"
SCRIPT = RUN / "launch.cmd"
SCRIPT.write_text(
    "@echo off\n" + clear + "set CLAUDECODE=\n"
    f'set "CIEL_RUNTIME_CONFIG_DIR={CFG}"\nset "CIEL_RUNTIME_ROUTER_PORT={ROUTER}"\nset "CIEL_RUNTIME_TEST_ISOLATED=1"\n'
    f'set "CLAUDE_CONFIG_DIR={SCRATCH / "claude-config"}"\nset "CODEX_HOME={CODEX_HOME}"\nset "CIEL_RUNTIME_CODEX_APP_SERVER_LISTEN=ws://127.0.0.1:{APPSRV}"\n'
    'set "CIEL_RUNTIME_CODEX_CLEAN_UNTRACKED=0"\nset "CIEL_RUNTIME_STARTUP_AUTO_ANSWER=1"\n'
    f'cd /d "{WS}"\npython "{REPO / "ciel_runtime.py"}" cli --ca-runtime {FLOW} --ca-no-update-check --ca-no-self-update-check\n'
    "echo LAUNCHER-EXIT=%ERRORLEVEL%\n", encoding="utf-8")


def launcher_pid() -> int:
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ? { $_.CommandLine -like '*ciel_runtime.py*' -and "
                          "$_.ParentProcessId -in @(Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | ? { $_.CommandLine -like '*"
                          + str(SCRIPT) + "*' } | % { $_.ProcessId }) } | % { $_.ProcessId }"], capture_output=True, text=True).stdout.split()
    return int(out[0]) if out else 0


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


def router_lines(*needles: str) -> list[str]:
    out = []
    for f in CFG.rglob("*.log*"):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        out += [line[-400:] for line in text.splitlines() if any(n in line for n in needles)]
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


def turn_servers(turn: str) -> list[str]:
    with LOCK:
        return [e["server"] for e in EVENTS if e.get("turn") == turn]


def kill_all() -> None:
    (RUN / "stop-main").write_text("1")
    ps = ("$ports = @(" + str(ROUTER) + "," + str(APPSRV) + "); "
          "$pids = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | ? { $ports -contains $_.LocalPort } | % { $_.OwningProcess }); "
          "$pids += @(Get-CimInstance Win32_Process | ? { $_.ProcessId -ne $PID -and $_.ProcessId -ne " + str(os.getpid()) + " -and $_.Name -ne 'powershell.exe' -and "
          "-not ($_.CommandLine -like '*e2e_direct.py*') -and "
          "($_.CommandLine -like '*" + str(RUN) + "*' -or $_.CommandLine -like '*" + str(SCRATCH) + "*') } | % { $_.ProcessId }); "
          "$pids | Sort-Object -Unique | % { taskkill /T /F /PID $_ | Out-Null; \"killed $_\" }")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    log("kill: " + r.stdout.strip().replace("\n", " "))


result: dict = {"flow": FLOW, "ports": PORTS}
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
    wait(lambda: bool(router_lines("launch_data_path ")), 60, "launch path resolved")
    r=http("POST", "/ca/events/receivers/default", {"enabled":True,"transport":"sse",
        "url":f"http://127.0.0.1:{PORTS['central']}/e2e/stream", "event_types":["com.ciel.e2e.direct.message"],
        "cursor_json_pointer":"/data/stream_id", "cursor_query_parameter":"after"})
    log(f"receiver configured ok={r.get('ok')}")
    wait(lambda: bool(STREAM_GETS), 40, "Runtime receiver SSE connection")
    time.sleep(15)
    shot("started")
    def publish(k):
        text=f"E2E-DIRECT-{k}-HEAD-{UID}\n" + "synthetic filler line\n"*40 + f"E2E-DIRECT-{k}-TAIL-{UID} reply briefly"
        ROWS.append({"seq":k,"text":text})
        log(f"published synthetic SSE event {k} chars={len(text)}")
    def hit(k, server=None):
        return [e for e in EVENTS if e.get(f"head{k}") and e.get(f"tail{k}") and (server is None or e["server"]==server)]
    publish(1)
    wait(lambda: bool(hit(1)), 150, "whole SSE event 1 at gateway")
    time.sleep(7)
    shot("sse1")
    result["sse1"]=hit(1)
    SELECTED[0]="B"
    restart_at=time.time()
    r=http("POST", "/ca/mcp", {"jsonrpc":"2.0","id":1,"method":"tools/call",
        "params":{"name":"restart_session","arguments":{"reason":"e2e direct selection restart"}}})
    log("restart_session response="+json.dumps(r,ensure_ascii=False)[:400])
    wait(lambda: len(router_lines("launch_data_path "))>=2, 120, "relaunch selection")
    wait(lambda: any(e.get("restart_notice") and e["server"]=="B" for e in EVENTS),150,"restart notice directly at B")
    time.sleep(6)
    publish(2)
    wait(lambda: bool(hit(2,"B")), 150, "whole SSE event 2 directly at B")
    time.sleep(7)
    shot("sse2-after-restart")
    result.update({"sse2":hit(2), "events":list(EVENTS), "stream_gets":STREAM_GETS,
        "data_path_lines":router_lines("launch_data_path "),
        "router_model_http":router_lines('access "POST /v1/messages','access "POST /v1/responses','access "POST /v1/chat/completions'),
        "router_control_http":router_lines('access "POST /ca/','access "GET /ca/')[-15:],
        "restart_lines":router_lines("session_restart","restart_notice_queued")[-20:],
        "channel_lines":router_lines("external_event_","channel_")[-30:]})

finally:
    (RUN / "result.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    for f in CFG.rglob("router.log"):
        shutil.copy(f, RUN / f"router-{f.parent.name}.log")
    kill_all()
    for server in SERVERS:
        server.shutdown()
    log("done")
