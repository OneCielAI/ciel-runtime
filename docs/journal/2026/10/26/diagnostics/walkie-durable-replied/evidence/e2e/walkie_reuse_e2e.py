"""Owned real-CLI E2E for the Walkie id-reuse skip (2026-10-26).

usage: python -I walkie_reuse_e2e.py --runtime-root <snapshot or worktree> --label <name>

The private runtime-input queue is seeded with the shape Walkie had after the unclean reboot: one
readable row (id 1), then a NUL run with CRLF where later rows were lost. The status history is
seeded with ids 1-4 as replied (fsynced, so it survived), and the cursor with last_id 4. Then the
real launcher, router and claude.exe start against a loopback model fixture, and notifications are
posted to /ca/chat/notify one at a time until one is injected. Old code is expected to reissue ids
2-4 and skip them as durable_replied; fixed code is expected to issue id 5 and inject it.

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
import re  # noqa: E402
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
parser.add_argument("--max-notifications", type=int, default=5)
ARGS = parser.parse_args()
RUNTIME_ROOT = Path(ARGS.runtime_root).resolve()
assert (RUNTIME_ROOT / "ciel_runtime.py").is_file(), RUNTIME_ROOT
for required in (CLAUDE_BIN, CONSOLE_DRIVER_DIR / "console_driver.py"):
    if not required.exists():
        raise SystemExit(f"missing {required}")

RUN = HERE / ("run-" + ARGS.label + "-" + time.strftime("%H%M%S"))
RUN.mkdir()
SCRATCH = Path(tempfile.mkdtemp(prefix="ciel-owned-walkie-", dir="C:/Windows/Temp"))
CFG, WS, HOME, TMP = (SCRATCH / n for n in ("cfg", "ws", "home", "tmp"))
CLAUDE_DIR = HOME / ".claude"
for d in (CFG, WS, HOME, TMP, CLAUDE_DIR):
    d.mkdir(parents=True, exist_ok=True)
(CFG / "log-level").write_text("DEBUG")
(WS / "README.txt").write_text("Owned local fixture workspace")

# No marker is a substring of another.
MARKERS = ("WALKIE-BASELINE-TURN", "WALKIE-NOTE-ONE", "WALKIE-NOTE-TWO", "WALKIE-NOTE-THREE", "WALKIE-NOTE-FOUR",
           "WALKIE-NOTE-FIVE", "WALKIE-PRECRASH-ROW")
REQS = []


def _text(m):
    c = m.get("content")
    return c if isinstance(c, str) else " ".join(str(x.get("text", "")) for x in (c or []) if isinstance(x, dict))


class Stub(BaseHTTPRequestHandler):
    """Loopback model: records which markers each request carries and replies STUB-REPLY-<n>."""

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
        self._json({"object": "list", "data": [{"id": "stub-model", "object": "model", "owned_by": "stub", "max_model_len": 128000}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        users = [_text(m) for m in body.get("messages") or [] if m.get("role") == "user"]
        last = users[-1] if users else ""
        n = len(REQS) + 1
        record = {"n": n, "at": time.time(), "path": self.path, "users": len(users),
                  "last": [w for w in MARKERS if w in last], "history": [w for w in MARKERS if any(w in u for u in users)]}
        REQS.append(record)
        reply = f"STUB-REPLY-{n}"
        base = {"id": f"c{n}", "object": "chat.completion.chunk", "created": int(time.time()), "model": "stub-model"}
        if self.path.split("?")[0].endswith("/v1/messages"):
            # Anthropic Messages shape, as in restart-delivery/evidence/fixture.py.
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
                           {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                            "usage": {"output_tokens": 2}},
                           {"type": "message_stop"}):
                    self.wfile.write(f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n".encode())
                    self.wfile.flush()
        elif body.get("stream"):
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
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


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


ROUTER = free_port()
stub = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
threading.Thread(target=stub.serve_forever, daemon=True).start()
FIXTURE_PORT = stub.server_port
assert ROUTER != FIXTURE_PORT

(CFG / "config.json").write_text(json.dumps({
    "current_provider": "vllm", "language": "en",
    "providers": {"vllm": {"base_url": f"http://127.0.0.1:{FIXTURE_PORT}", "current_model": "stub-model",
                           "api_key": "owned-dummy", "context_window": 128000, "max_output_tokens": 4096}},
}))
(CLAUDE_DIR / ".claude.json").write_text(json.dumps({
    "hasCompletedOnboarding": True, "theme": "dark",
    "projects": {str(WS): {"hasTrustDialogAccepted": True}, str(WS).replace("\\", "/"): {"hasTrustDialogAccepted": True}}}))

# Crash shape, seeded before launch (CIEL_RUNTIME_TEST_ISOLATED keeps workspace state directly in CFG).
QUEUE, HISTORY, CURSOR = CFG / "runtime-inputs.jsonl", CFG / "runtime-input-status.jsonl", CFG / "channel-llm-cursor.json"
OLD = time.time() - 4 * 3600


def iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(epoch))


precrash = {"id": 1, "time": iso(OLD), "created_at_epoch": OLD, "channel": "owned-e2e", "sender_id": "owned-driver",
            "recipients": ["all"], "thread_id": "1", "parent_id": None,
            "message": "WALKIE-PRECRASH-ROW delivered before the crash", "kind": "notification",
            "meta": {"source": "ciel-runtime-notify", "source_kind": "notification", "input_transport": "session_socket"},
            "delivery": ["llm"], "visibility": "private_runtime"}
# Rows 2-4 were lost: in their place a NUL run followed by CRLF, as at Walkie queue line 2681.
QUEUE.write_bytes(json.dumps(precrash).encode() + b"\r\n" + b"\x00" * 4096 + b"\r\n")
status_rows = []
for request_id in (1, 2, 3, 4):
    at = OLD + request_id * 60
    for offset, state, data in ((0, "queued", {"channel": "owned-e2e", "input_transport": "session_socket"}),
                                (1, "submitted", {"child_epoch": "precrash", "attempt_id": f"precrash-{request_id}"}),
                                (10, "replied", {"child_epoch": "precrash", "attempt_id": f"precrash-{request_id}"})):
        status_rows.append({"request_id": request_id, "status": state, "updated_at": iso(at + offset),
                            "updated_at_epoch": at + offset, "data": data})
HISTORY.write_bytes(b"".join(json.dumps(r, separators=(",", ":")).encode() + b"\n" for r in status_rows))
CURSOR.write_text(json.dumps({"last_id": 4}) + "\n", encoding="utf-8")
SEED = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (QUEUE, HISTORY, CURSOR)}

env = dict(os.environ)
env["OWNED_RESTART_RUNTIME_ROOT"] = str(RUNTIME_ROOT)
env.update(
    HOME=str(HOME), USERPROFILE=str(HOME), TEMP=str(TMP), TMP=str(TMP), CLAUDE_CONFIG_DIR=str(CLAUDE_DIR),
    CIEL_RUNTIME_CONFIG_DIR=str(CFG), CIEL_RUNTIME_TEST_ISOLATED="1", CIEL_RUNTIME_ROUTER_PORT=str(ROUTER),
    DISABLE_AUTOUPDATER="1", DISABLE_TELEMETRY="1", DISABLE_ERROR_REPORTING="1",
    HTTP_PROXY="http://127.0.0.1:1", HTTPS_PROXY="http://127.0.0.1:1", ALL_PROXY="http://127.0.0.1:1",
    NO_PROXY="127.0.0.1,localhost")
env["PATH"] = str(CLAUDE_BIN.parent) + ";" + env.get("PATH", "")

cmd = [sys.executable, "-I", str(HERE / "launch_shim.py"), "--", "cli", "--ca-runtime", "claude",
       "--ca-no-update-check", "--ca-no-self-update-check", "--ca-no-web-search",
       "--", "--permission-mode", "default", "--tools", "", "--debug-file", str(RUN / "native-debug.log")]
result = {"label": ARGS.label, "runtime_root": str(RUNTIME_ROOT), "scratch": str(SCRATCH), "stripped_env_names": STRIPPED,
          "claude_version": subprocess.run([str(CLAUDE_BIN), "--version"], env=env, capture_output=True, text=True).stdout.strip(),
          "command": cmd, "ports": {"router": ROUTER, "fixture": FIXTURE_PORT}, "seed_sha256": SEED, "events": []}
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


def reached(marker):
    return any(marker in q["last"] for q in REQS)


def statuses():
    rows = []
    for raw in HISTORY.read_bytes().splitlines():
        try:
            rows.append(json.loads(raw))
        except ValueError:
            pass
    return rows


def shot(label):
    (RUN / ("screen-" + label + ".txt")).write_text(screen(), encoding="utf-8")
    (RUN / "shot-request").write_text(label, encoding="utf-8")
    wait(lambda: not (RUN / "shot-request").exists(), 30, "screenshot " + label)


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


def outcome_for(request_id):
    logs = router_logs()
    for line in logs.splitlines():
        if f"channel_stdin_proxy_skipped_noise message_id={request_id} " in line + " ":
            return {"outcome": "skipped", "line": line.strip()}
        if "channel_stdin_proxy_blocked " in line and f"message_id={request_id} " in line + " ":
            return {"outcome": "blocked", "line": line.strip()}
        match = re.search(r"channel_stdin_proxy_injected .*?message_ids=([0-9,]+)", line)
        if match and str(request_id) in match.group(1).split(","):
            return {"outcome": "injected", "line": line.strip()}
    return None


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
                                str(CONSOLE_DRIVER_DIR), "claude"], env=env, creationflags=subprocess.DETACHED_PROCESS,
                               stdout=(RUN / "watcher-out.txt").open("w"), stderr=subprocess.STDOUT)
    owned.append(watcher)
    event("launched", pid=launcher.pid, watcher=watcher.pid)
    wait(lambda: "error" not in http("GET", f"http://127.0.0.1:{ROUTER}/health", timeout=2), 90, "owned router health")
    wait(lambda: "claude_session_socket" in router_logs(), 120, "native launch")
    wait(lambda: any(p.name().lower() == "claude.exe" for p in descendants()), 60, "native process present")
    time.sleep(12)
    (RUN / "input.txt").write_text("WALKIE-BASELINE-TURN owned baseline", encoding="utf-8")
    wait(lambda: not (RUN / "input.txt").exists(), 20, "watcher typed baseline")
    wait(lambda: reached("WALKIE-BASELINE-TURN"), 90, "baseline reached loopback")
    wait(lambda: "STUB-REPLY" in screen(), 40, "baseline rendered")
    time.sleep(2)
    shot("01-baseline")

    notifications = []
    injected = None
    for marker in MARKERS[1:1 + ARGS.max_notifications]:
        response = http("POST", f"http://127.0.0.1:{ROUTER}/ca/chat/notify", {
            "message": f"{marker} post-crash notification", "channel": "owned-e2e", "sender_id": "owned-driver",
            "input_transport": "session_socket"})
        request_id = (response.get("message") or {}).get("id")
        event("notify", marker=marker, request_id=request_id, error=response.get("error"))
        if not isinstance(request_id, int):
            raise RuntimeError(f"notify returned no id: {response}")
        outcome = wait(lambda: outcome_for(request_id), 60, f"injector decision for {request_id}")
        notifications.append({"marker": marker, "request_id": request_id, **outcome})
        event("decision", marker=marker, request_id=request_id, outcome=outcome["outcome"])
        if outcome["outcome"] == "injected":
            injected = notifications[-1]
            wait(lambda: reached(marker), 90, f"{marker} reached loopback")
            wait(lambda: any(r.get("request_id") == request_id and r.get("status") == "replied" for r in statuses()),
                 90, f"{request_id} replied")
            break
    result["notifications"] = notifications
    time.sleep(4)
    shot("02-after-notifications")
    result["status_rows_by_id"] = {}
    for row in statuses():
        result["status_rows_by_id"].setdefault(str(row.get("request_id")), []).append(
            {k: row.get(k) for k in ("status", "reason", "updated_at")})
    result["markers_at_fixture"] = sorted({m for q in REQS for m in q["last"]})
    logs = router_logs()
    result["log_lines"] = [line.strip() for line in logs.splitlines() if any(token in line for token in (
        "chat_message_id_above_issued_floor", "channel_llm_cursor_queue_generation_reset", "channel_stdin_proxy_skipped_noise",
        "channel_stdin_proxy_injected ", "channel_stdin_proxy_blocked ", "chat_message_issued_floor_unavailable"))]
    result["status"] = "injected" if injected else "never_injected"
except Exception:
    result["status"] = "failed"
    result["error"] = traceback.format_exc()
    event("failure", error=result["error"])
finally:
    stop_sampling.set()
    (RUN / "stop").write_text("1")
    time.sleep(1)
    result["requests"] = REQS
    result["foreground"] = dict(foreground)
    raw = RUN / "state-raw"
    raw.mkdir(exist_ok=True)
    for p in (QUEUE, HISTORY, CURSOR):
        if p.exists():
            shutil.copyfile(p, raw / p.name)
    (RUN / "router-combined.log").write_text(router_logs(), encoding="utf-8")
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
    # The fixed delivery observer (this worktree) applied to the queue and history this run left behind.
    probe = subprocess.run([sys.executable, "-I", "-c", (
        "import json,sys;sys.path.insert(0,sys.argv[1]);"
        "from pathlib import Path;"
        "from ciel_runtime_support.runtime_input_delivery_health import runtime_input_delivery_health,delivery_health_text;"
        "h=runtime_input_delivery_health(status_path=Path(sys.argv[2]),inputs_path=Path(sys.argv[3]));"
        "print(json.dumps({'reused_id_count':h.get('reused_id_count'),'reused_id_newest':h.get('reused_id_newest'),"
        "'text':delivery_health_text(h)}))"), str(WORKTREE), str(raw / HISTORY.name), str(raw / QUEUE.name)],
        capture_output=True, text=True)
    result["fixed_observer_on_run_state"] = probe.stdout.strip() or probe.stderr.strip()
    result["live_after"] = live_health()
    result["live_unchanged"] = all(result["live_after"][k].get("pid") == BEFORE[k].get("pid") for k in BEFORE)
    result["source_digests"] = {x: hashlib.sha256((RUNTIME_ROOT / x).read_bytes()).hexdigest() for x in (
        "ciel_runtime.py", "ciel_runtime_support/channel_message_repository.py", "ciel_runtime_support/runtime_input_status.py",
        "ciel_runtime_support/channel_pending_injection.py")}
    (RUN / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    stub.shutdown()
    stub.server_close()
    print("RESULT", RUN, result["status"], flush=True)
raise SystemExit(0 if result["status"] == "injected" else 1)
