"""Approval workflow E2E: real launcher and claude.exe, stub upstream, stub Walkie (SSE + MCP).

python -I e2e_workflow.py <repo> <evidence-dir>

Flow: Walkie SSE request -> side actor registers a P1 goal (proposal_due) -> main
writes a proposal with a mockup and benchmark -> main sends the approval buttons
through the Walkie MCP and registers the message -> the requester approves in
Walkie but the message.approved SSE is dropped -> the periodic gate check reads
the thread and the host applies the approval -> a Work fork writes QA evidence and
reports -> main reworks -> the rework fork reports again -> main approves -> the
workflow is completed.

Everything is local: a scratch folder under D:\\Temp (removed at the end), fresh
config/state/CLAUDE_CONFIG_DIR, cleared CIEL_RUNTIME_/CLAUDE_/ANTHROPIC_ env,
CIEL_RUNTIME_TEST_ISOLATED=1, loopback stub servers only. The evidence folder gets
screenshots, screen texts and a summary with no local paths, ids of real accounts
or keys.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = Path(sys.argv[1]).resolve()
EVIDENCE = Path(sys.argv[2]).resolve()
EVIDENCE.mkdir(parents=True, exist_ok=True)
STAMP = time.strftime("%Y%m%d-%H%M%S")
SCRATCH = Path(r"D:\Temp") / ("ciel-wf-e2e-" + STAMP)
# An isolated runtime (CIEL_RUNTIME_TEST_ISOLATED=1) accepts a config dir only
# under its process temp root (runtime_paths), so TEMP/TMP point into the scratch.
TMP = SCRATCH / "tmp"
CFG, WS, CCFG = TMP / "cfg", SCRATCH / "ws", SCRATCH / "claude-config"
for d in (TMP, CFG, WS, CCFG, WS / "mockups"):
    d.mkdir(parents=True, exist_ok=True)
(WS / "mockups" / "export-button.txt").write_text("[ Export CSV ] button in the report toolbar\n", encoding="utf-8")
# A main review needs a git worktree per fork (work_parallel_mcp._review_facts).
for git_args in (["init", "-q"], ["config", "user.name", "e2e"], ["config", "user.email", "e2e@example.invalid"],
                 ["add", "-A"], ["commit", "-q", "-m", "fixture"]):
    subprocess.run(["git", "-C", str(WS), *git_args], check=True, capture_output=True)
LOCK = threading.Lock()
LOG = (EVIDENCE / "driver.log").open("w", encoding="utf-8")
CONVERSATION = str(uuid.uuid4())
REQUESTER = "u-requester"
STATE: dict = {"handled": set(), "reviews": 0, "next_message": 9100, "messages": {}, "events": [], "rows": [],
               "approved_in_walkie": False, "sse_published": [], "walkie_calls": [], "reports": 0}
TITLE = "CIELE2E-WORKFLOW-" + STAMP


def log(text: str) -> None:
    line = time.strftime("%H:%M:%S ") + text
    print(line, flush=True)
    with LOCK:
        LOG.write(line + "\n")
        LOG.flush()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ------------------------------------------------------------------ Walkie stub
def root_message():
    return {"id": "9001", "conversation_id": CONVERSATION, "sender_id": REQUESTER,
            "text": "보고서 화면에 CSV 내보내기 버튼을 추가해 주세요. 승인 후 진행해 주세요."}


def thread_rows(root_id: str):
    rows = [m for m in STATE["messages"].values() if m.get("thread_root_id") == root_id]
    return sorted(rows, key=lambda m: int(m["id"]))


def walkie_tool(name: str, args: dict):
    STATE["walkie_calls"].append({"tool": name, "keys": sorted(args),
                                  "operation": args.get("operation_id"), "after": args.get("after")})
    if name == "walkie_read_messages":
        # Every message of the conversation after ``after`` (roots and thread
        # replies), ascending, one page; the approval re-check reads the request
        # message itself this way and never the thread.
        after = int(args.get("after") or 0)
        rows = [root_message()] + [m for m in STATE["messages"].values() if m["id"] != "9001"]
        rows = sorted((m for m in rows if int(m["id"]) > after), key=lambda m: int(m["id"]))[:200]
        return {"messages": rows}
    if name == "walkie_call_tool":
        inner = args.get("arguments") or {}
        root = str(inner.get("root_id") or "")
        after = int(inner.get("after") or 0)
        return [m for m in thread_rows(root) if int(m["id"]) > after]
    if name == "walkie_send_message":
        with LOCK:
            message_id = str(STATE["next_message"])
            STATE["next_message"] += 1
        text = str(args.get("text") or "")
        row = {"id": message_id, "conversation_id": args.get("conversation_id") or CONVERSATION,
               "thread_root_id": "9001", "sender_id": "u-agent", "text": text}
        if args.get("approvers"):
            row["approvers"] = list(args["approvers"])
            row["approvals"] = []
        STATE["messages"][message_id] = row
        log(f"walkie send {message_id}: {text[:80]!r}" + (" with approval buttons" if args.get("approvers") else ""))
        return {"ok": True, "message_id": message_id, "id": message_id}
    raise ValueError("unknown walkie tool " + name)


WALKIE_TOOLS = [
    {"name": "walkie_read_messages", "description": "Read conversation messages after an id.",
     "inputSchema": {"type": "object", "properties": {"conversation_id": {"type": "string"}, "after": {"type": "string"}}}},
    {"name": "walkie_call_tool", "description": "Call a Walkie operation (threads.read).",
     "inputSchema": {"type": "object", "properties": {"operation_id": {"type": "string"}, "schema_version": {"type": "integer"},
                                                      "arguments": {"type": "object"}}}},
    {"name": "walkie_send_message", "description": "Send a message; approvers adds approval buttons.",
     "inputSchema": {"type": "object", "properties": {"conversation_id": {"type": "string"}, "text": {"type": "string"},
                                                      "reply_to_message_id": {"type": "string"}, "client_id": {"type": "string"},
                                                      "approvers": {"type": "array", "items": {"type": "string"}}}}},
]


def approve_in_walkie(message_id: str) -> None:
    """The requester presses Approve; Walkie records it, but the SSE is dropped."""
    row = STATE["messages"][message_id]
    row["approvals"] = [{"id": "ap-1", "user_id": REQUESTER, "decision": "approved", "superseded_by": None,
                         "withdrawn_at": None}]
    STATE["approved_in_walkie"] = True
    log(f"walkie: {REQUESTER} approved message {message_id}; message.approved SSE intentionally dropped")


def cloud_event(kind: str, data: dict) -> dict:
    return {"specversion": "1.0", "id": "evt-" + uuid.uuid4().hex[:12], "type": "com.ciel.walkie." + kind,
            "source": "/conversations/" + CONVERSATION, "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "data": data}


# ------------------------------------------------------------------ scripted model
def texts(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(block.get("text", "") for block in content or [] if isinstance(block, dict) and block.get("type") == "text")


def anchor(messages):
    """(actor, index, payload) from the latest fork prompt; main otherwise."""
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.get("role") != "user":
            continue
        text = texts(message.get("content"))
        if "Assignment JSON:\n" in text:
            raw = text.split("Assignment JSON:\n", 1)[1].strip()
            try:
                return "work", index, json.JSONDecoder().raw_decode(raw)[0]
            except ValueError:
                return "work", index, {}
        if '"claim_generation"' in text and '"instruction"' in text:
            try:
                return "side", index, json.loads(text[text.index("{"):text.rindex("}") + 1])
            except ValueError:
                return "side", index, {}
        if "You are a planning fork" in text or "You are a review fork" in text:
            return "planner", index, {}
    return "main", -1, {}


def last_tool(messages):
    """(step, input, result text) of the last assistant tool_use and its result.

    Claude Code may put a plugin reminder in its own user row right behind the
    tool result, so every user row after the last assistant row is searched.
    """
    last = max((i for i, m in enumerate(messages) if m.get("role") == "assistant"), default=-1)
    if last < 0:
        return None, None, ""
    uses = [b for b in messages[last].get("content") or [] if isinstance(b, dict) and b.get("type") == "tool_use"]
    if not uses:
        return None, None, ""
    results = [b for m in messages[last + 1:] if isinstance(m.get("content"), list)
               for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]
    if not results:
        return None, None, ""
    use = uses[-1]
    step = use["id"].split("_")[2] if use["id"].startswith("toolu_wf_") else None
    content = results[-1].get("content")
    text = content if isinstance(content, str) else texts(content)
    return step, use.get("input"), text


def tool_name(tools, wanted):
    matches = [name for name in tools if name == wanted or name.endswith("__" + wanted)]
    if len(matches) != 1:
        raise RuntimeError("missing tool " + wanted + " in " + ",".join(sorted(tools))[:300])
    return matches[0]


def parse_json(text):
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        return json.loads(text[start:end + 1]) if start >= 0 and end > start else {}


PROPOSAL = {
    "request_restated": "Add a CSV export button to the report screen; approval required before work.",
    "analysis": "The report screen renders rows from report.py; no export exists today (read report.py and the toolbar).",
    "alternatives": "Server-side CSV endpoint (chosen: one source of truth) versus client-side export (rejected: large reports).",
    "ui_impact": {"changes": True, "detail": "A new Export CSV button in the report toolbar."},
    "benchmarks": [{"product": "Spreadsheet app", "screen": "File > Download > CSV", "source": "public help page",
                    "lesson": "Export lives next to other file actions", "decision": "adopt toolbar placement"}],
    "mockups": [{"file": "mockups/export-button.txt", "state": "default", "description": "Toolbar with the Export CSV button"}],
    "risks": "Large reports could be slow; revert by removing the endpoint and the button.",
    "spec": "Add export.csv generation; do not change the HTML report layout beyond the button.",
    "steps": [{"id": "S-1", "title": "Implement CSV export", "input": "report rows", "procedure": "write export_csv and a test",
               "done_when": "the QA command passes", "qa": ["QA-1"]}],
    "qa": [{"id": "QA-1", "claim": "CSV export has a header row", "method": "command", "how": "python -m pytest tests/test_export.py",
            "expected": "exit 0", "evidence": "pytest log", "must_fail_before": True}],
}


def main_turn(messages, tools):
    step, used, result = last_tool(messages)
    if step == "submit":
        packet = parse_json(result).get("approval_packet") or {}
        STATE["code"] = packet.get("code", "")
        STATE["root"] = used.get("root_id")
        text = (f"기술 제안서 요약: CSV 내보내기 버튼 추가. 목업 M-1 첨부, 벤치마킹 1건. 확인 코드 {STATE['code']}. "
                "아래 승인 버튼으로 승인해 주세요.")
        return ("send", tool_name(tools, "walkie_send_message"),
                {"conversation_id": CONVERSATION, "reply_to_message_id": "9001", "text": text, "approvers": [REQUESTER]})
    if step == "send":
        message_id = str(parse_json(result).get("message_id") or "")
        STATE["request_message"] = message_id
        return ("register", tool_name(tools, "work_approval_requested"),
                {"root_id": STATE["root"], "conversation_id": CONVERSATION, "message_id": message_id,
                 "thread_root_id": "9001", "approvers": [REQUESTER], "proposal_version": 1, "mockups_attached": ["M-1"]})
    if step == "register":
        STATE["registered"] = parse_json(result).get("state")
        return None, "WF-MAIN-REQUESTED " + str(STATE["registered"]), None
    if step == "status":
        goal = parse_json(result).get("goal") or {}
        lease = next((s.get("lease_id") for s in (goal.get("steps") or {}).values() if s.get("state") == "awaiting_review"), None)
        if STATE["reviews"] == 0:
            args = {"lease_id": lease, "decision": "rework", "decision_id": "wf-rework-1", "evidence": "main read the log",
                    "qa_verdicts": [{"id": "QA-1", "verdict": "fail", "note": "header row missing in the log"}],
                    "rework": {"failed_qa": ["QA-1"], "observed": "the log shows no header test",
                               "fix": "add the header row and its test", "evidence_required": "pytest log with the header test"}}
        else:
            args = {"lease_id": lease, "decision": "approve", "decision_id": "wf-approve-1", "evidence": "main read both logs",
                    "qa_verdicts": [{"id": "QA-1", "verdict": "pass", "note": "header test passes"}], "evidence_checked": True}
        return ("review", tool_name(tools, "work_parallel_item_review"), args)
    if step == "review":
        STATE.setdefault("review_results", []).append(parse_json(result).get("state") or result[:160])
        if "error" not in result[:200]:
            STATE["reviews"] += 1
            return None, "WF-MAIN-REVIEWED", None
        STATE["review_errors"] = STATE.get("review_errors", 0) + 1
        if STATE["review_errors"] <= 2:
            # Read the goal again and retry once the review is current.
            time.sleep(5)
            return ("status", tool_name(tools, "work_proposal_status"), {"root_id": STATE["root"]})
        return None, "WF-MAIN-REVIEW-FAILED", None
    # A plugin's appended row reaches the request as a reminder: fenced in a
    # user turn or as a system block; search the whole request body.
    notices = [m for m in re.finditer(r"Ciel Work workflow: goal (\S+)(?: \((.*?)\))? \[(\S*)\] is (\w+)",
                                      STATE.get("body_text", ""))]
    for match in reversed(notices):
        root, state = match.group(1), match.group(4)
        key = (root, state, STATE["reviews"] if state == "awaiting_review" else 0)
        if key in STATE["handled"]:
            break
        STATE["handled"].add(key)
        STATE["root"] = root
        if state == "proposal_due":
            return ("submit", tool_name(tools, "work_proposal_submit"), {"root_id": root, "proposal": PROPOSAL})
        if state == "awaiting_review":
            return ("status", tool_name(tools, "work_proposal_status"), {"root_id": root})
        break
    tail = []
    for message in messages[-4:]:
        content = message.get("content")
        kinds = [b.get("type") for b in content] if isinstance(content, list) else ["str"]
        tail.append({"role": message.get("role"), "kinds": kinds, "text": texts(content)[:300]})
    body_text = STATE.get("body_text", "")
    ciel = [body_text[max(0, m.start() - 120):m.start() + 240] for m in re.finditer("Ciel Work", body_text)]
    (EVIDENCE / "main-idle-debug.json").write_text(json.dumps({"count": len(messages), "tail": tail, "ciel": ciel,
                                                              "system": str(body_system(messages))[:0]},
                                                             ensure_ascii=False, indent=1), encoding="utf-8")
    return None, "WF-MAIN-IDLE", None


def body_system(_messages):
    return ""


def side_turn(payload):
    version = (payload.get("workload") or {}).get("version")
    answer = {"understanding": "보고서 화면에 CSV 내보내기 버튼 추가 요청으로 이해했습니다. 새 작업으로 등록합니다.",
              "decision": {"action": "create", "kind": "request", "title": "CSV export button",
                           "summary": "Add a CSV export button to the report screen", "reply": "요청을 등록했습니다.",
                           "priority": "P1", "note": "requested in Walkie"},
              "expected_version": version}
    return None, json.dumps(answer, ensure_ascii=False), None


def work_turn(messages, payload, tools):
    step, used, result = last_tool(messages)
    lease = payload.get("lease_id", "")
    worktree = payload.get("worktree")
    base = Path(worktree) if worktree else WS
    target = base / ".work-evidence" / lease / "qa.log"
    generation = payload.get("generation") or 1
    content = f"QA-1 python -m pytest tests/test_export.py\n{'1 passed (header)' if generation > 1 else '1 passed'}\nlease {lease}\n"
    if step is None or (step == "write" and "refused" in result):
        # The fork gate refuses a write until the host records the lease
        # binding ("retry the call after the binding is recorded").
        if step == "write":
            time.sleep(3)
        return ("write", tool_name(tools, "Write"), {"file_path": str(target), "content": content})
    if step == "report" and "refused" in result and STATE.get("report_retries", 0) < 3:
        STATE["report_retries"] = STATE.get("report_retries", 0) + 1
        return ("write", tool_name(tools, "Write"), {"file_path": str(target), "content": content})
    if step == "write":
        digest = hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else "0" * 64
        path = str(target.relative_to(base)).replace("\\", "/") if worktree else str(target)
        packet = payload.get("workflow") or {}
        qa = [row["id"] for row in packet.get("qa") or [{"id": "QA-1"}]]
        detail = {"summary": "CSV export implemented" + (" with the header row" if generation > 1 else ""),
                  "changes": "export.py, tests/test_export.py", "deviations": "", "open_issues": "",
                  "evidence_files": [{"path": path, "kind": "log", "sha256": digest}],
                  "qa_results": [{"id": q, "status": "pass", "command": "python -m pytest tests/test_export.py", "exit_code": 0,
                                  "actual": "1 passed", "evidence_files": [path]} for q in qa]}
        STATE["reports"] += 1
        STATE.setdefault("packets", []).append({"has_workflow": bool(packet), "qa": qa, "rework": bool(packet.get("rework")),
                                                "generation": generation})
        return ("report", tool_name(tools, "work_parallel_report"),
                {"lease_id": lease, "worker_token": payload.get("worker_token"), "event": "completion",
                 "plan_version": payload.get("plan_version"), "artifact": hashlib.sha256(content.encode()).hexdigest(),
                 "evidence": "qa log", "detail": detail})
    if step == "report":
        STATE.setdefault("report_results", []).append(parse_json(result).get("state") or result[:200])
        if parse_json(result).get("state") == "submitted":
            STATE["accepted_reports"] = STATE.get("accepted_reports", 0) + 1
        return None, "WF-FORK-REPORTED", None
    return None, "WF-FORK-DONE", None


class Upstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def sendj(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/e2e/stream"):
            return self.stream()
        return self.sendj({"object": "list", "data": [{"id": "stub-model", "object": "model", "type": "chat",
                                                       "context_window": 200000, "max_tokens": 8192,
                                                       "capabilities": {"tool_calling": True, "thinking": True}}]})

    def stream(self):
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.end_headers()
        sent = 0
        try:
            while True:
                with LOCK:
                    pending = STATE["events"][sent:]
                for event in pending:
                    self.wfile.write(("id: " + event["id"] + "\nevent: message\ndata: " + json.dumps(event) + "\n\n").encode())
                    self.wfile.flush()
                    sent += 1
                    STATE["sse_published"].append(event["type"])
                self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
                time.sleep(1)
        except OSError:
            return

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        if self.path.startswith("/walkie/mcp"):
            return self.walkie(json.loads(raw or b"{}"))
        body = json.loads(raw or b"{}")
        if "count_tokens" in self.path:
            return self.sendj({"input_tokens": 100})
        tools = {t.get("name"): t for t in body.get("tools") or [] if isinstance(t, dict)}
        messages = body.get("messages") or []
        STATE["body_text"] = json.dumps({"system": body.get("system"), "messages": messages}, ensure_ascii=False)
        actor, _, payload = anchor(messages)
        if not tools:
            actor = "aux"
        try:
            if actor == "aux":
                step, text, args = None, "WF-AUX", None
            elif actor == "side":
                step, text, args = side_turn(payload)
            elif actor == "work":
                step, text, args = work_turn(messages, payload, tools)
            elif actor == "planner":
                step, text, args = None, "{}", None
            else:
                step, text, args = main_turn(messages, tools)
        except Exception as error:  # the turn ends with the reason; the driver reports it
            step, text, args = None, "WF-STUB-ERROR " + type(error).__name__ + ": " + str(error)[:300], None
            log("stub error " + text)
        if step:
            content = [{"type": "tool_use", "id": "toolu_wf_" + step + "_" + uuid.uuid4().hex[:8], "name": text, "input": args}]
            reason = "tool_use"
        else:
            content = [{"type": "text", "text": text}]
            reason = "end_turn"
        STATE["rows"].append({"actor": actor, "step": step or "", "reply": (text if not step else text.rsplit("__", 1)[-1])[:60]})
        message = {"id": "msg_" + uuid.uuid4().hex[:10], "type": "message", "role": "assistant", "model": body.get("model"),
                   "content": content, "stop_reason": reason, "stop_sequence": None,
                   "usage": {"input_tokens": 100, "output_tokens": 10}}
        if not body.get("stream"):
            return self.sendj(message)
        events = [("message_start", {"type": "message_start", "message": {**message, "content": [], "stop_reason": None}})]
        for i, block in enumerate(content):
            empty = ({"type": "tool_use", "id": block["id"], "name": block["name"], "input": {}} if block["type"] == "tool_use"
                     else {"type": "text", "text": ""})
            delta = ({"type": "input_json_delta", "partial_json": json.dumps(block["input"], ensure_ascii=False)}
                     if block["type"] == "tool_use" else {"type": "text_delta", "text": block["text"]})
            events += [("content_block_start", {"type": "content_block_start", "index": i, "content_block": empty}),
                       ("content_block_delta", {"type": "content_block_delta", "index": i, "delta": delta}),
                       ("content_block_stop", {"type": "content_block_stop", "index": i})]
        events += [("message_delta", {"type": "message_delta", "delta": {"stop_reason": reason, "stop_sequence": None},
                                      "usage": {"output_tokens": 10}}), ("message_stop", {"type": "message_stop"})]
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("connection", "close")
        self.end_headers()
        try:
            for name, value in events:
                self.wfile.write(("event: " + name + "\ndata: " + json.dumps(value, ensure_ascii=False) + "\n\n").encode())
                self.wfile.flush()
        except OSError:
            pass
        self.close_connection = True

    def walkie(self, request):
        method, rid = request.get("method"), request.get("id")
        if rid is None:
            self.send_response(202)
            self.send_header("content-length", "0")
            self.end_headers()
            return
        if method == "initialize":
            result = {"protocolVersion": (request.get("params") or {}).get("protocolVersion", "2025-03-26"),
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "walkie-stub", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": WALKIE_TOOLS}
        elif method == "tools/call":
            params = request.get("params") or {}
            try:
                value = walkie_tool(params.get("name"), params.get("arguments") or {})
                result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "isError": False}
            except Exception as error:
                result = {"content": [{"type": "text", "text": str(error)}], "isError": True}
        else:
            result = {}
        self.sendj({"jsonrpc": "2.0", "id": rid, "result": result})


# ------------------------------------------------------------------ launch
UP, ROUTER = free_port(), free_port()
SERVER = ThreadingHTTPServer(("127.0.0.1", UP), Upstream)
threading.Thread(target=SERVER.serve_forever, daemon=True).start()
config = {"current_provider": "cielairouter", "language": "en", "work_mode": True, "work_auto_continue": False,
          "work_side_intake": {"enabled": True, "type": "btw_with_mcp"}, "work_workflow": {"enabled": True},
          "work_gate_check": {"minutes": 1},
          "providers": {"cielairouter": {"base_url": f"http://127.0.0.1:{UP}", "api_key": "e2e-stub-key",
                                         "current_model": "stub-model", "context_window": 200000, "max_output_tokens": 8192,
                                         "request_timeout_ms": 120000}}}
(CFG / "config.json").write_text(json.dumps(config, indent=1), encoding="utf-8")
(CCFG / ".claude.json").write_text(json.dumps({
    "hasCompletedOnboarding": True, "lastOnboardingVersion": "2.1.330", "numStartups": 3,
    "bypassPermissionsModeAccepted": True,
    "mcpServers": {"walkie": {"type": "http", "url": f"http://127.0.0.1:{UP}/walkie/mcp"}},
    "projects": {k: {"hasTrustDialogAccepted": True, "allowedTools": []}
                 for k in (str(WS), WS.as_posix(), str(WS).lower(), WS.as_posix().lower())}}), encoding="utf-8")
clear = "".join(f'for /f "delims==" %%v in (\'set {p} 2^>nul\') do set "%%v="\n'
                for p in ("CIEL_RUNTIME_", "ANTHROPIC_", "CLAUDE_", "CODEX_", "OPENAI_"))
SCRIPT = SCRATCH / "launch.cmd"
SCRIPT.write_text(
    "@echo off\n" + clear + "set CLAUDECODE=\n"
    f'set "CIEL_RUNTIME_CONFIG_DIR={CFG}"\nset "CIEL_RUNTIME_ROUTER_PORT={ROUTER}"\nset "CIEL_RUNTIME_TEST_ISOLATED=1"\n'
    f'set "CLAUDE_CONFIG_DIR={CCFG}"\nset "CIEL_RUNTIME_STARTUP_AUTO_ANSWER=1"\n'
    f'set "TEMP={TMP}"\nset "TMP={TMP}"\n'
    'set "DISABLE_AUTOUPDATER=1"\nset "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1"\nset "DISABLE_TELEMETRY=1"\n'
    f'cd /d "{WS}"\npython "{REPO / "ciel_runtime.py"}" cli --ca-runtime claude --ca-no-update-check --ca-no-self-update-check '
    '--dangerously-skip-permissions\n'
    "echo LAUNCHER-EXIT=%ERRORLEVEL%\n", encoding="utf-8")


def http(method, path, body=None, timeout=10):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{ROUTER}{path}", method=method, data=data,
                                 headers={"content-type": "application/json", "accept": "application/json"})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
    except Exception as e:
        return {"raw": f"{type(e).__name__}: {e}"}
    try:
        return json.loads(raw)
    except ValueError:
        return {"raw": raw[:300]}


def wait(pred, secs, label):
    end = time.time() + secs
    while time.time() < end:
        try:
            if pred():
                log(f"wait {label}: yes")
                return True
        except Exception:
            pass
        time.sleep(1)
    log(f"wait {label}: NO")
    return False


def launcher_pid():
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ? { $_.CommandLine -like '*ciel_runtime.py*' -and "
                          "$_.ParentProcessId -in @(Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | ? { $_.CommandLine -like '*"
                          + str(SCRIPT) + "*' } | % { $_.ProcessId }) } | % { $_.ProcessId }"], capture_output=True, text=True).stdout.split()
    return int(out[0]) if out else 0


SHOTS = EVIDENCE / "shots"
SHOTS.mkdir(exist_ok=True)


def shot(label):
    out = SHOTS / f"{len(list(SHOTS.glob('*.png'))) + 1:02d}-{label}.png"
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HERE / "shot_title.ps1"),
                        TITLE, str(out)], capture_output=True, text=True)
    log(f"shot {label}: {'saved' if 'saved' in r.stdout else r.stdout.strip()[:120] + r.stderr.strip()[:120]}")


def screen_text(label):
    # Attaching to the CLI console killed this driver once (no finally ran);
    # the PrintWindow screenshots are the screen evidence.
    return
    try:
        from console_driver import Console
        text = Console(STATE["pid"]).screen()
    except Exception as error:
        text = "screen unavailable: " + type(error).__name__
    text = text.replace(str(SCRATCH), "<scratch>").replace(str(WS), "<ws>")
    (SHOTS / (label + ".txt")).write_text(text, encoding="utf-8")


def session_identity():
    for scope in CFG.rglob("compaction-scope.json"):
        value = json.loads(scope.read_text(encoding="utf-8"))
        if value.get("session_id"):
            return value["runtime"] + ":" + value["session_id"]
    return None


def workflow_view():
    identity = session_identity()
    if not identity:
        return {}
    return http("GET", "/ca/work/api/v1/workflow?session_id=" + identity)


def goal_state():
    goals = workflow_view().get("goals") or []
    return goals[0]["state"] if goals else None


def kill_all():
    ps = ("$ports = @(" + str(ROUTER) + "); "
          "$pids = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | ? { $ports -contains $_.LocalPort } | % { $_.OwningProcess }); "
          "$pids += @(Get-CimInstance Win32_Process | ? { $_.ProcessId -ne $PID -and $_.ProcessId -ne " + str(os.getpid()) + " -and "
          "$_.Name -ne 'powershell.exe' -and -not ($_.CommandLine -like '*e2e_workflow.py*') -and "
          "($_.CommandLine -like '*" + str(SCRATCH) + "*') } | % { $_.ProcessId }); "
          "$pids | Sort-Object -Unique | % { taskkill /T /F /PID $_ | Out-Null; \"killed $_\" }")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    log("kill: " + r.stdout.strip().replace("\n", " "))


def leftovers():
    ps = ("@(Get-CimInstance Win32_Process | ? { $_.ProcessId -ne " + str(os.getpid()) + " -and "
          "-not ($_.CommandLine -like '*e2e_workflow.py*') -and $_.CommandLine -like '*" + str(SCRATCH) + "*' }).Count")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout.strip()
    return int(out or 0)


result = {"stamp": STAMP}
checks = {}
try:
    subprocess.Popen(["wt.exe", "-w", "new", "--size", "160,46", "--title", TITLE, "--suppressApplicationTitle", "cmd", "/k",
                      str(SCRIPT)])
    wait(lambda: bool(launcher_pid()), 60, "launcher process")
    STATE["pid"] = launcher_pid()
    checks["router_up"] = wait(lambda: "raw" not in http("GET", "/health", timeout=2), 120, "router up")
    checks["session_bound"] = wait(lambda: bool(session_identity()), 120, "session identity")
    receiver = http("POST", "/ca/events/receivers/default", {"enabled": True, "transport": "sse",
                                                             "url": f"http://127.0.0.1:{UP}/e2e/stream", "event_types": [],
                                                             "input_transport": "auto"})
    log("receiver ok=" + str(receiver.get("ok")))
    time.sleep(20)
    shot("started")
    screen_text("started")
    request = cloud_event("message.created", {"conversation": CONVERSATION, "message_id": "9001", "sender_id": REQUESTER,
                                              "text": root_message()["text"], "payload": {"text": root_message()["text"],
                                                                                          "sender_id": REQUESTER}})
    STATE["messages"]["9001"] = {**root_message()}
    with LOCK:
        STATE["events"].append(request)
    log("SSE request published")
    checks["1_goal_registered_proposal_due"] = wait(lambda: goal_state() in ("proposal_due", "approval_request_due",
                                                                            "awaiting_approval"), 240, "goal registered")
    view = workflow_view()
    result["no_fork_before_approval"] = not any(r["actor"] == "work" for r in STATE["rows"])
    checks["2_request_registered"] = wait(lambda: goal_state() == "awaiting_approval", 300, "approval request registered")
    result["no_fork_before_approval"] = result["no_fork_before_approval"] and not any(r["actor"] == "work" for r in STATE["rows"])
    shot("awaiting-approval")
    screen_text("awaiting-approval")
    result["awaiting_view"] = {k: (workflow_view().get("goals") or [{}])[0].get(k) for k in ("state", "owner", "blocked_reason")}
    if STATE.get("request_message"):
        approve_in_walkie(STATE["request_message"])
    checks["3_dropped_sse_recovered_by_check"] = wait(lambda: goal_state() in ("executing", "awaiting_review", "rework",
                                                                             "completed"), 300, "approval applied by the check")
    view = workflow_view()
    goal = (view.get("goals") or [{}])[0]
    result["approval"] = {"via": (goal.get("approval") or {}).get("via"), "sse_types": sorted(set(STATE["sse_published"]))}
    checks["4_fork_reported"] = wait(lambda: STATE.get("accepted_reports", 0) >= 1, 300, "fork evidence report accepted")
    checks["5_rework_requested"] = wait(lambda: STATE["reviews"] >= 1, 300, "main rework")
    shot("rework")
    screen_text("rework")
    checks["6_rework_reported"] = wait(lambda: STATE.get("accepted_reports", 0) >= 2, 300, "rework report accepted")
    checks["7_completed"] = wait(lambda: goal_state() == "completed", 400, "workflow completed")
    time.sleep(5)
    shot("completed")
    screen_text("completed")
    final = workflow_view()
    goal = (final.get("goals") or [{}])[0]
    result["final"] = {"state": goal.get("state"), "reworks": goal.get("reworks"),
                       "history": [h.get("to") for h in goal.get("history") or []],
                       "approval_via": (goal.get("approval") or {}).get("via"),
                       "proposal_ui_changes": (goal.get("proposal") or {}).get("ui_changes"),
                       "mockups_attached": (goal.get("approval_request") or {}).get("mockups_attached"),
                       "final_report": goal.get("final_report"), "metrics": final.get("metrics")}
    result["approval"]["via_final"] = result["final"]["approval_via"]
    checks["approval_via_periodic_check"] = result["final"]["approval_via"] == "check"
    checks["no_fork_before_approval"] = result["no_fork_before_approval"]
    result["packets"] = STATE.get("packets")
    result["review_results"] = STATE.get("review_results")
    result["report_results"] = STATE.get("report_results")
    result["walkie_tools"] = sorted({c["tool"] for c in STATE["walkie_calls"]})
    result["walkie_calls"] = STATE["walkie_calls"]
finally:
    result["rows"] = STATE["rows"][-80:]
    # Tool calls and results of every conversation (main and forks), no paths.
    calls = []
    for transcript in CCFG.rglob("*.jsonl"):
        for line in transcript.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                content = (json.loads(line).get("message") or {}).get("content")
            except ValueError:
                continue
            for block in content if isinstance(content, list) else []:
                if block.get("type") == "tool_use":
                    calls.append({"use": block.get("name", "")[-40:], "input": json.dumps(block.get("input"), ensure_ascii=False)[:240]})
                elif block.get("type") == "tool_result":
                    value = block.get("content")
                    value = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                    calls.append({"result": value[:240]})
    (EVIDENCE / "tool-calls.json").write_text(json.dumps(calls, ensure_ascii=False, indent=1).replace(str(SCRATCH), "<scratch>")
                                              .replace(str(SCRATCH).replace("\\", "\\\\"), "<scratch>"), encoding="utf-8")
    (SCRATCH / "stop").write_text("1")
    for log_file in CFG.rglob("router.log"):
        text = log_file.read_text(encoding="utf-8", errors="replace").replace(str(SCRATCH), "<scratch>")
        (EVIDENCE / "router-tail.log").write_text("\n".join(text.splitlines()[-400:]), encoding="utf-8")
        break
    kill_all()
    time.sleep(3)
    SERVER.shutdown()
    result["leftover_processes"] = leftovers()
    result["checks"] = checks
    (EVIDENCE / "result.json").write_text(json.dumps(result, indent=1, ensure_ascii=False, default=list), encoding="utf-8")
    shutil.rmtree(SCRATCH, ignore_errors=True)
    result["scratch_removed"] = not SCRATCH.exists()
    (EVIDENCE / "result.json").write_text(json.dumps(result, indent=1, ensure_ascii=False, default=list), encoding="utf-8")
    log("checks " + json.dumps(checks))
    log("leftover processes " + str(result["leftover_processes"]) + " scratch removed " + str(result["scratch_removed"]))
