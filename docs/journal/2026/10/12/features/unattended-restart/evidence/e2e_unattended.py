"""E2E: unattended restart - startup questions answered, "계속-세션재시작됨" sent, same conversation.

Usage: python e2e_unattended.py <flow>     flow: codex | claude

Isolated: scratch CIEL_RUNTIME_CONFIG_DIR and CODEX_HOME on G:, unique router/app-server ports,
CIEL_RUNTIME_TEST_ISOLATED, inherited CIEL_RUNTIME_/ANTHROPIC_/CLAUDE_/CODEX_/OPENAI_ variables cleared,
stub OpenAI-compatible upstream (provider vllm). Nobody presses a key: the console watcher only reads screens.
Claude Code keeps the default ~/.claude (its trust answer for the scratch folder lands in ~/.claude.json).

codex flow (the workspace is a git repo with no Codex trust entry, so Codex asks "Trust this folder?"):
  A  launch-state says codex-remote; `--ca-resume-last` with no saved thread -> trust answered, new thread,
     the stub gets "계속-세션재시작됨"; turn 1; a paused goal is set over the app-server
  B  restart_session -> "Resume paused goal?" answered Leave paused, "계속-세션재시작됨", same thread, goal paused
  C  "reboot": every process killed; launch-state says codex (plain TUI); `--ca-resume-last` -> resume --last,
     goal prompt answered, "계속-세션재시작됨" with turn 1 in the history
  D  "reboot" again; launch-state codex-remote; `--ca-resume-last` -> same thread, goal prompt answered, message
claude flow (fresh folder, so Claude Code shows its folder trust dialog):
  A  `--ca-resume-last` with no conversation -> trust answered, new conversation, "계속-세션재시작됨"; turn 1
  B  restart_session -> "계속-세션재시작됨", turn 1 in the history
  C  "reboot"; `--ca-resume-last` -> --continue, "계속-세션재시작됨", turn 1 in the history
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = Path(os.environ.get("E2E_REPO") or r"C:\Users\djlov\ciel-runtime")
FLOW = sys.argv[1]
MESSAGE = "계속-세션재시작됨"
RUN = HERE / f"e2e-{FLOW}"
shutil.rmtree(RUN, ignore_errors=True)
SCRATCH = Path(r"G:\ciel-e2e\unattended") / FLOW
shutil.rmtree(SCRATCH, ignore_errors=True)
CFG, WS, CODEX_HOME, SHOTS = SCRATCH / "cfg", SCRATCH / "ws", SCRATCH / "codex-home", RUN / "shots"
for d in (CFG, WS, CODEX_HOME, SHOTS):
    d.mkdir(parents=True, exist_ok=True)
(WS / "README.md").write_text("unattended restart e2e workspace\n")
subprocess.run(["git", "init", "-q", str(WS)], check=False)
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
        n = len(REQS) + 1
        record = {"n": n, "at": time.time(), "phase": PHASE[0], "path": self.path, "users": len(users),
                  "last": " ".join(last.split())[:1000],
                  # Claude Code puts its <system-reminder> blocks ahead of what was typed.
                  "tail": " ".join(last.split())[-200:],
                  "history_turn1": any("E2E-UA-TURN-1" in u for u in users),
                  "continue_messages_in_history": sum(1 for u in users if u.strip() == MESSAGE or u.strip().endswith(MESSAGE))}
        REQS.append(record)
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
        with open(RUN / "stub.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


PHASE = ["-"]
stub = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
STUB = stub.server_address[1]
threading.Thread(target=stub.serve_forever, daemon=True).start()
ROUTER, APPSRV = free_port(), free_port()
(CFG / "config.json").write_text(json.dumps({
    "current_provider": "vllm",
    "language": "en",
    "providers": {"vllm": {"base_url": f"http://127.0.0.1:{STUB}", "current_model": "stub-model", "api_key": "dummy",
                           "context_window": 128000, "max_output_tokens": 4096}},
}, indent=2), encoding="utf-8")
(CFG / "log-level").write_text("INFO")
# No [projects] trust entry: Codex asks "Trust this folder?" for the git workspace.
(CODEX_HOME / "config.toml").write_text("", encoding="utf-8")
WS_KEY = str(WS.resolve())
log(f"flow={FLOW} stub={STUB} router={ROUTER} appserver={APPSRV} scratch={SCRATCH}")


def set_last_runtime(mode: str) -> None:
    """launch-state.json as a previous launch in this folder would have left it."""
    path = CFG / "launch-state.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    item = {"cwd": WS_KEY, "provider": "vllm", "mode": mode, "model": "stub-model", "pid": 0, "time": time.time()}
    state.setdefault("by_cwd", {})[WS_KEY] = item
    state["last"] = item
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    log(f"launch-state by_cwd mode={mode}")


clear = "".join(f'for /f "delims==" %%v in (\'set {p} 2^>nul\') do set "%%v="\n' for p in ("CIEL_RUNTIME_", "ANTHROPIC_", "CLAUDE_", "CODEX_", "OPENAI_"))


def launch(tag: str) -> str:
    """A new terminal window running the reboot-script command (python entry point of this checkout)."""
    title = f"CIELE2E-UA-{FLOW}-{tag}"
    script = RUN / f"launch-{tag}.cmd"
    script.write_text(
        "@echo off\n" + clear + "set CLAUDECODE=\n"
        f'set "CIEL_RUNTIME_CONFIG_DIR={CFG}"\nset "CIEL_RUNTIME_ROUTER_PORT={ROUTER}"\nset "CIEL_RUNTIME_TEST_ISOLATED=1"\n'
        f'set "CODEX_HOME={CODEX_HOME}"\nset "CIEL_RUNTIME_CODEX_APP_SERVER_LISTEN=ws://127.0.0.1:{APPSRV}"\n'
        f'cd /d "{WS}"\npython "{REPO / "ciel_runtime.py"}" cli --ca-resume-last\n'
        "echo LAUNCHER-EXIT=%ERRORLEVEL%\n", encoding="utf-8")
    subprocess.Popen(["wt.exe", "-w", "new", "--title", title, "--suppressApplicationTitle", "cmd", "/k", str(script)])
    log(f"launched {tag}: {script.name}")
    pid = 0
    end = time.time() + 60
    while time.time() < end and not pid:
        pid = launcher_pid(script)
        time.sleep(0.5)
    log(f"launcher pid {tag}: {pid}")
    if pid:
        subprocess.Popen([sys.executable, str(HERE / "e2e_unattended_console.py"), str(pid), str(RUN), tag], creationflags=0x00000008)
    return title


def launcher_pid(script: Path) -> int:
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ? { $_.CommandLine -like '*ciel_runtime.py*' -and "
                          "$_.ParentProcessId -in @(Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | ? { $_.CommandLine -like '*"
                          + str(script) + "*' } | % { $_.ProcessId }) } | % { $_.ProcessId }"], capture_output=True, text=True).stdout.split()
    return int(out[0]) if out else 0


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


def shot(title: str, label: str) -> None:
    out = SHOTS / f"{len(list(SHOTS.iterdir())) + 1:02d}-{label}.png"
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HERE / "shot_title.ps1"), title, str(out)],
                       capture_output=True, text=True)
    log(f"shot {label}: {r.stdout.strip() or r.stderr.strip()[:200]}")


def router_lines(*needles: str) -> list[str]:
    out = []
    for f in CFG.rglob("*.log*"):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        out += [line[-360:] for line in text.splitlines() if any(n in line for n in needles)]
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


def continue_requests(phase: str) -> list[dict]:
    return [q for q in REQS if q["phase"] == phase and (q["last"].strip() == MESSAGE or q["tail"].endswith(" " + MESSAGE))]


def thread_ids() -> list[str]:
    ids = []
    for line in router_lines("codex_app_server_channel_target", "codex_remote_app_server_ready", "codex_remote_tui_start"):
        m = re.search(r"(?:thread=|resume=|resume )([0-9a-f]{8}-[0-9a-f-]{20,})", line)
        if m:
            ids.append(m.group(1))
    return ids


def ws_goal(method: str, objective: str = "") -> dict:
    sys.path.insert(0, str(REPO))
    from ciel_runtime_support.codex_app_server import CodexAppServerClient
    from ciel_runtime_support.codex_app_server_websocket import CodexAppServerWebSocketProcess
    tokens = list(CFG.glob("codex-remote/*/ws-token"))
    token = tokens[0].read_text(encoding="utf-8").strip() if tokens else ""
    thread = thread_ids()[-1] if thread_ids() else ""
    params = {"threadId": thread}
    if method == "thread/goal/set":
        params.update(objective=objective, status="paused")
    try:
        client = CodexAppServerClient(CodexAppServerWebSocketProcess.connect(f"ws://127.0.0.1:{APPSRV}", bearer_token=token))
        client.initialize(client_name="e2e-unattended", client_title=None, client_version="0")
        result = client.request(method, params)
    except Exception as exc:
        result = {"error": f"{type(exc).__name__}: {exc}"}
    log(f"ws {method} thread={thread} -> {json.dumps(result, ensure_ascii=False)[:300]}")
    return result


def kill_all(tag: str) -> None:
    """A reboot: every process of this run goes away at once."""
    (RUN / f"stop-{tag}").write_text("1")
    ps = ("$ports = @(" + str(ROUTER) + "," + str(APPSRV) + "); "
          "$pids = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | ? { $ports -contains $_.LocalPort } | % { $_.OwningProcess }); "
          "$pids += @(Get-CimInstance Win32_Process | ? { $_.ProcessId -ne $PID -and $_.ProcessId -ne " + str(os.getpid()) + " -and $_.Name -ne 'powershell.exe' -and "
          "-not ($_.CommandLine -like '*e2e_unattended.py*') -and "
          "($_.CommandLine -like '*" + str(RUN) + "*' -or $_.CommandLine -like '*" + str(SCRATCH) + "*') } | % { $_.ProcessId }); "
          "$pids | Sort-Object -Unique | % { taskkill /T /F /PID $_ | Out-Null; \"killed $_\" }")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    log(f"kill {tag}: " + r.stdout.strip().replace("\n", " "))
    time.sleep(3)


def phase_result(phase: str, extra: dict | None = None) -> dict:
    hits = continue_requests(phase)
    out = {"continue_message_requests": [{k: q[k] for k in ("n", "tail", "history_turn1", "continue_messages_in_history")} for q in hits],
           "answered": [line for line in router_lines("startup_prompt_answered") if line not in ANSWERED_SEEN],
           "threads": thread_ids()[-3:]}
    ANSWERED_SEEN.update(out["answered"])
    out.update(extra or {})
    log(f"phase {phase}: " + json.dumps(out, ensure_ascii=False))
    return out


ANSWERED_SEEN: set[str] = set()
result: dict = {"flow": FLOW, "phases": {}}
try:
    if FLOW == "codex":
        PHASE[0] = "A"
        set_last_runtime("codex-remote")
        title = launch("A")
        wait(lambda: "raw" not in http("GET", "/health", timeout=2), 90, "router up")
        wait(lambda: bool(continue_requests("A")), 150, "A: continue message at the stub")
        time.sleep(3)
        shot(title, "A-started-new-thread")
        notify("E2E-UA-TURN-1 hello")
        wait(lambda: any("E2E-UA-TURN-1" in q["last"] for q in REQS), 90, "A: turn 1 at the stub")
        time.sleep(2)
        goal = ws_goal("thread/goal/set", "E2E-UA-GOAL stay paused")
        result["phases"]["A"] = phase_result("A", {"goal_set": goal})

        PHASE[0] = "B"
        r = http("POST", "/ca/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                     "params": {"name": "restart_session", "arguments": {"reason": "e2e unattended restart"}}})
        log("restart_session -> " + json.dumps(r, ensure_ascii=False)[:300])
        wait(lambda: bool(continue_requests("B")), 150, "B: continue message at the stub")
        time.sleep(4)
        shot(title, "B-after-restart_session")
        result["phases"]["B"] = phase_result("B", {"goal": ws_goal("thread/goal/get")})
        kill_all("A")

        PHASE[0] = "C"
        set_last_runtime("codex")
        title = launch("C")
        wait(lambda: bool(continue_requests("C")), 150, "C: continue message at the stub")
        time.sleep(4)
        shot(title, "C-plain-codex-after-reboot")
        result["phases"]["C"] = phase_result("C")
        kill_all("C")

        PHASE[0] = "D"
        set_last_runtime("codex-remote")
        title = launch("D")
        wait(lambda: bool(continue_requests("D")), 150, "D: continue message at the stub")
        time.sleep(4)
        shot(title, "D-codex-remote-after-reboot")
        result["phases"]["D"] = phase_result("D", {"goal": ws_goal("thread/goal/get")})
        kill_all("D")
    else:
        PHASE[0] = "A"
        title = launch("A")
        wait(lambda: "raw" not in http("GET", "/health", timeout=2), 90, "router up")
        wait(lambda: bool(continue_requests("A")), 150, "A: continue message at the stub")
        time.sleep(3)
        shot(title, "A-started-new-conversation")
        notify("E2E-UA-TURN-1 hello")
        wait(lambda: any("E2E-UA-TURN-1" in q["last"] for q in REQS), 90, "A: turn 1 at the stub")
        time.sleep(3)
        result["phases"]["A"] = phase_result("A")

        PHASE[0] = "B"
        r = http("POST", "/ca/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                     "params": {"name": "restart_session", "arguments": {"reason": "e2e unattended restart"}}})
        log("restart_session -> " + json.dumps(r, ensure_ascii=False)[:300])
        wait(lambda: bool(continue_requests("B")), 150, "B: continue message at the stub")
        time.sleep(4)
        shot(title, "B-after-restart_session")
        result["phases"]["B"] = phase_result("B")
        kill_all("A")

        PHASE[0] = "C"
        title = launch("C")
        wait(lambda: bool(continue_requests("C")), 150, "C: continue message at the stub")
        time.sleep(4)
        shot(title, "C-after-reboot")
        result["phases"]["C"] = phase_result("C")
        kill_all("C")
finally:
    result["router_lines"] = router_lines("startup_prompt_", "restart_notice_queued", "session_restart ", "codex_remote_session_restart",
                                          "resume --last ->", "codex_remote_app_server_ready")[-60:]
    (RUN / "result.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    (RUN / "stub-requests.json").write_text(json.dumps(REQS, indent=1, ensure_ascii=False), encoding="utf-8")
    for f in CFG.rglob("router.log"):
        shutil.copy(f, RUN / f"router-{f.parent.name}.log")
    kill_all("final")
    log("done")
