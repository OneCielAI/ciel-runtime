"""E2E: restart_session waits for the running turn, renews the workspace MCP launch, resumes the same conversation.

Usage: python e2e_restart.py <mode>     mode: codex-remote | codex | claude

Isolated: scratch CIEL_RUNTIME_CONFIG_DIR and CODEX_HOME on G:, unique router/app-server ports,
CIEL_RUNTIME_TEST_ISOLATED, every inherited CIEL_RUNTIME_/ANTHROPIC_/CLAUDE_/CODEX_/OPENAI_ variable cleared.
Stub OpenAI-compatible upstream (provider vllm). One workspace MCP server (e2e_mcp_server.py, 3 tools) that
logs every start. Claude Code keeps the default ~/.claude (channel delivery reads its transcript there).

Steps:
  1 turn 1 (channel notify) -> tools the model sees, manifest, MCP server pid
  2 codex-remote: a paused thread goal is set over the app-server
  3 SSE clients on /ca/chat/stream and /ca/events/stream stay open for the whole run
  4 turn 2 is slow (stub sleeps); restart_session is called while it runs
  5 the restart must happen only after turn 2's reply; a new manifest + MCP server pid; restart notice turn
  6 turn 3 -> same conversation (turn 1 text still in the history), same tools, same thread id, goal kept
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
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = Path(os.environ.get("E2E_REPO") or r"C:\Users\djlov\ciel-runtime")
MODE = sys.argv[1]
SLOW = float(os.environ.get("E2E_SLOW_SECONDS") or 25)
RUN = HERE / f"e2e-{MODE}"
shutil.rmtree(RUN, ignore_errors=True)
SCRATCH = Path(r"G:\ciel-e2e\restart") / MODE
shutil.rmtree(SCRATCH, ignore_errors=True)
CFG, WS, CODEX_HOME, SHOTS = SCRATCH / "cfg", SCRATCH / "ws", SCRATCH / "codex-home", RUN / "shots"
for d in (CFG, WS, CODEX_HOME, SHOTS):
    d.mkdir(parents=True, exist_ok=True)
(WS / "README.md").write_text("restart e2e workspace\n")
MCP_STARTS = RUN / "mcp-starts.jsonl"
TITLE = f"CIELE2E-RESTART-{MODE}"
LOG = open(RUN / "driver.log", "w", encoding="utf-8")


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


REQS: list[dict] = []


def _text(m: dict) -> str:
    c = m.get("content")
    return c if isinstance(c, str) else " ".join(str(x.get("text", "")) for x in (c or []) if isinstance(x, dict))


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._json({"object": "list", "data": [{"id": "stub-model", "object": "model", "owned_by": "stub", "max_model_len": 128000}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        msgs = body.get("messages") or []
        users = [_text(m) for m in msgs if m.get("role") == "user"]
        last = users[-1] if users else ""
        tools = sorted({((t.get("function") or {}).get("name") or t.get("name") or "") for t in body.get("tools") or [] if isinstance(t, dict)})
        n = len(REQS) + 1
        record = {"n": n, "at": time.time(), "path": self.path, "users": len(users), "last": " ".join(last.split())[:2000],
                  "history": [w for w in ("E2E-RT-TURN-1", "E2E-RT-SLOWTURN-2", "E2E-RT-TURN-3", "재부팅 완료") if any(w in u for u in users)],
                  "tools": tools}
        REQS.append(record)
        slow = "E2E-RT-SLOWTURN-2" in last and not any("E2E-RT-SLOWTURN-2" in u for u in users[:-1])
        if slow:
            time.sleep(SLOW)
        reply = f"STUB-REPLY-{n}"
        if self.path.split("?")[0].endswith("/v1/messages"):
            message = {"id": f"msg_{n}", "type": "message", "role": "assistant", "model": body.get("model") or "stub-model",
                       "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 2}}
            if not body.get("stream"):
                self._json({**message, "content": [{"type": "text", "text": reply}], "stop_reason": "end_turn"})
            else:
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                for ev in ({"type": "message_start", "message": message},
                           {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                           {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": reply}},
                           {"type": "content_block_stop", "index": 0},
                           {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 2}},
                           {"type": "message_stop"}):
                    self.wfile.write(f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n".encode())
                    self.wfile.flush()
        elif body.get("stream"):
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            base = {"id": f"c{n}", "object": "chat.completion.chunk", "created": int(time.time()), "model": "stub-model"}
            for chunk in ({**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": reply}, "finish_reason": None}]},
                          {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                           "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}):
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        else:
            self._json({"id": f"c{n}", "object": "chat.completion", "created": int(time.time()), "model": "stub-model",
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}})
        record["replied_at"] = time.time()
        with open(RUN / "stub.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


stub = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
STUB = stub.server_address[1]
threading.Thread(target=stub.serve_forever, daemon=True).start()
ROUTER, APPSRV = free_port(), free_port()
(CFG / "config.json").write_text(json.dumps({
    "current_provider": "vllm",
    "language": "en",
    "providers": {"vllm": {"base_url": f"http://127.0.0.1:{STUB}", "current_model": "stub-model", "api_key": "dummy",
                           "context_window": 128000, "max_output_tokens": 4096}},
    "workspace_mcp": {"servers": {"e2e-tools": {
        "enabled": True, "transport": "stdio", "command": sys.executable,
        "args": [str(HERE / "e2e_mcp_server.py"), str(MCP_STARTS)], "runtimes": ["claude", "codex"]}}},
}, indent=2), encoding="utf-8")
(CFG / "log-level").write_text("INFO")
(CODEX_HOME / "config.toml").write_text(f'[projects."{str(WS).replace(chr(92), chr(92) * 2)}"]\ntrust_level = "trusted"\n', encoding="utf-8")
log(f"mode={MODE} stub={STUB} router={ROUTER} appserver={APPSRV} scratch={SCRATCH}")

clear = "".join(f'for /f "delims==" %%v in (\'set {p} 2^>nul\') do set "%%v="\n' for p in ("CIEL_RUNTIME_", "ANTHROPIC_", "CLAUDE_", "CODEX_", "OPENAI_"))
script = RUN / "launch.cmd"
script.write_text(
    "@echo off\n" + clear + "set CLAUDECODE=\n"
    f'set "CIEL_RUNTIME_CONFIG_DIR={CFG}"\nset "CIEL_RUNTIME_ROUTER_PORT={ROUTER}"\nset "CIEL_RUNTIME_TEST_ISOLATED=1"\n'
    f'set "CODEX_HOME={CODEX_HOME}"\nset "CIEL_RUNTIME_CODEX_APP_SERVER_LISTEN=ws://127.0.0.1:{APPSRV}"\n'
    f'cd /d "{WS}"\npython "{REPO / "ciel_runtime.py"}" cli --ca-runtime {MODE} --ca-no-update-check --ca-no-self-update-check\n'
    "echo LAUNCHER-EXIT=%ERRORLEVEL%\n", encoding="utf-8")
subprocess.Popen(["wt.exe", "-w", "new", "--title", TITLE, "--suppressApplicationTitle", "cmd", "/k", str(script)])


def http(method: str, path: str, body: dict | None = None, timeout: float = 10) -> dict:
    req = urllib.request.Request(f"http://127.0.0.1:{ROUTER}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json", "accept": "application/json, text/event-stream"})
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
        out += [line[-320:] for line in text.splitlines() if any(n in line for n in needles)]
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


def manifests() -> list[dict]:
    rows = []
    for path in CFG.rglob("manifest.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("schema") == "ciel-runtime.workspace-mcp-launch/v1":
            rows.append({"launch_id": data["launch_id"], "runtime": data["runtime"], "servers": data["server_names"], "dir": str(path.parent)})
    return rows


def mcp_starts() -> list[dict]:
    return [json.loads(line) for line in MCP_STARTS.read_text(encoding="utf-8").splitlines()] if MCP_STARTS.exists() else []


def thread_ids() -> list[str]:
    ids = []
    for line in router_lines("codex_app_server_channel_target", "codex_remote_app_server_ready", "codex_remote_tui_start"):
        m = re.search(r"(?:thread=|resume=|resume )([0-9a-f]{8}-[0-9a-f-]{20,})", line)
        if m:
            ids.append(m.group(1))
    return ids


def ws_client():
    sys.path.insert(0, str(REPO))
    from ciel_runtime_support.codex_app_server import CodexAppServerClient
    from ciel_runtime_support.codex_app_server_websocket import CodexAppServerWebSocketProcess
    tokens = list(CFG.glob("codex-remote/*/ws-token"))
    token = tokens[0].read_text(encoding="utf-8").strip() if tokens else ""
    client = CodexAppServerClient(CodexAppServerWebSocketProcess.connect(f"ws://127.0.0.1:{APPSRV}", bearer_token=token))
    client.initialize(client_name="e2e-restart", client_title=None, client_version="0")
    return client


def mcp_status() -> dict:
    """What the app-server itself reports: each MCP server, its state and tool names."""
    try:
        result = ws_client().request("mcpServerStatus/list", {"threadId": thread_ids()[-1]})
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    rows = {}
    for server in result.get("data") or []:
        tools = server.get("tools")
        names = sorted(tools) if isinstance(tools, dict) else sorted(str(t.get("name")) for t in tools or [] if isinstance(t, dict))
        rows[server.get("name")] = {"runtimeStatus": server.get("runtimeStatus"), "tools": names, "keys": sorted(server)}
    log("mcpServerStatus/list: " + json.dumps(rows, ensure_ascii=False)[:600])
    return rows


def send_message(text: str) -> None:
    r = http("POST", "/ca/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                 "params": {"name": "send_message", "arguments": {"channel": "e2e", "message": text, "recipients": "web"}}})
    log(f"send_message {text!r} -> {json.dumps(r, ensure_ascii=False)[:200]}")


def ws_goal(method: str, objective: str = "") -> dict:
    sys.path.insert(0, str(REPO))
    from ciel_runtime_support.codex_app_server import CodexAppServerClient
    from ciel_runtime_support.codex_app_server_websocket import CodexAppServerWebSocketProcess
    tokens = list(CFG.glob("codex-remote/*/ws-token"))
    token = tokens[0].read_text(encoding="utf-8").strip() if tokens else ""
    thread = thread_ids()[-1] if thread_ids() else ""
    client = CodexAppServerClient(CodexAppServerWebSocketProcess.connect(f"ws://127.0.0.1:{APPSRV}", bearer_token=token))
    client.initialize(client_name="e2e-restart", client_title=None, client_version="0")
    params = {"threadId": thread}
    if method == "thread/goal/set":
        params.update(objective=objective, status="paused")
    try:
        result = client.request(method, params)
    except Exception as exc:
        result = {"error": f"{type(exc).__name__}: {exc}"}
    log(f"ws {method} thread={thread} -> {json.dumps(result, ensure_ascii=False)[:300]}")
    return result


SSE: dict[str, list[dict]] = {"/ca/chat/stream": [], "/ca/events/stream": []}


def sse_reader(path: str) -> None:
    req = urllib.request.Request(f"http://127.0.0.1:{ROUTER}{path}", headers={"accept": "text/event-stream"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            SSE[path].append({"at": time.time(), "event": "connected"})
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("data:"):
                    SSE[path].append({"at": time.time(), "data": line[5:].strip()[:200]})
                elif line:
                    SSE[path].append({"at": time.time(), "line": line[:80]})
    except Exception as exc:
        SSE[path].append({"at": time.time(), "event": f"closed {type(exc).__name__}: {exc}"})


def launcher_pid() -> int:
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ? { $_.CommandLine -like '*ciel_runtime.py*' -and "
                          "$_.ParentProcessId -in @(Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | ? { $_.CommandLine -like '*"
                          + str(script) + "*' } | % { $_.ProcessId }) } | % { $_.ProcessId }"], capture_output=True, text=True).stdout.split()
    return int(out[0]) if out else 0


result: dict = {"mode": MODE}
try:
    wait(lambda: "raw" not in http("GET", "/health", timeout=2), 90, "router up")
    pid = 0
    wait(lambda: bool(launcher_pid()), 60, "launcher pid")
    pid = launcher_pid()
    watcher = subprocess.Popen([sys.executable, str(HERE / "e2e_restart_console.py"), str(pid), str(RUN)], creationflags=0x00000008)
    for path in SSE:
        threading.Thread(target=sse_reader, args=(path,), daemon=True).start()
    wait(lambda: len(mcp_starts()) >= 1, 120, "MCP server started")
    time.sleep(8 if MODE != "codex-remote" else 3)
    shot("started")

    # 1 first turn
    notify("E2E-RT-TURN-1 hello")
    wait(lambda: any("E2E-RT-TURN-1" in q["last"] for q in REQS), 90, "turn 1 at the stub")
    time.sleep(2)
    before = {"manifests": manifests(), "mcp_starts": mcp_starts(), "threads": thread_ids(),
              "tools": next((q["tools"] for q in REQS if "E2E-RT-TURN-1" in q["last"]), [])}
    log("before: " + json.dumps(before, ensure_ascii=False))

    send_message("E2E-SSE-BEFORE")
    # 2 goal (codex-remote)
    if MODE == "codex-remote":
        before["mcp_status"] = mcp_status()
        ws_goal("thread/goal/set", "E2E-GOAL-KEEP survive the restart")
        result["goal_before"] = ws_goal("thread/goal/get")

    # 4 slow turn + restart_session while it runs
    notify("E2E-RT-SLOWTURN-2 please take your time")
    wait(lambda: any("E2E-RT-SLOWTURN-2" in q["last"] for q in REQS), 90, "slow turn 2 at the stub")
    time.sleep(2)
    requested_at = time.time()
    r = http("POST", "/ca/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                 "params": {"name": "restart_session", "arguments": {"reason": "e2e restart during a turn"}}})
    log("restart_session -> " + json.dumps(r, ensure_ascii=False)[:400])
    time.sleep(4)
    shot("restart-waiting")
    restart_marker = {"codex-remote": "codex_remote_session_restart", "codex": "codex_session_restart", "claude": "claude_session_restart"}[MODE]
    wait(lambda: bool(router_lines(restart_marker)), SLOW + 120, "restart performed")
    restarted_line = (router_lines(restart_marker) or [""])[-1]
    slow = next((q for q in REQS if "E2E-RT-SLOWTURN-2" in q["last"]), {})
    log(f"restart line: {restarted_line}")
    log(f"slow turn reply at {slow.get('replied_at')}, restart requested at {requested_at}")
    claimed = router_lines("runtime_session_restart_claimed")
    log("claimed: " + (claimed[-1] if claimed else "-"))

    # 5 new MCP launch and the restart notice turn
    wait(lambda: len(mcp_starts()) >= 2, 120, "MCP server started again")
    wait(lambda: any("재부팅 완료" in q["last"] for q in REQS), 120, "restart notice turn at the stub")
    time.sleep(3)
    shot("after-restart")

    # 6 turn 3
    notify("E2E-RT-TURN-3 after the restart")
    wait(lambda: any("E2E-RT-TURN-3" in q["last"] for q in REQS), 120, "turn 3 at the stub")
    time.sleep(3)
    shot("turn-3")
    turn3 = next(q for q in REQS if "E2E-RT-TURN-3" in q["last"])
    after = {"manifests": manifests(), "mcp_starts": mcp_starts(), "threads": thread_ids(), "tools": turn3["tools"], "history": turn3["history"]}
    log("after: " + json.dumps(after, ensure_ascii=False))
    send_message("E2E-SSE-AFTER")
    time.sleep(2)
    if MODE == "codex-remote":
        result["goal_after"] = ws_goal("thread/goal/get")
        after["mcp_status"] = mcp_status()
    result.update(before=before, after=after, restart_line=restarted_line,
                  slow_replied_at=slow.get("replied_at"), restart_requested_at=requested_at)
    log("renewal lines:\n  " + "\n  ".join(router_lines("workspace_mcp_launch_renewed", "workspace_mcp_launch_prepared", restart_marker,
                                                       "runtime_session_restart_claimed", "channel_console_restart_requested")[-12:]))
finally:
    (RUN / "stop").write_text("1")
    time.sleep(1)
    for path, events in SSE.items():
        log(f"SSE {path}: {len(events)} items; first={events[:1]}; closed={[e for e in events if 'closed' in str(e.get('event', ''))]}")
    result["chat_messages"] = [{k: m.get(k) for k in ("id", "channel", "sender_id")} for m in (http("GET", "/ca/chat/messages?after=0").get("messages") or [])]
    (RUN / "sse.json").write_text(json.dumps(SSE, indent=1, ensure_ascii=False), encoding="utf-8")
    result["sse"] = {path: {"items": len(events), "first_at": events[0]["at"] if events else None,
                            "data_after_restart": len([e for e in events if "data" in e and e["at"] > (result.get("restart_requested_at") or 0)]),
                            "closed": [e["event"] for e in events if "closed" in str(e.get("event", ""))]} for path, events in SSE.items()}
    (RUN / "result.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    (RUN / "stub-requests.json").write_text(json.dumps(REQS, indent=1, ensure_ascii=False), encoding="utf-8")
    for f in CFG.rglob("router.log"):
        shutil.copy(f, RUN / f"router-{f.parent.name}.log")
    ps = ("$ports = @(" + str(ROUTER) + "," + str(APPSRV) + "); "
          "$pids = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | ? { $ports -contains $_.LocalPort } | % { $_.OwningProcess }); "
          "$pids += @(Get-CimInstance Win32_Process | ? { $_.ProcessId -ne $PID -and $_.ProcessId -ne " + str(os.getpid()) + " -and $_.Name -ne 'powershell.exe' -and "
          "($_.CommandLine -like '*" + str(RUN) + "*' -or $_.CommandLine -like '*" + str(SCRATCH) + "*') } | % { $_.ProcessId }); "
          "$pids | Sort-Object -Unique | % { taskkill /T /F /PID $_ | Out-Null; \"killed $_\" }")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    log("teardown: " + r.stdout.strip().replace("\n", " "))
    log("done")
