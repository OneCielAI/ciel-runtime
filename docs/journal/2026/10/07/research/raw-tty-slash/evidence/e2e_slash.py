"""E2E: session control (compact_session/new_session) and channel delivery per runtime.

Usage: python e2e_rc.py <mode> [steps...]
  mode: claude | codex | codex-remote | codex-app-server
Isolated: scratch CIEL_RUNTIME_CONFIG_DIR, unique router port, scratch workspace,
stub OpenAI-compatible upstream (provider vllm); every inherited CIEL_RUNTIME_*,
ANTHROPIC_*, CLAUDE_*, CODEX_* variable is cleared in the launch window.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = Path(os.environ.get("E2E_REPO") or r"C:\Users\djlov\ciel-runtime")
MODE = sys.argv[1]
RUN = HERE / f"e2e-{MODE}-{int(time.time())}"
RUN.mkdir()
CFG, WS, SHOTS = RUN / "cfg", Path(os.environ["E2E_WORKSPACE"]) if os.environ.get("E2E_WORKSPACE") else RUN / "workspace", RUN / "shots"
for d in (CFG, WS, SHOTS):
    d.mkdir(parents=True, exist_ok=True)
(WS / "README.md").write_text("e2e workspace\n")
TITLE = f"CIELE2E-{MODE}-{RUN.name[-5:]}"
LOG = open(RUN / "driver.log", "a", encoding="utf-8")


def log(m: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {m}"
    print(line, flush=True)
    LOG.write(line + "\n"); LOG.flush()


def free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


# ---- stub upstream (OpenAI chat completions) ----
REQS: list[dict] = []


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def do_GET(self):
        self._json({"object": "list", "data": [{"id": "stub-model", "object": "model", "owned_by": "stub", "max_model_len": 128000}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        msgs = body.get("messages") or []
        def text(m):
            c = m.get("content")
            return c if isinstance(c, str) else " ".join(str(x.get("text", "")) for x in (c or []) if isinstance(x, dict))
        def plain(m):
            # Claude Code prepends <system-reminder> blocks; keep the person's text.
            c = m.get("content")
            blocks = [{"text": c}] if isinstance(c, str) else [x for x in (c or []) if isinstance(x, dict) and x.get("type") in (None, "text")]
            kept = [str(b.get("text", "")) for b in blocks if not str(b.get("text", "")).lstrip().startswith("<system-reminder>")]
            return " ".join(kept) or text(m)
        users = [plain(m) for m in msgs if m.get("role") == "user"]
        anthropic_system = body.get("system")
        system = " ".join(text(m) for m in msgs if m.get("role") == "system") + " " + (anthropic_system if isinstance(anthropic_system, str) else " ".join(str(b.get("text", "")) for b in (anthropic_system or []) if isinstance(b, dict)))
        last = users[-1] if users else ""
        blob = (system + " " + last).lower()
        kind = "compact" if any(k in blob for k in ("summar", "compact", "checkpoint")) and "title" not in blob[:400] else "turn"
        n = len(REQS) + 1
        reply = f"STUB-SUMMARY-{n}" if kind == "compact" else f"STUB-REPLY-{n}"
        REQS.append({"n": n, "path": self.path, "kind": kind, "users": len(users), "last": " ".join(last.split())[:120]})
        with open(RUN / "stub.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(REQS[-1]) + "\n")
        with open(RUN / "stub-full.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"n": n, "users": users, "tools": [((t.get("function") or {}).get("name") or t.get("name")) for t in body.get("tools") or [] if isinstance(t, dict)], "last_role": (msgs[-1].get("role") if msgs else None), "stream": body.get("stream")}, ensure_ascii=False) + "\n")
        if "SLOWTURN" in last:
            # Keep the CLI busy so channel messages arrive mid-turn.
            time.sleep(float(os.environ.get("E2E_SLOW_SECONDS") or 45))
        if self.path.split("?")[0].endswith("/v1/messages"):
            usage = {"input_tokens": 10, "output_tokens": 2}
            message = {"id": f"msg_{n}", "type": "message", "role": "assistant", "model": body.get("model") or "stub-model",
                       "content": [], "stop_reason": None, "stop_sequence": None, "usage": usage}
            if not body.get("stream"):
                self._json({**message, "content": [{"type": "text", "text": reply}], "stop_reason": "end_turn"})
                return
            self.send_response(200); self.send_header("content-type", "text/event-stream"); self.end_headers()
            for ev in ({"type": "message_start", "message": message},
                       {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                       {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": reply}},
                       {"type": "content_block_stop", "index": 0},
                       {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 2}},
                       {"type": "message_stop"}):
                self.wfile.write(f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n".encode()); self.wfile.flush()
            return
        tool_names = [((t.get("function") or {}).get("name") or t.get("name")) for t in body.get("tools") or [] if isinstance(t, dict)]
        if body.get("tools") and not (RUN / "tools-schema.json").exists():
            (RUN / "tools-schema.json").write_text(json.dumps(body.get("tools"), indent=1, ensure_ascii=False), encoding="utf-8")
        tool_outputs = [text(m) for m in msgs if m.get("role") == "tool"]
        if tool_outputs:
            with open(RUN / "stub-tools.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"n": n, "tool_outputs": tool_outputs[-1:]}, ensure_ascii=False) + "\n")
        wants_tool = "RUN-CMD" in last and (msgs[-1].get("role") != "tool" if msgs else True) and (
            "exec_command" in tool_names or "functions__exec" in tool_names)
        if wants_tool and body.get("stream"):
            self.send_response(200); self.send_header("content-type", "text/event-stream"); self.end_headers()
            base = {"id": f"c{n}", "object": "chat.completion.chunk", "created": int(time.time()), "model": "stub-model"}
            if "functions__exec" in tool_names:
                # codex 0.160 code mode: one JS cell that calls the nested exec_command tool.
                source = "const r = await tools.exec_command({cmd: 'echo RAN-OK-E2E-" + str(n) + "'});\ntext(r);"
                call = {"index": 0, "id": f"call_{n}", "type": "function",
                        "function": {"name": "functions__exec", "arguments": json.dumps({"input": source})}}
            else:
                call = {"index": 0, "id": f"call_{n}", "type": "function",
                        "function": {"name": "exec_command", "arguments": json.dumps({"cmd": "echo RAN-OK-E2E-" + str(n)})}}
            for chunk in (
                {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [call]}, "finish_reason": None}]},
                {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}},
            ):
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode()); self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush()
            return
        if body.get("stream"):
            self.send_response(200); self.send_header("content-type", "text/event-stream"); self.end_headers()
            base = {"id": f"c{n}", "object": "chat.completion.chunk", "created": int(time.time()), "model": "stub-model"}
            for chunk in (
                {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": reply}, "finish_reason": None}]},
                {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}},
            ):
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode()); self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush()
        else:
            self._json({"id": f"c{n}", "object": "chat.completion", "created": int(time.time()), "model": "stub-model",
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}})


stub = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
STUB = stub.server_address[1]
threading.Thread(target=stub.serve_forever, daemon=True).start()
ROUTER = free_port()
APPSRV = free_port()
(CFG / "config.json").write_text(json.dumps({
    "current_provider": "vllm",
    "language": "en",
    "providers": {"vllm": {"base_url": f"http://127.0.0.1:{STUB}", "current_model": "stub-model", "api_key": "dummy",
                           "context_window": 128000, "max_output_tokens": 4096}},
}, indent=2))
(CFG / "log-level").write_text("INFO")
log(f"run={RUN} stub=127.0.0.1:{STUB} router={ROUTER} title={TITLE}")

# ---- launch window ----
clear = "".join(f'for /f "delims==" %%v in (\'set {p} 2^>nul\') do set "%%v="\n' for p in ("CIEL_RUNTIME_", "ANTHROPIC_", "CLAUDE_", "CODEX_", "OPENAI_"))
script = RUN / "launch.cmd"
script.write_text(
    "@echo off\n" + clear + "set CLAUDECODE=\n"
    f'set "CIEL_RUNTIME_CONFIG_DIR={CFG}"\nset "CIEL_RUNTIME_ROUTER_PORT={ROUTER}"\n'
    + (f'set "CODEX_HOME={os.environ["E2E_CODEX_HOME"]}"\n' if os.environ.get("E2E_CODEX_HOME") else "")
    + ('' if os.environ.get('E2E_NO_LISTEN') else f'set "CIEL_RUNTIME_CODEX_APP_SERVER_LISTEN=ws://127.0.0.1:{APPSRV}"\n')
    + (f'set "PATH={os.environ["E2E_CODEX_BIN"]};%PATH%"\n' if os.environ.get("E2E_CODEX_BIN") else "")
    + (f'set "CIEL_RUNTIME_CODEX_ROUTED_UPSTREAM=http://127.0.0.1:{STUB}"\n' if os.environ.get('E2E_ROUTED_STUB') else '')
    + f'set "CIEL_DIAG_RECEIPT_LOG={RUN / "receipt-diag.log"}"\n'
    + f'cd /d "{WS}"\n'
    f'python "{REPO / "ciel_runtime.py"}" cli --ca-runtime {MODE} --ca-no-update-check --ca-no-self-update-check{(" " + os.environ["E2E_EXTRA_ARGS"]) if os.environ.get("E2E_EXTRA_ARGS") else ""}\n'
    "echo LAUNCHER-EXIT=%ERRORLEVEL%\n", encoding="utf-8")
subprocess.Popen(["wt.exe", "-w", "new", "--title", TITLE, "--suppressApplicationTitle", "cmd", "/k", str(script)])


def shot(label: str) -> None:
    out = SHOTS / f"{len(list(SHOTS.iterdir())) + 1:02d}-{label}.png"
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HERE / "shot_title.ps1"), TITLE, str(out)], capture_output=True, text=True)
    log(f"shot {label}: {r.stdout.strip() or r.stderr.strip()[:200]}")


def keys(text: str) -> None:
    """Send keys to the test window (only my own E2E window, found by its unique title)."""
    ps = (f"$w = New-Object -ComObject WScript.Shell; if ($w.AppActivate('{TITLE}')) {{ Start-Sleep -Milliseconds 400; $w.SendKeys('{text}') ; 'sent' }} else {{ 'no window' }}")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    log(f"keys {text!r}: {r.stdout.strip()}")


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


def mcp(name: str, args: dict | None = None) -> dict:
    r = http("POST", "/ca/mcp", {"jsonrpc": "2.0", "id": int(time.time()), "method": "tools/call", "params": {"name": name, "arguments": args or {}}})
    log(f"mcp {name} -> {json.dumps(r)[:300]}")
    return r


def notify(text: str, transport: str | None = None) -> dict:
    body = {"message": text, "channel": "e2e", "sender_id": "e2e-driver"}
    if transport:
        body["input_transport"] = transport
    r = http("POST", "/ca/chat/notify", body)
    log(f"notify {text!r} -> {json.dumps(r)[:200]}")
    return r


def router_log_lines(*needles: str) -> list[str]:
    out = []
    for f in CFG.rglob("router.log*"):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if any(n in line for n in needles):
                out.append(line[-260:])
    return out


def wait_router(secs: float = 90) -> bool:
    end = time.time() + secs
    while time.time() < end:
        r = http("GET", "/health", timeout=2)
        if r and "raw" not in r:
            return True
        time.sleep(1)
    return False


def wait_stub(pred, secs: float = 60) -> bool:
    end = time.time() + secs
    while time.time() < end:
        if any(pred(q) for q in REQS):
            return True
        time.sleep(0.5)
    return False


def dump() -> None:
    log("stub requests: " + json.dumps([(q["n"], q["kind"], q["users"], q["last"][:60]) for q in REQS]))
    for line in router_log_lines("channel_compact_request", "codex_app_server", "codex_desktop_channel", "codex_remote", "channel_windows_console_proxy_started", "channel_stdin_injected", "channel_wake"):
        log("  LOG " + line)


# ---- command loop: append lines to <run>/cmd.txt; results go to driver.log ----
(HERE / f"e2e-current-{MODE}.txt").write_text(str(RUN))
log(f"router up: {wait_router()}")
CMD = RUN / "cmd.txt"
CMD.write_text("")
done = 0
while True:
    lines = CMD.read_text(encoding="utf-8").splitlines()
    for line in lines[done:]:
        done += 1
        verb, _, arg = line.strip().partition(" ")
        try:
            if verb == "shot":
                shot(arg or "state")
            elif verb == "notify":
                transport, _, text = arg.partition(" ") if arg.startswith(("tty ", "session_socket ", "router ")) else ("", "", arg)
                notify(text, transport or None)
            elif verb == "mcp":
                mcp(arg.strip())
            elif verb == "keys":
                keys(arg)
            elif verb == "waitstub":
                kind, _, count = arg.partition(" ")
                ok = wait_stub(lambda q, k=kind: q["kind"] == k and q["n"] > int(count or 0), 90)
                log(f"waitstub {kind}>{count}: {ok}")
            elif verb == "waitlast":
                ok = wait_stub(lambda q, t=arg: t in q["last"], 90)
                log(f"waitlast {arg!r}: {ok}")
            elif verb == "sleep":
                time.sleep(float(arg))
            elif verb == "dump":
                dump()
            elif verb == "raw":
                transport, _, text = arg.partition(" ")
                r = http("POST", "/ca/chat/messages", {"message": text, "channel": "e2e", "sender_id": "e2e-driver",
                                                       "input_mode": "tty", "input_transport": transport,
                                                       "response_mode": "tty", "raw_injection": True})
                log(f"raw[{transport}] {text!r} -> {json.dumps(r, ensure_ascii=False)[:300]}")
            elif verb == "goals":
                import sqlite3
                home = Path(os.environ.get("E2E_CODEX_HOME") or "")
                db = home / "goals_1.sqlite"
                rows = []
                if db.exists():
                    con = sqlite3.connect("file:" + str(db).replace(chr(92), "/") + "?mode=ro", uri=True)
                    rows = [list(r) for r in con.execute("select thread_id, status, substr(objective,1,80) from thread_goals")]
                    con.close()
                log(f"goals_1.sqlite thread_goals: {rows}")
            elif verb == "status":
                r = http("GET", "/ca/chat/requests?limit=20")
                log("requests: " + json.dumps(r.get("requests") if isinstance(r, dict) else r, ensure_ascii=False)[:600])
            elif verb == "turns":
                ended = http("GET", "/ca/tui/recent?kind=agent.turn_ended&limit=50")
                requests = http("GET", "/ca/tui/recent?kind=turn.completed&limit=200")
                rows = [{k: e.get(k) for k in ("id", "kind", "request_id", "text", "data")} for e in ended.get("events") or []]
                (RUN / "agent-turn-ended.json").write_text(json.dumps({"agent_turn_ended": ended.get("events") or [], "turn_completed_count": len(requests.get("events") or [])}, indent=1, ensure_ascii=False), encoding="utf-8")
                log("agent.turn_ended: " + json.dumps(rows, ensure_ascii=False))
                log(f"turn.completed (per model request) count: {len(requests.get('events') or [])}")
                for line in router_log_lines("agent_turn_ended", "agent_turn_event"):
                    log("  LOG " + line)
            elif verb == "teardown":
                ps = (
                    "$ports = @(" + str(ROUTER) + "," + str(APPSRV) + "); "
                    "$pids = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | ? { $ports -contains $_.LocalPort } | % { $_.OwningProcess }); "
                    "$pids += @(Get-CimInstance Win32_Process | ? { $_.ProcessId -ne $PID -and $_.Name -ne 'powershell.exe' -and $_.CommandLine -like '*" + RUN.name + "*' } | % { $_.ProcessId }); "
                    "$pids | Sort-Object -Unique | % { $c = (Get-CimInstance Win32_Process -Filter \"ProcessId=$_\").CommandLine; \"$_ $c\".Substring(0, [Math]::Min(160, (\"$_ $c\").Length)); taskkill /T /F /PID $_ | Out-Null }"
                )
                r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
                log("teardown:\n" + (r.stdout.strip() or r.stderr.strip()[:300]))
            elif verb == "quit":
                dump(); log("quit"); os._exit(0)
        except Exception as exc:
            log(f"ERR {line}: {type(exc).__name__}: {exc}")
    time.sleep(0.5)
