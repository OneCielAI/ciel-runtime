"""Owned real-claude.exe check of side actor retirement under a refused TaskStop (2026-11-23).

The installed claude.exe loads the packaged ciel-work and ciel-work-gate
plugins from a snapshot of the tree under test, plus the test-only stop-fault
mod beside this file, which refuses the first two TaskStop calls aimed at the
first task id it sees and logs every TaskStop with the engine's listed status.
The MCP backend is the real Work and side dispatch (dispatch_work_tool) on an
owned state directory; the Messages API is a scripted stand-in. No real
credentials; every directory is owned scratch under D:/Temp, removed at the end.

Flow (second version, after run 2 showed a finished side actor is already
listed "completed"): side session "fresh"; event 1 enters the real side inbox
and the stand-in holds its actor's reply, so the actor stays listed live. The
driver then edits the snapshot's register.js (a trailing comment), and the
engine's plugin-dir watch hot-reloads ciel-work; its session.start stops every
owned side actor still listed. stop-fault refuses that TaskStop twice. Event 2
is queued as soon as the first refusal shows in the debug log. Read back:
1. actor 1 listed "running" at both refusals, then a passed TaskStop that
   leaves it ended, and no "cleanup unconfirmed" line;
2. no TaskStop for any actor whose turn ended by itself;
3. event 2 processed while actor 1 was being retried, and event 1 processed
   again after its claim lease ran out (the killed actor never answered).

Only the process tree this driver started is killed. The live ~/.claude
settings, ~/.claude.json and the installed ciel-runtime bin are hashed before
and after; any change fails the run.

usage: python run_side_stop_real.py <tree under test> <evidence dir> [fork|fresh]
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
SESSION_MODE = sys.argv[3] if len(sys.argv) > 3 else 'fresh'
OUT.mkdir(parents=True, exist_ok=True)
HERE = Path(__file__).resolve().parent
CLI = str(Path.home() / ".local/bin/claude.exe")
PREFIXES = ("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI", "MAX_MCP")


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
    return {p.name if p.parent != home else "~/" + p.name: sha(p) for p in paths}


PROFILE_BEFORE = live_profile()
OWNED = Path(tempfile.mkdtemp(prefix="side-stop-", dir="D:/Temp"))
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
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox  # noqa: E402
from ciel_runtime_support.channel_mcp_http_controller import ChannelMcpHttpController, ChannelMcpHttpServices  # noqa: E402
from ciel_runtime_support.channel_mcp_tools import channel_mcp_tool_response  # noqa: E402
from ciel_runtime_support.http_response import HttpResponseAdapter  # noqa: E402

TOKEN = host_token()
env["CIEL_RUNTIME_WORK_PARALLEL_HOST_TOKEN"] = TOKEN
LOCK = threading.Lock()
CONFIG = {
    "work_mode": True, "work_auto_continue": False, "work_plan_approval_ask": False,
    "work_parallel_host": {"token": TOKEN, "capability_ready": True},
    "work_side_intake": {"enabled": True, "type": "btw_with_mcp", "session": SESSION_MODE},
    "current_provider": "anthropic",
    "providers": {"anthropic": {"current_model": MAIN_MODEL, "custom_models": []}},
}
STORE = session_work_plan_store(CIEL_CFG, IDENTITY)
STORE.transaction(lambda d: tree.add_node(d, None, "Side retirement check"))
INBOX = SideIntakeInbox(CIEL_CFG, IDENTITY)


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
    if name == "work_side_fail":
        entry["category"] = str(args.get("category") or "")[:80]
        entry["reason"] = str(args.get("reason") or "")[:300]
    if name in ("work_side_claim", "work_side_resolve", "work_side_reply", "work_side_understanding"):
        try:
            payload = json.loads(response["result"]["content"][0]["text"])
            claim = payload.get("claim") if isinstance(payload, dict) else None
            entry["claim"] = (claim or {}).get("receipt_id", "")[:12] if isinstance(claim, dict) else None
            entry["error"] = bool(response["result"].get("isError"))
        except Exception:
            entry["error"] = "unparsed"
    with LOCK:
        MCP_LOG.append(entry)
    return response


CONTROLLER = ChannelMcpHttpController(ChannelMcpHttpServices(
    version="side-stop-real", tool_schemas=lambda: work_tool_schemas(enabled=True),
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


SIDE = []
HELD = threading.Event()
RELEASE = threading.Event()
HOLD_MAX_SECONDS = 300


def all_user_text(body):
    parts = []
    for message in body.get("messages") or []:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        else:
            parts.extend(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")
    return "\n".join(parts)


def emit_text(handler, body, text, hold=False):
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
    if hold:
        # The actor's turn stays in flight (a ping every 2 s) until released or
        # the engine drops the stream (the actor was stopped).
        HELD.set()
        end = time.time() + HOLD_MAX_SECONDS
        try:
            while time.time() < end and not RELEASE.is_set():
                emit("ping", {"type": "ping"})
                time.sleep(2)
        except OSError:
            with LOCK:
                SIDE.append({"at": round(time.time(), 2), "agent": True, "label": "held-stream-dropped"})
            return
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
        text = all_user_text(body)
        if agent and "your answer was refused" in text:
            # The host's correction reached the actor: answer it correctly.
            with LOCK:
                SIDE.append({"at": round(time.time(), 2), "agent": True, "label": "correction-answered"})
            decision = {"action": "answer", "kind": "status", "summary": "corrected: nothing changed",
                        "reply": "nothing changed", "no_reply": True}
            return emit_text(self, body, json.dumps({"understanding": "gate re-check, nothing cleared",
                                                     "decision": decision,
                                                     "expected_version": STORE.read()["version"]}))
        label = next((m for m in ("SIDE-EVENT-1", "SIDE-EVENT-2") if m in text), None)
        with LOCK:
            SIDE.append({"at": round(time.time(), 2), "agent": bool(agent), "label": label})
        if agent and label:
            hold = label == "SIDE-EVENT-1" and not HELD.is_set()
            decision = {"action": "answer", "kind": "status", "summary": label + " checked",
                        "reply": label + " status recorded", "no_reply": True}
            return emit_text(self, body, json.dumps({"understanding": label + " is a status ping",
                                                     "decision": decision,
                                                     "expected_version": STORE.read()["version"]}), hold=hold)
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
FAULT = OWNED / "plugin-stop-fault"
shutil.copytree(HERE / "stop-fault", FAULT)
(CFG / ".claude.json").write_text(json.dumps({"hasCompletedOnboarding": True, "theme": "dark", "projects": {
    str(WS): {"hasTrustDialogAccepted": True}, str(WS).replace("\\", "/"): {"hasTrustDialogAccepted": True}}}),
    encoding="utf-8")
(CFG / "settings.json").write_text(json.dumps({"permissions": {"allow": ["mcp__ciel-runtime-router"], "deny": []}}),
                                   encoding="utf-8")
version = subprocess.run([CLI, "--version"], capture_output=True, text=True, timeout=60).stdout.strip()
mod_digests = {name: sha(PRODUCT / "hooks" / name) for name in ("register.js", "side-intake.js", "texts.generated.js")}
result = {"kind": "side actor retirement under refused TaskStop, real load", "claude_version": version,
          "side_session": SESSION_MODE, "mod_sha256": mod_digests,
          "fault_mod_sha256": sha(FAULT / "hooks" / "register.js"),
          "source_matches_snapshot": all(sha(SOURCE / "ciel_runtime_support/claude_mods/ciel-work/hooks" / n) == d
                                         for n, d in mod_digests.items()), "steps": {}}
DEBUG = OWNED / "claude-debug.txt"
proc = None
t0 = time.time()
receipts = {}


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


def stage(receipt_id: str) -> str | None:
    row = INBOX._load(receipt_id)
    return None if row is None else SideIntakeInbox._stage(SideIntakeInbox._stage_inputs(row), time.time())


def wait_stage(receipt_id: str, wanted: tuple, timeout: float) -> str | None:
    end = time.time() + timeout
    current = None
    while time.time() < end:
        current = stage(receipt_id)
        if current in wanted:
            return current
        time.sleep(0.5)
    return current


def own_tree_kill(pid: int) -> str:
    done = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)
    return (done.stdout + done.stderr).strip()[:400]


try:
    os.chdir(WS)
    proc = WindowsConPtySession([CLI, "--permission-mode", "default", "--plugin-dir", str(PRODUCT),
                                 "--plugin-dir", str(GATE_PLUGIN), "--plugin-dir", str(FAULT),
                                 "--strict-mcp-config", "--mcp-config",
                                 json.dumps(mcp_config), "--session-id", SESSION, "--debug-file", str(DEBUG)],
                                env, log=lambda *_: None, mirror_output=False, forward_stdin=False)
    result["root_pid_recorded"] = bool(proc.pid)
    loaded = wait_for(r"hooks module ciel-work@\S+ (loaded|failed to load)[^\n]*", 90)
    result["steps"]["mod_load_line"] = loaded.group(0)[:200] if loaded else None
    fault = wait_for(r"hooks module stop-fault@\S+ (loaded|failed to load)[^\n]*", 30)
    result["steps"]["fault_load_line"] = fault.group(0)[:200] if fault else None
    settled = wait_for(r"ciel-work@\w+[^\n]*?session\.start settled in ([\d.]+)ms", 90)
    result["steps"]["session_start_settled_ms"] = float(settled.group(1)) if settled else None
    time.sleep(5)
    receipts["e1"] = INBOX.receive("SIDE-EVENT-1 status ping from the isolated check", "side-stop-e1")["receipt_id"]
    result["steps"]["e1_sent_s"] = round(time.time() - t0, 1)
    result["steps"]["actor1_held"] = HELD.wait(90)
    result["steps"]["actor1_held_s"] = round(time.time() - t0, 1)
    # Hot reload: a trailing comment in the snapshot's register.js (never the source tree).
    module = PRODUCT / "hooks" / "register.js"
    result["reload"] = {"register_sha256_before": sha(module)}
    with module.open("a", encoding="utf-8") as stream:
        stream.write("\n// side-stop-real reload marker\n")
    result["reload"]["register_sha256_after"] = sha(module)
    result["reload"]["edited_s"] = round(time.time() - t0, 1)
    # A reload runs register and session.start again (plugin-authoring skill):
    # a second ciel-work session.start settle line is the engine's own signal.
    reload_end = time.time() + 120
    while time.time() < reload_end and len(re.findall(r"ciel-work@\S+ session\.start settled", debug_text())) < 2:
        time.sleep(1)
    result["reload"]["load_lines"] = len(re.findall(r"ciel-work@\S+ session\.start settled", debug_text()))
    result["reload"]["reloaded_s"] = round(time.time() - t0, 1)
    first_denial = wait_for(r"stop-fault #\d+ denied (\S+) \(1/2\)", 90)
    result["steps"]["first_denial_seen_s"] = round(time.time() - t0, 1) if first_denial else None
    receipts["e2"] = INBOX.receive("SIDE-EVENT-2 status ping from the isolated check", "side-stop-e2")["receipt_id"]
    result["steps"]["e2_sent_s"] = round(time.time() - t0, 1)
    result["steps"]["e2_stage"] = wait_stage(receipts["e2"], ("processed", "quarantined"), 120)
    result["steps"]["e2_done_s"] = round(time.time() - t0, 1)
    # Event 1's claim lease (120 s, no longer renewed after the reload) runs out,
    # then it is claimed again and answered at once.
    result["steps"]["e1_stage"] = wait_stage(receipts["e1"], ("processed", "quarantined"), 260)
    result["steps"]["e1_done_s"] = round(time.time() - t0, 1)
    end = time.time() + 20
    while time.time() < end and not re.search(r"stop-fault #\d+ passed", debug_text()):
        time.sleep(0.5)
    time.sleep(5)
    RELEASE.set()
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

    def stamp(line):
        match = re.match(r"(\d{4}-\d\d-\d\dT[\d:.]+Z)", line)
        return match.group(1) if match else None

    fault_lines = [line for line in text.splitlines() if "stop-fault #" in line]
    side_lines = [line for line in text.splitlines()
                  if "Ciel side" in line or "actor cleanup unconfirmed" in line or "side-fresh" in line
                  or "TaskStop" in line or "plugin-dir watch" in line
                  or re.search(r"ciel-work@\S+ (loaded|failed|session\.start|unloaded|reload)", line)
                  or re.search(r"(?i)reload", line) and "ciel-work" in line]
    reload_at = re.search(r"(\d{4}-\d\d-\d\dT[\d:.]+Z) [^\n]*ciel-work@\S+ reloaded", text)
    if reload_at:
        # Every engine line in the 10 s after the reload, paths masked, for the
        # first claim after it.
        start = reload_at.group(1)
        window = [line for line in text.splitlines() if line[:24] >= start][:600]
        (OUT / "debug-after-reload.txt").write_text("\n".join(
            re.sub(r"[A-Za-z]:[\\/][^\s\"']+", "<path>", line)[:400] for line in window
            if "ui.render settled" not in line), encoding="utf-8")
    (OUT / "debug-stop-lines.txt").write_text("\n".join(
        re.sub(r"[A-Za-z]:[\\/][^\s\"']+", "<path>", line)[:600] for line in (fault_lines + side_lines)[:400]),
        encoding="utf-8")
    RELEASE.set()
    denials = [re.search(r"denied (\S+) \((\d)/2\) listed=(\S+)", line) for line in fault_lines]
    passes = [re.search(r"passed (\S+) before=(\S+) after=(\S+) result=(.*)$", line) for line in fault_lines]
    denied_ids = [m.group(1) for m in denials if m]
    denied_listed = [m.group(3) for m in denials if m]
    passed = [{"id": m.group(1), "before": m.group(2), "after": m.group(3), "result": m.group(4)[:300]}
              for m in passes if m]
    actor1 = denied_ids[0] if denied_ids else None
    others = [p for p in passed if p["id"] != actor1]
    result["stop_calls"] = {"denied": denied_ids, "denied_listed": denied_listed, "passed": passed,
                            "times": [stamp(line) for line in fault_lines]}
    result["agent_completions"] = [re.sub(r"[A-Za-z]:[\\/][^\s\"']+", "<path>", line)[:300]
                                   for line in text.splitlines() if "agent_completion" in line][:20]
    result["side_log"] = [re.sub(r"[A-Za-z]:[\\/][^\s\"']+", "<path>", line)[:300] for line in side_lines[:40]]
    result["unconfirmed_lines"] = [line[:300] for line in text.splitlines() if "actor cleanup unconfirmed" in line]
    result["unloaded_lines"] = [line[:200] for line in text.splitlines()
                                if "ciel-work was unloaded" in line or "crashed the hooks worker" in line]
    result["failed_to_load"] = [line[:200] for line in text.splitlines() if "failed to load" in line]
    result["mcp_calls"] = [{"after_s": round(e["at"] - t0, 1), **{k: v for k, v in e.items() if k != "at"}}
                           for e in MCP_LOG if e["name"] != "work_parallel_status"][:200]
    result["model_requests"] = [{"after_s": round(r["at"] - t0, 1), "agent": r["agent"], "label": r["label"]}
                                for r in SIDE][:100]
    result["receipt_refs"] = {k: v[:12] for k, v in receipts.items()}
    final_stages = {k: stage(v) for k, v in receipts.items()}
    result["final_stages"] = final_stages
    # Correction delivery (Celly 2026-11-23: 0 of 15 failed actors got one).
    known = {v[:12] for v in receipts.values()}
    other_claims = sorted({e.get("claim") for e in MCP_LOG if e["name"] == "work_side_claim" and e.get("claim")} - known)
    other_stages = {}
    for row_id in other_claims:
        full = next((p.stem for p in (CIEL_CFG / "side-inboxes").rglob(row_id + "*.json")), None)
        other_stages[row_id] = stage(full) if full else None
    result["correction"] = {
        "not_delivered_lines": [line[:300] for line in text.splitlines() if "correction for" in line and "not delivered" in line],
        "asked_lines": [line[:300] for line in text.splitlines() if "asked the side actor" in line],
        "answered": sum(1 for r in SIDE if r["label"] == "correction-answered"),
        "resume_refused": [line[:300] for line in text.splitlines()
                           if "refused by a hook" in line and "Side SSE native fork authorization" in line],
        "other_events": other_stages,
    }
    actor1_pass = [p for p in passed if p["id"] == actor1]
    checks = {
        "mod_loaded": bool(result["steps"].get("mod_load_line")) and "failed" not in str(result["steps"].get("mod_load_line")),
        "fault_mod_loaded": bool(result["steps"].get("fault_load_line")) and "failed" not in str(result["steps"].get("fault_load_line")),
        "no_failed_to_load": not result["failed_to_load"],
        "no_unload": not result["unloaded_lines"],
        "actor1_held_live": bool(result["steps"].get("actor1_held")),
        "hot_reloaded": (result.get("reload") or {}).get("load_lines", 0) >= 2,
        "event1_processed": final_stages.get("e1") == "processed",
        "event2_processed": final_stages.get("e2") == "processed",
        "actor1_refused_twice_while_running": (denied_ids.count(actor1) == 2
                                              and set(denied_listed) == {"running"}) if actor1 else False,
        "actor1_stop_passed_and_ended": any(p["after"] in ("killed", "absent", "completed", "failed")
                                            for p in actor1_pass),
        "no_stop_for_actors_that_ended_themselves": not others,
        "no_cleanup_unconfirmed": not result["unconfirmed_lines"],
        "event2_queued_after_first_refusal": result["steps"].get("first_denial_seen_s") is not None,
        "correction_delivered_first_attempt": bool(result["correction"]["asked_lines"])
                                              and not result["correction"]["not_delivered_lines"]
                                              and not result["correction"]["resume_refused"],
        "corrected_answer_committed": result["correction"]["answered"] >= 1
                                      and any(s == "processed" for s in other_stages.values()),
    }
    result["checks"] = checks
    result["passed"] = all(checks.values()) and "failure" not in result
    profile_after = live_profile()
    result["profile_changed"] = sorted(k for k in set(PROFILE_BEFORE) | set(profile_after)
                                       if PROFILE_BEFORE.get(k) != profile_after.get(k))
    # ~/.claude.json is rewritten by every live Claude Code session on this machine
    # (this run uses its own CLAUDE_CONFIG_DIR); it is reported, not gated.
    result["profile_unchanged"] = not [k for k in result["profile_changed"] if k != "~/.claude.json"]
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
                      "stop_calls": result["stop_calls"], "final_stages": final_stages,
                      "unconfirmed": result["unconfirmed_lines"], "failure": result.get("failure"),
                      "scratch_removed": removed, "profile_unchanged": result["profile_unchanged"],
                      "profile_changed": result["profile_changed"]},
                     ensure_ascii=False, indent=1))
if not result["passed"]:
    raise SystemExit(1)
