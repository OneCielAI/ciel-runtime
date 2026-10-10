"""E2E: provider fallback through the real CLIs, configured and controlled from the UI only.

Two local stubs (127.0.0.1 only): A serves the configured provider `vllm` and answers 429 with Retry-After
while armed; B serves the backup provider `lm-studio`. Isolated Ciel config (CIEL_RUNTIME_TEST_ISOLATED, own
CLAUDE_CONFIG_DIR / CODEX_HOME), unique router port. Windows are classic conhost consoles; e2e_failover_steps.py
(detached, attached to each console) presses keys and reads the screen.

  1 menu: 19. Provider fallback -> automatic on, add backup lm-studio/stub-b-model
  2 Claude Code: turn 1 hits A's 429 -> same request served by B (statusline ⇄); turn 2 goes to B without
    touching A; /fallback status; after the Retry-After the next turn returns to A; /fallback lm-studio 5
    -> B; /fallback return -> A
  3 Codex TUI (routed provider vllm): A armed again -> the turn is served by B
Input to the agents goes through /ca/chat/notify; slash commands are typed into the console.
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
RUN = HERE / "e2e-1-failover"
shutil.rmtree(RUN, ignore_errors=True)
SCRATCH = Path(os.environ["E2E_SCRATCH"]) / "failover"
shutil.rmtree(SCRATCH, ignore_errors=True)
CFG, WS, CODEX, CLAUDE = SCRATCH / "cfg", SCRATCH / "ws", SCRATCH / "codex-home", SCRATCH / "cfg" / ".claude"
for d in (RUN / "shots", CFG, WS, CODEX, CLAUDE):
    d.mkdir(parents=True, exist_ok=True)
LOG = open(RUN / "driver.log", "w", encoding="utf-8")


def log(m: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {m}"
    print(line, flush=True)
    LOG.write(line + "\n")
    LOG.flush()


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class StubState:
    def __init__(self, name: str) -> None:
        self.name = name
        self.requests: list[dict] = []
        self.limited_until = 0.0
        self.retry_after = 25


def make_stub(state: StubState):
    class Stub(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, obj, code=200, extra=None):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _anthropic(self, body, reply):
            message = {"id": f"msg_{len(state.requests)}", "type": "message", "role": "assistant", "model": body.get("model") or "stub",
                       "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 2}}
            if not body.get("stream"):
                self._json({**message, "content": [{"type": "text", "text": reply}], "stop_reason": "end_turn"})
                return
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            for event in ({"type": "message_start", "message": message},
                          {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                          {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": reply}},
                          {"type": "content_block_stop", "index": 0},
                          {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 2}},
                          {"type": "message_stop"}):
                self.wfile.write(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode())
                self.wfile.flush()

        def do_GET(self):
            if self.path.startswith("/__state"):
                self._json({"count": len(state.requests), "requests": state.requests[-20:], "limited_until": state.limited_until})
                return
            self._json({"object": "list", "data": [{"id": f"stub-{state.name.lower()}-model", "object": "model", "max_model_len": 128000}]})

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}"
            if self.path.startswith("/__arm"):
                payload = json.loads(raw)
                state.retry_after = int(payload.get("retry_after") or 25)
                state.limited_until = time.time() + state.retry_after
                self._json({"ok": True, "limited_until": state.limited_until})
                return
            body = json.loads(raw)
            text = json.dumps(body.get("messages") or body.get("input") or [], ensure_ascii=False)
            limited = time.time() < state.limited_until
            messages = body.get("messages") or []
            last = messages[-1] if messages and isinstance(messages[-1], dict) else {}
            content = last.get("content")
            shape = {"last_role": last.get("role"),
                     "last_types": [b.get("type") for b in content if isinstance(b, dict)] if isinstance(content, list) else "str"}
            state.requests.append({"at": time.time(), "path": self.path, "model": body.get("model"), "limited": limited, **shape,
                                   "words": [w for w in ("TURN-1", "TURN-2", "TURN-3", "TURN-4", "TURN-5", "CODEX-TURN") if w in text]})
            with open(RUN / f"stub-{state.name}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(state.requests[-1]) + "\n")
            if limited:
                remaining = max(1, int(state.limited_until - time.time()))
                self._json({"error": {"message": "Rate limit reached for requests", "type": "rate_limit_error"}}, 429,
                           {"retry-after": str(remaining)})
                return
            reply = f"STUB-{state.name}-REPLY-{len(state.requests)}"
            if self.path.split("?")[0].endswith("/v1/messages"):
                self._anthropic(body, reply)
                return
            if body.get("stream"):
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                base = {"id": f"c{len(state.requests)}", "object": "chat.completion.chunk", "created": int(time.time()), "model": "stub"}
                for chunk in ({**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": reply}, "finish_reason": None}]},
                              {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                               "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}):
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                return
            self._json({"id": "c1", "object": "chat.completion", "created": int(time.time()), "model": "stub",
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}})
    return Stub


stubs = {}
for name in ("A", "B"):
    state = StubState(name)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_stub(state))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    stubs[name] = (state, server.server_address[1])
PORT_A, PORT_B = stubs["A"][1], stubs["B"][1]
ROUTER = free_port()
config = {
    "current_provider": "vllm",
    "language": "en",
    "providers": {
        "vllm": {"base_url": f"http://127.0.0.1:{PORT_A}", "current_model": "stub-a-model", "api_key": "dummy-a",
                 "context_window": 128000, "max_output_tokens": 4096},
        "lm-studio": {"base_url": f"http://127.0.0.1:{PORT_B}", "current_model": "stub-b-model", "api_key": "dummy-b",
                      "context_window": 128000, "max_output_tokens": 4096},
    },
}
(CFG / "config.json").write_text(json.dumps(config, indent=2))
(CFG / "log-level").write_text("INFO")
ws_key = str(WS).replace("\\", "/")
(CLAUDE / ".claude.json").write_text(json.dumps({
    "hasCompletedOnboarding": True, "theme": "dark", "bypassPermissionsModeAccepted": True,
    "projects": {ws_key: {"hasTrustDialogAccepted": True, "hasCompletedProjectOnboarding": True}}}), encoding="utf-8")
(CODEX / "config.toml").write_text(f'[projects."{str(WS).replace(chr(92), chr(92) * 2)}"]\ntrust_level = "trusted"\n', encoding="utf-8")
# /fallback as a project command of this workspace (Claude reads ~/.claude/commands, which this test leaves alone).
sys.path.insert(0, str(REPO))
from ciel_runtime_support.slash_command_assets import PROVIDER_FALLBACK_SLASH_COMMAND  # noqa: E402

(WS / ".claude" / "commands").mkdir(parents=True, exist_ok=True)
(WS / ".claude" / "commands" / "fallback.md").write_text(PROVIDER_FALLBACK_SLASH_COMMAND, encoding="utf-8")
log(f"stub A={PORT_A} B={PORT_B} router={ROUTER}")

clear = "".join(f'for /f "delims==" %%v in (\'set {p} 2^>nul\') do set "%%v="\n'
                for p in ("CIEL_RUNTIME_", "ANTHROPIC_", "CLAUDE_", "CODEX_", "OPENAI_"))


def launch(name: str, args: str, terminal: bool = False) -> int:
    """conhost for the menu; Windows Terminal for the CLIs (channel input is typed through it)."""
    script = RUN / f"{name}.cmd"
    script.write_text(
        "@echo off\n" + clear + "set CLAUDECODE=\n"
        f'set "CIEL_RUNTIME_CONFIG_DIR={CFG}"\nset "CIEL_RUNTIME_ROUTER_PORT={ROUTER}"\nset "CIEL_RUNTIME_TEST_ISOLATED=1"\n'
        # Claude keeps its own ~/.claude: channel delivery confirms input from the transcript there
        # (CLAUDE_CONFIG_DIR isolation breaks that confirmation - a known, separate issue).
        f'set "CODEX_HOME={CODEX}"\n'
        f'cd /d "{WS}"\npython "{REPO / "ciel_runtime.py"}" cli {args}\necho LAUNCHER-EXIT=%ERRORLEVEL%\n', encoding="utf-8")
    if terminal:
        subprocess.Popen(["wt.exe", "-w", "new", "--title", f"CIELE2E-FAILOVER-{name}", "--suppressApplicationTitle", "cmd", "/k", str(script)])
    else:
        subprocess.Popen(["conhost.exe", "cmd", "/k", str(script)])
    end = time.time() + 60
    while time.time() < end:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ? { $_.CommandLine -like '*ciel_runtime.py*' -and "
                              "$_.ParentProcessId -in @(Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | ? { $_.CommandLine -like '*"
                              + str(script) + "*' } | % { $_.ProcessId }) } | % { $_.ProcessId }"], capture_output=True, text=True).stdout.split()
        if out:
            return int(out[0])
        time.sleep(1)
    return 0


def steps(mode: str, pid: int) -> int:
    args = [sys.executable, str(HERE / "e2e_failover_steps.py"), mode, str(pid), str(RUN), str(ROUTER), str(PORT_A), str(PORT_B)]
    return subprocess.run(args, creationflags=0x00000008, timeout=1500).returncode


def teardown(tag: str) -> None:
    ps = ("$pids = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | ? { $_.LocalPort -eq " + str(ROUTER) + " } | % { $_.OwningProcess }); "
          "$pids += @(Get-CimInstance Win32_Process | ? { $_.ProcessId -ne $PID -and $_.ProcessId -ne " + str(os.getpid()) + " -and $_.Name -ne 'powershell.exe' -and "
          "($_.CommandLine -like '*" + str(RUN) + "*' -or $_.CommandLine -like '*" + str(CFG) + "*' -or $_.CommandLine -like '*" + str(CODEX) + "*') } | % { $_.ProcessId }); "
          "$pids | Sort-Object -Unique | % { taskkill /T /F /PID $_ | Out-Null; \"killed $_\" }")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    log(f"teardown {tag}: " + r.stdout.strip().replace("\n", " "))
    time.sleep(2)


for mode, args in (("menu", "--ca-menu --ca-no-update-check --ca-no-self-update-check"),
                   ("claude", "--ca-runtime claude --ca-no-update-check --ca-no-self-update-check"),
                   ("codex", "--ca-runtime codex --ca-no-update-check --ca-no-self-update-check")):
    pid = launch(mode, args, terminal=mode != "menu")
    log(f"{mode}: launcher python pid {pid}")
    log(f"{mode}: steps exit {steps(mode, pid)}")
    teardown(mode)
for name, (state, _port) in stubs.items():
    log(f"stub {name}: {len(state.requests)} requests: " + json.dumps([{k: r[k] for k in ('limited', 'words')} for r in state.requests]))
failover = CFG / "provider-failover.json"
log("provider-failover.json: " + (failover.read_text(encoding="utf-8") if failover.exists() else "missing"))
for f in CFG.rglob("router.log"):
    lines = [line for line in f.read_text(encoding="utf-8", errors="replace").splitlines()
             if "provider_failover" in line or "oauth_token_fallback" in line or " 429" in line]
    log(f"router log {f.parent.name}:\n" + "\n".join(lines[-40:]))
    shutil.copy(f, RUN / f"router-{f.parent.name}.log")
log("done")
