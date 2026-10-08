"""E2E: Claude Code critical-path removal prompt vs. a PermissionRequest hook.

Usage: python e2e_rm.py claude
Isolated: scratch CIEL_RUNTIME_CONFIG_DIR + CIEL_RUNTIME_TEST_ISOLATED (Ciel writes
its hooks into <cfg>/.claude/settings.json, so ~/.claude/settings.json is left
alone; Claude itself uses ~/.claude unless E2E_ISOLATE_CLAUDE=1), unique router port, scratch workspace, stub OpenAI-compatible upstream
(provider vllm). Every inherited CIEL_RUNTIME_*, ANTHROPIC_*, CLAUDE_*, CODEX_*
variable is cleared in the launch window.

The stub answers a user message containing "RUN-RM <command> END-RM" with one
Bash tool call running <command>. E2E_PROBE_HOOK=1 adds a workspace-level
PermissionRequest hook that records the raw hook input (and answers allow
while <run>/allow.flag exists). E2E_GUARD_HOOK=1 registers the repository's
ciel-runtime-tool-guard.py the same way. E2E_CONFIG_EXTRA merges JSON into the
Ciel config; E2E_LAUNCH_ARGS replaces "cli --ca-runtime <mode>". E2E_ENV adds "NAME=VALUE;..." to the window.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHOT = Path(os.environ.get("E2E_SHOT_SCRIPT") or HERE / "shot_title.ps1")
REPO = Path(os.environ.get("E2E_REPO") or r"C:\Users\djlov\ciel-runtime")
MODE = sys.argv[1]
RUN = HERE / f"e2e-{MODE}-{int(time.time())}"
RUN.mkdir()
CFG, WS, SHOTS = RUN / "cfg", RUN / "workspace", RUN / "shots"
for d in (CFG, WS, SHOTS, CFG / ".claude"):
    d.mkdir(parents=True, exist_ok=True)
(WS / "README.md").write_text("e2e workspace\n")
TITLE = f"CIELE2E-rm-{RUN.name[-5:]}"
LOG = open(RUN / "driver.log", "a", encoding="utf-8")


def log(m: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {m}"
    print(line, flush=True)
    LOG.write(line + "\n"); LOG.flush()


def free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


REQS: list[dict] = []


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def do_GET(self):
        self._json({"object": "list", "data": [{"id": "stub-model", "object": "model", "owned_by": "stub", "max_model_len": 128000}]})

    def _stream(self, n, delta, finish):
        self.send_response(200); self.send_header("content-type", "text/event-stream"); self.end_headers()
        base = {"id": f"c{n}", "object": "chat.completion.chunk", "created": int(time.time()), "model": "stub-model"}
        for chunk in (
            {**base, "choices": [{"index": 0, "delta": {"role": "assistant", **delta}, "finish_reason": None}]},
            {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}], "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}},
        ):
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode()); self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush()

    def _anthropic(self, n, body, match, tool_names):
        msgs = body.get("messages") or []
        last = msgs[-1] if msgs else {}
        content = last.get("content")
        answered = isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
        if match and not answered and "Bash" in tool_names:
            block = {"type": "tool_use", "id": f"toolu_e2e_{n}", "name": "Bash", "input": {}}
            delta = {"type": "input_json_delta", "partial_json": json.dumps({"command": match.group(1), "description": "e2e removal"})}
            stop = "tool_use"
        else:
            block = {"type": "text", "text": ""}
            delta = {"type": "text_delta", "text": f"STUB-REPLY-{n}"}
            stop = "end_turn"
        message = {"id": f"msg_{n}", "type": "message", "role": "assistant", "model": body.get("model") or "stub-model",
                   "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 2}}
        if not body.get("stream"):
            final = dict(block)
            if final["type"] == "text":
                final["text"] = delta["text"]
            else:
                final["input"] = json.loads(delta["partial_json"])
            self._json({**message, "content": [final], "stop_reason": stop})
            return
        self.send_response(200); self.send_header("content-type", "text/event-stream"); self.end_headers()
        for ev in ({"type": "message_start", "message": message},
                   {"type": "content_block_start", "index": 0, "content_block": block},
                   {"type": "content_block_delta", "index": 0, "delta": delta},
                   {"type": "content_block_stop", "index": 0},
                   {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None}, "usage": {"output_tokens": 2}},
                   {"type": "message_stop"}):
            self.wfile.write(f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n".encode()); self.wfile.flush()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        msgs = body.get("messages") or []

        def text(m):
            c = m.get("content")
            return c if isinstance(c, str) else " ".join(str(x.get("text", "")) for x in (c or []) if isinstance(x, dict))

        users = [text(m) for m in msgs if m.get("role") == "user"]
        last = users[-1] if users else ""
        n = len(REQS) + 1
        tool_names = [((t.get("function") or {}).get("name") or t.get("name")) for t in body.get("tools") or [] if isinstance(t, dict)]
        tool_outputs = [text(m) for m in msgs if m.get("role") == "tool"]
        last_role = msgs[-1].get("role") if msgs else None
        match = re.search(r"RUN-RM (.+?) END-RM", last)
        REQS.append({"n": n, "last": " ".join(last.split())[-160:], "last_role": last_role, "rm": bool(match)})
        with open(RUN / "stub.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({**REQS[-1], "tools": tool_names[:40], "tool_output": tool_outputs[-1:] if last_role == "tool" else []}, ensure_ascii=False) + "\n")
        if self.path.split("?")[0].endswith("/v1/messages"):
            self._anthropic(n, body, match, tool_names)
            return
        if match and last_role != "tool" and "Bash" in tool_names and body.get("stream"):
            call = {"index": 0, "id": f"call_{n}", "type": "function",
                    "function": {"name": "Bash", "arguments": json.dumps({"command": match.group(1), "description": "e2e removal"})}}
            self._stream(n, {"tool_calls": [call]}, "tool_calls")
            return
        reply = f"STUB-REPLY-{n}"
        if body.get("stream"):
            self._stream(n, {"content": reply}, "stop")
        else:
            self._json({"id": f"c{n}", "object": "chat.completion", "created": int(time.time()), "model": "stub-model",
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}})


stub = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
STUB = stub.server_address[1]
threading.Thread(target=stub.serve_forever, daemon=True).start()
ROUTER = free_port()
config = {
    "current_provider": "vllm",
    "language": "en",
    "providers": {"vllm": {"base_url": f"http://127.0.0.1:{STUB}", "current_model": "stub-model", "api_key": "dummy",
                           "context_window": 128000, "max_output_tokens": 4096}},
}
config.update(json.loads(os.environ.get("E2E_CONFIG_EXTRA") or "{}"))
(CFG / "config.json").write_text(json.dumps(config, indent=2))
(CFG / "log-level").write_text("INFO")
ws_key = str(WS).replace("\\", "/")
(CFG / ".claude" / ".claude.json").write_text(json.dumps({
    "hasCompletedOnboarding": True, "theme": "dark", "bypassPermissionsModeAccepted": True,
    "projects": {ws_key: {"hasTrustDialogAccepted": True, "hasCompletedProjectOnboarding": True}},
}), encoding="utf-8")
if os.environ.get("E2E_GUARD_HOOK"):
    # The repository's guard (the code under test), registered for this workspace only.
    (WS / ".claude").mkdir(exist_ok=True)
    py = sys.executable.replace(chr(92), "/")
    guard = str(REPO / "ciel-runtime-tool-guard.py").replace(chr(92), "/")
    (WS / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"PermissionRequest": [
        {"matcher": "*", "hooks": [{"type": "command", "command": f'"{py}" "{guard}"'}]}]}}, indent=1), encoding="utf-8")
if os.environ.get("E2E_PROBE_HOOK"):
    probe = RUN / "probe_hook.py"
    probe.write_text(
        "import json, sys, pathlib\n"
        f"run = pathlib.Path({str(RUN)!r})\n"
        "raw = sys.stdin.read()\n"
        "with open(run / 'perm-events.jsonl', 'a', encoding='utf-8') as f:\n"
        "    f.write(raw.strip() + '\\n')\n"
        "if (run / 'allow.flag').exists():\n"
        "    print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PermissionRequest', 'decision': {'behavior': 'allow'}}}))\n",
        encoding="utf-8")
    (WS / ".claude").mkdir(exist_ok=True)
    py = sys.executable.replace("\\", "/")
    (WS / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"PermissionRequest": [
        {"matcher": "*", "hooks": [{"type": "command", "command": f'"{py}" "{str(probe).replace(chr(92), "/")}"'}]}]}}, indent=1), encoding="utf-8")
log(f"run={RUN} stub=127.0.0.1:{STUB} router={ROUTER} title={TITLE}")

clear = "".join(f'for /f "delims==" %%v in (\'set {p} 2^>nul\') do set "%%v="\n' for p in ("CIEL_RUNTIME_", "ANTHROPIC_", "CLAUDE_", "CODEX_", "OPENAI_"))
extra_env = "".join(f'set "{pair.strip()}"\n' for pair in (os.environ.get("E2E_ENV") or "").split(";") if pair.strip())
script = RUN / "launch.cmd"
script.write_text(
    "@echo off\n" + clear + "set CLAUDECODE=\n"
    f'set "CIEL_RUNTIME_CONFIG_DIR={CFG}"\nset "CIEL_RUNTIME_ROUTER_PORT={ROUTER}"\n'
    'set "CIEL_RUNTIME_TEST_ISOLATED=1"\n'
    + (f'set "CLAUDE_CONFIG_DIR={CFG / ".claude"}"\n' if os.environ.get("E2E_ISOLATE_CLAUDE") else "")
    + extra_env
    + f'cd /d "{WS}"\n'
    f'python "{REPO / "ciel_runtime.py"}" {os.environ.get("E2E_LAUNCH_ARGS") or "cli --ca-runtime " + MODE} --ca-no-update-check --ca-no-self-update-check\n'
    "echo LAUNCHER-EXIT=%ERRORLEVEL%\n", encoding="utf-8")
subprocess.Popen(["wt.exe", "-w", "new", "--title", TITLE, "--suppressApplicationTitle", "cmd", "/k", str(script)])


def shot(label: str) -> None:
    out = SHOTS / f"{len(list(SHOTS.iterdir())) + 1:02d}-{label}.png"
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SHOT), TITLE, str(out)], capture_output=True, text=True)
    log(f"shot {label}: {r.stdout.strip() or r.stderr.strip()[:200]}")


def keys(text: str) -> None:
    """Send keys to the test window (only my own E2E window, found by its unique title)."""
    ps = (f"$w = New-Object -ComObject WScript.Shell; if ($w.AppActivate('{TITLE}')) {{ Start-Sleep -Milliseconds 400; $w.SendKeys('{text}') ; 'sent' }} else {{ 'no window' }}")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    log(f"keys {text!r}: {r.stdout.strip()}")


def http(method: str, path: str, body: dict | None = None, timeout: float = 10) -> dict:
    req = urllib.request.Request(f"http://127.0.0.1:{ROUTER}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
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


def notify(text: str) -> dict:
    body = {"message": text, "channel": "e2e", "sender_id": "e2e-driver"}
    if os.environ.get("E2E_TRANSPORT"):
        body["input_transport"] = os.environ["E2E_TRANSPORT"]
    r = http("POST", "/ca/chat/notify", body)
    log(f"notify {text!r} -> {json.dumps(r)[:200]}")
    return r


def wait_router(secs: float = 90) -> bool:
    end = time.time() + secs
    while time.time() < end:
        r = http("GET", "/health", timeout=2)
        if r and "raw" not in r:
            return True
        time.sleep(1)
    return False


def guard_lines() -> list[str]:
    out = []
    for f in CFG.rglob("events.log*"):
        out += [line[-260:] for line in f.read_text(encoding="utf-8", errors="replace").splitlines() if "PermissionRequest" in line or "removal" in line]
    return out


(HERE / f"e2e-current-rm.txt").write_text(str(RUN))
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
                notify(arg)
            elif verb == "keys":
                keys(arg)
            elif verb == "sleep":
                time.sleep(float(arg))
            elif verb == "mkdir":
                p = Path(arg); p.mkdir(parents=True, exist_ok=True); (p / "keep.txt").write_text("e2e\n")
                log(f"mkdir {arg}: exists={p.exists()}")
            elif verb == "exists":
                log(f"exists {arg}: {Path(arg).exists()}")
            elif verb == "allow":
                (RUN / "allow.flag").write_text("1") if arg != "off" else (RUN / "allow.flag").unlink(missing_ok=True)
                log(f"allow.flag {arg or 'on'}")
            elif verb == "dump":
                log("stub: " + json.dumps(REQS[-6:], ensure_ascii=False))
                pe = RUN / "perm-events.jsonl"
                log("perm-events: " + (pe.read_text(encoding="utf-8")[-3000:] if pe.exists() else "<none>"))
                for g in guard_lines()[-12:]:
                    log("  GUARD " + g)
            elif verb == "teardown":
                ps = (
                    "$ports = @(" + str(ROUTER) + "); "
                    "$pids = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | ? { $ports -contains $_.LocalPort } | % { $_.OwningProcess }); "
                    "$pids += @(Get-CimInstance Win32_Process | ? { $_.ProcessId -ne $PID -and $_.Name -ne 'powershell.exe' -and $_.CommandLine -like '*" + RUN.name + "*' } | % { $_.ProcessId }); "
                    "$pids | Sort-Object -Unique | % { $c = (Get-CimInstance Win32_Process -Filter \"ProcessId=$_\").CommandLine; \"$_ $c\".Substring(0, [Math]::Min(160, (\"$_ $c\").Length)); taskkill /T /F /PID $_ | Out-Null }"
                )
                r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
                log("teardown:\n" + (r.stdout.strip() or r.stderr.strip()[:300]))
            elif verb == "quit":
                log("quit"); os._exit(0)
        except Exception as exc:
            log(f"ERR {line}: {type(exc).__name__}: {exc}")
    time.sleep(0.5)
