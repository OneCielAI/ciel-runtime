"""Owned real-claude.exe E2E: idle gate re-check and live-item relay.

Real Claude Code engine + packaged ciel-work and ciel-work-gate plugins, against an
owned MCP backend (the real Work and side tools) and a scripted Messages stand-in.

1. Gate re-check: the board has one recorded wait. After main's turn ends (no
   Work fork runs), the ciel-work timer asks the router (work_side_gate_check);
   the router queues one durable side event; a real native side fork receives it
   and answers update + release_waits; the host commits it, clears the wait and
   tells main (appended notice + wake prompt).
2. Relay: a web-chat message about the item in progress is put into the durable
   side inbox. The side fork answers update + relay: true; main receives the
   original message with the "decide whether it matters" ask. (Delivery to a
   running Work fork on the item is covered by tests/side_relay_harness.mjs.)

The gate interval is 1 minute here so a run takes a few minutes. Evidence is
structural (markers, states, counts). Only the owned process tree is killed
(p0_common.Guard).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import p0_common as common  # noqa: E402

SOURCE = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
OUT.mkdir(parents=True, exist_ok=False)
CLI = str(Path.home() / ".local/bin/claude.exe")
GUARD = common.Guard(OUT)
OWNED = Path(tempfile.mkdtemp(prefix="ciel-gate-relay-e2e-"))
SNAPSHOT = common.snapshot_source(SOURCE, OWNED)
CFG, CIEL_CFG, WS = (OWNED / name for name in ("claude", "ciel", "workspace"))
for path in (CFG, CIEL_CFG, WS):
    path.mkdir(parents=True, exist_ok=True)
for args in (["init", "-q"], ["config", "user.name", "e2e"], ["config", "user.email", "e2e@localhost"]):
    subprocess.run(["git", "-C", str(WS), *args], check=True, capture_output=True)
(WS / "base.txt").write_text("base\n", encoding="utf-8")
subprocess.run(["git", "-C", str(WS), "add", "-A"], check=True, capture_output=True)
subprocess.run(["git", "-C", str(WS), "commit", "-q", "-m", "base"], check=True, capture_output=True)
SESSION = str(uuid.uuid4())
IDENTITY = "claude:" + SESSION
MAIN_MODEL = "fixture-main"
WAIT_TEXT = "CI run 4242 green"
RELAY_TEXT = "RELAY-MSG: the CSV delimiter must be a semicolon, not a comma"
env = common.stripped_env()
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
    "work_gate_check": {"minutes": 1},
    "current_provider": "anthropic",
    "providers": {"anthropic": {"current_model": MAIN_MODEL, "custom_models": []}},
}
STORE = session_work_plan_store(CIEL_CFG, IDENTITY)
_, GOAL = STORE.transaction(lambda d: tree.set_plan(d, {"title": "EXPORT-GOAL ship CSV export", "children": [
    {"title": "csv writer"}, {"title": "deploy"}]}, None))
NODES = {n["title"]: n["id"] for n in STORE.read()["nodes"]}
CSV, DEPLOY = NODES["csv writer"], NODES["deploy"]
STORE.transaction(lambda d: tree.update_node(d, DEPLOY, waiting_on=WAIT_TEXT))
STORE.transaction(lambda d: tree.update_node(d, CSV, status="in_progress"))


def snapshot_config():
    with LOCK:
        return json.loads(json.dumps(CONFIG))


def save_config(config):
    with LOCK:
        CONFIG.clear()
        CONFIG.update(json.loads(json.dumps(config)))


MCP_LOG = []
SENT_REPLIES = []
SEND_SCHEMA = {"name": "send_message", "description": "fixture web chat reply",
               "inputSchema": {"type": "object", "properties": {}}}


def tool_response(request_id, params):
    name, args = params.get("name"), params.get("arguments") or {}
    if name == "send_message":
        SENT_REPLIES.append({k: args.get(k) for k in ("channel", "reply_token")})
        return channel_mcp_tool_response(request_id, json.dumps({"ok": True}))
    if name not in WORK_TOOL_NAMES:
        return channel_mcp_tool_response(request_id, "unknown", True)
    response = dispatch_work_tool(request_id, name, args, load_config=snapshot_config, save_config=save_config,
                                  state_dir=CIEL_CFG, respond=channel_mcp_tool_response)
    entry = {"at": round(time.time(), 1), "name": name}
    if name == "work_side_gate_check":
        try:
            entry["result"] = json.loads(response["result"]["content"][0]["text"])
        except Exception:
            entry["result"] = "unparsed"
    if name in ("work_side_resolve", "work_side_wake"):
        entry["state"] = args.get("state")
        entry["action"] = (args.get("decision") or {}).get("action")
    MCP_LOG.append(entry)
    return response


CONTROLLER = ChannelMcpHttpController(ChannelMcpHttpServices(
    version="gate-relay-e2e", tool_schemas=lambda: [*work_tool_schemas(enabled=True), SEND_SCHEMA],
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


MAIN = []      # main requests: marker, wake, carries notice texts
SIDE = []      # side fork requests: kind, agent present
SEEN = {key: threading.Event() for key in ("turn1", "gate_side", "gate_main", "relay_side", "relay_main")}


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


def decision(payload):
    return json.dumps({"decision": payload, "expected_version": STORE.read()["version"]})


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
        whole = raw.decode("utf-8", "replace")
        if agent:
            if "Ciel gate re-check" in text:
                SIDE.append({"kind": "gate", "agent": True})
                SEEN["gate_side"].set()
                return emit_text(self, body, decision({
                    "action": "update", "kind": "request", "item_id": DEPLOY, "summary": "CI run 4242 passed",
                    "note": "fixture: run 4242 conclusion success", "reply": "released",
                    "release_waits": [{"id": DEPLOY, "waiting_on": WAIT_TEXT}]}))
            if "RELAY-MSG" in text:
                SIDE.append({"kind": "relay", "agent": True})
                SEEN["relay_side"].set()
                return emit_text(self, body, decision({
                    "action": "update", "kind": "request", "item_id": CSV, "summary": "CSV delimiter is a semicolon",
                    "note": "delimiter must be ;", "reply": "전달했습니다", "relay": True}))
            SIDE.append({"kind": "other", "agent": True})
            return emit_text(self, body, "fork ok")
        row = {"turn_one": "TURN-ONE" in text, "wake": "Ciel side wake" in text,
               "release_wake": 'beginning "Ciel side released recorded wait"' in text,
               "relay_wake": 'beginning "Ciel side relay for Work item"' in text,
               "release_notice": "Ciel side released recorded wait " + DEPLOY in whole,
               "relay_notice": "Ciel side relay for Work item " + CSV in whole,
               "relay_original": RELAY_TEXT in whole,
               "relay_ask": "Decide whether this matters to your current work" in whole}
        with LOCK:
            MAIN.append(row)
        if row["turn_one"]:
            SEEN["turn1"].set()
        if row["release_notice"]:
            SEEN["gate_main"].set()
        if row["relay_notice"] and row["relay_original"]:
            SEEN["relay_main"].set()
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
result = {"kind": "gate re-check + relay E2E", "claude_version": version,
          "snapshot_manifest": common.manifest(SNAPSHOT, SOURCE, "release-candidate"), "steps": {}}
proc = root_ident = None
kills = []
DEBUG = OWNED / "claude-debug.txt"
t0 = time.time()
try:
    os.chdir(WS)
    proc = WindowsConPtySession([CLI, "--permission-mode", "default", "--plugin-dir", str(PRODUCT),
                                 "--plugin-dir", str(GATE_PLUGIN), "--strict-mcp-config", "--mcp-config",
                                 json.dumps(mcp_config), "--session-id", SESSION, "--debug-file", str(DEBUG)],
                                env, log=lambda *_: None, mirror_output=False, forward_stdin=False)
    root_ident = GUARD.register_root(proc.pid)
    result["mod_load"] = common.wait_mod_loaded(DEBUG, 60)
    result["gate_load"] = common.plugin_load_verdict(DEBUG, "ciel-work-gate")
    if not result["mod_load"]["ok"]:
        raise common.ModNotLoaded("ciel-work did not load")
    proc.write(b"TURN-ONE reply briefly.\r")
    result["steps"]["turn1"] = SEEN["turn1"].wait(120)
    turn_end = time.time()
    # 1. The timer's first tick is 60 s after the turn ends.
    result["steps"]["gate_side_fork"] = SEEN["gate_side"].wait(180)
    result["steps"]["gate_side_after_s"] = round(time.time() - turn_end, 1)
    result["steps"]["gate_main_notice"] = SEEN["gate_main"].wait(120)
    time.sleep(3)
    doc = STORE.read()
    result["steps"]["deploy_wait_after"] = next(n for n in doc["nodes"] if n["id"] == DEPLOY).get("waiting_on", "")
    # 2. A follow-up message about the item in progress.
    receipt = side_receive(CIEL_CFG, IDENTITY, RELAY_TEXT, "webchat:relay-1", admit=False,
                           route={"source_kind": "web_chat", "channel": "web", "reply_token": "fixture-reply",
                                  "sender_id": "web"})
    result["steps"]["relay_receipt_stage"] = receipt.get("stage")
    result["steps"]["relay_side_fork"] = SEEN["relay_side"].wait(120)
    result["steps"]["relay_main_notice"] = SEEN["relay_main"].wait(120)
    time.sleep(3)
    row = SideIntakeInbox(CIEL_CFG, IDENTITY)._load(receipt["receipt_id"])
    result["steps"]["relay_result"] = {k: (row.get("result") or {}).get(k) for k in ("action", "item_id", "relay", "delivery")}
    result["steps"]["side_reviews"] = sorted((STORE.read().get("side_reviews") or {}).keys())
except Exception as error:
    result["failure"] = type(error).__name__ + ": " + str(error)[:500]
finally:
    if proc is not None:
        try:
            (OUT / "console.txt").write_text(common.screen_text(proc.output_tail())
                                             .replace(str(OWNED), "<owned>").replace(str(Path.home()), "<home>"),
                                             encoding="utf-8")
        except Exception:
            pass
    if root_ident is not None:
        kills = GUARD.kill_owned(root_ident)
    if proc is not None:
        proc.close()
    for server in (mcp_server, api_server):
        server.shutdown(); server.server_close()
    gate_calls = [e for e in MCP_LOG if e["name"] == "work_side_gate_check"]
    result["gate_calls"] = [{"after_s": round(e["at"] - t0, 1), "result": e.get("result")} for e in gate_calls]
    result["main_requests"] = list(MAIN)
    result["side_requests"] = list(SIDE)
    result["sent_replies"] = list(SENT_REPLIES)
    result["mcp_tail"] = [e for e in MCP_LOG if e["name"] in ("work_side_gate_check", "work_side_resolve",
                                                               "work_side_wake", "work_side_reply")][-30:]
    queued = [e for e in gate_calls if isinstance(e.get("result"), dict) and e["result"].get("queued")]
    checks = {
        "mod_loaded": bool((result.get("mod_load") or {}).get("ok")),
        "timer_called_router_after_idle_turn": bool(gate_calls),
        "router_queued_one_gate_event": len(queued) == 1 and queued[0]["result"].get("gates") == [DEPLOY],
        "side_fork_received_gate_event": bool(result["steps"].get("gate_side_fork")),
        "wait_released_on_board": result["steps"].get("deploy_wait_after") == "",
        "main_told_of_release": bool(result["steps"].get("gate_main_notice")),
        "side_fork_received_relay": bool(result["steps"].get("relay_side_fork")),
        "relay_committed": (result["steps"].get("relay_result") or {}).get("relay") == {"item_id": CSV},
        "main_got_original_and_ask": any(r["relay_notice"] and r["relay_original"] and r["relay_ask"] for r in MAIN),
        "release_wake_names_release": sum(r["release_wake"] for r in MAIN) == 1,
        "relay_wake_names_relay": sum(r["relay_wake"] for r in MAIN) == 1,
        "web_reply_sent_once": len(SENT_REPLIES) == 1,
        "gate_event_left_no_review_row": bool(queued) and queued[0]["result"].get("receipt_id")
                                         not in (result["steps"].get("side_reviews") or []),
    }
    result["checks"] = checks
    result["passed"] = all(checks.values()) and "failure" not in result
    result["kills"] = len(kills)
    final = GUARD.final_report(kills)
    result["owned_survivors"] = len(final["owned_survivors"])
    result["killed_protected_overlap"] = len(final["killed_protected_overlap"])
    text = json.dumps(result, ensure_ascii=False, indent=2)
    result["evidence_secret_hits"] = {"host_token": text.count(TOKEN)}
    (OUT / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "checks": checks, "failure": result.get("failure"),
                      "gate_calls": result["gate_calls"]}, ensure_ascii=False))
if not result["passed"]:
    raise SystemExit(1)
