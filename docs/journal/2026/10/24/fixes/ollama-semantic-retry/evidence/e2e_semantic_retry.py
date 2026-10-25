"""Isolated actual-runtime + actual-Claude-CLI E2E for the Ollama semantic retry.

python -I e2e_semantic_retry.py recover|exhaust [retries]

  recover  fake Ollama answers the marked main turn with reasoning only (done/stop)
           on POST 1 and with a real answer marker on POST 2+.
  exhaust  fake Ollama answers every marked main-turn POST with an empty done/stop.
  retries  provider option empty_response_retries written to the isolated config
           (default 1). Expected main-turn POSTs = retries + 1.

Isolation: fresh temporary HOME/USERPROFILE/APPDATA/LOCALAPPDATA/TEMP, runtime
config dir (CIEL_RUNTIME_TEST_ISOLATED=1 keeps router state in it),
CLAUDE_CONFIG_DIR, CODEX_HOME and workspace; new loopback ports for the router,
the fake Ollama and an egress sentinel proxy. launch.cmd clears every inherited
CIEL_RUNTIME_*, CLAUDE*, ANTHROPIC*, CODEX*, OPENAI* variable before setting
its own (Windows Terminal tabs take a fresh environment, so clearing happens in
the tab). The provider base URL is the fake Ollama; no real upstream is used.
HTTP(S)_PROXY points at a local sentinel that refuses and records any
non-loopback request.

Launch: this worktree's ciel_runtime.py ``cli --ca-runtime claude`` in its own
Windows Terminal window; the launcher spawns the runtime ``serve`` router and the
installed Claude Code CLI. A detached console agent types ONE prompt and Enter;
nothing else is typed (no continue). Evidence goes to run-<case>-r<retries>/.
Teardown kills only the launch.cmd process tree and the router pid recorded in
the isolated config that owns the isolated port.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STRIPPED = sorted(k for k in os.environ if k.upper().startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI")))
for _key in STRIPPED:  # wt.exe, the console agent and PowerShell helpers inherit nothing live
    os.environ.pop(_key)
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[7]
HELPERS = HERE.parents[3] / "16/features/cielairouter-direct-path/evidence/e2e"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
CASE = sys.argv[1]
RETRIES = int(sys.argv[2]) if len(sys.argv) > 2 else 1
assert CASE in ("recover", "exhaust"), CASE
RUN = HERE / f"run-{CASE}-r{RETRIES}"
shutil.rmtree(RUN, ignore_errors=True)
SHOTS = RUN / "shots"
SHOTS.mkdir(parents=True)
SCRATCH = Path(tempfile.mkdtemp(prefix=f"ciel-osr-e2e-{CASE}-r{RETRIES}-"))
HOME, CFG, CCFG, CODEX_HOME, WS = (SCRATCH / n for n in ("home", "cfg", "claude-config", "codex-home", "ws"))
for d in (HOME / "AppData" / "Roaming", HOME / "AppData" / "Local" / "Temp", CFG, CCFG, CODEX_HOME, WS):
    d.mkdir(parents=True, exist_ok=True)
UID = uuid.uuid4().hex[:10]
PROMPT_MARKER = f"OSR-E2E-{CASE.upper()}-{UID}"
ANSWER_MARKER = f"OSR-ANSWER-{UID}"
PROMPT = f"{PROMPT_MARKER} reply with one short line"
MODEL = "osr-fake"
LIVE_PORTS = (9385, 9464, 9489, 9505, 9524)
PYTHON = shutil.which("python")
EXHAUST_PHRASE = "automatic retries are exhausted"
MODEL_API_HOSTS = ("ollama.com", "api.anthropic.com", "openai.com", "cielairouter", "z.ai", "moonshot", "deepseek")
LOCK = threading.Lock()
REQUESTS: list[dict] = []
EGRESS: list[dict] = []
LOG = open(RUN / "driver.log", "w", encoding="utf-8")
T0 = time.time()


def log(message: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} +{time.time() - T0:6.1f}s {message}"
    print(line, flush=True)
    LOG.write(line + "\n")
    LOG.flush()


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def record(item: dict) -> dict:
    with LOCK:
        item = {"n": len(REQUESTS) + 1, "t": round(time.time() - T0, 2), "at": time.strftime("%H:%M:%S"), **item}
        REQUESTS.append(item)
    with open(RUN / "fake-ollama-requests.jsonl", "a", encoding="utf-8") as stream:
        stream.write(json.dumps(item, ensure_ascii=False) + "\n")
    return item


def main_posts() -> list[dict]:
    with LOCK:
        return [r for r in REQUESTS if r.get("kind") == "main"]


def chunk(content: str = "", thinking: str = "", done: bool = False, eval_count: int = 0) -> dict:
    message = {"role": "assistant", "content": content}
    if thinking:
        message["thinking"] = thinking
    item = {"model": MODEL, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "message": message, "done": done}
    if done:
        item.update({"done_reason": "stop", "total_duration": 1000000, "prompt_eval_count": 120, "eval_count": eval_count})
    return item


def main_turn_chunks(attempt: int) -> tuple[str, list[dict]]:
    if CASE == "exhaust":
        return "empty", [chunk(), chunk(done=True)]
    if attempt == 1:
        return "reasoning_only", [chunk(thinking="Considering the request"), chunk(thinking=" before answering."),
                                  chunk(done=True, eval_count=6)]
    return "answer", [chunk(content=ANSWER_MARKER), chunk(content=" recovered after the semantic replay."),
                      chunk(done=True, eval_count=9)]


class FakeOllama(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def _json(self, code: int, value) -> None:
        data = json.dumps(value).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        record({"method": "GET", "path": path, "kind": "meta"})
        if path == "/api/tags":
            return self._json(200, {"models": [{"name": f"{MODEL}:latest", "model": f"{MODEL}:latest", "size": 1,
                                                 "digest": "0" * 64, "details": {"family": "fakearch"}}]})
        if path == "/api/ps":
            return self._json(200, {"models": []})
        if path == "/api/version":
            return self._json(200, {"version": "0.0.0-fake"})
        if path == "/v1/models":
            return self._json(200, {"object": "list", "data": [{"id": MODEL, "object": "model"}]})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        path = self.path.split("?")[0]
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {}
        if path == "/api/show":
            record({"method": "POST", "path": path, "kind": "meta", "model": body.get("model")})
            return self._json(200, {"model_info": {"general.architecture": "fakearch", "fakearch.context_length": 131072},
                                    "capabilities": ["completion", "tools", "thinking"], "parameters": "",
                                    "details": {"family": "fakearch"}})
        if path != "/api/chat":
            record({"method": "POST", "path": path, "kind": "unexpected", "bytes": len(raw)})
            return self._json(404, {"error": "not found"})
        messages = body.get("messages") if isinstance(body.get("messages"), list) else []
        last = messages[-1] if messages else {}
        text = raw.decode("utf-8", "replace")
        marked = PROMPT_MARKER in text
        tools = len(body.get("tools") or [])
        kind = "main" if marked and tools else "aux"
        replay_tail = bool(last.get("role") == "user" and str(last.get("content") or "").startswith("[ciel-runtime] The previous attempt"))
        base = dict(body, messages=messages[:-1] if replay_tail else messages)
        attempt = len(main_posts()) + 1 if kind == "main" else 0
        stream = body.get("stream", True) is not False
        if kind == "main":
            served, chunks = main_turn_chunks(attempt)
        else:
            served, chunks = "aux_ok", [chunk(content="aux-ok"), chunk(done=True, eval_count=1)]
        record({"method": "POST", "path": path, "kind": kind, "attempt": attempt, "served": served, "stream": stream,
                "bytes": len(raw), "model": body.get("model"), "messages": len(messages), "tools": tools,
                "think": body.get("think"), "options_keys": sorted((body.get("options") or {}).keys()),
                "prompt_marker": marked, "replay_instruction_last": replay_tail,
                "replay_instruction_text": str(last.get("content") or "")[:200] if replay_tail else None,
                "base_sha256": digest(base), "body_sha256": digest(body),
                "compat_header": {k: v for k, v in self.headers.items() if "compat" in k.lower()}})
        if not stream:
            final = dict(chunks[-1])
            final["message"] = {"role": "assistant",
                                "content": "".join(c["message"].get("content", "") for c in chunks),
                                "thinking": "".join(c["message"].get("thinking", "") for c in chunks)}
            return self._json(200, final)
        self.send_response(200)
        self.send_header("content-type", "application/x-ndjson")
        self.send_header("connection", "close")
        self.end_headers()
        for item in chunks:
            self.wfile.write((json.dumps(item) + "\n").encode())
            self.wfile.flush()
            time.sleep(0.05)
        self.close_connection = True


class EgressSentinel(BaseHTTPRequestHandler):
    """HTTP(S)_PROXY target: refuses and records every proxied (non-loopback) request."""

    def log_message(self, *_args):
        pass

    def _refuse(self):
        with LOCK:
            EGRESS.append({"t": round(time.time() - T0, 2), "method": self.command, "target": self.path})
        self.send_response(403)
        self.send_header("content-length", "0")
        self.send_header("connection", "close")
        self.end_headers()
        self.close_connection = True

    do_CONNECT = do_GET = do_POST = do_HEAD = do_PUT = _refuse


def http_json(url: str, timeout: float = 2.0):
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def live_health() -> dict:
    """Read-only GET /health of the live routers (a busy router may time out; retried)."""
    out = {}
    for port in LIVE_PORTS:
        for attempt in range(1, 4):
            data = http_json(f"http://127.0.0.1:{port}/health", timeout=6.0)
            if data.get("pid"):
                break
            time.sleep(1.0)
        out[str(port)] = {"attempts": attempt, **{k: data.get(k) for k in ("ok", "pid", "version", "error") if k in data}}
    return out


def powershell(script: str) -> str:
    return subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True).stdout


def processes() -> list[dict]:
    raw = powershell("Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress")
    try:
        return json.loads(raw)
    except ValueError:
        return []


def owned_tree(root_pid: int, table: list[dict]) -> list[dict]:
    children: dict[int, list[dict]] = {}
    for p in table:
        children.setdefault(p["ParentProcessId"], []).append(p)
    out, todo = [], [root_pid]
    by_pid = {p["ProcessId"]: p for p in table}
    if root_pid in by_pid:
        out.append(by_pid[root_pid])
    while todo:
        for child in children.get(todo.pop(), []):
            if child["ProcessId"] != root_pid and child not in out:
                out.append(child)
                todo.append(child["ProcessId"])
    return out


def wait(predicate, seconds: float, label: str) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        try:
            if predicate():
                log(f"wait {label}: True")
                return True
        except Exception:
            pass
        time.sleep(0.5)
    log(f"wait {label}: False")
    return False


def screen() -> str:
    try:
        return (RUN / "screen-latest.txt").read_text(encoding="utf-8")
    except OSError:
        return ""


def shot(label: str) -> None:
    out = SHOTS / f"{len(list(SHOTS.iterdir())) + 1:02d}-{label}.png"
    result = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HELPERS / "shot_title.ps1"),
                             TITLE, str(out)], capture_output=True, text=True)
    log(f"shot {label}: {(result.stdout or result.stderr).strip()[:200]}")
    (RUN / f"screen-at-{label}.txt").write_text(screen(), encoding="utf-8")


def router_log_lines(*needles: str) -> list[str]:
    path = CFG / "router.log"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [line[-500:] for line in text.splitlines() if any(n in line for n in needles)]


def sse_traces() -> list[dict]:
    out = []
    for path in sorted(CFG.glob("router-sse-trace.jsonl*")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def transcript_facts() -> dict:
    files = sorted((CCFG / "projects").rglob("*.jsonl")) if (CCFG / "projects").exists() else []
    entries = []
    for path in files:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                entries.append((path.name, json.loads(line)))
            except ValueError:
                pass
    users = [e for _, e in entries if e.get("type") == "user" and PROMPT_MARKER in json.dumps(e.get("message"), ensure_ascii=False)]
    assistants = [e for _, e in entries if e.get("type") == "assistant"]
    blocks = []
    for e in assistants:
        message = e.get("message") or {}
        for block in message.get("content") or []:
            if isinstance(block, dict):
                blocks.append({"message_id": message.get("id"), "stop_reason": message.get("stop_reason"),
                               "model": message.get("model"), "type": block.get("type"),
                               "text": block.get("text") if block.get("type") == "text" else None,
                               "thinking_len": len(block.get("thinking") or "") if block.get("type") == "thinking" else None,
                               "isApiErrorMessage": e.get("isApiErrorMessage")})
    return {"files": [str(p) for p in files], "user_prompt_entries": len(users),
            "assistant_message_ids": sorted({b["message_id"] for b in blocks if b["message_id"]}),
            "assistant_blocks": blocks,
            "api_error_entries": sum(1 for e in assistants if e.get("isApiErrorMessage")),
            "entry_types": [e.get("type") for _, e in entries]}


# ---------------------------------------------------------------- setup
FAKE_PORT, SENTINEL_PORT, ROUTER_PORT, APP_PORT = free_port(), free_port(), free_port(), free_port()
servers = []
for port, handler in ((FAKE_PORT, FakeOllama), (SENTINEL_PORT, EgressSentinel)):
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    servers.append(server)
(CFG / "config.json").write_text(json.dumps({
    "current_provider": "ollama",
    "language": "en",
    "providers": {"ollama": {"base_url": f"http://127.0.0.1:{FAKE_PORT}", "api_key": "ollama", "current_model": MODEL,
                             "custom_models": [MODEL], "request_timeout_ms": 120000, "stream_enabled": True,
                             "empty_response_retries": RETRIES}},
}, indent=2), encoding="utf-8")
(CFG / "log-level").write_text("INFO", encoding="utf-8")
(CODEX_HOME / "config.toml").write_text("", encoding="utf-8")
(CCFG / ".claude.json").write_text(json.dumps({
    "hasCompletedOnboarding": True, "lastOnboardingVersion": "2.1.310", "numStartups": 3,
    "bypassPermissionsModeAccepted": True,
    "projects": {key: {"hasTrustDialogAccepted": True, "allowedTools": []}
                 for key in {str(WS), WS.as_posix(), str(WS).lower(), WS.as_posix().lower()}}}), encoding="utf-8")
(RUN / "prompt.txt").write_text(PROMPT, encoding="utf-8")
TITLE = f"CIELE2E-OSR-{CASE}-r{RETRIES}-{UID}"
SCRIPT = RUN / "launch.cmd"
clear = "".join(f'for /f "delims==" %%v in (\'set {p} 2^>nul\') do set "%%v="\n'
                for p in ("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI"))
appdata, local = HOME / "AppData" / "Roaming", HOME / "AppData" / "Local"
proxy = f"http://127.0.0.1:{SENTINEL_PORT}"
SCRIPT.write_text(
    "@echo off\n" + clear +
    f'set "HOME={HOME}"\nset "USERPROFILE={HOME}"\nset "HOMEDRIVE={HOME.drive}"\nset "HOMEPATH={str(HOME)[len(HOME.drive):]}"\n'
    f'set "APPDATA={appdata}"\nset "LOCALAPPDATA={local}"\n'
    f'set "XDG_CONFIG_HOME={HOME / ".config"}"\nset "TEMP={local / "Temp"}"\nset "TMP={local / "Temp"}"\n'
    f'set "HTTP_PROXY={proxy}"\nset "HTTPS_PROXY={proxy}"\nset "ALL_PROXY={proxy}"\n'
    'set "NO_PROXY=127.0.0.1,localhost,::1"\n'
    f'set "CIEL_RUNTIME_CONFIG_DIR={CFG}"\nset "CIEL_RUNTIME_ROUTER_PORT={ROUTER_PORT}"\nset "CIEL_RUNTIME_TEST_ISOLATED=1"\n'
    'set "CIEL_RUNTIME_SKIP_MENU=1"\nset "CIEL_RUNTIME_STARTUP_AUTO_ANSWER=1"\nset "CIEL_RUNTIME_SSE_TRACE=1"\n'
    f'set "CIEL_RUNTIME_CODEX_APP_SERVER_LISTEN=ws://127.0.0.1:{APP_PORT}"\nset "CIEL_RUNTIME_CODEX_CLEAN_UNTRACKED=0"\n'
    f'set "CLAUDE_CONFIG_DIR={CCFG}"\nset "CODEX_HOME={CODEX_HOME}"\n'
    'set "DISABLE_AUTOUPDATER=1"\nset "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1"\n'
    f'(set CIEL_RUNTIME_ & set CLAUDE & set ANTHROPIC & set CODEX & set OPENAI & set HOME & set USERPROFILE & set APPDATA & set LOCALAPPDATA & set TEMP & set HTTP & set ALL_PROXY & set NO_PROXY) > "{RUN / "launch-env.txt"}" 2>&1\n'
    f'cd /d "{WS}"\n"{PYTHON}" "{REPO / "ciel_runtime.py"}" cli --ca-runtime claude '
    "--ca-no-update-check --ca-no-self-update-check\n"
    "echo LAUNCHER-EXIT=%ERRORLEVEL%\n", encoding="utf-8")
log(f"case={CASE} retries={RETRIES} fake={FAKE_PORT} sentinel={SENTINEL_PORT} router={ROUTER_PORT} scratch={SCRATCH} repo={REPO}")
assert all(p not in LIVE_PORTS for p in (FAKE_PORT, SENTINEL_PORT, ROUTER_PORT, APP_PORT))

result: dict = {"case": CASE, "retries": RETRIES, "expected_main_posts": RETRIES + 1, "uid": UID,
                "prompt": PROMPT, "answer_marker": ANSWER_MARKER if CASE == "recover" else None,
                "ports": {"fake_ollama": FAKE_PORT, "egress_sentinel": SENTINEL_PORT, "router": ROUTER_PORT,
                          "codex_app_server_unused": APP_PORT},
                "scratch": str(SCRATCH), "repo": str(REPO), "title": TITLE, "python": PYTHON,
                "driver_python": sys.executable, "driver_stripped_env_names": STRIPPED}
result["live_health_before"] = live_health()
log("live health before: " + json.dumps(result["live_health_before"]))
cmd_pid = launcher_pid = 0
try:
    subprocess.Popen(["wt.exe", "-w", "new", "--title", TITLE, "--suppressApplicationTitle", "cmd", "/k", str(SCRIPT)])

    def find_pids() -> bool:
        global cmd_pid, launcher_pid
        table = processes()
        cmds = [p for p in table if p["Name"].lower() == "cmd.exe" and str(SCRIPT) in (p.get("CommandLine") or "")]
        if cmds:
            cmd_pid = cmds[0]["ProcessId"]
            kids = [p for p in table if p["ParentProcessId"] == cmd_pid and "ciel_runtime.py" in (p.get("CommandLine") or "")]
            launcher_pid = kids[0]["ProcessId"] if kids else 0
        return bool(cmd_pid and launcher_pid)

    wait(find_pids, 60, "launch.cmd + launcher pids")
    log(f"cmd pid {cmd_pid} launcher pid {launcher_pid}")
    if launcher_pid:
        subprocess.Popen([sys.executable, "-I", str(HERE / "e2e_console_agent.py"), str(launcher_pid), str(RUN), str(RUN / "prompt.txt")],
                         creationflags=0x00000008)
    wait(lambda: http_json(f"http://127.0.0.1:{ROUTER_PORT}/health").get("ok"), 90, "isolated router /health")
    result["router_health"] = http_json(f"http://127.0.0.1:{ROUTER_PORT}/health")
    wait(lambda: (RUN / "ready").exists(), 120, "Claude composer ready")
    shot("ready")
    wait(lambda: (RUN / "typed.json").exists(), 30, "prompt typed once")
    time.sleep(1.0)
    shot("typed")
    wait(lambda: len(main_posts()) >= 1, 60, "first main-turn POST at fake Ollama")
    if CASE == "recover":
        wait(lambda: ANSWER_MARKER in screen(), 120, "answer marker on Claude screen")
    else:
        wait(lambda: "retries are" in screen() and "exhausted" in screen(), 120, "exhaustion notice on Claude screen")
    wait(lambda: any(t.get("finished_at") and t.get("tools_count") for t in sse_traces()), 30, "main-turn SSE trace finished")
    time.sleep(3)
    shot("response")
    posts_at_response = len(main_posts())
    log(f"main-turn POSTs at response: {posts_at_response}; quiet window 25 s, nothing typed")
    time.sleep(25)
    shot("after-quiet")
    result["main_posts_at_response"] = posts_at_response
finally:
    (RUN / "stop").write_text("stop", encoding="utf-8")
    time.sleep(1.0)
    table = processes()
    tree = owned_tree(cmd_pid, table) if cmd_pid else []
    router_pid = 0
    try:
        router_pid = int((CFG / "router.pid").read_text().strip())
    except (OSError, ValueError):
        pass
    listener = powershell(f"(Get-NetTCPConnection -State Listen -LocalPort {ROUTER_PORT} -ErrorAction SilentlyContinue).OwningProcess").split()
    owned = {p["ProcessId"]: p for p in tree}
    if router_pid and str(router_pid) in listener:
        owned.setdefault(router_pid, next((p for p in table if p["ProcessId"] == router_pid), {"ProcessId": router_pid}))
    result["owned_processes"] = [{"pid": p.get("ProcessId"), "ppid": p.get("ParentProcessId"), "name": p.get("Name"),
                                  "cmd": (p.get("CommandLine") or "")[:300]} for p in owned.values()]
    result["router_pid_file"] = router_pid
    result["router_port_listeners"] = listener
    kills = []
    for pid in [cmd_pid, router_pid] if cmd_pid else [router_pid]:
        if pid and pid in owned:
            out = subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, text=True)
            kills.append({"pid": pid, "rc": out.returncode, "out": (out.stdout + out.stderr).strip()[:400]})
    result["kills"] = kills
    time.sleep(2)
    alive = {p["ProcessId"] for p in processes()}
    result["owned_still_alive"] = sorted(pid for pid in owned if pid in alive)
    log(f"teardown kills={kills} still_alive={result['owned_still_alive']}")
    for server in servers:
        server.shutdown()
    result["live_health_after"] = live_health()
    log("live health after: " + json.dumps(result["live_health_after"]))

# ---------------------------------------------------------------- facts
posts = main_posts()
traces = sse_traces()
main_traces = [t for t in traces if t.get("tools_count")]
for t in main_traces:
    names = [e.get("event") for e in t.get("events") or []]
    t["event_names"] = names
transcript = transcript_facts()
for name in ("router.log", "router-sse-trace.jsonl", "router-last-sse.json", "router-activity.json"):
    if (CFG / name).exists():
        shutil.copy(CFG / name, RUN / name)
semantic_lines = router_log_lines("ollama_semantic", "ollama_stream_request", "ollama_reasoning_only_stream", "ollama_empty_stream")
access_lines = [line for line in router_log_lines('access "POST /v1/messages')
                if re.search(r'access "POST /v1/messages(\?\S*)? HTTP', line)]
texts = [b["text"] for b in transcript["assistant_blocks"] if b["type"] == "text" and b["text"]]
result.update({
    "fake_ollama_requests": list(REQUESTS),
    "main_posts": posts,
    "main_post_count": len(posts),
    "aux_post_count": sum(1 for r in REQUESTS if r.get("kind") == "aux"),
    "egress_sentinel_requests": EGRESS,
    "router_semantic_lines": semantic_lines,
    "router_messages_access_lines": access_lines,
    "main_sse_traces": [{k: t.get(k) for k in ("time", "finished_at", "outcome", "stop_reason", "text_len", "chunks",
                                              "tools_count", "messages_count", "event_count", "event_names")}
                        for t in main_traces],
    "transcript": transcript,
    "final_screen": screen(),
})
first, rest = (posts[0] if posts else {}), posts[1:]
expected_text = ANSWER_MARKER if CASE == "recover" else EXHAUST_PHRASE
checks = {
    f"main_post_count_is_{RETRIES + 1}": len(posts) == RETRIES + 1,
    "no_post_after_response": result.get("main_posts_at_response") == len(posts),
    "replays_carry_runtime_instruction": bool(rest) and all(p["replay_instruction_last"] for p in rest),
    "replays_rebuilt_from_first_request": bool(rest) and all(p["base_sha256"] == first.get("base_sha256") for p in rest),
    "one_main_sse_trace": len(main_traces) == 1,
    "single_message_start_and_stop": len(main_traces) == 1 and main_traces[0]["event_names"].count("message_start") == 1
                                      and main_traces[0]["event_names"].count("message_stop") == 1,
    "sse_outcome": (main_traces[0].get("outcome") if main_traces else None) == ("success" if CASE == "recover" else "exhausted"),
    "sse_stop_reason_end_turn": (main_traces[0].get("stop_reason") if main_traces else None) == "end_turn",
    "one_claude_messages_request_for_turn": len(access_lines) - result["aux_post_count"] == 1,
    "transcript_one_user_prompt": transcript["user_prompt_entries"] == 1,
    "transcript_one_assistant_message": len(transcript["assistant_message_ids"]) == 1,
    "transcript_has_expected_text": any(expected_text in t for t in texts),
    "transcript_no_please_retry": not any("Please retry" in t for t in texts),
    "transcript_no_api_error": transcript["api_error_entries"] == 0,
    "screen_shows_expected_text": (expected_text in screen().replace("\n", " ")) or (
        CASE == "exhaust" and "retries are" in screen() and "exhausted" in screen()),
    "router_log_retry_line": sum("ollama_semantic_retry provider=" in l for l in semantic_lines) == RETRIES,
    "router_log_outcome_line": any(("ollama_semantic_recovered" if CASE == "recover" else "ollama_semantic_exhausted") in l
                                   for l in semantic_lines),
    # Every proxied request was refused by the sentinel; none targeted a model API.
    "no_model_api_egress_attempt": not any(any(h in e["target"] for h in MODEL_API_HOSTS) for e in EGRESS),
    "owned_processes_stopped": not result.get("owned_still_alive"),
    "live_router_pids_observed_and_unchanged": all(
        result["live_health_before"][p].get("pid") and
        result["live_health_before"][p].get("pid") == result["live_health_after"][p].get("pid")
        for p in result["live_health_before"]),
}
if CASE == "exhaust":
    checks["exactly_one_exhaustion_text"] = sum(t.count(EXHAUST_PHRASE) for t in texts) == 1
else:
    checks["exhaustion_text_absent"] = not any(EXHAUST_PHRASE in t for t in texts)
result["checks"] = checks
result["all_checks_pass"] = all(checks.values())
(RUN / "result.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
log("checks: " + json.dumps(checks))
log(f"all_checks_pass={result['all_checks_pass']} main_post_count={len(posts)} egress={len(EGRESS)}")
LOG.close()
