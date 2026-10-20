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
    if _name.upper().startswith(PURGE):
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
REPO = HERE.parents[7]
sys.path.insert(0, str(HERE))
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
(CLAUDE_DIR / "settings.json").write_text(json.dumps({"skipDangerousModePermissionPrompt": True}))
(CODEX_HOME / "config.toml").write_text(
    f'[projects."{str(WS).replace(chr(92), chr(92) * 2)}"]\ntrust_level = "trusted"\n[analytics]\nenabled = false\n', encoding="utf-8")

env = dict(os.environ)
if ARGS.runtime_root:
    runtime_root = Path(ARGS.runtime_root).resolve()
    assert (runtime_root / "ciel_runtime.py").is_file(), runtime_root
    env["OWNED_RESTART_RUNTIME_ROOT"] = str(runtime_root)
for name in list(env):
    if name.upper().startswith(PURGE):
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

cmd = [sys.executable, str(HERE / "launch_shim.py")]
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
        [sys.executable, str(HERE / "console_watch.py"), str(launcher.pid), str(RUN), str(CONSOLE_DRIVER_DIR), MODE],
        env=env, creationflags=subprocess.DETACHED_PROCESS, stdout=(RUN / "watcher-out.txt").open("w"), stderr=subprocess.STDOUT)
    owned.append(watcher)
    event("launched", pid=launcher.pid, watcher=watcher.pid)
    wait(lambda: "error" not in http("GET", "/health", timeout=2), 90, "router health")
    marker = "claude_session_socket" if MODE == "claude" else "codex_remote_tui_start"
    wait(lambda: marker in router_logs(), 120, "native launch " + marker)
    wait(lambda: bool(natives()), 60, "native process present")
    time.sleep(12)
    event("children_before", processes=[describe(p) for p in descendants()])
    history = CFG / "runtime-input-status.jsonl"
    clean_history = history.read_bytes() if history.exists() else b""
    history.write_bytes(clean_history + b"{incomplete")
    notify("E2E-RT-TURN-4 must wait for verified ownership")
    wait(lambda: "reason=wake_history_incomplete" in router_logs(), 35, "history corruption held without crash")
    time.sleep(3)
    assert launcher.poll() is None, "launcher died on corrupt history"
    assert not reached("E2E-RT-TURN-4"), "input sent without ownership"
    event("corrupt_history_guard_pass", launcher_alive=True, transport_calls=0)
    # Restore only this owned fixture after checking no send; production never
    # repairs unknown user history automatically.
    history.write_bytes(clean_history)
    wait(lambda: reached("E2E-RT-TURN-4"), 40, "owned fixture recovery delivered held input")
    time.sleep(3)

    wait(lambda: "STUB-REPLY" in screen(), 40, "held input reply rendered")
    assert launcher.poll() is None
    assert "channel_windows_console_proxy_exception" not in router_logs()
    (RUN / "shot-request").write_text("history guard survived and held input replied", encoding="utf-8")
    wait(lambda: not (RUN / "shot-request").exists(), 20, "owned screenshot attempted")
    event("acceptance_pass", checks={"alive_during_corruption": True,
                                    "no_send_without_history": True,
                                    "owned_fixture_recovery_replied": True})
    result["status"] = "passed_corrupt_history_guard"

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
    print("RESULT", RUN, result["status"], flush=True)
