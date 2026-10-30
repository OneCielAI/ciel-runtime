"""Owned real-CLI E2E for the CielAiRouter alias safeguard (2026-10-26).

usage: python -I alias_safeguard_e2e.py --runtime-root <snapshot or worktree> --label <name>

Emily's shape: the configured CielAiRouter model is an alias, but the upstream answers with another
native model name (claude-opus-5-5). The loopback fixture answers the first real turn with usage at
95% of the 200,000-token window under that native name. Fixed code is expected to queue the 90%
safeguard and type /compact exactly once; old code is expected to queue nothing.

Everything is owned by this script: scratch HOME/config/workspace under C:/Windows/Temp, free
loopback ports, a loopback fixture, and a console started minimized without activation. Inherited
CIEL_RUNTIME_*/provider variables are removed first. Teardown kills only processes whose command
line or CIEL_RUNTIME_CONFIG_DIR carries this run's scratch path, plus the launcher tree it created.
Live routers 9505/9524 are only read through GET /health, before and after.
"""
import os
import sys

PURGE = ("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC_", "CODEX_", "OPENAI_", "OPENROUTER_", "GEMINI_", "GOOGLE_", "AZURE_",
         "MOONSHOT_", "KIMI_", "XAI_", "GROK_", "ZAI_", "DEEPSEEK_", "NVIDIA_", "OLLAMA_", "FIREWORKS_", "OPENCODE_")
STRIPPED = sorted(n for n in os.environ if n.upper().startswith(PURGE)
                  or any(w in n.upper() for w in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "CREDENTIAL")))
for _name in STRIPPED:
    del os.environ[_name]

import argparse  # noqa: E402
import ctypes  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
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
HELPERS = next(p for p in HERE.parents if (p / "ciel_runtime.py").exists()) / "docs/journal/2026/10/26/fixes/cielairouter-alias-safeguard/evidence/e2e"
WORKTREE = next(p for p in HERE.parents if (p / "ciel_runtime.py").exists())
CONSOLE_DRIVER_DIR = WORKTREE / "docs/journal/2026/10/16/features/cielairouter-direct-path/evidence/e2e"
CLAUDE_BIN = Path("C:/Users/djlov/AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe")

parser = argparse.ArgumentParser()
parser.add_argument("--runtime-root", required=True)
parser.add_argument("--label", required=True)
parser.add_argument("--expect", choices=("compact", "none"), default="compact")
ARGS = parser.parse_args()
RUNTIME_ROOT = Path(ARGS.runtime_root).resolve()
assert (RUNTIME_ROOT / "ciel_runtime.py").is_file(), RUNTIME_ROOT
for required in (CLAUDE_BIN, CONSOLE_DRIVER_DIR / "console_driver.py"):
    if not required.exists():
        raise SystemExit(f"missing {required}")

RUN = HERE / ("run-" + ARGS.label + "-" + time.strftime("%H%M%S"))
RUN.mkdir()
SCRATCH = Path(tempfile.mkdtemp(prefix="ciel-owned-alias-", dir="C:/Windows/Temp"))
CFG, WS, HOME, TMP = (SCRATCH / n for n in ("cfg", "ws", "home", "tmp"))
CLAUDE_DIR = CFG / ".claude"
for d in (CFG, WS, HOME, TMP, CLAUDE_DIR):
    d.mkdir(parents=True, exist_ok=True)
(CFG / "log-level").write_text("DEBUG")
(WS / "README.txt").write_text("Owned local fixture workspace")

ALIAS = "owned-route-alias"
NATIVE_MODEL = "claude-opus-5-5"
WINDOW = 200000
HIGH_TOKENS = 1000  # 95% of WINDOW, reported under NATIVE_MODEL
BASELINE = "ALIAS-SAFEGUARD-BASELINE"
HIGH_REPLY = "ALIAS-HIGH-USAGE-REPLY"
REQS = []
COMPACTS = []
HALT = threading.Event()
EVENTS = []
EGRESS = []
QUEUE_MARKER = "RECOVERY-QUEUED-ONCE"


def _text(m):
    c = m.get("content")
    return c if isinstance(c, str) else " ".join(str(x.get("text", "")) for x in (c or []) if isinstance(x, dict))


class Stub(BaseHTTPRequestHandler):
    """Loopback CielAiRouter: answers every request as NATIVE_MODEL; only the first baseline turn is high."""

    def log_message(self, *a):
        pass

    def _json(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/events":
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            seen = 0
            try:
                while not HALT.is_set():
                    if len(EVENTS) > seen:
                        self.wfile.write(("data: " + json.dumps(EVENTS[seen]) + chr(10) * 2).encode())
                        self.wfile.flush()
                        seen += 1
                    else:
                        HALT.wait(.1)
            except OSError:
                pass
            return
        # CielAiRouter offers only catalog rows with tool calling and thinking (as in
        # docs/journal/2026/10/20/diagnostics/claude-stream-recovery/fixture.py).
        self._json({"object": "list", "data": [{"id": ALIAS, "object": "model", "owned_by": "owned-fixture",
                                                "max_model_len": WINDOW, "context_length": WINDOW,
                                                "max_output_tokens": 4096,
                                                "capabilities": {"tool_calling": True, "reasoning": True,
                                                                 "thinking": True, "supportsThinking": True}}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        # The cielairouter route can append its own user message after the prompt, so the
        # turn is every user message after the last assistant message (as in the 10-20 fixture).
        trailing = []
        for m in reversed(body.get("messages") or []):
            if m.get("role") != "user":
                break
            trailing.append(_text(m))
        last = " ".join(reversed(trailing))
        n = len(REQS) + 1
        high = BASELINE in last and bool(body.get("tools")) and not any(q["high"] for q in REQS)
        record = {"n": n, "at": time.time(), "path": self.path, "model": body.get("model"),
                  "stream": bool(body.get("stream")), "tools": len(body.get("tools") or []), "high": high,
                  "baseline_in_last": BASELINE in last, "queue_in_last": QUEUE_MARKER in last, "compact_like": "summar" in json.dumps(body.get("system") or "").lower()}
        REQS.append(record)
        if self.path.split("?")[0].endswith("/count_tokens"):
            self._json({"input_tokens": 100})
            return
        compact = "CRITICAL: Respond with TEXT ONLY" in json.dumps(body.get("messages", []))
        record["native_compact"] = compact
        if compact:
            COMPACTS.append(record)
            record["compact_number"] = len(COMPACTS)
            if len(COMPACTS) <= 2:
                # Explicit terminal HTTP failure; not an idle sleep and not uncertain submission.
                time.sleep(2)
                data = json.dumps({'type': 'error', 'error': {'type': 'api_error', 'message': 'owned compact terminal failure'}}).encode()
                self.send_response(500)
                self.send_header('content-type', 'application/json')
                self.send_header('content-length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                self.wfile.flush()
                return
        tokens = HIGH_TOKENS if high else 100
        reply = "RECOVERY-QUEUE-REPLIED" if QUEUE_MARKER in last and not compact else HIGH_REPLY if high else f"OWNED-REPLY-{n} summary: the owned baseline turn finished."
        compact_tool = next((tool.get('name') for tool in body.get('tools', [])
                             if str(tool.get('name', '')).endswith('__compact_session')), None)
        trigger_tool = (QUEUE_MARKER in last and not compact and compact_tool
                        and not any(x.get('trigger_compact_tool') for x in REQS[:-1]))
        record['trigger_compact_tool'] = bool(trigger_tool)
        message = {"id": f"msg_alias_{n}", "type": "message", "role": "assistant", "model": NATIVE_MODEL,
                   "content": [], "stop_reason": None, "stop_sequence": None,
                   "usage": {"input_tokens": tokens, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                             "output_tokens": 2}}
        if trigger_tool:
            events = [
                {'type': 'message_start', 'message': message},
                {'type': 'content_block_start', 'index': 0, 'content_block': {
                    'type': 'tool_use', 'id': 'tool_owned_compact_retrigger', 'name': compact_tool, 'input': {}}},
                {'type': 'content_block_delta', 'index': 0, 'delta': {
                    'type': 'input_json_delta', 'partial_json': json.dumps({'reason': 'owned SSE retrigger after compact failures'})}},
                {'type': 'content_block_stop', 'index': 0},
                {'type': 'message_delta', 'delta': {'stop_reason': 'tool_use'}, 'usage': {'output_tokens': 10}},
                {'type': 'message_stop'}]
            self.send_response(200)
            self.send_header('content-type', 'text/event-stream')
            self.end_headers()
            for ev in events:
                self.wfile.write(('event: ' + ev['type'] + chr(10) + 'data: ' + json.dumps(ev) + chr(10)*2).encode())
                self.wfile.flush()
        elif not body.get("stream"):
            self._json({**message, "content": [{"type": "text", "text": reply}], "stop_reason": "end_turn"})
        else:
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            for ev in ({"type": "message_start", "message": message},
                       {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                       {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": reply}},
                       {"type": "content_block_stop", "index": 0},
                       {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                        "usage": {"output_tokens": 2}},
                       {"type": "message_stop"}):
                self.wfile.write(f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n".encode())
                self.wfile.flush()
        record["replied_at"] = time.time()
        with open(RUN / "stub.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Sentinel(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass
    def deny(self):
        EGRESS.append({'method': self.command, 'destination': self.path})
        self.send_response(403)
        self.send_header('content-length', '0')
        self.end_headers()
    do_CONNECT = do_GET = do_POST = do_HEAD = deny

sentinel = ThreadingHTTPServer(('127.0.0.1', 0), Sentinel)
threading.Thread(target=sentinel.serve_forever, daemon=True).start()
SENTINEL_PORT = sentinel.server_port
ROUTER = free_port()
stub = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
threading.Thread(target=stub.serve_forever, daemon=True).start()
FIXTURE_PORT = stub.server_port
assert ROUTER != FIXTURE_PORT

(CFG / "config.json").write_text(json.dumps({
    "current_provider": "cielairouter", "language": "en", "work_mode": False,
    "providers": {"cielairouter": {"base_url": f"http://127.0.0.1:{FIXTURE_PORT}", "current_model": ALIAS,
                                   "api_key": "owned-dummy", "context_window": WINDOW, "max_model_len": WINDOW,
                                   "max_output_tokens": 4096, "gateway_selection": "off",
                                   "compact_safeguard": False, "gateway_retries": 0}},
}))
(CLAUDE_DIR / ".claude.json").write_text(json.dumps({
    "hasCompletedOnboarding": True, "theme": "dark",
    "projects": {str(WS): {"hasTrustDialogAccepted": True}, str(WS).replace("\\", "/"): {"hasTrustDialogAccepted": True}}}))

env = dict(os.environ)
env["OWNED_RESTART_RUNTIME_ROOT"] = str(RUNTIME_ROOT)
env.update(
    HOME=str(HOME), USERPROFILE=str(HOME), TEMP=str(TMP), TMP=str(TMP), CLAUDE_CONFIG_DIR=str(CLAUDE_DIR),
    CIEL_RUNTIME_CONFIG_DIR=str(CFG), CIEL_RUNTIME_TEST_ISOLATED="1", CIEL_RUNTIME_ROUTER_PORT=str(ROUTER),
    DISABLE_AUTOUPDATER="1", DISABLE_TELEMETRY="1", DISABLE_ERROR_REPORTING="1",
    HTTP_PROXY=f"http://127.0.0.1:{SENTINEL_PORT}", HTTPS_PROXY=f"http://127.0.0.1:{SENTINEL_PORT}", ALL_PROXY=f"http://127.0.0.1:{SENTINEL_PORT}",
    NO_PROXY="127.0.0.1,localhost")
env["PATH"] = str(CLAUDE_BIN.parent) + ";" + env.get("PATH", "")

cmd = [sys.executable, "-I", str(HELPERS / "launch_shim.py"), "--", "cli", "--ca-runtime", "claude",
       "--ca-no-update-check", "--ca-no-self-update-check", "--ca-no-web-search",
       "--", "--permission-mode", "default", "--debug-file", str(RUN / "native-debug.log")]
result = {"label": ARGS.label, "expect": ARGS.expect, "runtime_root": str(RUNTIME_ROOT), "scratch": str(SCRATCH),
          "stripped_env_names": STRIPPED,
          "claude_version": subprocess.run([str(CLAUDE_BIN), "--version"], env=env, capture_output=True, text=True).stdout.strip(),
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
    watcher = subprocess.Popen([sys.executable, "-I", str(HELPERS / "console_watch.py"), str(launcher.pid), str(RUN),
                                str(CONSOLE_DRIVER_DIR), "claude"], env=env, creationflags=subprocess.DETACHED_PROCESS,
                               stdout=(RUN / "watcher-out.txt").open("w"), stderr=subprocess.STDOUT)
    owned.append(watcher)
    event("launched", pid=launcher.pid, watcher=watcher.pid)
    wait(lambda: "error" not in http("GET", f"http://127.0.0.1:{ROUTER}/health", timeout=2), 90, "owned router health")
    wait(lambda: "claude_session_socket" in router_logs(), 120, "native launch")
    wait(lambda: any(p.name().lower() == "claude.exe" for p in descendants()), 60, "native process present")
    time.sleep(12)
    (RUN / "input.txt").write_text(f"{BASELINE} owned baseline turn", encoding="utf-8")
    wait(lambda: not (RUN / "input.txt").exists(), 20, "watcher typed baseline")
    wait(lambda: any(q["high"] for q in REQS), 90, "high-usage turn reached loopback")
    wait(lambda: HIGH_REPLY in screen(), 40, "high-usage reply rendered")
    shot("01-gate-default-on")
    request_count = len(REQS)
    for value in ("off", "status", "on"):
        (RUN / "input.txt").write_text("/sse-gate " + value, encoding="utf-8")
        wait(lambda: not (RUN / "input.txt").exists(), 20, "typed gate " + value)
        mode = "off" if value == "status" else value
        wait(lambda: "❯ /sse-gate " + value in screen() and "SSE gate " + mode + ";" in screen().rsplit("❯ /sse-gate " + value, 1)[-1], 50, "local gate response " + mode)
        wait(lambda: "SSE gate " + mode + ":" in screen(), 20, "live statusline mode " + mode)
        time.sleep(2)
        shot("gate-" + value)
        config = json.loads((CFG / "config.json").read_text(encoding="utf-8"))
        assert config.get("sse_gate_enabled") is (mode == "on"), config.get("sse_gate_enabled")
        assert len(REQS) == request_count, "slash forwarded upstream"
        event("gate_command_verified", command=value, enabled=config.get("sse_gate_enabled"), upstream_requests=len(REQS))
    result["status"] = "gate_slash_local_verified"
    result["local_only_request_count"] = request_count

except Exception:
    result["status"] = "failed"
    result["error"] = traceback.format_exc()
    event("failure", error=result["error"])
finally:
    HALT.set()
    stop_sampling.set()
    (RUN / "stop").write_text("1")
    time.sleep(1)
    result["egress_denied"] = EGRESS
    result["requests"] = REQS
    result["foreground"] = dict(foreground)
    (RUN / 'compact-events.txt').write_text(chr(10).join(compact_lines()), encoding='utf-8')
    victims = descendants()
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
    result["live_unchanged"] = all(result["live_after"][k].get("pid") == BEFORE[k].get("pid") for k in BEFORE)
    result["source_digests"] = {x: hashlib.sha256((RUNTIME_ROOT / x).read_bytes()).hexdigest() for x in (
        "ciel_runtime.py", "ciel_runtime_support/runtime_compaction_control.py", "ciel_runtime_support/runtime_compaction_binding.py",
        "ciel_runtime_support/streaming_anthropic.py", "ciel_runtime_support/claude_router.py")}
    (RUN / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    stub.shutdown()
    stub.server_close()
    sentinel.shutdown()
    sentinel.server_close()
    print("RESULT", RUN, result["status"], flush=True)
raise SystemExit(0 if result["status"] in ("gate_slash_local_verified",) else 1)
