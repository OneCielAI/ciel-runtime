"""E2E: reasoning effort a native Codex launch sends through Ciel (unset -> medium, configured kept).

Usage: python e2e_effort.py <label> <mode> <variant: unset|high|routed-high>
  routed-high: provider vllm (stub chat completions) with effort_level high; config.toml without effort
  mode: codex | codex-remote
Isolated: scratch CIEL_RUNTIME_CONFIG_DIR, unique router port, scratch CODEX_HOME with a
test-only API-key auth.json and a copied models_cache.json (gpt-6.1-sol default effort low),
workspace outside git; the Codex routed upstream is a local stub that records every
/responses body. Every inherited CIEL_RUNTIME_*, CODEX_*, OPENAI_*, ANTHROPIC_*, CLAUDE_*
variable is cleared in the launch window. E2E_REPO selects the Ciel source tree.
Input only via /ca/chat/notify.
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
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = Path(os.environ.get("E2E_REPO") or r"C:\Users\djlov\ciel-runtime")
LABEL, MODE, VARIANT = sys.argv[1], sys.argv[2], sys.argv[3]
RUN = HERE / LABEL
shutil.rmtree(RUN, ignore_errors=True)
SCRATCH = Path(os.environ["E2E_SCRATCH"]) / LABEL
shutil.rmtree(SCRATCH, ignore_errors=True)
CFG, WS, HOME, SHOTS = SCRATCH / "cfg", SCRATCH / "ws", SCRATCH / "codex-home", RUN / "shots"
for d in (RUN, CFG, WS, HOME, SHOTS):
    d.mkdir(parents=True, exist_ok=True)
TITLE = f"CIELE2E-EFFORT-{LABEL}"
LOG = open(RUN / "driver.log", "a", encoding="utf-8")


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


BODIES: list[dict] = []


def sse(n: int, text: str) -> bytes:
    rid = f"resp_{n}"
    events = [
        {"type": "response.created", "response": {"id": rid}},
        {"type": "response.output_item.done", "output_index": 0,
         "item": {"type": "message", "role": "assistant", "id": f"m{n}", "content": [{"type": "output_text", "text": text}]}},
        {"type": "response.completed",
         "response": {"id": rid, "status": "completed", "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}},
    ]
    return b"".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events)


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.split("?")[0].endswith("/models"):
            data = json.dumps({"object": "list", "data": [{"id": "stub-model", "object": "model", "max_model_len": 128000}]}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(404)
        self.send_header("content-length", "0")
        self.end_headers()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        n = len(BODIES) + 1
        row = {"n": n, "path": self.path, "model": body.get("model"), "reasoning": body.get("reasoning"), "text": body.get("text"),
               "reasoning_effort": body.get("reasoning_effort")}
        BODIES.append(row)
        with open(RUN / "stub-bodies.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        if self.path.split("?")[0].endswith("/chat/completions"):
            base = {"id": f"c{n}", "object": "chat.completion.chunk", "created": int(time.time()), "model": "stub-model"}
            chunks = [{**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": f"STUB-REPLY-{n}"}, "finish_reason": None}]},
                      {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                       "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}]
            data = b"".join(f"data: {json.dumps(c)}\n\n".encode() for c in chunks) + b"data: [DONE]\n\n"
        else:
            data = sse(n, f"STUB-REPLY-{n}")
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


stub = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
STUB = stub.server_address[1]
threading.Thread(target=stub.serve_forever, daemon=True).start()
ROUTER, APPSRV = free_port(), free_port()

if VARIANT == "routed-high":
    providers = {"vllm": {"base_url": f"http://127.0.0.1:{STUB}", "current_model": "stub-model", "api_key": "dummy",
                          "context_window": 128000, "max_output_tokens": 4096, "effort_level": "high"}}
    current = "vllm"
else:
    providers = {"codex": {"route_through_router": True, "current_model": "gpt-6.1-sol"}}
    current = "codex"
(CFG / "config.json").write_text(json.dumps({"current_provider": current, "language": "en", "providers": providers}, indent=2))
(CFG / "log-level").write_text("INFO")
top = 'model_reasoning_effort = "high"\n' if VARIANT == "high" else ""
ws_key = str(WS).replace("\\", "\\\\")
(HOME / "config.toml").write_text(top + f'[projects."{ws_key}"]\ntrust_level = "trusted"\n\n[tui]\n', encoding="utf-8")
(HOME / "auth.json").write_text(json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "sk-e2e-local-test-only"}))
shutil.copy(os.environ["E2E_MODELS_CACHE"], HOME / "models_cache.json")
log(f"run={RUN.name} mode={MODE} variant={VARIANT} repo={REPO} stub={STUB} router={ROUTER}")
BEFORE = (HOME / "config.toml").read_bytes()
VERSIONS: list[tuple[str, bytes]] = [("before", BEFORE)]


def watch_config() -> None:
    # Every distinct content of config.toml while the launch runs, in order.
    while True:
        try:
            data = (HOME / "config.toml").read_bytes()
        except OSError:
            data = b""
        if data != VERSIONS[-1][1]:
            VERSIONS.append((time.strftime("%H:%M:%S"), data))
        time.sleep(0.05)


threading.Thread(target=watch_config, daemon=True).start()
log("codex config.toml before:\n" + BEFORE.decode("utf-8").replace(ws_key, "<ws>"))

clear = "".join(f'for /f "delims==" %%v in (\'set {p} 2^>nul\') do set "%%v="\n'
                for p in ("CIEL_RUNTIME_", "ANTHROPIC_", "CLAUDE_", "CODEX_", "OPENAI_"))
script = RUN / "launch.cmd"
script.write_text(
    "@echo off\n" + clear + "set CLAUDECODE=\n"
    f'set "CIEL_RUNTIME_CONFIG_DIR={CFG}"\nset "CIEL_RUNTIME_ROUTER_PORT={ROUTER}"\nset "CODEX_HOME={HOME}"\n'
    f'set "CIEL_RUNTIME_CODEX_APP_SERVER_LISTEN=ws://127.0.0.1:{APPSRV}"\n'
    f'set "CIEL_RUNTIME_CODEX_ROUTED_UPSTREAM=http://127.0.0.1:{STUB}/backend-api/codex"\n'
    f'cd /d "{WS}"\n'
    f'python "{REPO / "ciel_runtime.py"}" cli --ca-runtime {MODE} --ca-no-update-check --ca-no-self-update-check\n'
    "echo LAUNCHER-EXIT=%ERRORLEVEL%\n", encoding="utf-8")
subprocess.Popen(["wt.exe", "-w", "new", "--title", TITLE, "--suppressApplicationTitle", "cmd", "/k", str(script)])


def shot(label: str) -> None:
    out = SHOTS / f"{len(list(SHOTS.iterdir())) + 1:02d}-{label}.png"
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HERE / "shot_title.ps1"), TITLE, str(out)],
                       capture_output=True, text=True)
    log(f"shot {label}: {r.stdout.strip() or r.stderr.strip()[:200]}")


def http(method: str, path: str, body: dict | None = None, timeout: float = 10) -> dict:
    req = urllib.request.Request(f"http://127.0.0.1:{ROUTER}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json"})
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
        return {"raw": raw[:300]}


def router_lines(*needles: str) -> list[str]:
    out = []
    for f in CFG.rglob("router.log*"):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if any(n in line for n in needles):
                out.append(line[-300:])
    return out


def wait(pred, secs: float) -> bool:
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(1)
    return False


log(f"router up: {wait(lambda: 'raw' not in http('GET', '/health', timeout=2), 90)}")
time.sleep(25)
shot("ready")
r = http("POST", "/ca/chat/notify", {"message": "E2E effort check: reply with one word.", "channel": "e2e", "sender_id": "e2e-driver"})
log(f"notify -> {json.dumps(r)[:200]}")
log(f"model request seen: {wait(lambda: any(str(b['path']).split('?')[0].endswith(('/responses', '/chat/completions')) for b in BODIES), 120)}")
time.sleep(6)
shot("after-turn")
for b in BODIES:
    log(f"stub body n={b['n']} path={b['path']} model={b['model']} reasoning={json.dumps(b['reasoning'])} reasoning_effort={b['reasoning_effort']} text={json.dumps(b['text'])}")
for index, (stamp, data) in enumerate(VERSIONS[1:], 1):
    log(f"config.toml version {index} at {stamp}: previous content kept byte-for-byte at the end: {data.endswith(VERSIONS[index - 1][1])}\n"
        + data.decode("utf-8", "replace").replace(ws_key, "<ws>"))
log(f"config.toml versions after the original: {len(VERSIONS) - 1}")
for line in router_lines("codex_reasoning_effort_default", "codex_config_effort_default", "model_reasoning_effort"):
    log("LOG " + line)
for f in CFG.rglob("router.log"):
    shutil.copy(f, RUN / "router.log")
    break
for desktop_config in CFG.glob("codex-desktop/*/codex-home/config.toml"):
    log("desktop app codex-home config.toml:" + chr(10) + desktop_config.read_text(encoding="utf-8").replace(ws_key, "<ws>"))
for p in [*HOME.rglob("rollout-*.jsonl"), *CFG.glob("codex-desktop/*/codex-home/sessions/**/rollout-*.jsonl")]:
    for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if '"turn_context"' in raw[:200]:
            o = json.loads(raw)["payload"]
            log(f"rollout turn_context model={o.get('model')} effort={o.get('effort')}")
ps = ("$ports = @(" + str(ROUTER) + "," + str(APPSRV) + "); "
      "$pids = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | ? { $ports -contains $_.LocalPort } | % { $_.OwningProcess }); "
      "$pids += @(Get-CimInstance Win32_Process | ? { $_.ProcessId -ne $PID -and $_.ProcessId -ne " + str(os.getpid()) + " -and $_.Name -ne 'powershell.exe' -and ($_.CommandLine -like '*" + str(RUN) + "*' -or $_.CommandLine -like '*" + str(CFG) + "*') } | % { $_.ProcessId }); "
      "$pids | Sort-Object -Unique | % { taskkill /T /F /PID $_ | Out-Null; \"killed $_\" }")
rr = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
log("teardown: " + (rr.stdout.strip().replace("\n", " ") or rr.stderr.strip()[:200]))
log("done")
