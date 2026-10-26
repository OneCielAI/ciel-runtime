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

import uuid
import hashlib
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
REPO = next(p for p in HERE.parents if (p / "ciel_runtime.py").is_file())
sys.path.insert(0, str(HERE))
import fixture  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("mode", choices=("claude", "codex-remote"))
parser.add_argument("--probe-timeout", type=float, default=None)
parser.add_argument("--label", default="")
parser.add_argument("--runtime-root", default="", help="Installed snapshot to verify (fixtures remain in repository)")
parser.add_argument("--slow", type=float, default=60.0)
parser.add_argument("--typed-post-probe", action="store_true", help="Additional raw console input probe (not needed for restart delivery acceptance)")
parser.add_argument("--provider", default="vllm", choices=("vllm", "cielairouter"),
                    help="Configured provider; cielairouter uses the central base (gateway selection off) on the loopback fixture")
parser.add_argument("--auto", action="store_true", help="Acceptance: production stream recovery continues text-only breaks; the harness sends no recovery input")
ARGS = parser.parse_args()
MODE = ARGS.mode

CLAUDE_BIN = Path("C:/Users/djlov/.local/bin/claude.exe")
CODEX_BIN = Path("C:/Users/djlov/AppData/Roaming/npm/node_modules/@openai/codex/node_modules/@openai/codex-win32-x64/"
                 "vendor/x86_64-pc-windows-msvc/bin/codex.exe")
NATIVE_NAME = "claude.exe" if MODE == "claude" else "codex.exe"
CONSOLE_DRIVER_DIR = REPO / "docs/journal/2026/10/16/features/cielairouter-direct-path/evidence/e2e"
for binary in (CLAUDE_BIN, CODEX_BIN, CONSOLE_DRIVER_DIR / "console_driver.py"):
    if not binary.exists():
        raise SystemExit(f"missing {binary}")

RUN = HERE / ("run-" + MODE + (("-" + ARGS.label) if ARGS.label else "") + "-" + time.strftime("%H%M%S"))
RUN.mkdir()
SCRATCH = Path(tempfile.mkdtemp(prefix="ciel-owned-stream-", dir="C:/Windows/Temp"))
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
fixture.WORKSPACE = WS
(WS / "notes.txt").write_text("harmless owned fixture notes", encoding="utf-8")
stub = ThreadingHTTPServer(("127.0.0.1", 0), fixture.Stub)
threading.Thread(target=stub.serve_forever, daemon=True).start()
FIXTURE_PORT = stub.server_port
assert len({ROUTER, APP, FIXTURE_PORT}) == 3

_provider_config = {"base_url": f"http://127.0.0.1:{FIXTURE_PORT}", "current_model": "owned-stream-model",
                    "api_key": "owned-dummy", "context_window": 128000, "max_output_tokens": 4096}
if ARGS.provider == "cielairouter":
    # No discovery against a real gateway: requests go to the configured base.
    _provider_config["gateway_selection"] = "off"
(CFG / "config.json").write_text(json.dumps({
    "current_provider": ARGS.provider, "language": "en",
    "providers": {ARGS.provider: _provider_config},
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
    APPDATA=str(HOME / "AppData/Roaming"), LOCALAPPDATA=str(HOME / "AppData/Local"),
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
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
SESSION_ID = str(uuid.uuid4())
if MODE == "claude":
    cmd += ["--", "--session-id", SESSION_ID, "--debug-file", str(RUN / "native-debug.log")]

result = {"mode": MODE, "label": ARGS.label, "scratch": str(SCRATCH), "versions": versions, "path_resolution": resolved,
          "command": cmd, "probe_timeout_shim": ARGS.probe_timeout,
          "ports": {"router": ROUTER, "appserver": APP if MODE == "codex-remote" else None, "fixture": FIXTURE_PORT},
          "events": [], "session_id": SESSION_ID, "provider": ARGS.provider, "claude_sha256": hashlib.sha256(CLAUDE_BIN.read_bytes()).hexdigest()}
result["source"] = {"repo_head": subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip(),
                    "runtime_tree_dirty_paths": subprocess.run(["git", "status", "--short", "--", "ciel_runtime.py", "ciel_runtime_support"],
                                                               cwd=REPO, capture_output=True, text=True).stdout.splitlines(),
                    "claude_bin": str(CLAUDE_BIN)}
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
    return any(q.get("case") == marker and q.get("main") for q in fixture.REQS)


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


def auto_requests():
    return [q for q in fixture.REQS if q.get("case") == fixture.AUTO_CASE and q.get("main")]


def ledger():
    # The router-instance directory is the config directory itself in isolated test mode.
    for candidate in CFG.rglob("claude-stream-recovery.json"):
        try:
            return json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    return {}


def recovery_log_lines():
    return [scrub(line)[:400] for line in router_logs().splitlines() if "claude_stream_recovery" in line]


def run_auto_acceptance(before_native, observations, shot, send_case):
    """Production recovery only: no harness-sent continuation anywhere below."""

    checks = result.setdefault("auto_checks", {})
    # 1. Text-only break: one automatic continuation, rendered in the same TUI.
    send_case("OWNED-TEXT-CLOSED")
    wait(lambda: "Server error mid-response" in screen(), 20, "text-closed yellow error rendered")
    shot("auto-text-closed-error")
    wait(lambda: bool(auto_requests()), 60, "automatic continuation reached API")
    wait(lambda: f"OWNED-REPLY-OK-{fixture.AUTO_CASE}" in screen(), 40, "automatic continuation rendered")
    time.sleep(6)
    shot("auto-text-recovered")
    rows = observations()
    checks["text_closed"] = {
        "auto_requests": [q["n"] for q in auto_requests()],
        "same_native_identities": before_native == [(p.pid, p.create_time()) for p in natives()],
        "server_errors": len([r for r in rows["errors"] if r.get("error") == "server_error"]),
    }
    event("auto_text_closed", **checks["text_closed"])
    # 2. A continuation that breaks again spends the same chain budget (default 1): no second one.
    count = len(auto_requests())
    send_case("OWNED-TEXT-CHAIN")
    wait(lambda: len(auto_requests()) >= count + 1, 60, "chain first continuation reached API")
    wait(lambda: any(q.get("action") == "auto_recovery_closed_text_then_error" and q.get("done_at") for q in fixture.REQS), 40, "chain continuation broke again")
    time.sleep(18)
    shot("auto-chain-exhausted")
    checks["chain"] = {"auto_requests_after": len(auto_requests()) - count,
                       "ledger_handled": {k: v.get("status") for k, v in (ledger().get("handled") or {}).items()}}
    event("auto_chain", **checks["chain"])
    # 3. New input during the quiet interval cancels the pending continuation.
    count = len(auto_requests())
    send_case("OWNED-TEXT-MANUAL")
    wait(lambda: "Server error mid-response" in screen() and len(observations()["errors"]) >= 4, 20, "manual-case error recorded")
    notify("OWNED-MANUAL-FOLLOWUP person typed a new request after the error")
    wait(lambda: reached("OWNED-MANUAL-FOLLOWUP"), 40, "manual follow-up reached API")
    time.sleep(12)
    checks["manual"] = {"auto_requests_after": len(auto_requests()) - count}
    event("auto_manual", **checks["manual"])
    # 4. Tool-bearing breaks are never continued by Ciel.
    count = len(auto_requests())
    send_case("OWNED-PARTIAL-TOOL")
    time.sleep(15)
    shot("auto-partial-tool")
    send_case("OWNED-COMPLETE-TOOL")
    time.sleep(20)
    shot("auto-completed-tool")
    marker_file = WS / "owned-tool-count.txt"
    final = observations()
    checks["tools"] = {
        "auto_requests_after": len(auto_requests()) - count,
        "completed_tool_executed_lines": len(marker_file.read_text(encoding="utf-8").splitlines()) if marker_file.exists() else 0,
        "partial_tool_not_dispatched": not any(u.get("id") == "toolu_owned_partial" for u in final["uses"]),
    }
    event("auto_tools", **checks["tools"])
    checks["recovery_log"] = recovery_log_lines()
    checks["ledger"] = ledger()
    passed = (
        len(checks["text_closed"]["auto_requests"]) == 1
        and checks["text_closed"]["same_native_identities"]
        and checks["chain"]["auto_requests_after"] == 1
        and checks["manual"]["auto_requests_after"] == 0
        and checks["tools"]["auto_requests_after"] == 0
        and checks["tools"]["completed_tool_executed_lines"] == 1
        and checks["tools"]["partial_tool_not_dispatched"]
    )
    result["per_case"] = {
        case: {"main_requests": [q["n"] for q in fixture.REQS if q.get("case") == case and q.get("main")],
               "actions": [q.get("action") for q in fixture.REQS if q.get("case") == case and q.get("main")]}
        for case in fixture.MARKERS + (fixture.AUTO_CASE,)}
    result["status"] = "passed_auto_stream_recovery" if passed else "failed_auto_stream_recovery"
    event("auto_complete", status=result["status"])
try:
    # Run 202918's console took keyboard focus and received keystrokes meant for
    # another window. Show it without activating it (SW_SHOWNOACTIVATE).
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 4
    launcher = subprocess.Popen(cmd, env=env, cwd=WS, creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup)
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
    # Record the installed native version; the acceptance re-proves behaviour on it.
    event("claude_version", version=versions[str(CLAUDE_BIN)])
    assert "(Claude Code)" in versions[str(CLAUDE_BIN)], versions
    def main_rows():
        p = next(iter(CLAUDE_DIR.glob("projects/*/" + SESSION_ID + ".jsonl")), None)
        if p is None:
            return []
        rows = []
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
        return rows
    def observations():
        rows = main_rows()
        return {"total": len(rows), "errors": [r for r in rows if r.get("isApiErrorMessage")],
                "uses": [b for r in rows for b in ((r.get("message") or {}).get("content") or [])
                         if isinstance(b, dict) and b.get("type") == "tool_use"]}
    def shot(label):
        (RUN / "shot-request").write_text(label, encoding="utf-8")
        wait(lambda: not (RUN / "shot-request").exists(), 20, "screenshot attempted " + label)
        for name in ("owned-console.png", "screen-latest.txt"):
            p = RUN / name
            if p.exists():
                shutil.copyfile(p, RUN / (label + "-" + name))
    def send_case(marker):
        notify(marker + " controlled owned loopback fault probe")
        wait(lambda: reached(marker), 80, marker + " reached main request")
        wait(lambda: any(q.get("case") == marker and q.get("done_at") for q in fixture.REQS), 40, marker + " first response done")
    send_case("OWNED-BASELINE")
    wait(lambda: "OWNED-REPLY-OK-OWNED-BASELINE" in screen(), 40, "baseline TUI rendered")
    time.sleep(3)
    before_native = [(p.pid, p.create_time()) for p in natives()]
    result["native_before"] = [describe(p) for p in natives()]
    if ARGS.auto:
        run_auto_acceptance(before_native, observations, shot, send_case)
        raise SystemExit(0)
    # Text-only case: one complete visible text block, then a server error event.
    send_case("OWNED-TEXT-CLOSED")
    time.sleep(12)
    obs = observations()
    text_requests = [q for q in fixture.REQS if q.get("case") == "OWNED-TEXT-CLOSED" and q.get("main")]
    result["after_text_closed"] = {"main_request_numbers": [q["n"] for q in text_requests],
                                   "error_count": len(obs["errors"]),
                                   "screen_has_server_error": "Server error mid-response" in screen()}
    shot("text-closed-error")
    server_errors = [r for r in obs["errors"] if r.get("error") == "server_error"]
    result["after_text_closed"]["server_error_records"] = [
        {k: r.get(k) for k in ("uuid", "requestId", "isSidechain", "truncatedAfterOutput", "timestamp")} for r in server_errors]
    stalled = bool(server_errors) and len(text_requests) == 1 and launcher.poll() is None
    result["after_text_closed"]["native_stalled_without_automatic_request"] = stalled
    event("text_closed_observed", **result["after_text_closed"])
    if stalled:
        # One owned recovery input after the observed stop, through the existing notify/session_socket path.
        notify("OWNED-RECOVERY Continue the interrupted owned task from the existing transcript; do not rerun completed tools.")
        wait(lambda: reached("OWNED-RECOVERY"), 80, "one recovery message reached API")
        wait(lambda: "OWNED-REPLY-OK-OWNED-RECOVERY" in screen(), 40, "same native TUI recovered")
        time.sleep(4)
        result["after_recovery"] = {
            "same_native_identities": before_native == [(p.pid, p.create_time()) for p in natives()],
            "recovery_main_requests": [q["n"] for q in fixture.REQS if q.get("case") == "OWNED-RECOVERY" and q.get("main")]}
        shot("text-recovered")
        event("recovery_observed", **result["after_recovery"])
    else:
        # Native continued (or never failed); no recovery input is sent. The
        # variants below still run so each failure shape is observed once.
        result["after_recovery"] = {"skipped": "native did not stall", "same_native_identities": None}
        event("recovery_skipped", reason="native did not stall")
    # Variants: open text block, partial tool JSON, completed harmless tool before failure.
    send_case("OWNED-TEXT-OPEN")
    time.sleep(12)
    shot("text-open")
    send_case("OWNED-PARTIAL-TOOL")
    time.sleep(12)
    shot("partial-tool")
    send_case("OWNED-COMPLETE-TOOL")
    time.sleep(20)
    shot("completed-tool")
    marker_file = WS / "owned-tool-count.txt"
    result["completed_tool_marker_lines"] = marker_file.read_text(encoding="utf-8").splitlines() if marker_file.exists() else []
    final = observations()
    result["main_observations"] = {"error_count": len(final["errors"]),
                                   "tool_uses": [{"id": u.get("id"), "name": u.get("name")} for u in final["uses"]]}
    result["per_case"] = {
        case: {"main_requests": [q["n"] for q in fixture.REQS if q.get("case") == case and q.get("main")],
               "actions": [q.get("action") for q in fixture.REQS if q.get("case") == case and q.get("main")],
               "fail_modes": [q.get("fail_mode") for q in fixture.REQS if q.get("case") == case and q.get("fail_mode")]}
        for case in fixture.MARKERS}
    result["checks"] = {
        "same_session_native_after_recovery": result["after_recovery"]["same_native_identities"],
        "partial_tool_not_dispatched": not any(u.get("id") == "toolu_owned_partial" for u in final["uses"]),
        "completed_tool_executed_lines": len(result["completed_tool_marker_lines"])}
    result["status"] = "observed_owned_native_recovery_poc"
    event("observation_complete", checks=result["checks"])

except SystemExit:
    pass
except Exception:
    result["status"] = "failed"
    result["error"] = traceback.format_exc()
    event("failure", error=result["error"])
finally:
    stop_sampling.set()
    (RUN / "stop").write_text("1")
    time.sleep(1)
    result["requests"] = fixture.REQS
    (RUN / "router-combined.log").write_text(scrub(router_logs()), encoding="utf-8")
    for p in sorted(CFG.rglob("*")):
        if p.is_file() and p.suffix in (".json", ".jsonl") and p.name != "config.json" and "token" not in p.name.lower():
            dst = RUN / "state" / p.relative_to(CFG)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(scrub(p.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
    transcript_metadata = []
    for p in CLAUDE_DIR.rglob("*.jsonl"):
        rows = []
        for line_number, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            message = row.get("message") or {}
            content = message.get("content")
            blocks_meta = []
            for block in (content if isinstance(content, list) else []):
                if not isinstance(block, dict):
                    continue
                brief = {k: block.get(k) for k in ("type", "id", "name", "tool_use_id", "is_error") if k in block}
                if isinstance(block.get("text"), str):
                    brief.update(text_length=len(block["text"]), text_sha256=hashlib.sha256(block["text"].encode()).hexdigest())
                if block.get("type") == "tool_use":
                    brief["input_keys"] = sorted((block.get("input") or {}).keys())
                blocks_meta.append(brief)
            keys = ("type", "subtype", "uuid", "parentUuid", "sessionId", "timestamp", "requestId", "promptId", "isSidechain",
                    "isApiErrorMessage", "error", "truncatedAfterOutput", "origin", "sourceToolAssistantUUID", "toolUseID")
            meta = {"line": line_number, **{k: row.get(k) for k in keys if k in row}, "role": message.get("role"),
                    "message_id": message.get("id"), "model": message.get("model"), "stop_reason": message.get("stop_reason"),
                    "usage": message.get("usage"), "blocks": blocks_meta,
                    "markers": [x for x in fixture.MARKERS + ("OWNED-REPLY-OK", "OWNED-FALLBACK-OK", "OWNED-TOOL-EXECUTED-ONCE") if x in line]}
            if row.get("isApiErrorMessage") and isinstance(content, list):
                # Native fixed error text only; it contains no model or user payload.
                meta["native_error_text"] = [b.get("text") for b in content if isinstance(b, dict)]
            rows.append(meta)
        dst = RUN / "transcript-metadata" / p.relative_to(CLAUDE_DIR).as_posix().replace("/", "__")
        dst.parent.mkdir(exist_ok=True)
        dst.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        transcript_metadata.append({"path": str(dst), "rows": len(rows), "main": p.name == SESSION_ID + ".jsonl"})
    result["transcript_files"] = transcript_metadata
    debug = RUN / "native-debug.log"
    if debug.exists():
        keep = ("API REQUEST", "request_id", "requestId", "Stream", "stream", "fallback", "server error", "Server error",
                "partial", "retry", "tool_use", "toolu_owned", "messaging", "socket")
        lines = [scrub(l)[:600] for l in debug.read_text(encoding="utf-8", errors="replace").splitlines() if any(k in l for k in keep)]
        (RUN / "native-debug-metadata.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        debug.unlink()
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
    result["teardown"]["fixture_listener_after"] = listener_pid(FIXTURE_PORT)
    (RUN / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print("RESULT", RUN, result["status"], flush=True)
