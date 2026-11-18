"""Owned real-claude.exe load of the KOBE hooks-worker hotfix (2026-11-18).

Real Claude Code engine (the installed claude.exe) loads the packaged ciel-work
and ciel-work-gate plugins from a snapshot of the tree under test, against an
owned MCP backend (the real Work and side tools, dispatch_work_tool) and a
scripted Messages stand-in. No real credentials; every directory is owned
scratch under D:/Temp, removed at the end.

It reads three facts from the engine's own debug log and the backend's log:
1. session.start settles fast (the engine's "session.start settled in" line);
2. /work-mode view answers fast (the engine's command.run line);
3. the gate check fires while a main turn is running: the stand-in holds the
   main reply for 100 s, and the backend sees work_side_gate_check with
   idle false inside that window (interval 1 minute here).

Only the process tree this driver started is killed (taskkill /T on its own
root pid). The live ~/.claude settings, ~/.claude.json and the installed
ciel-runtime bin are hashed before and after; any change fails the run.

usage: python run_kobe_real_load.py <tree under test> <evidence dir>
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SOURCE = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
CLI = str(Path.home() / ".local/bin/claude.exe")
PREFIXES = ("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI", "MAX_MCP")
HOLD_SECONDS = 100


def sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def live_profile() -> dict:
    home = Path.home()
    paths = [home / ".claude" / "settings.json", home / ".claude.json"]
    bin_dir = home / "AppData/Local/ciel-runtime/bin"
    if bin_dir.is_dir():
        paths += sorted(p for p in bin_dir.iterdir() if p.is_file())
    return {str(i): sha(p) for i, p in enumerate(paths)}


PROFILE_BEFORE = live_profile()
OWNED = Path(tempfile.mkdtemp(prefix="kobe-load-", dir="D:/Temp"))
SNAPSHOT = OWNED / "snapshot"
shutil.copytree(SOURCE / "ciel_runtime_support", SNAPSHOT / "ciel_runtime_support",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
CFG, CIEL_CFG, WS = (OWNED / name for name in ("claude", "ciel", "workspace"))
for path in (CFG, CIEL_CFG, WS):
    path.mkdir(parents=True, exist_ok=True)
for args in (["init", "-q"], ["config", "user.name", "e2e"], ["config", "user.email", "e2e@localhost"]):
    subprocess.run(["git", "-C", str(WS), *args], check=True, capture_output=True)
(WS / "base.txt").write_text("base\n", encoding="utf-8")
subprocess.run(["git", "-C", str(WS), "add", "-A"], check=True, capture_output=True)
subprocess.run(["git", "-C", str(WS), "commit", "-q", "-m", "base"], check=True, capture_output=True)

env = {k: v for k, v in os.environ.items() if not k.upper().startswith(PREFIXES)}
for key in [k for k in os.environ if k.upper().startswith(PREFIXES)]:
    os.environ.pop(key, None)
SESSION = str(uuid.uuid4())
IDENTITY = "claude:" + SESSION
MAIN_MODEL = "fixture-main"
env.update(CIEL_RUNTIME_TEST_ISOLATED="1", CIEL_RUNTIME_CONFIG_DIR=str(CIEL_CFG), CLAUDE_CONFIG_DIR=str(CFG),
           ANTHROPIC_AUTH_TOKEN="fixture", ANTHROPIC_API_KEY="", ANTHROPIC_MODEL=MAIN_MODEL,
           CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", DISABLE_AUTOUPDATER="1", ENABLE_TOOL_SEARCH="false",
           PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
os.environ["CIEL_RUNTIME_CONFIG_DIR"] = str(CIEL_CFG)
os.environ["CIEL_RUNTIME_TEST_ISOLATED"] = "1"
sys.path.insert(0, str(SNAPSHOT))
from ciel_runtime_support import work_plan_tree as tree  # noqa: E402
from ciel_runtime_support.windows_conpty import WindowsConPtySession  # noqa: E402
from ciel_runtime_support.work_parallel_host import host_token  # noqa: E402
from ciel_runtime_support.work_mcp_tools import WORK_TOOL_NAMES, dispatch_work_tool, work_tool_schemas  # noqa: E402
from ciel_runtime_support.work_plan_store import session_work_plan_store  # noqa: E402
from ciel_runtime_support.channel_mcp_http_controller import ChannelMcpHttpController, ChannelMcpHttpServices  # noqa: E402
from ciel_runtime_support.channel_mcp_tools import channel_mcp_tool_response  # noqa: E402
from ciel_runtime_support.http_response import HttpResponseAdapter  # noqa: E402

TOKEN = host_token()
env["CIEL_RUNTIME_WORK_PARALLEL_HOST_TOKEN"] = TOKEN
LOCK = threading.Lock()
CONFIG = {
    "work_mode": True, "work_auto_continue": False, "work_plan_approval_ask": False,
    "work_parallel_host": {"token": TOKEN, "capability_ready": True},
    "work_side_intake": {"enabled": True, "type": "btw_with_mcp"},
    "work_gate_check": {"minutes": 1},
    "current_provider": "anthropic",
    "providers": {"anthropic": {"current_model": MAIN_MODEL, "custom_models": []}},
}
STORE = session_work_plan_store(CIEL_CFG, IDENTITY)
# A board with a recorded approval wait on a P1 goal and one on an ordinary goal.
_, GOAL = STORE.transaction(lambda d: tree.set_plan(d, {"title": "Ordinary goal", "children": [
    {"title": "deploy", "waiting_on": "approval from the release owner"}]}, None))
STORE.transaction(lambda d: tree.add_node(d, None, "Urgent goal", priority="P1"))
URGENT = next(n["id"] for n in STORE.read()["nodes"] if n["title"] == "Urgent goal")
STORE.transaction(lambda d: tree.add_node(d, URGENT, "hotfix", waiting_on="approval from the on-call lead"))
NODES = {n["title"]: n["id"] for n in STORE.read()["nodes"]}


def snapshot_config():
    with LOCK:
        return json.loads(json.dumps(CONFIG))


def save_config(config):
    with LOCK:
        CONFIG.clear()
        CONFIG.update(json.loads(json.dumps(config)))


MCP_LOG = []


def tool_response(request_id, params):
    name, args = params.get("name"), params.get("arguments") or {}
    if name not in WORK_TOOL_NAMES:
        return channel_mcp_tool_response(request_id, "unknown", True)
    started = time.time()
    response = dispatch_work_tool(request_id, name, args, load_config=snapshot_config, save_config=save_config,
                                  state_dir=CIEL_CFG, respond=channel_mcp_tool_response)
    entry = {"at": round(started, 2), "name": name}
    if name == "work_side_gate_check":
        entry["idle"] = args.get("idle")
        entry["host_report"] = args.get("host_report") is not None
        try:
            result = json.loads(response["result"]["content"][0]["text"])
            entry["result"] = {k: result.get(k) for k in ("queued", "reason", "main_idle", "gates", "host_due")}
        except Exception:
            entry["result"] = "unparsed"
    if name == "work_parallel_status":
        entry["projection"] = args.get("projection")
    with LOCK:
        MCP_LOG.append(entry)
    return response


CONTROLLER = ChannelMcpHttpController(ChannelMcpHttpServices(
    version="kobe-real-load", tool_schemas=lambda: work_tool_schemas(enabled=True),
    tool_call_response=tool_response, write_json=HttpResponseAdapter.write_json,
    write_accepted=HttpResponseAdapter.write_accepted, log=lambda *_: None))


class Mcp(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def do_GET(self):
        if not CONTROLLER.get(self, self.path.split("?")[0]):
            self.send_response(404); self.send_header("content-length", "0"); self.end_headers()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        if not CONTROLLER.post(self, self.path.split("?")[0], body):
            self.send_response(404); self.send_header("content-length", "0"); self.end_headers()


MAIN = []
SIDE = []
SEEN = {key: threading.Event() for key in ("busy_started", "busy_done", "gate_side")}


def last_user_text(body):
    for message in reversed(body.get("messages") or []):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        return "\n".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")
    return ""


def emit_text(handler, body, text, hold=0.0):
    msg = {"id": "msg_" + uuid.uuid4().hex[:12], "type": "message", "role": "assistant",
           "model": body.get("model") or MAIN_MODEL, "content": [], "stop_reason": None,
           "usage": {"input_tokens": 20, "output_tokens": 5}}
    handler.send_response(200)
    handler.send_header("content-type", "text/event-stream")
    handler.send_header("connection", "close")
    handler.end_headers()

    def emit(kind, value):
        handler.wfile.write(("event: " + kind + "\ndata: " + json.dumps(value) + "\n\n").encode())
        handler.wfile.flush()

    emit("message_start", {"type": "message_start", "message": msg})
    emit("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
    end = time.time() + hold
    while time.time() < end:
        # The main turn stays in flight: a ping every 5 s keeps the stream open.
        emit("ping", {"type": "ping"})
        time.sleep(min(5.0, max(0.0, end - time.time())))
    emit("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}})
    emit("content_block_stop", {"type": "content_block_stop", "index": 0})
    emit("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                           "usage": {"output_tokens": 5}})
    emit("message_stop", {"type": "message_stop"})


class Messages(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}"
        body = json.loads(raw)
        if "count_tokens" in self.path:
            data = b'{"input_tokens":20}'
            self.send_response(200); self.send_header("content-length", str(len(data))); self.end_headers()
            self.wfile.write(data)
            return
        agent = self.headers.get("x-claude-code-agent-id") or ""
        text = last_user_text(body)
        if agent:
            gate = "Ciel gate re-check" in text
            with LOCK:
                SIDE.append({"at": round(time.time(), 2), "gate": gate,
                             "p1_first": gate and text.find("[P1] ") >= 0
                             and text.find("[P1] ") < text.find("- " + NODES["deploy"])})
            if gate:
                SEEN["gate_side"].set()
                decision = {"action": "answer", "kind": "status", "summary": "no change",
                            "reply": "checked; both approvals are still open"}
                return emit_text(self, body, json.dumps({"understanding": "gate re-check, nothing cleared",
                                                         "decision": decision,
                                                         "expected_version": STORE.read()["version"]}))
            return emit_text(self, body, "fork ok")
        busy = "BUSY-TURN" in text
        with LOCK:
            MAIN.append({"at": round(time.time(), 2), "busy": busy})
        if busy:
            SEEN["busy_started"].set()
            emit_text(self, body, "busy turn done", hold=HOLD_SECONDS)
            with LOCK:
                MAIN.append({"at": round(time.time(), 2), "busy_end": True})
            SEEN["busy_done"].set()
            return
        emit_text(self, body, "ack")


mcp_server = ThreadingHTTPServer(("127.0.0.1", 0), Mcp)
api_server = ThreadingHTTPServer(("127.0.0.1", 0), Messages)
for server in (mcp_server, api_server):
    threading.Thread(target=server.serve_forever, daemon=True).start()
env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{api_server.server_port}"
mcp_config = {"mcpServers": {"ciel-runtime-router": {"type": "http",
                                                    "url": f"http://127.0.0.1:{mcp_server.server_port}/ca/mcp"}}}
PRODUCT = OWNED / "plugin-ciel-work"
shutil.copytree(SNAPSHOT / "ciel_runtime_support/claude_mods/ciel-work", PRODUCT)
GATE_PLUGIN = OWNED / "plugin-ciel-work-gate"
shutil.copytree(SNAPSHOT / "ciel_runtime_support/claude_mods/ciel-work-gate", GATE_PLUGIN)
(CFG / ".claude.json").write_text(json.dumps({"hasCompletedOnboarding": True, "theme": "dark", "projects": {
    str(WS): {"hasTrustDialogAccepted": True}, str(WS).replace("\\", "/"): {"hasTrustDialogAccepted": True}}}),
    encoding="utf-8")
(CFG / "settings.json").write_text(json.dumps({"permissions": {"allow": ["mcp__ciel-runtime-router"], "deny": []}}),
                                   encoding="utf-8")
version = subprocess.run([CLI, "--version"], capture_output=True, text=True, timeout=60).stdout.strip()
mod_digests = {name: sha(PRODUCT / "hooks" / name) for name in ("register.js", "side-intake.js", "observatory.client.js")}
result = {"kind": "KOBE hooks-worker hotfix real load", "claude_version": version, "mod_sha256": mod_digests,
          "source_matches_snapshot": all(sha(SOURCE / "ciel_runtime_support/claude_mods/ciel-work/hooks" / n) == d
                                         for n, d in mod_digests.items()), "steps": {}}
DEBUG = OWNED / "claude-debug.txt"
proc = None
t0 = time.time()


def debug_text() -> str:
    return DEBUG.read_text(encoding="utf-8", errors="replace") if DEBUG.exists() else ""


def wait_for(pattern: str, timeout: float) -> re.Match | None:
    end = time.time() + timeout
    while time.time() < end:
        match = re.search(pattern, debug_text())
        if match:
            return match
        time.sleep(0.5)
    return None


def own_tree_kill(pid: int) -> str:
    done = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)
    return (done.stdout + done.stderr).strip()[:400]


try:
    os.chdir(WS)
    proc = WindowsConPtySession([CLI, "--permission-mode", "default", "--plugin-dir", str(PRODUCT),
                                 "--plugin-dir", str(GATE_PLUGIN), "--strict-mcp-config", "--mcp-config",
                                 json.dumps(mcp_config), "--session-id", SESSION, "--debug-file", str(DEBUG)],
                                env, log=lambda *_: None, mirror_output=False, forward_stdin=False)
    result["root_pid_recorded"] = bool(proc.pid)
    loaded = wait_for(r"hooks module ciel-work@\S+ (loaded|failed to load)[^\n]*", 90)
    result["steps"]["mod_load_line"] = loaded.group(0)[:200] if loaded else None
    settled = wait_for(r"ciel-work@\w+[^\n]*?session\.start settled in ([\d.]+)ms", 90)
    result["steps"]["session_start_settled_ms"] = float(settled.group(1)) if settled else None
    time.sleep(3)
    proc.write(b"/work-mode view\r")
    view = wait_for(r"ciel-work[^\n]*answered command\.run[^\n]* in ([\d.]+)ms", 60)
    result["steps"]["work_mode_view_line"] = view.group(0)[:200] if view else None
    result["steps"]["work_mode_view_ms"] = float(view.group(1)) if view else None
    time.sleep(2)
    proc.write(b"\x1b")
    time.sleep(1)
    busy_sent = time.time()
    proc.write(b"BUSY-TURN hold this turn.\r")
    result["steps"]["busy_started"] = SEEN["busy_started"].wait(60)
    busy_start = time.time()
    result["steps"]["gate_side_fork_seen"] = SEEN["gate_side"].wait(HOLD_SECONDS + 30)
    result["steps"]["busy_done"] = SEEN["busy_done"].wait(HOLD_SECONDS + 60)
    busy_end = time.time()
    time.sleep(5)
    result["window"] = {"busy_sent_s": round(busy_sent - t0, 1), "busy_start_s": round(busy_start - t0, 1),
                        "busy_end_s": round(busy_end - t0, 1)}
except Exception as error:
    result["failure"] = type(error).__name__ + ": " + str(error)[:500]
finally:
    if proc is not None:
        try:
            screen = proc.output_tail().decode("utf-8", errors="replace")
            screen = re.sub(r"\x1b\[[0-9;?<>=]*[ -/]*[@-~]", "", screen)
            (OUT / "console-tail.txt").write_text(screen[-6000:].replace(str(OWNED), "<owned>")
                                                  .replace(str(Path.home()), "<home>"), encoding="utf-8")
        except Exception:
            pass
        result["kill"] = own_tree_kill(proc.pid) if proc.poll() is None else "exited"
        try:
            proc.close()
        except Exception:
            pass
    for server in (mcp_server, api_server):
        server.shutdown(); server.server_close()
    text = debug_text()
    ciel_lines = [line for line in text.splitlines() if "ciel-work" in line and (
        "settled in" in line or "answered command.run" in line or "unloaded" in line or "failed to load" in line
        or "hooks worker" in line or "Work gate check" in line or "Gate re-check" in line)]
    (OUT / "debug-ciel-work-lines.txt").write_text("\n".join(
        re.sub(r"[A-Za-z]:[\\/][^\s\"']+", "<path>", line)[:400] for line in ciel_lines[:400]), encoding="utf-8")
    gate_calls = [e for e in MCP_LOG if e["name"] == "work_side_gate_check"]
    window = result.get("window") or {}
    in_busy = [e for e in gate_calls if window and window["busy_start_s"] <= e["at"] - t0 <= window["busy_end_s"]]
    result["gate_calls"] = [{"after_s": round(e["at"] - t0, 1), "idle": e.get("idle"), "host_report": e["host_report"],
                             "result": e.get("result")} for e in gate_calls]
    result["side_requests"] = [{"after_s": round(r["at"] - t0, 1), "gate": r["gate"], "p1_first": r["p1_first"]}
                               for r in SIDE]
    result["main_requests"] = [{"after_s": round(r["at"] - t0, 1), **{k: v for k, v in r.items() if k != "at"}}
                               for r in MAIN]
    status_reads = [e for e in MCP_LOG if e["name"] == "work_parallel_status"]
    result["router_calls"] = {"total": len(MCP_LOG),
                              "status_full": sum(1 for e in status_reads if not e.get("projection")),
                              "status_version": sum(1 for e in status_reads if e.get("projection") == "version")}
    result["unloaded_lines"] = [line[:200] for line in text.splitlines()
                                if "ciel-work was unloaded" in line or "crashed the hooks worker" in line]
    result["failed_to_load"] = [line[:200] for line in text.splitlines() if "failed to load" in line]
    queued_busy = [e for e in in_busy if not e["host_report"] and isinstance(e.get("result"), dict)
                   and e["result"].get("queued") and e.get("idle") is False]
    side_busy = [r for r in SIDE if r["gate"] and window and window["busy_start_s"] <= r["at"] - t0 <= window["busy_end_s"]]
    checks = {
        "mod_loaded": bool(result["steps"].get("mod_load_line")) and "failed" not in str(result["steps"].get("mod_load_line")),
        "no_failed_to_load": not result["failed_to_load"],
        "no_unload": not result["unloaded_lines"],
        "session_start_settled_under_2s": (result["steps"].get("session_start_settled_ms") or 1e9) < 2000,
        "work_mode_view_under_5s": (result["steps"].get("work_mode_view_ms") or 1e9) < 5000,
        "gate_check_asked_while_main_busy": bool(queued_busy),
        "side_fork_checked_gates_while_main_busy": bool(side_busy),
        "p1_gate_listed_first": any(r["p1_first"] for r in side_busy),
    }
    result["checks"] = checks
    result["passed"] = all(checks.values()) and "failure" not in result
    result["profile_unchanged"] = live_profile() == PROFILE_BEFORE
    result["passed"] = result["passed"] and result["profile_unchanged"]
    os.chdir(SOURCE)
    removed = False
    for _ in range(30):
        try:
            shutil.rmtree(OWNED)
            removed = True
            break
        except OSError:
            time.sleep(2)
    result["scratch_removed"] = removed
    dumped = json.dumps(result, ensure_ascii=False, indent=2)
    dumped = dumped.replace(str(OWNED), "<owned>").replace(str(Path.home()), "<home>")
    result_text = dumped if TOKEN not in dumped else dumped.replace(TOKEN, "<token>")
    (OUT / "result.json").write_text(result_text, encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "checks": checks, "steps": result["steps"],
                      "router_calls": result["router_calls"], "failure": result.get("failure"),
                      "gate_calls": result["gate_calls"][:12], "window": result.get("window"),
                      "scratch_removed": removed, "profile_unchanged": result["profile_unchanged"]},
                     ensure_ascii=False, indent=1))
if not result["passed"]:
    raise SystemExit(1)
