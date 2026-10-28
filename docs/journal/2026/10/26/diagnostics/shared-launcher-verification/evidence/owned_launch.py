"""Owned production launcher verification; reviewed console driver reused from alias safeguard E2E.
Fresh stripped env/HOME/APPDATA/LOCALAPPDATA/config/workspace/ports. No production monkeypatches.
Actual installed ciel-runtime.cmd wrapper selects --runtime-root via its supported HOME_OVERRIDE.
Real npm Codex CLI; loopback fixture only. Teardown uses owned paths/descendants, never global stop.
--failure supplies a synthetic invalid bind address to verify startup provenance, not a remote repro.
--tty-negative runs native interactive Codex with stdout redirected to reproduce its tty guard.
"""
import os
import sys

PURGE = ("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC_", "CODEX_", "OPENAI_", "OPENROUTER_", "GEMINI_", "GOOGLE_", "AZURE_",
         "MOONSHOT_", "KIMI_", "XAI_", "GROK_", "ZAI_", "DEEPSEEK_", "NVIDIA_", "OLLAMA_", "FIREWORKS_", "OPENCODE_")
STRIPPED = sorted(n for n in os.environ if n.upper().startswith(PURGE)
                  or any(w in n.upper() for w in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "CREDENTIAL")))
for _name in STRIPPED:
    del os.environ[_name]

import re
import argparse  # noqa: E402
import ctypes  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import shutil  # noqa: E402
import socket  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402
import urllib.request  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402
from pathlib import Path  # noqa: E402

import psutil  # noqa: E402

HERE = Path(__file__).resolve().parent
WORKTREE = next(p for p in HERE.parents if (p / "ciel_runtime.py").exists())
CONSOLE_DRIVER_DIR = WORKTREE / "docs/journal/2026/10/16/features/cielairouter-direct-path/evidence/e2e"
CLAUDE_BIN = Path("C:/Users/djlov/AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe")

parser = argparse.ArgumentParser()
parser.add_argument("--runtime-root", required=True)
parser.add_argument("--label", required=True)
parser.add_argument("--mode", default="codex-remote")
parser.add_argument("--failure", action="store_true")
parser.add_argument("--tty-negative", action="store_true")
ARGS = parser.parse_args()
RUNTIME_ROOT = Path(ARGS.runtime_root).resolve()
assert (RUNTIME_ROOT / "ciel_runtime.py").is_file(), RUNTIME_ROOT
for required in (CLAUDE_BIN, CONSOLE_DRIVER_DIR / "console_driver.py"):
    if not required.exists():
        raise SystemExit(f"missing {required}")

RUN = HERE / ("run-" + ARGS.label + "-" + time.strftime("%H%M%S"))
RUN.mkdir()
SCRATCH = Path(tempfile.mkdtemp(prefix="ciel-owned-launcher-", dir="C:/Windows/Temp"))
CFG, WS, HOME, TMP = (SCRATCH / n for n in ("cfg", "ws", "home", "tmp"))
CLAUDE_DIR = HOME / ".claude"
for d in (CFG, WS, HOME, TMP, CLAUDE_DIR):
    d.mkdir(parents=True, exist_ok=True)
(CFG / "log-level").write_text("DEBUG")
(WS / "README.txt").write_text("Owned local fixture workspace")

ALIAS = "stub-model"
NATIVE_MODEL = "claude-opus-5-5"
WINDOW = 200000
HIGH_TOKENS = 190000  # 95% of WINDOW, reported under NATIVE_MODEL
BASELINE = "ALIAS-SAFEGUARD-BASELINE"
HIGH_REPLY = "ALIAS-HIGH-USAGE-REPLY"
REQS = []


def _text(m):
    c = m.get("content")
    return c if isinstance(c, str) else " ".join(str(x.get("text", "")) for x in (c or []) if isinstance(x, dict))


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


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


ROUTER = free_port()
KEY="owned-dummy"
EVENTS=[]
LOCK=threading.Lock()
UID="launcher"
SELECTED=["A"]
BREAK_ONCE={"B":False}
ROWS=[]
STREAM_GETS=[]
from urllib.parse import parse_qs, urlsplit
from datetime import datetime, timezone
def iso(t): return datetime.fromtimestamp(t, timezone.utc).isoformat()
def record(server,path,**kw):
    row={"server":server,"path":path,**kw}; EVENTS.append(row); return row
stub = ThreadingHTTPServer(("127.0.0.1", 0), make_handler("central", {}))
threading.Thread(target=stub.serve_forever, daemon=True).start()
FIXTURE_PORT = stub.server_port
assert ROUTER != FIXTURE_PORT

(CFG / "config.json").write_text(json.dumps({
    "current_provider": "cielairouter", "language": "en",
    "providers": {"cielairouter": {"base_url": f"http://127.0.0.1:{FIXTURE_PORT}", "current_model": ALIAS,
                                   "api_key": "owned-dummy", "context_window": WINDOW, "max_model_len": WINDOW,
                                   "max_output_tokens": 4096, "gateway_selection": "off", "gateway_data_path": "router",
                                   "compact_safeguard": True}},
}))
if ARGS.failure:
    cfg=json.loads((CFG/"config.json").read_text())
    # Synthetically invalid numeric bind address; no existing listener is touched.
    cfg["web_backend"]={"enabled":True,"host":"127.0.0.256","port":ROUTER}
    (CFG/"config.json").write_text(json.dumps(cfg))
(CLAUDE_DIR / ".claude.json").write_text(json.dumps({
    "hasCompletedOnboarding": True, "theme": "dark",
    "projects": {str(WS): {"hasTrustDialogAccepted": True}, str(WS).replace("\\", "/"): {"hasTrustDialogAccepted": True}}}))

env = dict(os.environ)
for d in (HOME / "AppData/Roaming", HOME / "AppData/Local", HOME / "codex"):
    d.mkdir(parents=True, exist_ok=True)
(HOME / "codex/config.toml").write_text('[projects.'+json.dumps(str(WS))+']'+chr(10)+'trust_level = "trusted"'+chr(10))
APPSRV=free_port()
env.update(HOME=str(HOME), USERPROFILE=str(HOME), TEMP=str(TMP), TMP=str(TMP),
    APPDATA=str(HOME / "AppData/Roaming"), LOCALAPPDATA=str(HOME / "AppData/Local"), CODEX_HOME=str(HOME / "codex"),
    CIEL_RUNTIME_CONFIG_DIR=str(CFG), CIEL_RUNTIME_ROUTER_PORT=str(ROUTER), CIEL_RUNTIME_HOME_OVERRIDE=str(RUNTIME_ROOT),
    CIEL_RUNTIME_CODEX_APP_SERVER_LISTEN=f"ws://127.0.0.1:{APPSRV}", CIEL_RUNTIME_SKIP_MENU="1", CIEL_RUNTIME_CODEX_CLEAN_UNTRACKED="0",
    CIEL_RUNTIME_STARTUP_AUTO_ANSWER="1", HTTP_PROXY="http://127.0.0.1:1", HTTPS_PROXY="http://127.0.0.1:1", ALL_PROXY="http://127.0.0.1:1", NO_PROXY="127.0.0.1,localhost")
env["PATH"]="C:/Users/djlov/AppData/Roaming/npm;"+env.get("PATH", "")
batch = RUN / "launch.cmd"
launch_line = 'call "C:/Users/djlov/.local/bin/ciel-runtime.cmd" --ca-runtime '+ARGS.mode+' --ca-no-update-check --ca-no-self-update-check'
if ARGS.tty_negative:
    launch_line = 'call "C:/Users/djlov/AppData/Roaming/npm/codex.cmd" > "'+str(RUN / "native-stdout.txt")+'"'
batch.write_text('@echo off'+chr(10)+launch_line+chr(10)+'echo OWNED-LAUNCHER-EXIT=%ERRORLEVEL%'+chr(10))

cmd = ["cmd.exe", "/d", "/k", str(batch)]

result = {"label": ARGS.label, "expect": ARGS.mode, "runtime_root": str(RUNTIME_ROOT), "scratch": str(SCRATCH),
          "stripped_env_names": STRIPPED,
      
          "command": cmd, "ports": {"router": ROUTER, "fixture": FIXTURE_PORT},
          "fixture": {"alias": ALIAS, "native_model": NATIVE_MODEL, "window": WINDOW, "high_tokens": HIGH_TOKENS},
          "events": []}
owned = []
lock = threading.Lock()
NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def event(kind, **data):
    row = {"at": round(time.time(), 3), "kind": kind, **data}
    with lock:
        result["events"].append(row)
        (RUN / "progress.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(row, ensure_ascii=False)[:600], flush=True)


def http(method, url, body=None, timeout=5):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json"})
    try:
        with NO_PROXY_OPENER.open(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def live_health():
    out = {}
    for port in (9505, 9524):
        h = http("GET", f"http://127.0.0.1:{port}/health", timeout=3)
        out[str(port)] = {k: h.get(k) for k in ("pid", "version", "source_fingerprint", "error")}
    return out


def router_logs():
    parts = []
    for p in sorted(CFG.rglob("*.log")):
        try:
            parts.append(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    return "\n".join(parts)


def state_files():
    out = {}
    for name in ("compaction-scope.json", "compaction-admission.json"):
        for p in CFG.rglob(name):
            try:
                out[name] = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
    return out


launcher = watcher = None


def wait(fn, secs, label):
    end = time.time() + secs
    while time.time() < end:
        value = fn()
        if value:
            event("wait_pass", label=label)
            return value
        if launcher is not None and launcher.poll() is not None:
            raise RuntimeError(f"launcher exited code={launcher.returncode} while waiting for {label}")
        time.sleep(0.4)
    raise RuntimeError("timeout " + label)


def descendants():
    if launcher is None:
        return []
    try:
        return psutil.Process(launcher.pid).children(recursive=True)
    except psutil.Error:
        return []


def describe(p):
    try:
        return {"pid": p.pid, "name": p.name(), "ppid": p.ppid(), "cmd": p.cmdline()}
    except psutil.Error:
        return {"pid": p.pid}


def screen():
    p = RUN / "screen-latest.txt"
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def shot(label):
    (RUN / ("screen-" + label + ".txt")).write_text(screen(), encoding="utf-8")
    (RUN / "shot-request").write_text(label, encoding="utf-8")
    wait(lambda: not (RUN / "shot-request").exists(), 30, "screenshot " + label)


def compact_lines():
    return [line.strip() for line in router_logs().splitlines() if any(token in line for token in (
        "runtime_compaction_native_model", "proactive_usage_observed", "compact_command", "channel_compact",
        "runtime_compaction_control event="))]


# The console starts minimized without activation; sample the foreground window throughout.
USER32 = ctypes.windll.user32
foreground = {"samples": 0, "owned_hits": 0, "owned_intervals": []}
stop_sampling = threading.Event()


def sampler():
    inside = None
    while not stop_sampling.is_set():
        try:
            hwnd = int((RUN / "hwnd.txt").read_text())
        except (OSError, ValueError):
            hwnd = 0
        fg = USER32.GetForegroundWindow()
        foreground["samples"] += 1
        now = round(time.time(), 2)
        if hwnd and fg == hwnd:
            foreground["owned_hits"] += 1
            if inside is None:
                inside = [now, now]
                foreground["owned_intervals"].append(inside)
            inside[1] = now
        else:
            inside = None
        time.sleep(0.05)


BEFORE = live_health()
result["live_before"] = BEFORE
try:
    startup = subprocess.STARTUPINFO()
    startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 7  # SW_SHOWMINNOACTIVE: never take keyboard focus from the user
    launcher = subprocess.Popen(cmd, env=env, cwd=WS, creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup)
    owned.append(launcher)
    threading.Thread(target=sampler, daemon=True).start()
    watcher = subprocess.Popen([sys.executable, "-I", str(HERE / "console_watch.py"), str(launcher.pid), str(RUN),
                                str(CONSOLE_DRIVER_DIR), "codex-remote"], env=env, creationflags=subprocess.DETACHED_PROCESS,
                               stdout=(RUN / "watcher-out.txt").open("w"), stderr=subprocess.STDOUT)
    owned.append(watcher)
    event("launched", pid=launcher.pid, watcher=watcher.pid)
    if ARGS.tty_negative:
        wait(lambda: "stdout is not a terminal" in screen() and "OWNED-LAUNCHER-EXIT=1" in screen(), 35, "native stdout tty guard")
        shot("01-native-stdout-not-terminal")
        result["status"]="pass"
    elif ARGS.failure:
        wait(lambda: "state=child_exited" in screen(), 70, "child-exit diagnostic rendered")
        shot("01-startup-failure")
        result["status"]="pass"
    else:
        wait(lambda: "error" not in http("GET", f"http://127.0.0.1:{ROUTER}/health", timeout=2), 90, "owned router health")
        result["owned_health"] = http("GET", f"http://127.0.0.1:{ROUTER}/health")
        if ARGS.mode == "codex-remote":
            wait(lambda: "codex_remote_tui_start" in router_logs(), 90, "remote TUI started")
        wait(lambda: "stub-model" in screen(), 75, "native model rendered")
        time.sleep(4)
        shot("01-started")
        (RUN / "input.txt").write_text("E2E-TURN-1 reply briefly", encoding="utf-8")
        wait(lambda: any(q.get("turn") == "1" for q in EVENTS), 60, "prompt reached fixture")
        wait(lambda: "STUB-REPLY-central" in screen(), 60, "reply rendered")
        shot("02-reply")
        result["status"] = "pass"

except Exception:
    result["status"] = "failed"
    result["error"] = traceback.format_exc()
    event("failure", error=result["error"])
finally:
    stop_sampling.set()
    (RUN / "stop").write_text("1")
    time.sleep(1)
    result["requests"] = EVENTS
    result["foreground"] = dict(foreground)
    raw = RUN / "state-raw"
    raw.mkdir(exist_ok=True)
    for name in ("compaction-scope.json", "compaction-admission.json", "channel-compact-request.json"):
        for p in CFG.rglob(name):
            shutil.copyfile(p, raw / name)
    for p in CLAUDE_DIR.rglob("*.jsonl"):
        if "projects" in p.parts:
            shutil.copyfile(p, raw / ("transcript-" + p.name))
    (RUN / "router-combined.log").write_text(router_logs(), encoding="utf-8")
    for p in CFG.rglob("app-server.log"):
        shutil.copyfile(p, RUN / "app-server.log")
    victims = []
    for p in psutil.process_iter():
        if p.pid == os.getpid():
            continue
        try:
            owned_by_run = str(SCRATCH) in " ".join(p.cmdline()) or str(SCRATCH) in str(p.environ().get("CIEL_RUNTIME_CONFIG_DIR", ""))
        except psutil.Error:
            owned_by_run = False
        if owned_by_run:
            victims.append(p)
    victims += [p for p in descendants() if p not in victims]
    result["teardown_owned_identities"] = [describe(p) for p in victims]
    for p in victims:
        try:
            p.kill()
        except psutil.Error:
            pass
    for p in owned:
        if p.poll() is None:
            try:
                p.kill()
            except OSError:
                pass
    time.sleep(2)
    result["live_after"] = live_health()
    result["live_unchanged"] = result["live_after"] == BEFORE
    result["source_digests"] = {x: hashlib.sha256((RUNTIME_ROOT / x).read_bytes()).hexdigest() for x in (
        "ciel_runtime.py", "ciel_runtime_support/router_process_lifecycle.py", "ciel_runtime_support/codex_app_server_session.py",
        "ciel_runtime_support/channel_terminal_proxy.py", "ciel_runtime_support/codex_app_server.py")}
    (RUN / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    stub.shutdown()
    stub.server_close()
    print("RESULT", RUN, result["status"], flush=True)
raise SystemExit(0 if result["status"] in ("pass",) else 1)
