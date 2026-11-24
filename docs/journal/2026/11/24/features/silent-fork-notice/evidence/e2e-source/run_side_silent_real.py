"""Owned real-claude.exe check of the silent running-fork notice (user 2026-11-24).

Adapted from 2026/11/23 side-actor-background run_side_stall_real.py.
Claude Code loads ciel-work and ciel-work-gate from a snapshot of the tree
under test. The MCP backend is the real Work and side dispatch on an owned
state directory; the Messages API is a scripted stand-in. No real credentials;
scratch under D:/Temp, removed at the end.

The isolated ledger holds one P1 fork lease ``running`` with its slot held
(owner: this live driver process, so no sweep frees it). Its transcript, at the
path the router reads (CLAUDE_CONFIG_DIR/projects/<slug>/<session>/subagents/
agent-<host_id>.jsonl), ends at a Bash tool call 57 minutes old with no result,
the Prody n1200 shape. The host's own gate timer (first tick 60 s after start)
queues the blocker check; the side actor answers with one stall notice of kind
"silent". Read back:
1. the gate-check event names the silent fork (lease, last record, minutes);
2. the decision commits after the router's own re-read and the board records
   that silence as told;
3. main's conversation gets one "Ciel side: stalled Work fork" notice with the
   silence, what was checked and what is needed;
4. the fork is not listed again (silent_forks on the final ledger and board,
   and every later gate check).

usage: python run_side_silent_real.py <tree under test> <evidence dir>
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
HOUR = 3600.0
MINUTE = 60.0


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
OWNED = Path(tempfile.mkdtemp(prefix="side-silent-", dir="D:/Temp"))
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
# The MCP backend runs in this process with no router fork-turn watch, so the
# fork transcript resolver falls back to CLAUDE_CONFIG_DIR and the lease's
# workspace repo: both point at the owned scratch, never the real profile.
os.environ["CLAUDE_CONFIG_DIR"] = str(CFG)
sys.path.insert(0, str(SNAPSHOT))
from ciel_runtime_support import runtime_paths  # noqa: E402
from ciel_runtime_support import work_plan_tree as tree  # noqa: E402
from ciel_runtime_support import work_gate_check as gate  # noqa: E402
from ciel_runtime_support import work_silent_forks as silent  # noqa: E402
from ciel_runtime_support.fork_turn_watch import subagents_dir  # noqa: E402
from ciel_runtime_support.workspace_router_selection import _process_identity  # noqa: E402

# Isolation accepts only a config directory under the system temp root; any
# other is replaced by the process's own temporary profile (runtime_paths). The
# MCP backend runs in this process, so the store, the inbox and the seeded
# ledger all use the directory it actually chose.
CIEL_CFG = Path(runtime_paths.CONFIG_DIR)
env["CIEL_RUNTIME_CONFIG_DIR"] = str(CIEL_CFG)
from ciel_runtime_support.windows_conpty import WindowsConPtySession  # noqa: E402
from ciel_runtime_support.work_parallel_host import host_token  # noqa: E402
from ciel_runtime_support.work_parallel import default_policy  # noqa: E402
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
    "work_side_intake": {"enabled": True, "type": "btw_with_mcp", "session": "fresh"},
    "work_gate_check": {"minutes": 1},
    "current_provider": "anthropic",
    "providers": {"anthropic": {"current_model": MAIN_MODEL, "custom_models": []}},
}
STORE = session_work_plan_store(CIEL_CFG, IDENTITY)
OLD = time.time() - 2 * HOUR
_, GOAL = STORE.transaction(lambda d: tree.add_node(d, None, "Silent fork check", priority="P1", now=OLD))
_, ITEM = STORE.transaction(lambda d: tree.add_node(d, GOAL, "run the playwright probe", now=OLD))
LEASE = "silent" + uuid.uuid4().hex[:10]
AGENT = "a" + uuid.uuid4().hex[:16]
LEDGER = {"schema": 1, "version": 1, "policy": default_policy(), "launches": [], "leases": {LEASE: {
    "id": LEASE, "scope": IDENTITY, "item_id": ITEM, "priority": "P1", "created_at": OLD, "role": "worker",
    "mode": "fork", "plan_version": 1, "state": "running", "host_id": AGENT,
    "slot": True, "usage": 1, "artifact": None, "rework": 0, "evidence": None, "idle_proof": None,
    "cleanup_proof": None, "workspace": {"path": str(WS), "repo": str(WS)},
    "owner": {"session_pid": os.getpid(), "identity": _process_identity(os.getpid())}}}}
(CIEL_CFG / "work-parallel.json").write_text(json.dumps(LEDGER), encoding="utf-8")
INBOX = SideIntakeInbox(CIEL_CFG, IDENTITY)
# The fork's transcript stopped at a Bash call 57 minutes ago (Prody n1200 shape).
STOPPED = time.time() - 57 * MINUTE
SUBAGENTS = subagents_dir(CFG, WS, SESSION)
SUBAGENTS.mkdir(parents=True, exist_ok=True)
TRANSCRIPT = SUBAGENTS / f"agent-{AGENT}.jsonl"
STOPPED_ISO = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(STOPPED))
TRANSCRIPT.write_text(json.dumps({"type": "user", "uuid": "u0", "timestamp": STOPPED_ISO, "agentId": AGENT,
                                  "message": {"role": "user", "content": "run the probe"}}) + "\n"
                      + json.dumps({"type": "assistant", "uuid": "u1", "timestamp": STOPPED_ISO, "agentId": AGENT,
                                    "message": {"role": "assistant", "stop_reason": None, "content": [
                                        {"type": "tool_use", "id": "toolu_probe", "name": "Bash",
                                         "input": {"command": "node probe.js"}}]}}) + "\n", encoding="utf-8")
os.utime(TRANSCRIPT, (STOPPED, STOPPED))
MARK = "lease " + LEASE + " silent since " + STOPPED_ISO


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
    response = dispatch_work_tool(request_id, name, args, load_config=snapshot_config, save_config=save_config,
                                  state_dir=CIEL_CFG, respond=channel_mcp_tool_response)
    entry = {"at": round(time.time(), 2), "name": name, "error": bool(response["result"].get("isError"))}
    if name == "work_side_gate_check" and "host_report" not in args:
        try:
            value = json.loads(response["result"]["content"][0]["text"])
            entry["gate"] = {k: value.get(k) for k in ("queued", "reason", "stalled", "parked", "gates",
                                                       "host_only")}
        except Exception:
            entry["gate"] = "unparsed"
    if name == "work_side_resolve":
        try:
            value = json.loads(response["result"]["content"][0]["text"])
            entry["result"] = {k: (value.get("result") or {}).get(k) for k in ("stall_notices", "unnoticed_stalls")}
        except Exception:
            entry["result"] = "unparsed"
    with LOCK:
        MCP_LOG.append(entry)
    return response


CONTROLLER = ChannelMcpHttpController(ChannelMcpHttpServices(
    version="side-stall-real", tool_schemas=lambda: work_tool_schemas(enabled=True),
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
MAIN_TEXTS = []
GATE_TEXTS = []
SECTION = "응답 없이 멈춘 포크 (호스트가"


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


def emit_text(handler, body, text):
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
        # The event's fact section header (the gate_actor rules also say
        # "응답 없이 멈춘 포크 (이벤트의 ...", so match the host-facts header; run1 matched the rules).
        gate_event = SECTION in text and "Ciel 게이트 재확인" in text
        with LOCK:
            SIDE.append({"at": round(time.time(), 2), "agent": agent[:16], "gate_event": gate_event})
            if not agent:
                MAIN_TEXTS.append(text[-4000:])
            if agent and gate_event:
                start = text.find(SECTION)
                GATE_TEXTS.append(text[start:start + 1200])
        if agent and gate_event:
            decision = {"action": "answer", "kind": "status",
                        "summary": ITEM + " 유지: fork transcript silent since " + STOPPED_ISO,
                        "reply": "silent fork reported",
                        "stall_notices": [{"item_id": ITEM, "lease_id": LEASE, "kind": "silent", "waiting_on": "",
                                           "checked": "item progress and note record nothing after the probe",
                                           "needed": "check the fork, then resume or stop it"}]}
            return emit_text(self, body, json.dumps({"understanding": "one silent running fork",
                                                     "decision": decision,
                                                     "expected_version": STORE.read()["version"]}))
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
mod_digests = {name: sha(PRODUCT / "hooks" / name) for name in ("register.js", "side-intake.js", "texts.generated.js")}
result = {"kind": "silent running-fork notice, real load", "claude_version": version, "mod_sha256": mod_digests,
          "python_sha256": {name: sha(SNAPSHOT / "ciel_runtime_support" / name) for name in
                            ("work_gate_check.py", "work_silent_forks.py", "side_work_decision.py",
                             "side_intake_mcp.py", "work_mcp_tools.py", "work_texts.py")},
          "board": {"goal": GOAL, "item": ITEM, "lease": LEASE, "agent": AGENT, "stopped_at": STOPPED_ISO},
          "steps": {}}
DEBUG = OWNED / "claude-debug.txt"
proc = None
t0 = time.time()


def debug_text() -> str:
    return DEBUG.read_text(encoding="utf-8", errors="replace") if DEBUG.exists() else ""


def wait_for(pattern: str, timeout: float):
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


def stall_resolved():
    with LOCK:
        return any(e["name"] == "work_side_resolve" and isinstance(e.get("result"), dict)
                   and e["result"].get("stall_notices") for e in MCP_LOG)


try:
    os.chdir(WS)
    proc = WindowsConPtySession([CLI, "--permission-mode", "default", "--plugin-dir", str(PRODUCT),
                                 "--plugin-dir", str(GATE_PLUGIN), "--strict-mcp-config", "--mcp-config",
                                 json.dumps(mcp_config), "--session-id", SESSION, "--debug-file", str(DEBUG)],
                                env, log=lambda *_: None, mirror_output=False, forward_stdin=False)
    loaded = wait_for(r"hooks module ciel-work@\S+ (loaded|failed to load)[^\n]*", 90)
    result["steps"]["mod_load_line"] = loaded.group(0)[:200] if loaded else None
    settled = wait_for(r"ciel-work@\w+[^\n]*?session\.start settled in ([\d.]+)ms", 90)
    result["steps"]["session_start_settled_ms"] = float(settled.group(1)) if settled else None
    end = time.time() + 240
    while time.time() < end and not stall_resolved():
        time.sleep(1)
    result["steps"]["stall_resolved_s"] = round(time.time() - t0, 1) if stall_resolved() else None
    resolved_at = time.time()
    # Main hears of it: the appended notice reaches main's next request.
    end = time.time() + 120
    while time.time() < end and not any("Ciel side: stalled Work fork" in text for text in MAIN_TEXTS):
        time.sleep(1)
    result["steps"]["main_saw_notice_s"] = round(time.time() - t0, 1)
    # Later gate checks (1-minute interval, turn ends) must not list the fork again.
    end = max(time.time() + 75, resolved_at + 150)
    while time.time() < end:
        time.sleep(1)
except Exception as error:
    result["failure"] = type(error).__name__ + ": " + str(error)[:500]
finally:
    if proc is not None:
        try:
            screen = proc.output_tail().decode("utf-8", errors="replace")
            screen = re.sub(r"\x1b\[[0-9;?<>=]*[ -/]*[@-~]", "", screen)
            (OUT / "console-tail.txt").write_text(screen[-6000:], encoding="utf-8")
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
    mask = lambda line: re.sub(r"[A-Za-z]:[\\/][^\s\"']+", "<path>", line)
    (OUT / "debug-side-lines.txt").write_text("\n".join(
        mask(line)[:400] for line in text.splitlines()
        if "Gate re-check" in line or "Side SSE" in line or "Ciel side" in line or "agent_completion" in line
        or re.search(r"ciel-work@\S+ (loaded|failed|session\.start|unloaded)", line))[:60000], encoding="utf-8")
    gate_calls = [e for e in MCP_LOG if e["name"] == "work_side_gate_check" and "gate" in e]
    queued = [e for e in gate_calls if isinstance(e["gate"], dict) and e["gate"].get("queued")]
    resolve_at = next((e["at"] for e in MCP_LOG if e["name"] == "work_side_resolve" and isinstance(e.get("result"), dict)
                       and e["result"].get("stall_notices")), None)
    later = [e for e in gate_calls if resolve_at is not None and e["at"] > resolve_at]
    notices = [t for t in MAIN_TEXTS if "Ciel side: stalled Work fork" in t]
    notice_text = notices[-1][notices[-1].find("Ciel side: stalled Work fork"):][:1200] if notices else ""
    node = next((n for n in STORE.read()["nodes"] if n["id"] == ITEM), {})
    ledger_after = json.loads((CIEL_CFG / "work-parallel.json").read_text(encoding="utf-8"))
    lease_after = ledger_after["leases"].get(LEASE) or {}
    after = silent.silent_forks(ledger_after["leases"], IDENTITY, STORE.read(), directory=SUBAGENTS)
    result["mcp_calls"] = [{"after_s": round(e["at"] - t0, 1), **{k: v for k, v in e.items() if k != "at"}}
                           for e in MCP_LOG if e["name"] not in ("work_parallel_status", "work_status")][:200]
    result["gate_event_section"] = GATE_TEXTS[-1] if GATE_TEXTS else ""
    result["main_notice"] = notice_text
    result["notice_count"] = len({t[t.find("Ciel side: stalled Work fork"):][:200] for t in notices})
    result["told_note_line"] = next((line for line in str(node.get("note") or "").splitlines()
                                     if gate.STALL_TOLD in line), "")
    result["lease_after"] = {k: lease_after.get(k) for k in ("state", "slot")}
    result["silent_after"] = [row["lease_id"] for row in after]
    result["later_gate_checks"] = [e["gate"] for e in later]
    # Claude Code itself laid out this session's transcript under the same slug rule.
    result["engine_session_transcript_at_slug"] = (SUBAGENTS.parent.parent / (SESSION + ".jsonl")).is_file()
    checks = {
        "mod_loaded": bool(result["steps"].get("mod_load_line")) and "failed" not in str(result["steps"].get("mod_load_line")),
        "gate_event_named_the_fork": any(LEASE in (e["gate"].get("stalled") or []) for e in queued),
        "event_text_has_silence_facts": LEASE in result["gate_event_section"]
                                        and "tool_use:Bash" in result["gate_event_section"],
        "stall_decision_committed": stall_resolved(),
        "board_records_told_silence": MARK in result["told_note_line"],
        "main_got_one_notice": result["notice_count"] == 1,
        "notice_says_silence_checked_and_needed": STOPPED_ISO in notice_text and "tool_use:Bash" in notice_text
                                                  and "item progress and note record nothing" in notice_text
                                                  and "check the fork, then resume or stop it" in notice_text,
        "lease_still_running": result["lease_after"] == {"state": "running", "slot": True},
        "not_listed_again": result["silent_after"] == [] and all(
            LEASE not in ((g or {}).get("stalled") or []) for g in result["later_gate_checks"] if isinstance(g, dict)),
    }
    result["checks"] = checks
    result["passed"] = all(checks.values()) and "failure" not in result
    profile_after = live_profile()
    result["profile_changed"] = sorted(k for k in set(PROFILE_BEFORE) | set(profile_after)
                                       if PROFILE_BEFORE.get(k) != profile_after.get(k))
    result["profile_unchanged"] = not [k for k in result["profile_changed"] if k != "~/.claude.json"]
    result["passed"] = result["passed"] and result["profile_unchanged"]
    os.chdir(SOURCE)
    removed = False

    def writable(remove, path, _info):
        os.chmod(path, 0o600)
        remove(path)

    for _ in range(30):
        try:
            shutil.rmtree(OWNED, onerror=writable)
            removed = True
            break
        except OSError:
            time.sleep(2)
    result["scratch_removed"] = removed
    dumped = json.dumps(result, ensure_ascii=False, indent=2)
    for raw, label in ((str(OWNED), "<owned>"), (str(Path.home()), "<home>")):
        dumped = dumped.replace(json.dumps(raw)[1:-1], label).replace(raw, label)
    dumped = dumped.replace(TOKEN, "<token>")
    (OUT / "result.json").write_text(dumped, encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "checks": checks, "steps": result["steps"],
                      "main_notice": notice_text[:600], "failure": result.get("failure"),
                      "scratch_removed": removed, "profile_unchanged": result["profile_unchanged"]},
                     ensure_ascii=False, indent=1))
if not result["passed"]:
    raise SystemExit(1)
