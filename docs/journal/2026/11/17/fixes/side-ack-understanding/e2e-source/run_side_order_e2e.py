"""Owned real-claude.exe E2E: receipt ACK → understanding → board commit → final reply.

Real Claude Code + packaged ciel-work/ciel-work-gate, an owned MCP backend with
the real Work tools plus a stand-in ``walkie`` server (read + send), and a
scripted Messages stand-in. One Walkie root message is received into the
durable side inbox. The side fork's answer is scripted: understanding in its own
words and a P3 create decision.

Pass:
- the source got exactly three sends, in the order ack, understanding, final;
- three distinct client_ids;
- no send repeats the person's message, and only the first is the receipt ACK
  (no "ACK" prefix on the later two);
- the board item was created after the understanding send;
- the P3 queue entry left no main review.

A second event is forced to fail: its scripted side answer carries no decision.
Its source then gets the receipt ACK and the truthful failure reply, once.

A third event is another Ciel session's receipt ACK (this build's template). It
gets no send and starts no side actor.

    python run_side_order_e2e.py <source tree> <out dir>
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
sys.path.insert(0, str(HERE.parent.parent / "work-workspace-trust" / "e2e-source"))
import p0_common as common  # noqa: E402

SOURCE = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
OUT.mkdir(parents=True, exist_ok=False)
CLI = str(Path.home() / ".local/bin/claude.exe")
common.PROTECTED_PIDS_FILE = HERE.parent.parent / "work-workspace-trust" / "e2e-source" / "protected-pids.json"
GUARD = common.Guard(OUT)
OWNED = Path(tempfile.mkdtemp(prefix="ciel-side-order-e2e-"))
SNAPSHOT = common.snapshot_source(SOURCE, OWNED)
CFG, CIEL_CFG, WS = (OWNED / name for name in ("claude", "ciel", "workspace"))
for path in (CFG, CIEL_CFG, WS):
    path.mkdir(parents=True, exist_ok=True)
SESSION = str(uuid.uuid4())
IDENTITY = "claude:" + SESSION
MAIN_MODEL = "fixture-main"
CONVERSATION = "256a28fd-f1c3-443a-9782-19405288a8d3"
MESSAGE = "결제 재시도 버그를 오늘 안에 스테이징에서 고쳐 주세요"
FAIL_MESSAGE = "이 메시지는 강제로 실패시키는 시험입니다"
INBOUND_ACK = "메시지를 받았습니다. 내용을 확인한 뒤 이해한 내용과 처리 결과를 이어서 알려 드리겠습니다."
UNDERSTANDING = "결제 재시도 버그 수정 요청으로 이해했습니다. 오늘 안에 스테이징에서 확인해야 하며, 새 작업으로 대기열에 넣겠습니다."
env = common.stripped_env()
env.update(CIEL_RUNTIME_TEST_ISOLATED="1", CIEL_RUNTIME_CONFIG_DIR=str(CIEL_CFG), CLAUDE_CONFIG_DIR=str(CFG),
           ANTHROPIC_AUTH_TOKEN="fixture", ANTHROPIC_API_KEY="", ANTHROPIC_MODEL=MAIN_MODEL,
           CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", DISABLE_AUTOUPDATER="1", ENABLE_TOOL_SEARCH="false",
           PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
os.environ.update(CIEL_RUNTIME_CONFIG_DIR=str(CIEL_CFG), CIEL_RUNTIME_TEST_ISOLATED="1")
from ciel_runtime_support.windows_conpty import WindowsConPtySession  # noqa: E402
from ciel_runtime_support.work_parallel_host import host_token  # noqa: E402
from ciel_runtime_support.work_plan_store import session_work_plan_store  # noqa: E402
from ciel_runtime_support.work_mcp_tools import WORK_TOOL_NAMES, dispatch_work_tool, work_tool_schemas  # noqa: E402
from ciel_runtime_support.channel_mcp_http_controller import ChannelMcpHttpController, ChannelMcpHttpServices  # noqa: E402
from ciel_runtime_support.channel_mcp_tools import channel_mcp_tool_response  # noqa: E402
from ciel_runtime_support.http_response import HttpResponseAdapter  # noqa: E402
from ciel_runtime_support.side_intake_service import receive  # noqa: E402

TOKEN = host_token()
env["CIEL_RUNTIME_WORK_PARALLEL_HOST_TOKEN"] = TOKEN
BOARD = session_work_plan_store(CIEL_CFG, IDENTITY)
CONFIG = {
    "work_mode": True, "work_auto_continue": False, "work_plan_approval_ask": False,
    "work_parallel_host": {"token": TOKEN, "capability_ready": True},
    "work_parallel": {"enabled": False},
    "current_provider": "anthropic",
    "providers": {"anthropic": {"current_model": MAIN_MODEL, "custom_models": []}},
    "work_side_intake": {"enabled": True, "type": "btw_with_mcp"},
}
LOCK = threading.Lock()
SENDS = []          # walkie_send_message calls: {client_id, text, reply_to, at, board_nodes}
EVENTS = []         # ordered host tool events of interest
MESSAGES_BY_ID = {}


def walkie_event(message_id, text):
    MESSAGES_BY_ID[message_id] = {"id": message_id, "conversation_id": CONVERSATION, "text": text,
                                  "reply_to_message_id": None, "thread_root_id": None, "sender": "person"}
    return json.dumps({"type": "com.ciel.walkie.support.message", "data": {
        "conversation": CONVERSATION, "message_id": message_id, "thread_root_id": None,
        "reply_to_message_id": None, "event_type": "message.created", "text": text,
        "message_context": {"id": message_id}}}, ensure_ascii=False)


def walkie_tool(name, args):
    if name == "walkie_read_messages":
        after = int(args.get("after") or 0)
        rows = [row for key, row in sorted(MESSAGES_BY_ID.items(), key=lambda kv: int(kv[0])) if int(key) > after]
        return {"messages": rows}
    if name == "walkie_call_tool":
        return {"rows": []}
    if name == "walkie_send_message":
        with LOCK:
            SENDS.append({"client_id": args.get("client_id"), "text": args.get("text"),
                          "reply_to": args.get("reply_to_message_id"), "at": time.time(),
                          "board_nodes": len(BOARD.read()["nodes"])})
        return {"ok": True, "message_id": str(900000 + len(SENDS))}
    return {"error": "unknown"}


WALKIE_SCHEMAS = [
    {"name": name, "description": name, "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True}}
    for name in ("walkie_read_messages", "walkie_call_tool", "walkie_send_message")]


def tool_response(request_id, params):
    name, args = params.get("name"), params.get("arguments") or {}
    if name in ("work_side_ack", "work_side_understanding", "work_side_resolve", "work_side_reply"):
        with LOCK:
            EVENTS.append({"tool": name, "state": args.get("state"), "at": time.time()})
    if name not in WORK_TOOL_NAMES:
        return channel_mcp_tool_response(request_id, "unknown", True)
    return dispatch_work_tool(request_id, name, args, load_config=lambda: json.loads(json.dumps(CONFIG)),
                              state_dir=CIEL_CFG, respond=channel_mcp_tool_response)


def walkie_response(request_id, params):
    value = walkie_tool(params.get("name"), params.get("arguments") or {})
    return channel_mcp_tool_response(request_id, json.dumps(value, ensure_ascii=False), False)


def controller(schemas, handler, version):
    return ChannelMcpHttpController(ChannelMcpHttpServices(
        version=version, tool_schemas=schemas, tool_call_response=handler,
        write_json=HttpResponseAdapter.write_json, write_accepted=HttpResponseAdapter.write_accepted,
        log=lambda *_: None))


ROUTER = controller(lambda: work_tool_schemas(enabled=True), tool_response, "side-order-e2e")
WALKIE = controller(lambda: WALKIE_SCHEMAS, walkie_response, "walkie-standin")


def mcp_handler(ctrl):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def do_GET(self):
            if not ctrl.get(self, self.path.split("?")[0]):
                self.send_response(404)
                self.send_header("content-length", "0")
                self.end_headers()

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
            if not ctrl.post(self, self.path.split("?")[0], body):
                self.send_response(404)
                self.send_header("content-length", "0")
                self.end_headers()
    return Handler


SIDE_TURNS = {"ok": 0, "fail": 0, "inbound_ack": 0}


def last_user_text(body):
    for message in reversed(body.get("messages") or []):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        return "\n".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")
    return ""


def stream(handler, body, text):
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
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        if "count_tokens" in self.path:
            raw = b'{"input_tokens":20}'
            self.send_response(200)
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        agent = self.headers.get("x-claude-code-agent-id") or ""
        text = last_user_text(body)
        if agent and '"claim_generation"' in text:
            if INBOUND_ACK in text and MESSAGE not in text and FAIL_MESSAGE not in text:
                SIDE_TURNS["inbound_ack"] += 1
                stream(self, body, "I could not decide.")
                return
            if FAIL_MESSAGE in text:
                SIDE_TURNS["fail"] += 1
                stream(self, body, "I could not decide.")      # no decision JSON: invalid_answer
                return
            SIDE_TURNS["ok"] += 1
            version = json.loads(text[text.index("{"):])["workload"]["version"] if "{" in text else 0
            answer = {"understanding": UNDERSTANDING, "expected_version": version,
                      "decision": {"action": "create", "kind": "request", "title": "결제 재시도 버그 수정",
                                   "summary": "payment retry fix on staging today", "priority": "P3",
                                   "reply": "결제 재시도 버그 수정을 새 작업으로 등록했습니다."}}
            stream(self, body, json.dumps(answer, ensure_ascii=False))
            return
        stream(self, body, "ok")


servers = {"router": ThreadingHTTPServer(("127.0.0.1", 0), mcp_handler(ROUTER)),
           "walkie": ThreadingHTTPServer(("127.0.0.1", 0), mcp_handler(WALKIE)),
           "api": ThreadingHTTPServer(("127.0.0.1", 0), Messages)}
for server in servers.values():
    threading.Thread(target=server.serve_forever, daemon=True).start()
env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{servers['api'].server_port}"
mcp_config = {"mcpServers": {
    "ciel-runtime-router": {"type": "http", "url": f"http://127.0.0.1:{servers['router'].server_port}/ca/mcp"},
    "walkie": {"type": "http", "url": f"http://127.0.0.1:{servers['walkie'].server_port}/ca/mcp"}}}
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
ROUTE = {"source_kind": "external_event", "channel": "external:default",
         "sender_id": "http://walkie.invalid/conversations/" + CONVERSATION}


def redact(text):
    return (str(text).replace(str(OWNED), "<owned>").replace(OWNED.as_posix(), "<owned>")
            .replace(str(Path.home()), "<home>").replace(Path.home().as_posix(), "<home>"))


result = {"kind": "side ACK/understanding/final order E2E", "claude_version": version,
          "snapshot_manifest": common.manifest(SNAPSHOT, SOURCE, "stage2"), "steps": {}}
proc = root_ident = None
kills = []
DEBUG = OWNED / "claude-debug.txt"
try:
    os.chdir(WS)
    proc = WindowsConPtySession([CLI, "--permission-mode", "default", "--plugin-dir", str(PRODUCT),
                                 "--plugin-dir", str(GATE_PLUGIN), "--strict-mcp-config", "--mcp-config",
                                 json.dumps(mcp_config), "--session-id", SESSION, "--debug-file", str(DEBUG)],
                                env, log=lambda *_: None, mirror_output=False, forward_stdin=False)
    root_ident = GUARD.register_root(proc.pid)
    result["mod_load"] = common.wait_mod_loaded(DEBUG, 60)
    if not result["mod_load"]["ok"]:
        raise common.ModNotLoaded("ciel-work did not load")
    proc.write(b"hello\r")
    time.sleep(8)
    ok_key = receive(CIEL_CFG, IDENTITY, walkie_event("300001", MESSAGE), "walkie:300001", admit=False,
                     route=ROUTE)["receipt_id"]
    for _ in range(240):
        if len([s for s in SENDS if s["reply_to"] == "300001"]) >= 3:
            break
        time.sleep(0.5)
    result["steps"]["ok_event_sends"] = len([s for s in SENDS if s["reply_to"] == "300001"])
    fail_key = receive(CIEL_CFG, IDENTITY, walkie_event("300002", FAIL_MESSAGE), "walkie:300002", admit=False,
                       route=ROUTE)["receipt_id"]
    for _ in range(600):
        if any(s["reply_to"] == "300002" and s is not None and s["text"] and "처리하지 못했습니다" in s["text"]
               for s in SENDS):
            break
        time.sleep(0.5)
    receive(CIEL_CFG, IDENTITY, walkie_event("300003", INBOUND_ACK), "walkie:300003", admit=False, route=ROUTE)
    time.sleep(20)
except Exception as error:
    result["failure"] = type(error).__name__ + ": " + str(error)[:500]
finally:
    if proc is not None:
        try:
            (OUT / "console.txt").write_text(redact(common.screen_text(proc.output_tail())), encoding="utf-8")
        except Exception:
            pass
    if root_ident is not None:
        kills = GUARD.kill_owned(root_ident)
    if proc is not None:
        proc.close()
    for server in servers.values():
        server.shutdown()
        server.server_close()
    # This build's receipt ACK text; older builds echo the message instead.
    RECEIPT_ACK_TEXT = '메시지를 받았습니다. 내용을 확인한 뒤 이해한 내용과 처리 결과를 이어서 알려 드리겠습니다.'
    ok = [s for s in SENDS if s["reply_to"] == "300001"]
    bad = [s for s in SENDS if s["reply_to"] == "300002"]
    inbound_ack = [s for s in SENDS if s["reply_to"] == "300003"]
    doc = BOARD.read()
    result["sends_ok_event"] = [{"client_id": s["client_id"], "text": s["text"], "board_nodes_at_send": s["board_nodes"]}
                                for s in ok]
    result["sends_failed_event"] = [{"client_id": s["client_id"], "text": s["text"]} for s in bad]
    result["sends_inbound_ack_event"] = [{"client_id": s["client_id"], "text": s["text"]} for s in inbound_ack]
    result["side_turns"] = dict(SIDE_TURNS)
    result["host_events"] = EVENTS[-40:]
    result["side_reviews"] = sorted((doc.get("side_reviews") or {}).keys())
    result["nodes"] = [{"title": n.get("title"), "priority": n.get("priority")} for n in doc["nodes"]]
    checks = {
        "three_sends_for_ok_event": len(ok) == 3,
        "order_ack_understanding_final": len(ok) == 3 and ok[0]["text"] == RECEIPT_ACK_TEXT
        and ok[1]["text"] == UNDERSTANDING and "등록했습니다" in (ok[2]["text"] or ""),
        "distinct_client_ids": len({s["client_id"] for s in ok}) == len(ok) == 3,
        "no_send_echoes_the_message": all(MESSAGE not in (s["text"] or "") for s in ok),
        "later_sends_have_no_ack_prefix": all(not (s["text"] or "").startswith("ACK") for s in ok[1:]),
        "board_committed_after_understanding": len(ok) == 3 and ok[1]["board_nodes"] == 0 and ok[2]["board_nodes"] == 1,
        "p3_left_no_main_review": result["side_reviews"] == [],
        "failed_event_ack_then_one_truthful_reply": len(bad) == 2 and bad[0]["text"] == RECEIPT_ACK_TEXT
        and "처리하지 못했습니다" in (bad[1]["text"] or ""),
        "inbound_receipt_ack_no_send_no_actor": inbound_ack == [] and SIDE_TURNS["inbound_ack"] == 0,
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
    print(json.dumps({"passed": result["passed"], "checks": checks, "sends_ok": [s["text"][:40] for s in ok],
                      "sends_fail": [s["text"][:40] for s in bad], "side_turns": SIDE_TURNS,
                      "failure": result.get("failure")}, ensure_ascii=False))
if not result["passed"]:
    raise SystemExit(1)
