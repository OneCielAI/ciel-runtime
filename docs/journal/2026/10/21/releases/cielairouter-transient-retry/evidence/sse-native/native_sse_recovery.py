"""Owned, isolated native restart E2E (real claude.exe / real codex.exe, loopback model fixture).

usage: native_restart.py <claude|codex-remote> [--probe-timeout SECONDS] [--label NAME]

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
    if _name.upper().startswith(PURGE) or any(w in _name.upper() for w in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "CREDENTIAL")):
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
REPO = Path('C:/Users/djlov/ciel-runtime')
HELPERS = REPO / 'docs/journal/2026/10/20/diagnostics/restart-delivery/evidence'
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
stub = ThreadingHTTPServer(("127.0.0.1", 0), fixture.Stub)
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
    if name.upper().startswith(PURGE) or any(w in name.upper() for w in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "CREDENTIAL")):
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
    cmd += ["--", "--permission-mode", "default", "--tools", "", "--debug-file", str(RUN / "native-debug.log")]

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
                "cmd": p.cmdline()}
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
    return [p for p in descendants() if p.name().lower() == NATIVE_NAME]



launcher = watcher = None
suspended = False
try:
    assert MODE == "claude"
    launcher = subprocess.Popen(cmd, env=env, cwd=WS, creationflags=subprocess.CREATE_NEW_CONSOLE)
    owned.append(launcher)
    threading.Thread(target=sampler, daemon=True).start()
    watcher = subprocess.Popen([sys.executable, "-I", str(HELPERS / "console_watch.py"), str(launcher.pid), str(RUN), str(CONSOLE_DRIVER_DIR), MODE], env=env, creationflags=subprocess.DETACHED_PROCESS, stdout=(RUN / "watcher-out.txt").open("w"), stderr=subprocess.STDOUT)
    owned.append(watcher)
    event("launched", pid=launcher.pid, watcher=watcher.pid)
    wait(lambda: "error" not in http("GET", "/health", timeout=2), 90, "owned router health")
    wait(lambda: "claude_session_socket" in router_logs(), 120, "native launch")
    wait(lambda: bool(natives()), 60, "native process present")
    time.sleep(12)
    typed("E2E-RT-TURN-1 owned baseline")
    wait(lambda: reached("E2E-RT-TURN-1"), 90, "baseline reached loopback")
    wait(lambda: "STUB-REPLY" in screen(), 40, "baseline rendered")
    time.sleep(3)
    before_ids = [(p.pid, p.create_time()) for p in natives()]
    event("native_before", natives=[describe(p) for p in natives()])
    # Suspend only our launcher so queue seeding cannot race injection.
    psutil.Process(launcher.pid).suspend()
    suspended = True
    event("owned_launcher_suspended_for_fixture_seed", pid=launcher.pid)
    history, queue = CFG / "runtime-input-status.jsonl", CFG / "runtime-inputs.jsonl"
    old = time.time() - 400
    assert not queue.exists() or not queue.read_bytes()
    assert not history.exists() or not history.read_bytes()
    message = {"id": 1, "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(old)),
      "created_at_epoch": old, "channel": "external:default", "sender_id": "urn:owned:sse-recovery", "recipients": ["all"], "thread_id": "owned-stale", "parent_id": None, "kind": "external_event",
      "message": "E2E-RT-TURN-4 synthetic stale SSE queue input", "delivery": ["llm"], "visibility": "private_runtime",
      "meta": {"source": "ciel-runtime-external-event", "source_kind": "external_event", "receiver_id": "default", "transport": "sse", "event_id": "owned-stale-sse", "event_type": "owned.message", "event_source": "urn:owned:sse-recovery", "transport_event_id": "owned-stale-sse", "input_transport": "session_socket", "raw_preserved": True}}
    status = {"request_id": 1, "status": "queued", "updated_at_epoch": old,
      "data": {"channel": "external:default", "input_transport": "session_socket"}}
    # Append new synthetic rows only; no cursor edits, truncation or repairs.
    with history.open("ab") as f: f.write(json.dumps(status).encode() + b"\n")
    with queue.open("ab") as f: f.write(json.dumps(message).encode() + b"\n")
    paths = [history, queue, CFG / "channel-llm-cursor.json"]
    untouched = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths if p.exists()}
    def mcp(action):
        response = http("POST", "/ca/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "recover_sse_delivery", "arguments": {"action": action, "reason": "owned native stale SSE recovery acceptance"}}}, timeout=15)
        result.setdefault("mcp", []).append(response)
        assert not response.get("error"), response
        assert not response["result"].get("isError"), response
        return json.loads(response["result"]["content"][0]["text"])
    observation = mcp("status")
    event("status_observed", response=observation)
    assert observation["evidence"]["restart_eligible"], observation
    assert observation["evidence"]["known_queued_count"] == 1
    assert observation["evidence"]["admission_paused"] is False
    reset = mcp("auto")
    event("recovery_auto", response=reset)
    assert reset["reset"] and reset["restart"]["queued"], reset
    assert reset["restart"]["request"]["target_pid"] == launcher.pid
    assert reset["restart"]["request"]["resume"] is True
    request = CFG / "runtime-session-restart.json"
    durable = request.read_bytes()
    second = mcp("auto")
    event("recovery_cooldown", response=second)
    assert second["reason"] == "cooldown_active" and not second["reset"], second
    assert request.read_bytes() == durable
    assert all(p.read_bytes() == raw and p.stat().st_mtime_ns == mt for p, (raw,mt) in untouched.items())
    result["tool_preserved_queue_history_cursor_bytes_and_mtime"] = True

    psutil.Process(launcher.pid).resume()
    suspended = False
    event("owned_launcher_resumed", pid=launcher.pid)
    wait(lambda: "claude_session_restart " in router_logs(), 90, "production restart executed")
    wait(lambda: any((p.pid, p.create_time()) not in before_ids for p in natives()), 90, "new native process")
    after = natives()
    event("native_after", natives=[describe(p) for p in after], old_native_alive=[i for i in before_ids if alive(i)])
    assert not any(alive(i) for i in before_ids)
    assert any("--continue" in p.cmdline() for p in after), [p.cmdline() for p in after]
    assert not request.exists(), "launcher did not consume restart request"
    wait(lambda: reached("E2E-RT-TURN-4"), 90, "stale input reached resumed CLI")
    def states():
        return [json.loads(l) for l in history.read_text(encoding="utf-8").splitlines() if l.strip()]
    wait(lambda: any(r.get("request_id") == 1 and r.get("status") == "replied" for r in states()), 90, "stale input confirmed replied")
    time.sleep(6)
    logs = router_logs()
    injections = [line for line in logs.splitlines() if "channel_stdin_proxy_injected " in line and "message_ids=1 " in line]
    assert len(injections) == 1, injections
    assert len([q for q in fixture.REQS if "E2E-RT-TURN-4" in q["last"]]) == 1
    assert [r["status"] for r in states() if r.get("request_id") == 1] == ["queued", "submitted", "replied"]
    assert history.read_bytes().startswith(untouched[history][0])
    assert queue.read_bytes().startswith(untouched[queue][0])
    (RUN / "shot-request").write_text("native SSE recovery resumed and replied", encoding="utf-8")
    wait(lambda: not (RUN / "shot-request").exists(), 20, "owned console screenshot attempted")
    result["checks"] = {"actual_native": True, "mcp_auto_reset": True, "cooldown_second_call": True,
        "production_restart_claimed": True, "pid_changed": True, "resume_flag": True,
        "restart_request_consumed": True, "original_stale_input_replied_once": True,
        "history_queue_prefix_preserved": True, "tool_preserved_queue_history_cursor": True}
    event("acceptance_pass", checks=result["checks"])
    result["status"] = "passed_native_sse_recovery"
except Exception:
    result["status"] = "failed"
    result["error"] = traceback.format_exc()
    event("failure", error=result["error"])
finally:
    if suspended and launcher is not None:
        try: psutil.Process(launcher.pid).resume()
        except psutil.Error: pass
    stop_sampling.set()
    (RUN / "stop").write_text("1")
    time.sleep(1)
    result["requests"] = [{k: q.get(k) for k in ("n", "at", "path", "users", "last", "history", "replied_at")} for q in fixture.REQS]
    result["limitations"] = ["Local fixture provider, not live infrastructure.", "Queue age synthetic; only owned launcher suspended to prevent setup races. No transport failure inferred.", "MCP and resumed restart use production router/service/launcher without callback replacement."]
    (RUN / "router-combined.log").write_text(scrub(router_logs()), encoding="utf-8")
    for p in sorted(CFG.rglob("*")):
        if p.is_file() and p.suffix in (".json", ".jsonl") and p.name != "config.json" and "token" not in p.name.lower():
            dst = RUN / "state" / p.relative_to(CFG)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(scrub(p.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
    victims = []
    for p in psutil.process_iter():
        if p.pid == os.getpid(): continue
        try: owned_by_run = str(SCRATCH) in " ".join(p.cmdline()) or str(SCRATCH) in str(p.environ().get("CIEL_RUNTIME_CONFIG_DIR", ""))
        except psutil.Error: owned_by_run = False
        if owned_by_run: victims.append(p)
    if launcher is not None:
        victims += [p for p in descendants() if p not in victims]
    result["teardown_owned_identities"] = [describe(p) for p in victims]
    for p in victims:
        try: p.kill()
        except psutil.Error: pass
    for p in owned:
        if p.poll() is None:
            try: p.kill()
            except OSError: pass
    time.sleep(2)
    result["teardown"] = {"router_listener_after": listener_pid(ROUTER)}
    result["timeline"] = timeline
    import hashlib
    runtime_root = Path(ARGS.runtime_root or REPO)
    result["source_digests"] = {x: hashlib.sha256((runtime_root/x).read_bytes()).hexdigest() for x in ("ciel_runtime.py", "ciel_runtime_support/runtime_sse_recovery.py", "ciel_runtime_support/runtime_session_restart.py", "ciel_runtime_support/runtime_launch.py")}
    (RUN / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    stub.shutdown()
    stub.server_close()
    print("RESULT", RUN, result["status"], flush=True)
raise SystemExit(0 if result["status"] == "passed_native_sse_recovery" else 1)
