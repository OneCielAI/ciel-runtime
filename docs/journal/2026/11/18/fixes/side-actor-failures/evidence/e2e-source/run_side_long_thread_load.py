"""Owned real-claude.exe load of the side-actor failures hotfix (2026-11-18).

Real Claude Code engine (the installed claude.exe) loads the packaged ciel-work
and ciel-work-gate plugins from a snapshot of the tree under test. Owned stand-ins:
- the router MCP backend (the real Work and side tools, dispatch_work_tool);
- a stub Walkie MCP server with a 2,400-reply thread (threads.read pages of 200);
- a scripted Messages API. The side fork's first answer carries no decision
  JSON; after the host's corrective message it answers with a valid decision.

It shows, from the engine's own traffic (user design 2026-11-18): an event on a
2,400-reply thread is decided with no host Walkie read at all (the fork gets the
event and its source_ref, no thread), the fork is asked once to correct its
answer, and the decision commits.

No real credentials; every directory is owned scratch under D:/Temp, removed at
the end. Only this driver's own process tree is killed. The live ~/.claude
settings, ~/.claude.json and the installed ciel-runtime bin are hashed before
and after; any change fails the run.

usage: python run_side_long_thread_load.py <tree under test> <evidence dir>
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
REPLIES = 2400
CONVERSATION = "34886995-4a66-4527-a618-814e9cb0a5bf"


def sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def live_profile() -> dict:
    # ~/.claude.json is rewritten by every live Claude Code session all the time;
    # it is checked separately for any mention of this run (see below).
    home = Path.home()
    paths = [home / ".claude" / "settings.json"]
    bin_dir = home / "AppData/Local/ciel-runtime/bin"
    if bin_dir.is_dir():
        paths += sorted(p for p in bin_dir.iterdir() if p.is_file())
    return {str(i): sha(p) for i, p in enumerate(paths)}


PROFILE_BEFORE = live_profile()
OWNED = Path(tempfile.mkdtemp(prefix="side-load-", dir="D:/Temp"))
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
from ciel_runtime_support.side_intake_service import receive as side_receive  # noqa: E402
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
    "work_gate_check": {"minutes": 0},
    "current_provider": "anthropic",
    "providers": {"anthropic": {"current_model": MAIN_MODEL, "custom_models": []}},
}
STORE = session_work_plan_store(CIEL_CFG, IDENTITY)
STORE.transaction(lambda d: tree.set_plan(d, {"title": "Long-thread goal", "children": [{"title": "summary"}]}, None))
EVENT = json.dumps({"specversion": "1.0", "type": "com.ciel.walkie.message", "id": "evt-long-1",
                    "source": "walkie/conversations/" + CONVERSATION,
                    "data": {"event_type": "message.created", "conversation": CONVERSATION, "message_id": "45",
                             "thread_root_id": "40", "reply_to_message_id": "40",
                             "text": "LONG-THREAD-EVENT where does this thread stand?"}})
RECEIPT = side_receive(CIEL_CFG, IDENTITY, EVENT, "walkie:evt-long-1", admit=False,
                       route={"source_kind": "external_event"})["receipt_id"]


def snapshot_config():
    with LOCK:
        return json.loads(json.dumps(CONFIG))


def save_config(config):
    with LOCK:
        CONFIG.clear()
        CONFIG.update(json.loads(json.dumps(config)))


MCP_LOG = []


def router_response(request_id, params):
    name, args = params.get("name"), params.get("arguments") or {}
    if name not in WORK_TOOL_NAMES:
        return channel_mcp_tool_response(request_id, "unknown", True)
    response = dispatch_work_tool(request_id, name, args, load_config=snapshot_config, save_config=save_config,
                                  state_dir=CIEL_CFG, respond=channel_mcp_tool_response)
    entry = {"at": round(time.time(), 2), "name": name}
    if name == "work_side_renew":
        entry["source_bounded"] = args.get("source_bounded")
    if name == "work_side_understanding":
        entry["state"] = args.get("state")
    if name in ("work_side_understanding", "work_side_resolve", "work_side_fail"):
        try:
            entry["error"] = bool(response.get("result", {}).get("isError")) or "error" in response
        except Exception:
            entry["error"] = "unparsed"
    if name == "work_side_fail":
        entry["category"] = args.get("category")
    with LOCK:
        MCP_LOG.append(entry)
    return response


WALKIE_LOG = []
ROOT_ROW = {"id": "40", "conversation_id": CONVERSATION, "text": "thread root", "thread_root_id": None}


def walkie_page(after):
    start = max(41, int(after) + 1)
    end = 41 + REPLIES
    return [{"id": str(n), "conversation_id": CONVERSATION, "thread_root_id": "40",
             "reply_to_message_id": "40", "text": "reply %d" % n} for n in range(start, min(end, start + 200))]


WALKIE_TOOLS = [{"name": n, "description": "stub Walkie " + n, "inputSchema": {"type": "object", "properties": {}}}
                for n in ("walkie_read_messages", "walkie_call_tool", "walkie_send_message")]


def walkie_response(request_id, params):
    name, args = params.get("name"), params.get("arguments") or {}
    with LOCK:
        WALKIE_LOG.append({"name": name, "operation": args.get("operation_id"),
                           "after": (args.get("arguments") or {}).get("after")})
    if name == "walkie_read_messages":
        return channel_mcp_tool_response(request_id, json.dumps({"messages": [ROOT_ROW]}))
    if name == "walkie_call_tool" and args.get("operation_id") == "threads.read":
        return channel_mcp_tool_response(request_id, json.dumps(walkie_page((args.get("arguments") or {}).get("after", "0"))))
    return channel_mcp_tool_response(request_id, json.dumps({"ok": True}))


def controller(tools, response, version):
    return ChannelMcpHttpController(ChannelMcpHttpServices(
        version=version, tool_schemas=tools, tool_call_response=response, write_json=HttpResponseAdapter.write_json,
        write_accepted=HttpResponseAdapter.write_accepted, log=lambda *_: None))


ROUTER = controller(lambda: work_tool_schemas(enabled=True), router_response, "side-load-router")
WALKIE = controller(lambda: WALKIE_TOOLS, walkie_response, "side-load-walkie")


def mcp_handler(ctrl):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def do_GET(self):
            if not ctrl.get(self, self.path.split("?")[0]):
                self.send_response(404); self.send_header("content-length", "0"); self.end_headers()

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
            if not ctrl.post(self, self.path.split("?")[0], body):
                self.send_response(404); self.send_header("content-length", "0"); self.end_headers()
    return Handler


SIDE = []
SEEN = {key: threading.Event() for key in ("first_side", "corrected_side")}


def last_user_text(body):
    for message in reversed(body.get("messages") or []):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        return "\n".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")
    return ""


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


def source_facts(text):
    """The bounded-thread facts of the spawn prompt (JSON), counts only."""
    try:
        start = text.index("{")
        value = json.loads(text[start:text.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
        return {"parsed": False}
    return {"parsed": True, "event_id": value.get("event_id"), "source_context": value.get("source_context"),
            "source_ref": value.get("source_ref"), "has_thread": "source_message" in value,
            "has_target_message": "target_message" in value}


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
        if agent and "LONG-THREAD-EVENT" in text and "parent_session_id" in text:
            with LOCK:
                SIDE.append({"at": round(time.time(), 2), "kind": "spawn", **source_facts(text)})
            SEEN["first_side"].set()
            # A real failure shape (Andy): prose with no decision JSON.
            return emit_text(self, body, "I read the thread; it is about the export timeline.")
        if agent and "your answer was refused" in text:
            with LOCK:
                SIDE.append({"at": round(time.time(), 2), "kind": "correction",
                             "names_error": "side answer carries no decision JSON" in text})
            SEEN["corrected_side"].set()
            decision = {"action": "answer", "kind": "status", "summary": "thread status summarized",
                        "reply": "The thread is still discussing the export timeline."}
            return emit_text(self, body, json.dumps({"understanding": "You asked where this long thread stands.",
                                                     "decision": decision,
                                                     "expected_version": STORE.read()["version"]}))
        emit_text(self, body, "ack")


servers = [ThreadingHTTPServer(("127.0.0.1", 0), mcp_handler(ROUTER)),
           ThreadingHTTPServer(("127.0.0.1", 0), mcp_handler(WALKIE)),
           ThreadingHTTPServer(("127.0.0.1", 0), Messages)]
for server in servers:
    threading.Thread(target=server.serve_forever, daemon=True).start()
router_server, walkie_server, api_server = servers
env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{api_server.server_port}"
mcp_config = {"mcpServers": {
    "ciel-runtime-router": {"type": "http", "url": f"http://127.0.0.1:{router_server.server_port}/ca/mcp"},
    "walkie": {"type": "http", "url": f"http://127.0.0.1:{walkie_server.server_port}/ca/mcp"}}}
PRODUCT = OWNED / "plugin-ciel-work"
shutil.copytree(SNAPSHOT / "ciel_runtime_support/claude_mods/ciel-work", PRODUCT)
GATE_PLUGIN = OWNED / "plugin-ciel-work-gate"
shutil.copytree(SNAPSHOT / "ciel_runtime_support/claude_mods/ciel-work-gate", GATE_PLUGIN)
(CFG / ".claude.json").write_text(json.dumps({"hasCompletedOnboarding": True, "theme": "dark", "projects": {
    str(WS): {"hasTrustDialogAccepted": True}, str(WS).replace("\\", "/"): {"hasTrustDialogAccepted": True}}}),
    encoding="utf-8")
(CFG / "settings.json").write_text(json.dumps({"permissions": {"allow": ["mcp__ciel-runtime-router", "mcp__walkie"],
                                                               "deny": []}}), encoding="utf-8")
version = subprocess.run([CLI, "--version"], capture_output=True, text=True, timeout=60).stdout.strip()
mod_digests = {name: sha(PRODUCT / "hooks" / name) for name in ("register.js", "side-intake.js")}
result = {"kind": "side-actor failures hotfix real load", "claude_version": version, "mod_sha256": mod_digests,
          "source_matches_snapshot": all(sha(SOURCE / "ciel_runtime_support/claude_mods/ciel-work/hooks" / n) == d
                                         for n, d in mod_digests.items()), "steps": {}}
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


def receipt_row():
    return SideIntakeInbox(CIEL_CFG, IDENTITY)._load(RECEIPT) or {}


try:
    os.chdir(WS)
    proc = WindowsConPtySession([CLI, "--permission-mode", "default", "--plugin-dir", str(PRODUCT),
                                 "--plugin-dir", str(GATE_PLUGIN), "--strict-mcp-config", "--mcp-config",
                                 json.dumps(mcp_config), "--session-id", SESSION, "--debug-file", str(DEBUG)],
                                env, log=lambda *_: None, mirror_output=False, forward_stdin=False)
    loaded = wait_for(r"hooks module ciel-work@\S+ (loaded|failed to load)[^\n]*", 90)
    result["steps"]["mod_load_line"] = loaded.group(0)[:200] if loaded else None
    result["steps"]["first_side_request"] = SEEN["first_side"].wait(150)
    result["steps"]["corrected_side_request"] = SEEN["corrected_side"].wait(120)
    end = time.time() + 60
    while time.time() < end and receipt_row().get("stage") != "processed":
        time.sleep(1)
    row = receipt_row()
    result["steps"]["receipt"] = {"stage": row.get("stage"), "source_bounded": row.get("source_bounded"),
                                  "action": (row.get("result") or {}).get("action"),
                                  "failure": (row.get("side_failure") or {}).get("category")}
except Exception as error:
    result["failure"] = type(error).__name__ + ": " + str(error)[:500]
finally:
    if proc is not None:
        if proc.poll() is None:
            done = subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, text=True)
            result["kill"] = (done.stdout + done.stderr).strip()[:300]
        try:
            proc.close()
        except Exception:
            pass
    for server in servers:
        server.shutdown(); server.server_close()
    text = debug_text()
    result["side_requests"] = [{"after_s": round(r["at"] - t0, 1), **{k: v for k, v in r.items() if k != "at"}}
                               for r in SIDE]
    result["walkie_calls"] = {"read_messages": sum(1 for c in WALKIE_LOG if c["name"] == "walkie_read_messages"),
                              "threads_read": sum(1 for c in WALKIE_LOG if c["operation"] == "threads.read"),
                              "sends": sum(1 for c in WALKIE_LOG if c["name"] == "walkie_send_message")}
    result["router"] = [{k: v for k, v in e.items() if k != "at"} for e in MCP_LOG
                        if e["name"] in ("work_side_renew", "work_side_understanding", "work_side_resolve", "work_side_fail")]
    result["unloaded_or_failed"] = [line[:200] for line in text.splitlines()
                                    if "ciel-work was unloaded" in line or "failed to load" in line]
    result["debug_side_lines"] = [re.sub(r"[A-Za-z]:[\\/][^\s\"']+", "<path>", line)[:300] for line in text.splitlines()
                                  if "Ciel side" in line or "$.session.send" in line][:30]
    first = next((r for r in SIDE if r["kind"] == "spawn"), {})
    receipt = result["steps"].get("receipt") or {}
    checks = {
        "mod_loaded": "loaded" in str(result["steps"].get("mod_load_line")) and "failed" not in str(result["steps"].get("mod_load_line")),
        "no_unload_or_load_failure": not result["unloaded_or_failed"],
        "no_walkie_read_before_decision": result["walkie_calls"]["threads_read"] == 0
                                          and result["walkie_calls"]["read_messages"] == 0,
        "fork_got_event_and_source_ref": first.get("source_context") == "event_only" and not first.get("has_thread")
                                         and (first.get("source_ref") or {}).get("target_message_id") == "45",
        "fork_corrected_once": sum(1 for r in SIDE if r["kind"] == "correction") == 1
                               and all(r.get("names_error") for r in SIDE if r["kind"] == "correction"),
        "decision_committed": receipt.get("stage") == "processed" and receipt.get("action") == "answer",
        "no_failure_counted": not any(e["name"] == "work_side_fail" for e in MCP_LOG),
    }
    result["checks"] = checks
    try:
        live_json = (Path.home() / ".claude.json").read_text(encoding="utf-8", errors="replace")
    except OSError:
        live_json = ""
    result["live_claude_json_mentions_run"] = SESSION in live_json or str(OWNED) in live_json         or str(OWNED).replace("\\", "/") in live_json
    result["profile_unchanged"] = live_profile() == PROFILE_BEFORE and not result["live_claude_json_mentions_run"]
    result["passed"] = all(checks.values()) and "failure" not in result and result["profile_unchanged"]
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
    dumped = json.dumps(result, ensure_ascii=False, indent=2).replace(str(OWNED), "<owned>").replace(str(Path.home()), "<home>")
    (OUT / "result.json").write_text(dumped.replace(TOKEN, "<token>"), encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "checks": checks, "steps": result["steps"],
                      "walkie_calls": result["walkie_calls"], "side_requests": result["side_requests"],
                      "router": result["router"][:12], "failure": result.get("failure"),
                      "scratch_removed": removed, "profile_unchanged": result["profile_unchanged"]},
                     ensure_ascii=False, indent=1))
if not result["passed"]:
    raise SystemExit(1)
