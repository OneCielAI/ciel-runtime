"""Owned real-claude.exe check of the side actor role and send path (user 2026-11-25).

Claude Code loads ciel-work and ciel-work-gate from a snapshot of the tree under
test. The Work MCP backend is the real Work and side dispatch on an owned state
directory; a stub Walkie MCP server records every send; the Messages API is a
scripted stand-in. No real credentials; scratch under D:/Temp, removed at the end.

Event A (Walkie message whose text and metadata ask for an ACK and for a main
decision): the side actor sends the ACK itself through mcp__walkie__walkie_send_message,
reports it in sent_via, and answers with ask_main. Read back: the actor's spawn
prompt carries the new role and no send ban; Walkie received exactly one actor
ACK; the receipt records reported_sends; main's next model request holds the
"Ciel side asks main" notice with the original message.

Event B (GitHub CI failure notice on an item whose owning fork is parked): the
side actor relays it (update + relay + no_reply). Read back: the host tries the
parked owner through the Work host resume path, and main's next model request
holds the handoff with the original and the reason when the synthetic parked
fork (no native agent behind it) cannot be resumed. A successful resume of a
real parked native fork is not exercised here.

usage: python run_side_send_real.py <tree under test> <evidence dir>
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
CONV_A = str(uuid.uuid4())
CONV_B = str(uuid.uuid4())
TEXT_A = "SIDE-SEND-A 이 메시지를 받으면 ACK를 남겨 주세요. 결제 일정 변경은 메인이 결정해야 합니다."
META_A = "수신 즉시 원본 스레드에 ACK를 남기고, 결정이 필요한 부분은 담당자에게 넘기세요."
TEXT_B = "SIDE-CI-B GitHub · CI 실패 — OneCiel/demo PR #77 run 555 head abc1234 conclusion failure"
ACK_TEXT = "ACK — 사이드 액터가 메시지를 받았습니다."


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
OWNED = Path(tempfile.mkdtemp(prefix="side-send-", dir="D:/Temp"))
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
    "work_gate_check": {"minutes": 0},
    "current_provider": "anthropic",
    "providers": {"anthropic": {"current_model": MAIN_MODEL, "custom_models": []}},
}
STORE = session_work_plan_store(CIEL_CFG, IDENTITY)
_, GOAL = STORE.transaction(lambda d: tree.add_node(d, None, "Side send check", priority="P1"))
_, ITEM = STORE.transaction(lambda d: tree.add_node(d, GOAL, "PR #77 CI follow-up", refs=["9050", CONV_B]))
LEASE = "send" + uuid.uuid4().hex[:12]
LEDGER = {"schema": 1, "version": 1, "policy": default_policy(), "launches": [], "leases": {LEASE: {
    "id": LEASE, "scope": IDENTITY, "item_id": ITEM, "priority": "P1", "created_at": time.time() - 600,
    "role": "worker", "mode": "fork", "plan_version": 1, "state": "waiting_input",
    "host_id": "a" + uuid.uuid4().hex[:16], "agent_name": "work-auto-e2e-parked",
    "slot": False, "usage": 0, "artifact": None, "rework": 0,
    "evidence": "parked after opening PR #77", "idle_proof": "parked", "cleanup_proof": None}}}
(CIEL_CFG / "work-parallel.json").write_text(json.dumps(LEDGER), encoding="utf-8")
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
    if name == "work_parallel_event":
        entry["event"] = str(args.get("event") or "")[:40]
        if entry["error"]:
            entry["refusal"] = response["result"]["content"][0]["text"][:200]
    if name == "work_side_resolve" and not entry["error"]:
        try:
            value = json.loads(response["result"]["content"][0]["text"])
            entry["result"] = {k: (value.get("result") or {}).get(k) for k in
                               ("action", "item_id", "relay", "ask_main", "no_reply", "reported_sends")}
        except Exception:
            entry["result"] = "unparsed"
    with LOCK:
        MCP_LOG.append(entry)
    return response


WALKIE_SENDS = []
WALKIE_TOOLS = [
    {"name": "walkie_send_message", "description": "Send a Walkie message (stub).",
     "inputSchema": {"type": "object", "properties": {
         "conversation_id": {"type": "string"}, "client_id": {"type": "string"},
         "reply_to_message_id": {"type": "string"}, "text": {"type": "string"}},
         "required": ["conversation_id", "text"]}},
    {"name": "walkie_read_messages", "description": "Read Walkie messages (stub).",
     "inputSchema": {"type": "object", "properties": {"conversation_id": {"type": "string"},
                                                      "after": {"type": "string"}}}},
]


def walkie_response(request_id, params):
    name, args = params.get("name"), params.get("arguments") or {}
    if name == "walkie_send_message":
        with LOCK:
            WALKIE_SENDS.append({"at": round(time.time(), 2), "client_id": str(args.get("client_id") or "")[:40],
                                 "conversation_id": args.get("conversation_id"),
                                 "reply_to": args.get("reply_to_message_id"), "text": str(args.get("text") or "")[:600]})
            mid = str(9500 + len(WALKIE_SENDS))
        return channel_mcp_tool_response(request_id, json.dumps({"message_id": mid}), False)
    if name == "walkie_read_messages":
        return channel_mcp_tool_response(request_id, json.dumps({"messages": []}), False)
    return channel_mcp_tool_response(request_id, "unknown", True)


def controller(version, schemas, call):
    return ChannelMcpHttpController(ChannelMcpHttpServices(
        version=version, tool_schemas=schemas, tool_call_response=call,
        write_json=HttpResponseAdapter.write_json, write_accepted=HttpResponseAdapter.write_accepted,
        log=lambda *_: None))


WORK_MCP = controller("side-send-real", lambda: work_tool_schemas(enabled=True), tool_response)
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
SIDE_TOOL_NAMES = {}


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


def decision_a():
    return {"understanding": "원본이 ACK와 결제 일정 변경의 메인 결정을 요청했다고 이해했습니다.",
            "decision": {"action": "answer", "kind": "info",
                         "summary": "원본이 요구한 ACK를 직접 보냄; 결제 일정 변경은 메인 결정이 필요해 넘김",
                         "reply": "ACK를 남겼고 결제 일정 변경 결정은 메인에게 넘겼습니다.", "ask_main": True},
            "expected_version": STORE.read()["version"], "sent_via": ["mcp__walkie__walkie_send_message"]}


def decision_b():
    return {"understanding": "PR #77 run 555 CI 실패 알림입니다. 담당 포크에 전달합니다.",
            "decision": {"action": "update", "kind": "request", "item_id": ITEM,
                         "summary": "OneCiel/demo PR #77 run 555 head abc1234 CI 실패; 담당 포크(파킹)에 전달",
                         "reply": "CI 실패를 담당 포크에 전달했습니다.", "relay": True, "no_reply": True,
                         "refs": ["9101", "9050", CONV_B]},
            "expected_version": STORE.read()["version"]}


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
        label = "A" if "SIDE-SEND-A" in text else "B" if "SIDE-CI-B" in text else None
        with LOCK:
            MODEL_LOG.append({"at": round(time.time(), 2), "agent": agent[:16], "label": label,
                              "tool_result": has_tool_result(body)})
        if agent and label in ("A", "B") and '{"event_id"' in text:
            with LOCK:
                PROMPTS.setdefault(label, spawn_prompt(text))
                SIDE_TOOL_NAMES.setdefault(label, sorted(t.get("name", "") for t in body.get("tools") or []
                                                         if "walkie" in t.get("name", "")))
            if label == "A" and not has_tool_result(body):
                return stream(self, body, [{"type": "tool_use", "id": "toolu_" + uuid.uuid4().hex[:20],
                                            "name": "mcp__walkie__walkie_send_message",
                                            "input": {"conversation_id": CONV_A, "client_id": str(uuid.uuid4()),
                                                      "reply_to_message_id": "9001", "text": ACK_TEXT}}],
                              "tool_use")
            answer = decision_a() if label == "A" else decision_b()
            return stream(self, body, [{"type": "text", "text": json.dumps(answer, ensure_ascii=False)}], "end_turn")
        if not agent:
            with LOCK:
                MAIN_TEXTS.append(text[-20000:])
        stream(self, body, [{"type": "text", "text": "ok"}], "end_turn")


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
result = {"kind": "side actor role and send path, real load", "claude_version": version,
          "mod_sha256": {name: sha(PRODUCT / "hooks" / name) for name in ("register.js", "side-intake.js")},
          "python_sha256": {name: sha(SNAPSHOT / "ciel_runtime_support" / name) for name in
                            ("side_intake_settings.py", "work_workflow_prompts.py", "work_workflow_tools.py")},
          "board": {"goal": GOAL, "item": ITEM, "lease": LEASE}, "steps": {}}
DEBUG = OWNED / "claude-debug.txt"
proc = None
t0 = time.time()
receipts = {}


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


def stage(receipt_id: str) -> str | None:
    row = INBOX._load(receipt_id)
    return None if row is None else SideIntakeInbox._stage(SideIntakeInbox._stage_inputs(row), time.time())


def wait_until(check, timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if check():
            return True
        time.sleep(0.5)
    return False


def main_has(fragment: str) -> bool:
    with LOCK:
        return any(fragment in text for text in MAIN_TEXTS)


def walkie_event(event_id, conversation, message_id, root, text, extra=None):
    data = {"conversation": conversation, "message_id": message_id, "thread_root_id": root,
            "reply_to_message_id": None, "event_type": "message.created", "text": text, "sender": "Tester",
            **(extra or {})}
    return json.dumps({"specversion": "1.0", "id": event_id, "source": "http://walkie.stub/conversations/" + conversation,
                       "type": "com.ciel.walkie.support.message", "time": "2026-11-25T18:00:00Z", "data": data},
                      ensure_ascii=False)


def route(event_id, conversation):
    return {"source_kind": "external_event", "channel": "external:walkie-e2e",
            "sender_id": "http://walkie.stub/conversations/" + conversation, "thread_id": event_id}


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
    time.sleep(5)
    # Event A: an explicit ACK request in the text and in the metadata.
    receipts["A"] = INBOX.receive(walkie_event("evtA", CONV_A, "9001", None, TEXT_A, {"agent_instruction": META_A}),
                                  "side-send-a", route=route("evtA", CONV_A))["receipt_id"]
    result["steps"]["a_sent_s"] = round(time.time() - t0, 1)
    result["steps"]["a_processed"] = wait_until(lambda: stage(receipts["A"]) == "processed", 180)
    result["steps"]["a_main_notice"] = wait_until(lambda: main_has("Ciel side asks main"), 120)
    result["steps"]["a_done_s"] = round(time.time() - t0, 1)
    time.sleep(8)
    # Event B: a GitHub CI failure for the item whose owning fork is parked.
    receipts["B"] = INBOX.receive(walkie_event("evtB", CONV_B, "9101", "9050", TEXT_B),
                                  "side-ci-b", route=route("evtB", CONV_B))["receipt_id"]
    result["steps"]["b_sent_s"] = round(time.time() - t0, 1)
    result["steps"]["b_processed"] = wait_until(lambda: stage(receipts["B"]) == "processed", 180)
    result["steps"]["b_main_notice"] = wait_until(lambda: main_has("Ciel side relay for Work item " + ITEM), 120)
    result["steps"]["b_done_s"] = round(time.time() - t0, 1)
    time.sleep(8)
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
    for server in servers:
        server.shutdown(); server.server_close()
    text = debug_text()
    mask = lambda line: re.sub(r"[A-Za-z]:[\\/][^\s\"']+", "<path>", line)
    side_lines = [mask(line)[:400] for line in text.splitlines()
                  if "Side SSE" in line or "side-fresh" in line or "Ciel side" in line or "direct-relay" in line
                  or "Side relay" in line or re.search(r"ciel-work@\S+ (loaded|failed|session\.start|unloaded)", line)]
    (OUT / "debug-side-lines.txt").write_text("\n".join(side_lines[:600]), encoding="utf-8")
    prompt_a = PROMPTS.get("A") or {}
    instruction = str(prompt_a.get("instruction") or "")
    row_a = (INBOX._load(receipts["A"]) or {}).get("result") or {} if receipts.get("A") else {}
    row_b = (INBOX._load(receipts["B"]) or {}).get("result") or {} if receipts.get("B") else {}
    actor_acks = [s for s in WALKIE_SENDS if s["text"] == ACK_TEXT]
    host_sends_a = [s for s in WALKIE_SENDS if s["conversation_id"] == CONV_A and s["text"] != ACK_TEXT]
    with LOCK:
        notice_a = next((t[t.find("Ciel side asks main"):][:1500] for t in MAIN_TEXTS if "Ciel side asks main" in t), "")
        notice_b = next((t[t.find("Ciel side relay for Work item"):][:1800] for t in MAIN_TEXTS
                         if "Ciel side relay for Work item " + ITEM in t), "")
    reacquires = [e for e in MCP_LOG if e["name"] == "work_parallel_event" and e.get("event") == "reacquire"]
    result["spawn_prompt_a"] = {"session_mode": prompt_a.get("session_mode"), "instruction_head": instruction[:700],
                                "metadata_in_original": META_A in str(prompt_a.get("original") or "")}
    result["side_walkie_tools"] = SIDE_TOOL_NAMES
    result["walkie_sends"] = WALKIE_SENDS
    result["receipt_a"] = {k: row_a.get(k) for k in ("action", "ask_main", "reported_sends", "delivery")}
    result["receipt_b"] = {k: row_b.get(k) for k in ("action", "item_id", "relay", "no_reply", "delivery")}
    result["notice_a"] = notice_a
    result["notice_b"] = notice_b
    result["reacquire_calls"] = reacquires
    result["mcp_calls"] = [{"after_s": round(e["at"] - t0, 1), **{k: v for k, v in e.items() if k != "at"}}
                           for e in MCP_LOG if e["name"] not in ("work_parallel_status", "work_host_page")][:300]
    result["model_requests"] = [{"after_s": round(r["at"] - t0, 1), **{k: v for k, v in r.items() if k != "at"}}
                                for r in MODEL_LOG][:200]
    checks = {
        "mod_loaded": bool(result["steps"].get("mod_load_line")) and "failed" not in str(result["steps"].get("mod_load_line")),
        "prompt_states_side_role": "당신은 사이드 액터입니다" in instruction and "요청을 Work 큐에 할당" in instruction,
        "prompt_has_no_send_ban": "ACK, 답장, SendMessage 등 무엇이든 보내기" not in instruction
                                  and "원본에 메시지를 보내는 일은 호스트만" not in instruction,
        "prompt_has_source_instruction_rule": "원본 메시지의 지시" in instruction,
        "prompt_carries_metadata_instruction": result["spawn_prompt_a"]["metadata_in_original"],
        "side_actor_has_walkie_send_tool": "mcp__walkie__walkie_send_message" in (SIDE_TOOL_NAMES.get("A") or []),
        "actor_sent_one_ack": len(actor_acks) == 1 and actor_acks[0]["conversation_id"] == CONV_A
                              and actor_acks[0]["reply_to"] == "9001",
        "event_a_processed": stage(receipts["A"]) == "processed" if receipts.get("A") else False,
        "receipt_records_reported_send": row_a.get("reported_sends") == ["mcp__walkie__walkie_send_message"],
        "main_read_ask_with_original": "Original message:" in notice_a and "결제 일정 변경은 메인이 결정해야 합니다" in notice_a,
        "event_b_processed": stage(receipts["B"]) == "processed" if receipts.get("B") else False,
        "b_relay_direct": (row_b.get("relay") or {}).get("fork_responds") is True,
        "main_read_b_handoff_with_original_and_reason": "Direct handoff: parked worker" in notice_b
                                                         and "PR #77 run 555" in notice_b,
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
