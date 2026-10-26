"""Owned native Claude SSE acceptance with damaged synthetic history and loopback model fixture.

usage: native_sse_acceptance.py claude [--probe-timeout SECONDS] [--label NAME]

Everything this script starts is owned by it: a fresh scratch tree (HOME, CLAUDE_CONFIG_DIR, CODEX_HOME,
TEMP, ciel config dir, workspace), its own router/app-server/fixture ports, explicit native binary paths,
and a model fixture on 127.0.0.1.  Inherited CIEL_RUNTIME_*/provider variables are removed from this
process before anything else runs and again from the child environment.  No live session, real inbox or
remote host is touched.  Teardown kills only processes whose command line or environment carries this
run's scratch path, plus the launcher tree it created.
"""
import os
import sys

PURGE = ("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC_", "CODEX_", "OPENAI_", "OPENROUTER_", "GEMINI_", "GOOGLE_", "AZURE_",
         "MOONSHOT_", "KIMI_", "XAI_", "GROK_", "ZAI_", "DEEPSEEK_", "NVIDIA_", "OLLAMA_", "FIREWORKS_", "OPENCODE_")
for _name in list(os.environ):
    if _name.upper().startswith(PURGE) or any(word in _name.upper() for word in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "CREDENTIAL")):
        del os.environ[_name]

import argparse  # noqa: E402
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
from http.server import ThreadingHTTPServer  # noqa: E402
from pathlib import Path  # noqa: E402

import psutil  # noqa: E402

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[6]
HELPERS = REPO / "docs/journal/2026/10/20/diagnostics/restart-delivery/evidence"
sys.path.insert(0, str(HELPERS))
import fixture  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("mode", choices=("claude", "codex-remote"))
parser.add_argument("--probe-timeout", type=float, default=None)
parser.add_argument("--label", default="")
parser.add_argument("--runtime-root", default="", help="Installed snapshot to verify (fixtures remain in repository)")
parser.add_argument("--slow", type=float, default=60.0)
parser.add_argument("--typed-post-probe", action="store_true", help="Additional raw console input probe (not needed for restart delivery acceptance)")
ARGS = parser.parse_args()
MODE = ARGS.mode

CLAUDE_BIN = Path("C:/Users/djlov/AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe")
CODEX_BIN = Path("C:/Users/djlov/AppData/Roaming/npm/node_modules/@openai/codex/node_modules/@openai/codex-win32-x64/"
                 "vendor/x86_64-pc-windows-msvc/bin/codex.exe")
NATIVE_NAME = "claude.exe" if MODE == "claude" else "codex.exe"
CONSOLE_DRIVER_DIR = REPO / "docs/journal/2026/10/16/features/cielairouter-direct-path/evidence/e2e"
for binary in (CLAUDE_BIN, CODEX_BIN, CONSOLE_DRIVER_DIR / "console_driver.py"):
    if not binary.exists():
        raise SystemExit(f"missing {binary}")

RUN = HERE / ("run-" + MODE + (("-" + ARGS.label) if ARGS.label else "") + "-" + time.strftime("%H%M%S"))
RUN.mkdir()
SCRATCH = Path(tempfile.mkdtemp(prefix="ciel-owned-restart-", dir="C:/Windows/Temp"))
CFG, WS, HOME, CODEX_HOME, TMP = (SCRATCH / n for n in ("cfg", "ws", "home", "codex", "tmp"))
CLAUDE_DIR = HOME / ".claude"
for d in (CFG, WS, HOME, CODEX_HOME, TMP, CLAUDE_DIR):
    d.mkdir(parents=True, exist_ok=True)
(CFG / "log-level").write_text("DEBUG")
(WS / "README.txt").write_text("Owned local fixture workspace")


def free_ports(count):
    held, ports = [], []
    while len(ports) < count:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        if s.getsockname()[1] not in ports:
            ports.append(s.getsockname()[1])
        held.append(s)
    for s in held:
        s.close()
    return ports


ROUTER, APP = free_ports(2)
fixture.RUN = RUN
fixture.SLOW = ARGS.slow
sse_release = threading.Event()
sse_sent = threading.Event()
sse_release_repeat = threading.Event()
sse_repeat_sent = threading.Event()
class SseModelStub(fixture.Stub):
    def do_GET(self):
        if self.path.split("?")[0] != "/events":
            return super().do_GET()
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        if sse_release.wait(120):
            row = {"specversion": "1.0", "id": "owned-native-sse", "source": "urn:owned:sse", "type": "owned.message",
                   "data": {"text": "E2E-RT-TURN-4 synthetic native SSE"}}
            frame = "id: owned-native-sse" + chr(10) + "data: " + json.dumps(row) + chr(10) * 2
            self.wfile.write(frame.encode("utf-8"))
            self.wfile.flush()
            sse_sent.set()
            if sse_release_repeat.wait(120):
                row = {"specversion": "1.0", "id": "owned-native-sse-repeat", "source": "urn:owned:sse",
                       "type": "owned.message", "data": {"text": "E2E-RT-TURN-3 synthetic repeat scan"}}
                frame = "id: owned-native-sse-repeat" + chr(10) + "data: " + json.dumps(row) + chr(10) * 2
                self.wfile.write(frame.encode("utf-8"))
                self.wfile.flush()
                sse_repeat_sent.set()
            # Keep this exact owned SSE connection open; no reconnect flood.
            while not (RUN / "stop").exists():
                time.sleep(0.1)
stub = ThreadingHTTPServer(("127.0.0.1", 0), SseModelStub)
threading.Thread(target=stub.serve_forever, daemon=True).start()
FIXTURE_PORT = stub.server_port
assert len({ROUTER, APP, FIXTURE_PORT}) == 3

(CFG / "config.json").write_text(json.dumps({
    "current_provider": "vllm", "language": "en",
    "providers": {"vllm": {"base_url": f"http://127.0.0.1:{FIXTURE_PORT}", "current_model": "stub-model",
                           "api_key": "owned-dummy", "context_window": 128000, "max_output_tokens": 4096}},
}))
# Claude reads <CLAUDE_CONFIG_DIR>/.claude.json when CLAUDE_CONFIG_DIR is set (not HOME/.claude.json).
(CLAUDE_DIR / ".claude.json").write_text(json.dumps({
    "hasCompletedOnboarding": True, "theme": "dark",
    "projects": {str(WS): {"hasTrustDialogAccepted": True}, str(WS).replace("\\", "/"): {"hasTrustDialogAccepted": True}}}))

(CODEX_HOME / "config.toml").write_text(
    f'[projects."{str(WS).replace(chr(92), chr(92) * 2)}"]\ntrust_level = "trusted"\n[analytics]\nenabled = false\n', encoding="utf-8")

env = dict(os.environ)
if ARGS.runtime_root:
    runtime_root = Path(ARGS.runtime_root).resolve()
    assert (runtime_root / "ciel_runtime.py").is_file(), runtime_root
    env["OWNED_RESTART_RUNTIME_ROOT"] = str(runtime_root)
for name in list(env):
    if name.upper().startswith(PURGE) or any(word in name.upper() for word in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "CREDENTIAL")):
        del env[name]
env.update(
    HOME=str(HOME), USERPROFILE=str(HOME), TEMP=str(TMP), TMP=str(TMP),
    CLAUDE_CONFIG_DIR=str(CLAUDE_DIR), CODEX_HOME=str(CODEX_HOME),
    CIEL_RUNTIME_CONFIG_DIR=str(CFG), CIEL_RUNTIME_TEST_ISOLATED="1", CIEL_RUNTIME_ROUTER_PORT=str(ROUTER),
    CIEL_RUNTIME_SESSION_RESTART_TURN_WAIT_SECONDS="2",
    DISABLE_AUTOUPDATER="1", DISABLE_TELEMETRY="1", DISABLE_ERROR_REPORTING="1",
    HTTP_PROXY="http://127.0.0.1:1", HTTPS_PROXY="http://127.0.0.1:1", ALL_PROXY="http://127.0.0.1:1",
    NO_PROXY="127.0.0.1,localhost")
if MODE == "codex-remote":
    env["CIEL_RUNTIME_CODEX_APP_SERVER_LISTEN"] = f"ws://127.0.0.1:{APP}"
env["PATH"] = str(CLAUDE_BIN.parent) + ";" + str(CODEX_BIN.parent) + ";" + env.get("PATH", "")
resolved = {"claude": shutil.which("claude", path=env["PATH"]), "codex": shutil.which("codex", path=env["PATH"])}
versions = {str(p): subprocess.run([str(p), "--version"], env=env, capture_output=True, text=True).stdout.strip()
            for p in (CLAUDE_BIN, CODEX_BIN)}

cmd = [sys.executable, "-I", str(HELPERS / "launch_shim.py")]
if ARGS.probe_timeout is not None:
    cmd += ["--listener-probe-timeout", str(ARGS.probe_timeout)]
cmd += ["--", "cli", "--ca-runtime", MODE, "--ca-no-update-check", "--ca-no-self-update-check", "--ca-no-web-search"]
if MODE == "claude":
    cmd += ["--", "--debug-file", str(RUN / "native-debug.log")]

result = {"mode": MODE, "label": ARGS.label, "scratch": str(SCRATCH), "versions": versions, "path_resolution": resolved,
          "command": cmd, "probe_timeout_shim": ARGS.probe_timeout,
          "ports": {"router": ROUTER, "appserver": APP if MODE == "codex-remote" else None, "fixture": FIXTURE_PORT},
          "events": []}
owned = []
lock = threading.Lock()


def event(kind, **data):
    row = {"at": round(time.time(), 3), "kind": kind, **data}
    with lock:
        result["events"].append(row)
        (RUN / "progress.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(row, ensure_ascii=False)[:600], flush=True)


def http(method, path, body=None, timeout=5):
    req = urllib.request.Request(f"http://127.0.0.1:{ROUTER}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def router_logs():
    parts = []
    for p in sorted(CFG.rglob("*.log")):
        try:
            parts.append(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    return "\n".join(parts)


def wait(fn, secs, label):
    end = time.time() + secs
    while time.time() < end:
        if fn():
            event("wait_pass", label=label)
            return
        if launcher is not None and launcher.poll() is not None:
            raise RuntimeError(f"launcher exited code={launcher.returncode} while waiting for {label}")
        time.sleep(0.4)
    raise RuntimeError("timeout " + label)


def notify(text):
    event("notify", text=text, response=http("POST", "/ca/chat/notify", {
        "message": text, "channel": "owned-e2e", "sender_id": "owned-driver",
        "input_transport": "session_socket" if MODE == "claude" else "router"}))


def typed(text):
    event("typed", text=text)
    (RUN / "input.txt").write_text(text, encoding="utf-8")
    wait(lambda: not (RUN / "input.txt").exists(), 20, "watcher consumed typed input")


def descendants():
    if launcher is None:
        return []
    try:
        return psutil.Process(launcher.pid).children(recursive=True)
    except psutil.Error:
        return []


def describe(p):
    try:
        return {"pid": p.pid, "ct": round(p.create_time(), 3), "name": p.name(), "ppid": p.ppid(),
                "cmd": " ".join(p.cmdline())[:260]}
    except psutil.Error:
        return {"pid": p.pid}


def listener_pid(port):
    try:
        for c in psutil.net_connections("tcp"):
            if c.status == "LISTEN" and c.laddr.port == port:
                return c.pid
    except psutil.Error:
        return -1
    return None


def alive(identity):
    pid, ct = identity
    try:
        return psutil.pid_exists(pid) and abs(psutil.Process(pid).create_time() - ct) < 0.01
    except psutil.Error:
        return False


timeline, stop_sampling = [], threading.Event()


def sampler():
    last = {}
    while not stop_sampling.is_set():
        try:
            snap = {"app": listener_pid(APP) if MODE == "codex-remote" else None, "router": listener_pid(ROUTER),
                    "procs": sorted((p.pid, p.name()) for p in descendants())}
        except psutil.Error:
            time.sleep(0.25)
            continue
        if snap != last:
            row = {"at": round(time.time(), 3), **{k: v for k, v in snap.items() if last.get(k) != v}}
            timeline.append(row)
            with (RUN / "process-listener-timeline.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            last = snap
        time.sleep(0.25)


def scrub(text):
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{12,}", r"\1<redacted>", text)
    return re.sub(r"(?i)((?:token|api[_-]?key|authorization)[\"'=: ]+)[A-Za-z0-9._~+/=-]{16,}", r"\1<redacted>", text)


def screen():
    p = RUN / "screen-latest.txt"
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def reached(marker):
    return any(marker in q["last"] for q in fixture.REQS)


def natives():
    result = []
    for process in descendants():
        try:
            if process.name().lower() == NATIVE_NAME:
                result.append(process)
        except psutil.NoSuchProcess:
            pass
    return result


launcher = watcher = None
t_restart = None
try:
    launcher = subprocess.Popen(cmd, env=env, cwd=WS, creationflags=subprocess.CREATE_NEW_CONSOLE)
    owned.append(launcher)
    threading.Thread(target=sampler, daemon=True).start()
    watcher = subprocess.Popen(
        [sys.executable, "-I", str(HELPERS / "console_watch.py"), str(launcher.pid), str(RUN), str(CONSOLE_DRIVER_DIR), MODE],
        env=env, creationflags=subprocess.DETACHED_PROCESS, stdout=(RUN / "watcher-out.txt").open("w"), stderr=subprocess.STDOUT)
    owned.append(watcher)
    event("launched", pid=launcher.pid, watcher=watcher.pid)
    wait(lambda: "error" not in http("GET", "/health", timeout=2), 90, "router health")
    marker = "claude_session_socket" if MODE == "claude" else "codex_remote_tui_start"
    wait(lambda: marker in router_logs(), 120, "native launch " + marker)
    wait(lambda: bool(natives()), 60, "native process present")
    time.sleep(12)
    event("children_before", processes=[describe(p) for p in descendants()])
    assert MODE == "claude", "native SSE regression uses Claude session socket only"
    history = CFG / "runtime-input-status.jsonl"
    queue_path = CFG / "runtime-inputs.jsonl"
    old_states = {1: "submitted", 2: "replied", 3: "interrupted_uncertain",
                  4: "submitted", 5: "replied", 6: "interrupted_uncertain"}
    prefix = b"{malformed" + bytes([10])
    for i, state in old_states.items():
        row = {"request_id": i, "status": state, "updated_at": "2026-10-20T00:00:00+00:00",
               "updated_at_epoch": 1792454400.0, "data": {"attempt_id": f"old-{i}", "child_epoch": "old-child"}}
        prefix += (bytes(19) if i > 3 else b"") + json.dumps(row).encode() + bytes([10])
    prefix += b"{broken" + bytes([0, 10, 255, 10]) + b"{" + b'"unfinished"' + b":"
    history.write_bytes(prefix)
    # Queue seed is published only after status owners exist. All contents are synthetic.
    # Shaped like real admitted external events and current, so only durable
    # status (not scope/replay-age filters) can stop them.
    now = time.time()
    old_queue = [{"id": i, "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)), "created_at_epoch": now,
                  "channel": "external:default", "sender_id": "urn:owned:old", "recipients": ["all"],
                  "thread_id": f"owned-old-{i}", "parent_id": None, "kind": "external_event",
                  "message": f"synthetic old input {i}", "delivery": ["llm"], "visibility": "private_runtime",
                  "meta": {"source": "ciel-runtime-external-event", "source_kind": "external_event",
                           "receiver_id": "default", "transport": "sse", "event_id": f"owned-old-{i}",
                           "event_type": "owned.message", "event_source": "urn:owned:old",
                           "transport_event_id": f"owned-old-{i}", "input_transport": "session_socket",
                           "raw_preserved": True}} for i in old_states]
    temporary = CFG / "queue-seed.tmp"
    temporary.write_text("".join(json.dumps(row) + chr(10) for row in old_queue), encoding="utf-8")
    temporary.replace(queue_path)
    receiver_response = http("POST", "/ca/events/receivers/default", {
        "enabled": True, "transport": "sse", "input_transport": "session_socket",
        "url": f"http://127.0.0.1:{FIXTURE_PORT}/events"})
    assert receiver_response.get("ok"), receiver_response
    event("receiver_enabled", receiver_id="default", transport="sse")
    sse_release.set()
    wait(sse_sent.is_set, 30, "local SSE event emitted")
    def status_rows():
        data = history.read_bytes()
        assert data.startswith(prefix), "old bytes rewritten"
        return [json.loads(line) for line in data[len(prefix):].splitlines() if line.strip()]
    wait(lambda: any(r.get("request_id") == 7 and r.get("status") == "queued" for r in status_rows()),
         30, "real receiver gateway queued id 7 over damaged history")
    wait(lambda: reached("E2E-RT-TURN-4"), 60, "native CLI received new SSE input")
    wait(lambda: any(r.get("request_id") == 7 and r.get("status") == "replied" for r in status_rows()),
         60, "native transcript confirmed replied id 7")
    wait(lambda: "STUB-REPLY" in screen(), 40, "native reply rendered")
    time.sleep(5)
    rows = status_rows()
    new_states = [r["status"] for r in rows if r.get("request_id") == 7]
    assert new_states == ["queued", "submitted", "replied"], new_states
    assert all(r.get("request_id") == 7 for r in rows), "old state was changed"
    logs = router_logs()
    sends = [line for line in logs.splitlines() if "channel_stdin_proxy_injected " in line and "message_ids=7 " in line]
    assert len(sends) == 1, sends
    assert not any("channel_stdin_proxy_injected " in line and "message_ids=" + str(i) + " " in line
                   for i in old_states for line in logs.splitlines()), "old input sent"
    assert len([q for q in fixture.REQS if "E2E-RT-TURN-4" in q["last"]]) == 1
    for i in (1, 4):
        assert f"message_id={i} reason=ownership_submitted action=no_replay" in logs, i
    for i in (2, 5):
        assert f"message_id={i} reason=durable_replied" in logs, i
    for i in (3, 6):
        assert f"message_id={i} reason=interrupted_uncertain action=no_replay" in logs, i
    # Repeat scan: a later SSE input forces a rescan from the held frontier.
    log_mark = len(logs.splitlines())
    sse_release_repeat.set()
    wait(sse_repeat_sent.is_set, 30, "local repeat SSE event emitted")
    wait(lambda: reached("E2E-RT-TURN-3"), 60, "native CLI received repeat-scan input")
    wait(lambda: any(r.get("request_id") == 8 and r.get("status") == "replied" for r in status_rows()),
         60, "native transcript confirmed replied id 8")
    time.sleep(5)
    rows = status_rows()
    repeat_logs = router_logs().splitlines()[log_mark:]
    repeat_sends = [line for line in repeat_logs if "channel_stdin_proxy_injected " in line]
    assert len(repeat_sends) == 1 and "message_ids=8 " in repeat_sends[0], repeat_sends
    assert any("message_id=7 reason=durable_replied" in line for line in repeat_logs), "repeat scan skipped no 7"
    assert [r["status"] for r in rows if r.get("request_id") == 7] == ["queued", "submitted", "replied"]
    assert [r["status"] for r in rows if r.get("request_id") == 8] == ["queued", "submitted", "replied"]
    assert all(r.get("request_id") in (7, 8) for r in rows), "old state was changed"
    assert len([q for q in fixture.REQS if "E2E-RT-TURN-4" in q["last"]]) == 1
    assert len([q for q in fixture.REQS if "E2E-RT-TURN-3" in q["last"]]) == 1
    assert not any("synthetic old input" in str(q) for q in fixture.REQS)
    assert "STUB-REPLY" in screen()
    assert launcher.poll() is None
    assert not list(CFG.rglob("*archive*")) and not list(CFG.rglob("*quarantine*"))
    (RUN / "shot-request").write_text("native SSE over damaged history replied once", encoding="utf-8")
    wait(lambda: not (RUN / "shot-request").exists(), 20, "owned decisive screenshot attempted")
    import hashlib
    event("acceptance_pass", checks={"real_native_cli": True, "sse_gateway_queued": True,
          "native_transcript_replied": True, "socket_send_count": len(sends), "new_statuses": new_states,
          "old_history_bytes_unchanged": history.read_bytes().startswith(prefix),
          "old_prefix_sha256": hashlib.sha256(prefix).hexdigest(), "old_states": old_states,
          "old_inputs_sent": 0, "repeat_scan_sends": [line.split("message_ids=")[1].split()[0] for line in repeat_sends],
          "repeat_scan_resent_7": False,
          "archive_or_quarantine_files": 0})
    result["status"] = "passed_native_sse_damaged_history"

except Exception:
    result["status"] = "failed"
    result["error"] = traceback.format_exc()
    event("failure", error=result["error"])
finally:
    stop_sampling.set()
    (RUN / "stop").write_text("1")
    time.sleep(1)
    result["requests"] = [{k: q.get(k) for k in ("n", "at", "path", "users", "last", "history", "tools", "replied_at")}
                          for q in fixture.REQS]
    result["restart_requested_at"] = t_restart
    if t_restart:
        post = [q for q in fixture.REQS if q["at"] > t_restart]
        result["post_restart_requests"] = [{"n": q["n"], "last": q["last"][:120], "history": q["history"]} for q in post]
        result["queued_old_as_last_message_after_restart"] = any("E2E-QUEUED-OLD" in q["last"] for q in post)
    (RUN / "router-combined.log").write_text(scrub(router_logs()), encoding="utf-8")
    for p in sorted(CFG.rglob("*")):
        if p.is_file() and p.suffix in (".json", ".jsonl") and p.name != "config.json" and "token" not in p.name.lower():
            dst = RUN / "state" / p.relative_to(CFG)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(scrub(p.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
    for p in CLAUDE_DIR.rglob("*.jsonl"):
        dst = RUN / "claude-transcripts" / p.name
        dst.parent.mkdir(exist_ok=True)
        rows = []
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            message = row.get("message") or {}
            rows.append({**{k: row.get(k) for k in ("type", "uuid", "parentUuid", "sessionId", "timestamp") if k in row},
                         "role": message.get("role"),
                         "marker_observations": [x for x in ("E2E-RT-TURN-1", "E2E-RT-SLOWTURN-2", "E2E-RT-TURN-3",
                                                              "E2E-RT-TURN-4", "E2E-QUEUED-OLD", "STUB-REPLY") if x in line]})
        dst.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    # Ownership-verified teardown: only this run's launcher tree and processes carrying its scratch path.
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
    if launcher is not None:
        victims += [p for p in descendants() if p not in victims]
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
    result["teardown"] = {"killed": [describe(p) for p in victims],
                          "router_listener_after": listener_pid(ROUTER),
                          "app_listener_after": listener_pid(APP) if MODE == "codex-remote" else None}
    result["timeline"] = timeline
    (RUN / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    stub.shutdown()
    stub.server_close()
    print("RESULT", RUN, result["status"], flush=True)
raise SystemExit(0 if result["status"] == "passed_native_sse_damaged_history" else 1)
