"""E2E (Walkie 53255): a new company prompt announced in a Walkie event reloads the session after the running turn.

Usage: python e2e_company_prompt.py <claude|codex>

Isolated: scratch CIEL_RUNTIME_CONFIG_DIR and CODEX_HOME on G:, unique router port, CIEL_RUNTIME_TEST_ISOLATED,
inherited CIEL_RUNTIME_/ANTHROPIC_/CLAUDE_/CODEX_/OPENAI_ variables cleared, stub OpenAI-compatible upstream (provider
vllm) and a stub Walkie instruction endpoint (company prompt marker + X-Walkie-Company-Prompt-* headers). The stub
records only which company prompt marker a request carries and whether turn 1 is in its history, never request text
(Claude Code also sends the user's own ~/.claude/CLAUDE.md).

  1  launch with company prompt v7 served -> AGENTS.md/CLAUDE.md hold v7; turn 1 (E2E-TURN-1) carries PROMPT-V7-BODY
  2  the endpoint switches to v8; a long turn starts (the stub answers after 25 s)
  3  during that turn a signed CloudEvent with data.agent_context.company_prompt = v8 reaches /ca/events/webhooks/e2e
  4  expected: the turn completes, then the launcher restarts (source=company-prompt), fetches v8 just before the
     relaunch, the resumed session gets "계속-세션재시작됨" and its requests carry PROMPT-V8-BODY with turn 1 in history
"""
from __future__ import annotations

import base64
import hashlib
import hmac
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
REPO = Path(r"C:\Users\djlov\ciel-runtime")
FLOW = sys.argv[1]
MESSAGE = "계속-세션재시작됨"
RUN = HERE / f"e2e-{FLOW}"
shutil.rmtree(RUN, ignore_errors=True)
SCRATCH = Path(r"G:\ciel-e2e\company-prompt") / FLOW
shutil.rmtree(SCRATCH, ignore_errors=True)
CFG, WS, CODEX_HOME, SHOTS = SCRATCH / "cfg", SCRATCH / "ws", SCRATCH / "codex-home", RUN / "shots"
for d in (CFG, WS, CODEX_HOME, SHOTS):
    d.mkdir(parents=True, exist_ok=True)
LOG = open(RUN / "driver.log", "w", encoding="utf-8")
SHA = {v: hashlib.sha256(f"e2e company prompt v{v}".encode()).hexdigest() for v in (7, 8)}
SERVED = [7]
GETS: list[dict] = []
REQS: list[dict] = []
LONG_DONE: list[int] = []
LOCK = threading.Lock()


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


class Stub(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code: int, data: bytes, ctype: str, headers: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/instructions"):
            v = SERVED[0]
            GETS.append({"at": time.strftime("%H:%M:%S"), "t": time.time(), "version": v})
            body = (f"<!-- company-prompt version={v} sha256={SHA[v]} source=latest -->\n"
                    f"# Company prompt PROMPT-V{v}-BODY\nAnswer briefly.\n").encode()
            self._send(200, body, "text/markdown; charset=utf-8",
                       {"X-Walkie-Company-Prompt-Version": str(v), "X-Walkie-Company-Prompt-Sha256": SHA[v], "Cache-Control": "no-store"})
            return
        self._send(200, json.dumps({"object": "list", "data": [{"id": "stub-model", "object": "model", "owned_by": "stub",
                                                                   "max_model_len": 128000}]}).encode(), "application/json")

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        body = json.loads(raw or b"{}")
        text = raw.decode("utf-8", "replace")
        msgs = body.get("messages") or []
        users = [m for m in msgs if m.get("role") == "user"]
        last = json.dumps(users[-1], ensure_ascii=False) if users else ""
        with LOCK:
            n = len(REQS) + 1
            record = {"n": n, "start": time.time(), "end": 0.0, "at": time.strftime("%H:%M:%S"),
                      "v7": "PROMPT-V7-BODY" in text, "v8": "PROMPT-V8-BODY" in text,
                      # Where each prompt sits in the request (last occurrence, 0..1), the message count, and the
                      # stub replies already in the history (continuity without storing any text).
                      "v7_at": round(text.rfind("PROMPT-V7-BODY") / max(1, len(text)), 3) if "PROMPT-V7-BODY" in text else None,
                      "v8_at": round(text.rfind("PROMPT-V8-BODY") / max(1, len(text)), 3) if "PROMPT-V8-BODY" in text else None,
                      "messages": len(msgs),
                      "replies_in_history": sorted({int(x) for x in re.findall(r"STUB-REPLY-(\d+)", text)}),
                      "turn1_in_history": "E2E-TURN-1" in text,
                      # Only the first request of the long turn is slow (Claude Code carries it in later requests too).
                      "long_turn": "E2E-LONG-TURN" in last and not LONG_DONE, "path": self.path,
                      "event_v8": "E2E-EVENT-V8" in last, "continue_message": MESSAGE in last,
                      "title_request": "Do not answer the request" in last or "title" in last.lower()[:400] and len(users) == 1}
            REQS.append(record)
        if record["long_turn"]:
            LONG_DONE.append(n)
            time.sleep(25)
        reply = f"STUB-REPLY-{n}"
        if self.path.split("?")[0].endswith("/v1/messages"):
            message = {"id": f"msg_{n}", "type": "message", "role": "assistant", "model": body.get("model") or "stub-model",
                       "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 2}}
            if not body.get("stream"):
                self._send(200, json.dumps({**message, "content": [{"type": "text", "text": reply}], "stop_reason": "end_turn"}).encode(),
                           "application/json")
            else:
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("connection", "close")
                self.end_headers()
                for ev in ({"type": "message_start", "message": message},
                           {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                           {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": reply}},
                           {"type": "content_block_stop", "index": 0},
                           {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 2}},
                           {"type": "message_stop"}):
                    self.wfile.write(f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n".encode())
                    self.wfile.flush()
                self.close_connection = True
        elif body.get("stream"):
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("connection", "close")
            self.end_headers()
            base = {"id": f"c{n}", "object": "chat.completion.chunk", "created": int(time.time()), "model": "stub-model"}
            for chunk in ({**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": reply}, "finish_reason": None}]},
                          {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                           "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}):
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            self.close_connection = True
        else:
            self._send(200, json.dumps({"id": f"c{n}", "object": "chat.completion", "created": int(time.time()), "model": "stub-model",
                                        "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
                                        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}).encode(), "application/json")
        record["end"] = time.time()
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
    "remote_instructions": {"enabled": True, "claude_url": f"http://127.0.0.1:{STUB}/instructions",
                            "codex_url": f"http://127.0.0.1:{STUB}/instructions"},
}, indent=2), encoding="utf-8")
(CFG / "log-level").write_text("INFO")
(CODEX_HOME / "config.toml").write_text(f"[projects.'{WS}']\ntrust_level = \"trusted\"\n", encoding="utf-8")
log(f"flow={FLOW} stub={STUB} router={ROUTER} scratch={SCRATCH}")

clear = "".join(f'for /f "delims==" %%v in (\'set {p} 2^>nul\') do set "%%v="\n' for p in ("CIEL_RUNTIME_", "ANTHROPIC_", "CLAUDE_", "CODEX_", "OPENAI_"))
TITLE = f"CIELE2E-CP-{FLOW}-{int(time.time())}"
SCRIPT = RUN / "launch.cmd"
SCRIPT.write_text(
    "@echo off\n" + clear + "set CLAUDECODE=\n"
    f'set "CIEL_RUNTIME_CONFIG_DIR={CFG}"\nset "CIEL_RUNTIME_ROUTER_PORT={ROUTER}"\nset "CIEL_RUNTIME_TEST_ISOLATED=1"\n'
    f'set "CODEX_HOME={CODEX_HOME}"\nset "CIEL_RUNTIME_CODEX_APP_SERVER_LISTEN=ws://127.0.0.1:{APPSRV}"\n'
    'set "CIEL_RUNTIME_STARTUP_AUTO_ANSWER=1"\n'
    f'cd /d "{WS}"\npython "{REPO / "ciel_runtime.py"}" cli --ca-runtime {FLOW} --ca-no-update-check --ca-no-self-update-check\n'
    "echo LAUNCHER-EXIT=%ERRORLEVEL%\n", encoding="utf-8")


def launcher_pid() -> int:
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ? { $_.CommandLine -like '*ciel_runtime.py*' -and "
                          "$_.ParentProcessId -in @(Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | ? { $_.CommandLine -like '*"
                          + str(SCRIPT) + "*' } | % { $_.ProcessId }) } | % { $_.ProcessId }"], capture_output=True, text=True).stdout.split()
    return int(out[0]) if out else 0


def http(method: str, path: str, body: bytes | dict | None = None, headers: dict | None = None, timeout: float = 10) -> dict:
    data = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(f"http://127.0.0.1:{ROUTER}{path}", method=method, data=data,
                                 headers={"content-type": "application/json", "accept": "application/json", **(headers or {})})
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
        out += [line[-420:] for line in text.splitlines() if any(n in line for n in needles)]
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


def held_files() -> dict:
    out = {}
    for name in ("AGENTS.md", "CLAUDE.md"):
        try:
            m = re.search(r"company-prompt version=(\d+)", (WS / name).read_text(encoding="utf-8"))
            out[name] = int(m.group(1)) if m else None
        except OSError:
            out[name] = None
    return out


SECRET_KEY = os.urandom(24)
SECRET = "whsec_" + base64.b64encode(SECRET_KEY).decode()


def send_event(version: int, text: str) -> dict:
    event = json.dumps({"specversion": "1.0", "id": f"e2e-cp-{version}-{int(time.time())}", "source": "http://walkie.e2e/conversations/1",
                        "type": "com.ciel.walkie.support.message", "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "datacontenttype": "application/json",
                        "data": {"agent_context": {"company_prompt": {"version": version, "sha8": SHA[version][:8], "source": "latest"}},
                                 "text": text}}).encode()
    wid, ts = f"msg_{int(time.time() * 1000)}", str(int(time.time()))
    sig = base64.b64encode(hmac.new(SECRET_KEY, wid.encode() + b"." + ts.encode() + b"." + event, hashlib.sha256).digest()).decode()
    r = http("POST", "/ca/events/webhooks/e2e", event, {"webhook-id": wid, "webhook-timestamp": ts, "webhook-signature": f"v1,{sig}"})
    log(f"webhook event v{version} -> {json.dumps(r)[:200]}")
    return r


def kill_all() -> None:
    (RUN / "stop-main").write_text("1")
    ps = ("$ports = @(" + str(ROUTER) + "," + str(APPSRV) + "); "
          "$pids = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | ? { $ports -contains $_.LocalPort } | % { $_.OwningProcess }); "
          "$pids += @(Get-CimInstance Win32_Process | ? { $_.ProcessId -ne $PID -and $_.ProcessId -ne " + str(os.getpid()) + " -and $_.Name -ne 'powershell.exe' -and "
          "-not ($_.CommandLine -like '*e2e_company_prompt.py*') -and "
          "($_.CommandLine -like '*" + str(RUN) + "*' -or $_.CommandLine -like '*" + str(SCRATCH) + "*') } | % { $_.ProcessId }); "
          "$pids | Sort-Object -Unique | % { taskkill /T /F /PID $_ | Out-Null; \"killed $_\" }")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    log("kill: " + r.stdout.strip().replace("\n", " "))


result: dict = {"flow": FLOW}
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
    wait(lambda: held_files().get("AGENTS.md") == 7 and held_files().get("CLAUDE.md") == 7, 60, "v7 instruction files")
    time.sleep(12)
    notify("E2E-TURN-1 please reply")
    wait(lambda: any(q["turn1_in_history"] and q["end"] for q in REQS), 120, "turn 1 answered")
    time.sleep(5)
    shot("turn1-v7")
    r = http("POST", "/ca/events/receivers/e2e", {"enabled": True, "transport": "webhook", "webhook_secret": SECRET})
    log(f"receiver -> ok={r.get('ok')} {json.dumps(r.get('receiver', {}).get('transport'))}")
    SERVED[0] = 8
    log("instruction endpoint now serves v8")
    before = len(REQS)
    notify("E2E-LONG-TURN please reply")
    wait(lambda: any(q["long_turn"] for q in REQS[before:]), 120, "long turn started at the stub")
    long_turn = next(q for q in REQS[before:] if q["long_turn"])
    time.sleep(3)
    send_event(8, "E2E-EVENT-V8 company prompt changed")
    time.sleep(4)
    shot("event-during-long-turn")
    wait(lambda: bool(router_lines("session_restart exit_code")), 240, "restart after the turn")
    wait(lambda: any(q["continue_message"] for q in REQS), 180, "continue message at the stub")
    wait(lambda: any(q["v8"] and q["end"] for q in REQS), 120, "v8 request answered")
    time.sleep(8)
    shot("after-restart-v8")
    restart_lines = router_lines("session_restart exit_code", "company_prompt_reload", "runtime_session_restart_queued",
                                 "remote_instructions_", "session_restart_prepare")
    restart_t = None
    for line in router_lines("session_restart exit_code"):
        m = re.match(r"\[?(\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d)", line.strip())
        if m:
            restart_t = m.group(1)
            break
    v8_gets = [g for g in GETS if g["version"] == 8]
    result.update({
        "long_turn": {"start": long_turn["start"], "end": long_turn["end"], "duration": round(long_turn["end"] - long_turn["start"], 1)},
        "requests_in_flight_at_relaunch_check": "see restart vs long_turn end",
        "first_v8_get": v8_gets[0] if v8_gets else None,
        "gets": GETS,
        "held_files_end": held_files(),
        "requests": REQS,
        "router": restart_lines[-40:],
        "restart_line_time": restart_t,
    })
finally:
    (RUN / "result.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    kill_all()
    log("done")
