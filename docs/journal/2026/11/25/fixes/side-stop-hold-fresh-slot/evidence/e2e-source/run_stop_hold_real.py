"""Owned real-claude.exe check of the Stop hold and the fresh reserved slot (2026-11-25).

Claude Code loads ciel-work and ciel-work-gate from a snapshot of the tree under
test. The Work MCP backend is the real Work and side dispatch on an owned state
directory (side intake on, session "fresh"); a stub Walkie MCP server records
every call; the Messages API is a scripted stand-in. No real credentials;
scratch under D:/Temp, removed at the end; the live profile is hashed before
and after.

Phase 1, Stop hold (Celly 2026-11-25): a main turn is started by typed input and
stopped with Esc. A Walkie message whose agent_context.guidance asks for
messages.work.update(state="ack", memo) then arrives. Read back: Claude Code
refuses the side spawn, the host records no failed attempt (no work_side_fail,
receipt still received), logs the pause, and no side model request is made.
Typed input resumes it: the side actor runs, calls walkie_call_tool
messages.work.update with state ack and a memo, answers create + no_reply with
sent_via, and the host posts no separate reply.

Phase 2, reserved slot (Andy 2026-11-25): an ordinary event's side actor is held
open by the stub model; a P0 event then arrives. Read back: the P0's side actor
starts while the ordinary one is still running, and finishes first.

usage: python run_stop_hold_real.py <tree under test> <evidence dir>
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
CONV = {name: str(uuid.uuid4()) for name in ("A", "B", "C")}
TEXT = {
    "A": "STOP-HOLD-A Eric 담당 CI 변경 실행안을 첨부합니다. 이 계획대로 진행해도 될까요?",
    "B": "SLOT-B 일반 요청: 다음 주 보고서 양식을 정리해 주세요.",
    "C": "SLOT-C 긴급: 결제 서버가 응답하지 않습니다.",
}
MESSAGE_ID = {"A": "9201", "B": "9301", "C": "9401"}
GUIDANCE = ('메시지를 받자마자 Walkie MCP의 messages.work.update(conversation_id, message_id="9201", '
            'state="ack", memo="<짧은 확인 메모>")로 ACK와 메모를 먼저 보낸다. 9201의 자식으로 답변은 '
            '작업을 맡은 쪽이 한다.')
MEMO = "Work 큐에 새 항목으로 등록했습니다(사이드 액터)."


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
OWNED = Path(tempfile.mkdtemp(prefix="stop-hold-", dir="D:/Temp"))
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
from ciel_runtime_support import runtime_paths  # noqa: E402
from ciel_runtime_support import work_plan_tree as tree  # noqa: E402

CIEL_CFG = Path(runtime_paths.CONFIG_DIR)
env["CIEL_RUNTIME_CONFIG_DIR"] = str(CIEL_CFG)
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
    "work_side_intake": {"enabled": True, "type": "btw_with_mcp", "session": "fresh", "max_active": 2},
    "work_gate_check": {"minutes": 0},
    "current_provider": "anthropic",
    "providers": {"anthropic": {"current_model": MAIN_MODEL, "custom_models": []}},
}
STORE = session_work_plan_store(CIEL_CFG, IDENTITY)
_, GOAL = STORE.transaction(lambda d: tree.add_node(d, None, "Stop hold check", priority="P2"))
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
    response = dispatch_work_tool(request_id, name, args, load_config=snapshot_config, save_config=save_config,
                                  state_dir=CIEL_CFG, respond=channel_mcp_tool_response)
    entry = {"at": round(time.time(), 2), "name": name, "error": bool(response["result"].get("isError"))}
    if name == "work_side_fail":
        entry["category"] = str(args.get("category") or "")[:60]
        entry["reason"] = str(args.get("reason") or "")[:200]
    if name == "work_side_claim" and not entry["error"]:
        try:
            value = json.loads(response["result"]["content"][0]["text"])
            claim = value.get("claim") or {}
            entry["urgent_only"] = args.get("urgent_only") is True
            entry["claimed"] = str(claim.get("receipt_id") or "")[:12] or None
        except Exception:
            entry["claimed"] = "unparsed"
    if name == "work_side_resolve" and not entry["error"]:
        try:
            value = json.loads(response["result"]["content"][0]["text"])
            entry["result"] = {k: (value.get("result") or {}).get(k) for k in
                               ("action", "item_id", "no_reply", "reported_sends", "priority")}
        except Exception:
            entry["result"] = "unparsed"
    with LOCK:
        MCP_LOG.append(entry)
    return response


WALKIE_CALLS = []
WALKIE_TOOLS = [
    {"name": "walkie_send_message", "description": "Send a Walkie message (stub).",
     "inputSchema": {"type": "object", "properties": {
         "conversation_id": {"type": "string"}, "client_id": {"type": "string"},
         "reply_to_message_id": {"type": "string"}, "text": {"type": "string"}},
         "required": ["conversation_id", "text"]}},
    {"name": "walkie_call_tool", "description": "Call a Walkie operation by operation_id (stub).",
     "inputSchema": {"type": "object", "properties": {
         "operation_id": {"type": "string"}, "schema_version": {"type": "integer"},
         "arguments": {"type": "object"}}, "required": ["operation_id"]}},
    {"name": "walkie_read_messages", "description": "Read Walkie messages (stub).",
     "inputSchema": {"type": "object", "properties": {"conversation_id": {"type": "string"},
                                                      "after": {"type": "string"}}}},
]


def walkie_response(request_id, params):
    name, args = params.get("name"), params.get("arguments") or {}
    with LOCK:
        WALKIE_CALLS.append({"at": round(time.time(), 2), "tool": name,
                             "operation_id": args.get("operation_id"),
                             "arguments": json.loads(json.dumps(args.get("arguments") or {}))
                             if name == "walkie_call_tool" else None,
                             "conversation_id": args.get("conversation_id"),
                             "text": str(args.get("text") or "")[:400] if name == "walkie_send_message" else None})
    if name == "walkie_send_message":
        return channel_mcp_tool_response(request_id, json.dumps({"message_id": "9999"}), False)
    if name == "walkie_call_tool":
        if args.get("operation_id") == "messages.work.update":
            return channel_mcp_tool_response(request_id, json.dumps({"ok": True, "state": "ack"}), False)
        return channel_mcp_tool_response(request_id, json.dumps({"messages": [], "items": []}), False)
    if name == "walkie_read_messages":
        return channel_mcp_tool_response(request_id, json.dumps({"messages": []}), False)
    return channel_mcp_tool_response(request_id, "unknown", True)


def controller(version, schemas, call):
    return ChannelMcpHttpController(ChannelMcpHttpServices(
        version=version, tool_schemas=schemas, tool_call_response=call,
        write_json=HttpResponseAdapter.write_json, write_accepted=HttpResponseAdapter.write_accepted,
        log=lambda *_: None))


WORK_MCP = controller("stop-hold-real", lambda: work_tool_schemas(enabled=True), tool_response)
WALKIE_MCP = controller("walkie-stub", lambda: WALKIE_TOOLS, walkie_response)


def mcp_handler(target):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def do_GET(self):
            if not target.get(self, self.path.split("?")[0]):
                self.send_response(404); self.send_header("content-length", "0"); self.end_headers()

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
            if not target.post(self, self.path.split("?")[0], body):
                self.send_response(404); self.send_header("content-length", "0"); self.end_headers()
    return Handler


MODEL_LOG = []
PROMPTS = {}
MAIN_TEXTS = []
MAIN_LONG_STARTED = threading.Event()
MAIN_LONG_RELEASE = threading.Event()
B_STARTED = threading.Event()
B_RELEASE = threading.Event()
TIMES = {}


def user_texts(body):
    parts = []
    for message in body.get("messages") or []:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        else:
            for block in content or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
    return "\n".join(parts)


def has_tool_result(body):
    for message in body.get("messages") or []:
        content = message.get("content")
        if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return True
    return False


def spawn_prompt(text):
    start = text.find('{"event_id"')
    if start < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(text[start:])[0]
    except ValueError:
        return None


def stream(handler, body, blocks, stop):
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
    for index, block in enumerate(blocks):
        if block["type"] == "text":
            emit("content_block_start", {"type": "content_block_start", "index": index,
                                         "content_block": {"type": "text", "text": ""}})
            emit("content_block_delta", {"type": "content_block_delta", "index": index,
                                         "delta": {"type": "text_delta", "text": block["text"]}})
        else:
            emit("content_block_start", {"type": "content_block_start", "index": index,
                                         "content_block": {"type": "tool_use", "id": block["id"],
                                                           "name": block["name"], "input": {}}})
            emit("content_block_delta", {"type": "content_block_delta", "index": index,
                                         "delta": {"type": "input_json_delta",
                                                   "partial_json": json.dumps(block["input"])}})
        emit("content_block_stop", {"type": "content_block_stop", "index": index})
    emit("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                           "usage": {"output_tokens": 5}})
    emit("message_stop", {"type": "message_stop"})


def answer(label):
    version = STORE.read()["version"]
    if label == "A":
        return {"understanding": "CI 변경 실행안의 진행 승인을 묻는 요청으로 이해했습니다. Work 큐에 새 항목으로 넣습니다.",
                "decision": {"action": "create", "kind": "request", "title": "Eric CI 변경 실행안 검토",
                             "note": "원본 9201: 실행안 승인 여부 회신 필요", "priority": "P2",
                             "summary": "원본이 정한 ACK와 memo를 messages.work.update로 보냄; 큐 등록",
                             "reply": "큐에 등록했습니다.", "no_reply": True, "refs": [MESSAGE_ID["A"], CONV["A"]]},
                "expected_version": version, "sent_via": ["mcp__walkie__walkie_call_tool"]}
    return {"understanding": "이 이벤트를 확인했습니다.",
            "decision": {"action": "answer", "kind": "status", "summary": label + " 처리",
                         "reply": label + " 처리했습니다.", "no_reply": True},
            "expected_version": version}


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
        text = user_texts(body)
        label = next((k for k, marker in (("A", "STOP-HOLD-A"), ("B", "SLOT-B"), ("C", "SLOT-C")) if marker in text), None)
        side = bool(agent) and '{"event_id"' in text
        with LOCK:
            MODEL_LOG.append({"at": round(time.time(), 2), "agent": agent[:16], "label": label, "side": side,
                              "tool_result": has_tool_result(body)})
        try:
            if side:
                with LOCK:
                    PROMPTS.setdefault(label or "other", spawn_prompt(text))
                    TIMES.setdefault("side_first_" + str(label), time.time())
                if label == "A" and not has_tool_result(body):
                    return stream(self, body, [{"type": "tool_use", "id": "toolu_" + uuid.uuid4().hex[:20],
                                                "name": "mcp__walkie__walkie_call_tool",
                                                "input": {"operation_id": "messages.work.update", "schema_version": 1,
                                                          "arguments": {"conversation_id": CONV["A"],
                                                                        "message_id": MESSAGE_ID["A"],
                                                                        "state": "ack", "memo": MEMO}}}], "tool_use")
                if label == "B":
                    B_STARTED.set()
                    B_RELEASE.wait(150)
                    TIMES.setdefault("b_answered", time.time())
                if label == "C":
                    TIMES.setdefault("c_answered", time.time())
                return stream(self, body, [{"type": "text", "text": json.dumps(answer(label or "other"),
                                                                               ensure_ascii=False)}], "end_turn")
            if not agent:
                with LOCK:
                    MAIN_TEXTS.append(text[-20000:])
                if "MAIN-LONG" in text.split("resume please")[0][-4000:] and "resume please" not in text:
                    MAIN_LONG_STARTED.set()
                    # Hold the response open and note when Claude Code closes the
                    # request (Esc aborts it): a readable socket with no data.
                    import select
                    import socket as _socket
                    end = time.time() + 90
                    while time.time() < end and not MAIN_LONG_RELEASE.is_set():
                        readable, _, _ = select.select([self.connection], [], [], 0.5)
                        if readable:
                            try:
                                peeked = self.connection.recv(1, _socket.MSG_PEEK)
                            except OSError:
                                peeked = b""
                            if not peeked:
                                TIMES.setdefault("main_client_closed", time.time())
                                return
            stream(self, body, [{"type": "text", "text": "ok"}], "end_turn")
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            with LOCK:
                MODEL_LOG.append({"at": round(time.time(), 2), "agent": agent[:16], "label": label,
                                  "side": side, "client_closed": True})


servers = [ThreadingHTTPServer(("127.0.0.1", 0), handler) for handler in
           (mcp_handler(WORK_MCP), mcp_handler(WALKIE_MCP), Messages)]
for server in servers:
    threading.Thread(target=server.serve_forever, daemon=True).start()
work_server, walkie_server, api_server = servers
env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{api_server.server_port}"
mcp_config = {"mcpServers": {
    "ciel-runtime-router": {"type": "http", "url": f"http://127.0.0.1:{work_server.server_port}/ca/mcp"},
    "walkie": {"type": "http", "url": f"http://127.0.0.1:{walkie_server.server_port}/ca/mcp"}}}
PRODUCT = OWNED / "plugin-ciel-work"
shutil.copytree(SNAPSHOT / "ciel_runtime_support/claude_mods/ciel-work", PRODUCT)
GATE_PLUGIN = OWNED / "plugin-ciel-work-gate"
shutil.copytree(SNAPSHOT / "ciel_runtime_support/claude_mods/ciel-work-gate", GATE_PLUGIN)
(CFG / ".claude.json").write_text(json.dumps({"hasCompletedOnboarding": True, "theme": "dark", "projects": {
    str(WS): {"hasTrustDialogAccepted": True}, str(WS).replace("\\", "/"): {"hasTrustDialogAccepted": True}}}),
    encoding="utf-8")
(CFG / "settings.json").write_text(json.dumps({"permissions": {
    "allow": ["mcp__ciel-runtime-router", "mcp__walkie"], "deny": []}}), encoding="utf-8")
version = subprocess.run([CLI, "--version"], capture_output=True, text=True, timeout=60).stdout.strip()
result = {"kind": "Stop hold and fresh reserved slot, real load", "claude_version": version,
          "mod_sha256": {name: sha(PRODUCT / "hooks" / name) for name in ("register.js", "side-intake.js")},
          "python_sha256": {name: sha(SNAPSHOT / "ciel_runtime_support" / name) for name in
                            ("side_intake_mcp.py", "side_intake_settings.py", "work_workflow_prompts.py")},
          "steps": {}}
DEBUG = OWNED / "claude-debug.txt"
proc = None
t0 = time.time()
receipts = {}


def debug_text() -> str:
    return DEBUG.read_text(encoding="utf-8", errors="replace") if DEBUG.exists() else ""


def transcript_text() -> str:
    parts = []
    for path in (CFG / "projects").glob("**/*.jsonl"):
        try:
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    return "\n".join(parts)


def wait_for(pattern: str, timeout: float, source=debug_text):
    end = time.time() + timeout
    while time.time() < end:
        match = re.search(pattern, source())
        if match:
            return match
        time.sleep(0.5)
    return None


def row(receipt_id: str) -> dict:
    return INBOX._load(receipt_id) or {}


def stage(receipt_id: str) -> str | None:
    value = INBOX._load(receipt_id)
    return None if value is None else SideIntakeInbox._stage(SideIntakeInbox._stage_inputs(value), time.time())


def wait_until(check, timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if check():
            return True
        time.sleep(0.5)
    return False


def walkie_event(event_id, label, extra=None):
    data = {"conversation": CONV[label], "message_id": MESSAGE_ID[label], "thread_root_id": None,
            "reply_to_message_id": None, "event_type": "message.created", "text": TEXT[label],
            "sender": "Tester", **(extra or {})}
    return json.dumps({"specversion": "1.0", "id": event_id, "source": "http://walkie.stub/conversations/" + CONV[label],
                       "type": "com.ciel.walkie.support.message", "time": "2026-11-25T21:00:00Z", "data": data},
                      ensure_ascii=False)


def route(event_id, label):
    return {"source_kind": "external_event", "channel": "external:walkie-e2e",
            "sender_id": "http://walkie.stub/conversations/" + CONV[label], "thread_id": event_id}


def side_requests(label):
    with LOCK:
        return [r for r in MODEL_LOG if r.get("side") and r.get("label") == label and not r.get("client_closed")]


def type_line(text: str):
    proc.write(text.encode("utf-8"))
    time.sleep(0.6)
    proc.write(b"\r")


def own_tree_kill(pid: int) -> str:
    done = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)
    return (done.stdout + done.stderr).strip()[:400]


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
    time.sleep(6)
    # Phase 1: a typed main turn, stopped with Esc while the model streams.
    type_line("MAIN-LONG please keep working on the report")
    result["steps"]["main_long_started"] = MAIN_LONG_STARTED.wait(60)
    time.sleep(2)
    TIMES["esc"] = time.time()
    proc.write(b"\x1b")
    interrupted = wait_for(r"Request interrupted by user", 30, transcript_text)
    result["steps"]["main_interrupted_text"] = interrupted.group(0) if interrupted else None
    MAIN_LONG_RELEASE.set()
    time.sleep(3)
    receipts["A"] = INBOX.receive(walkie_event("evtA", "A", {"agent_context": {"guidance": GUIDANCE}}),
                                  "stop-hold-a", route=route("evtA", "A"))["receipt_id"]
    TIMES["a_received"] = time.time()
    paused = wait_for(r"Side actors paused[^\n]*", 60, lambda: debug_text() + transcript_text())
    result["steps"]["paused_line"] = paused.group(0)[:300] if paused else None
    time.sleep(25)
    held = row(receipts["A"])
    result["steps"]["a_while_held"] = {"stage": stage(receipts["A"]), "side_failure": held.get("side_failure"),
                                       "side_requests": len(side_requests("A"))}
    TIMES["person_input"] = time.time()
    type_line("resume please")
    result["steps"]["a_processed"] = wait_until(lambda: stage(receipts["A"]) == "processed", 180)
    resumed = wait_for(r"Side actors resumed[^\n]*", 30, lambda: debug_text() + transcript_text())
    result["steps"]["resumed_line"] = resumed.group(0)[:300] if resumed else None
    time.sleep(10)
    # Phase 2: an ordinary side actor held open; a P0 then arrives.
    receipts["B"] = INBOX.receive(walkie_event("evtB", "B"), "slot-b", route=route("evtB", "B"))["receipt_id"]
    result["steps"]["b_started"] = B_STARTED.wait(120)
    TIMES["b_started"] = time.time()
    p0 = {"message_context": {"thread_task": {"priority": 1, "emergency": True}}}
    receipts["C"] = INBOX.receive(walkie_event("evtC", "C", p0), "slot-c", route=route("evtC", "C"))["receipt_id"]
    TIMES["c_received"] = time.time()
    result["steps"]["c_processed_while_b_held"] = wait_until(lambda: stage(receipts["C"]) == "processed", 120)
    result["steps"]["b_stage_when_c_done"] = stage(receipts["B"])
    B_RELEASE.set()
    result["steps"]["b_processed"] = wait_until(lambda: stage(receipts["B"]) == "processed", 120)
    time.sleep(5)
except Exception as error:
    result["failure"] = type(error).__name__ + ": " + str(error)[:500]
finally:
    MAIN_LONG_RELEASE.set()
    B_RELEASE.set()
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
    for server in servers:
        server.shutdown(); server.server_close()
    text = debug_text()
    mask = lambda line: re.sub(r"[A-Za-z]:[\\/][^\s\"']+", "<path>", line)
    side_lines = [mask(line)[:400] for line in text.splitlines()
                  if "Side SSE" in line or "side-fresh" in line or "Ciel side" in line or "Side actors" in line
                  or "nobody has sent input" in line
                  or re.search(r"ciel-work@\S+ (loaded|failed|session\.start|unloaded)", line)]
    (OUT / "debug-side-lines.txt").write_text("\n".join(side_lines[:600]), encoding="utf-8")
    prompt_a = PROMPTS.get("A") or {}
    instruction = str(prompt_a.get("instruction") or "")
    result_a = row(receipts["A"]).get("result") or {} if receipts.get("A") else {}
    fails = [e for e in MCP_LOG if e["name"] == "work_side_fail"]
    work_updates = [c for c in WALKIE_CALLS if c["tool"] == "walkie_call_tool" and c["operation_id"] == "messages.work.update"]
    sends_a = [c for c in WALKIE_CALLS if c["tool"] == "walkie_send_message" and c["conversation_id"] == CONV["A"]]
    a_requests = side_requests("A")
    first_a = min((r["at"] for r in a_requests), default=None)
    first_c = min((r["at"] for r in side_requests("C")), default=None)
    claims = [{"after_s": round(e["at"] - t0, 1), "urgent_only": e.get("urgent_only"), "claimed": e.get("claimed")}
              for e in MCP_LOG if e["name"] == "work_side_claim" and e.get("claimed")]
    result["claims"] = claims
    result["side_fail_calls"] = fails
    result["walkie_calls"] = WALKIE_CALLS
    result["receipt_a"] = {k: result_a.get(k) for k in ("action", "no_reply", "reported_sends", "delivery", "item_id")}
    result["times_after_s"] = {k: round(v - t0, 1) for k, v in TIMES.items()}
    result["model_requests"] = [{"after_s": round(r["at"] - t0, 1), **{k: v for k, v in r.items() if k != "at"}}
                                for r in MODEL_LOG][:200]
    result["spawn_prompt_a_has_ack_rule"] = "messages.work.update" in instruction and "ACK와 memo로 반응했으면" in instruction
    # The stopped main request: Claude Code closed it within seconds of Esc.
    closed_after_esc = (TIMES["main_client_closed"] - TIMES["esc"]
                        if TIMES.get("main_client_closed") and TIMES.get("esc") else None)
    result["main_request_closed_after_esc_s"] = round(closed_after_esc, 2) if closed_after_esc is not None else None
    result["resume_delay_after_input_s"] = (round(first_a - TIMES["person_input"], 1)
                                            if first_a and TIMES.get("person_input") else None)
    checks = {
        "mod_loaded": bool(result["steps"].get("mod_load_line")) and "failed" not in str(result["steps"].get("mod_load_line")),
        "main_turn_stopped_by_esc": closed_after_esc is not None and 0 <= closed_after_esc <= 10,
        "actor_started_soon_after_person_input": result["resume_delay_after_input_s"] is not None
                                                 and result["resume_delay_after_input_s"] <= 20,
        "engine_refused_spawn_after_stop": "$.agent.spawn (ciel-work): refused: the session was stopped" in text,
        "pause_logged_with_engine_reason": "nobody has sent input since" in str(result["steps"].get("paused_line")),
        # "processing" while held is the unspawned claim's lease; no failure is recorded at any point.
        "held_event_not_failed": (result["steps"].get("a_while_held") or {}).get("stage") in ("received", "processing")
                                 and not (result["steps"].get("a_while_held") or {}).get("side_failure")
                                 and bool(receipts.get("A")) and not row(receipts["A"]).get("side_failure"),
        "no_side_fail_call": not fails,
        "no_side_model_request_while_held": first_a is not None and first_a > TIMES.get("person_input", 0),
        "a_processed_after_person_input": result["steps"].get("a_processed") is True,
        "resume_logged": bool(result["steps"].get("resumed_line")),
        "spawn_prompt_has_ack_rule": result["spawn_prompt_a_has_ack_rule"],
        "ack_via_messages_work_update": len(work_updates) == 1
                                        and (work_updates[0]["arguments"] or {}).get("state") == "ack"
                                        and (work_updates[0]["arguments"] or {}).get("message_id") == MESSAGE_ID["A"]
                                        and bool((work_updates[0]["arguments"] or {}).get("memo")),
        "no_separate_reply_to_a": not sends_a,
        "receipt_a_no_reply_and_reported_send": result_a.get("no_reply") is True
                                                and result_a.get("reported_sends") == ["mcp__walkie__walkie_call_tool"],
        "p0_started_while_ordinary_running": result["steps"].get("b_started") is True and first_c is not None
                                             and first_c < TIMES.get("b_answered", float("inf")),
        "p0_claim_was_reserved": any(c["urgent_only"] for c in claims if c["claimed"] == (receipts.get("C") or "")[:12]),
        "p0_processed_before_ordinary": result["steps"].get("c_processed_while_b_held") is True
                                        and result["steps"].get("b_stage_when_c_done") != "processed",
        "ordinary_processed": result["steps"].get("b_processed") is True,
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
    result_text = dumped if TOKEN not in dumped else dumped.replace(TOKEN, "<token>")
    (OUT / "result.json").write_text(result_text, encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "checks": checks, "steps": result["steps"],
                      "failure": result.get("failure"), "scratch_removed": removed,
                      "profile_unchanged": result["profile_unchanged"]}, ensure_ascii=False, indent=1))
if not result["passed"]:
    raise SystemExit(1)
