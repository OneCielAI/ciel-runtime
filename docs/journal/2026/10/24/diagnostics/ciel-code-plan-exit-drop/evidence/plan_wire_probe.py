"""Capture what real Claude Code sends while Plan Mode is on, and run the router's
plan-mode inference on it.

Usage: python plan_wire_probe.py <repo-root> <claude.exe> <out-dir>

Isolated: temp HOME/APPDATA/CLAUDE_CONFIG_DIR/workspace, every inherited
CIEL_RUNTIME_*/CLAUDE*/ANTHROPIC*/CODEX*/OPENAI* removed, Claude Code pointed at
an in-process loopback fake Messages endpoint. No live router or session is used.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

root, claude_exe, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
out.mkdir(parents=True, exist_ok=True)
for key in list(os.environ):
    if key.startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI")):
        os.environ.pop(key)
temp = Path(tempfile.mkdtemp(prefix="plan-wire-probe-"))
for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME"):
    os.environ[name] = str(temp)
os.environ["CIEL_RUNTIME_CONFIG_DIR"] = str(temp / "runtime")
ws = temp / "ws"
ws.mkdir()

captured: list[dict] = []
SSE = [
    ("message_start", {"type": "message_start", "message": {"id": "msg_probe", "type": "message", "role": "assistant",
        "model": "probe", "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 0}}}),
    ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "OK"}}),
    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
        "usage": {"output_tokens": 1}}),
    ("message_stop", {"type": "message_stop"}),
]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(404)
        self.end_headers()

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            body = json.loads(raw)
        except Exception:
            body = None
        if self.path.startswith("/v1/messages/count_tokens"):
            payload = json.dumps({"input_tokens": 1}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        captured.append({"path": self.path, "body": body})
        if isinstance(body, dict) and body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for event, data in SSE:
                self.wfile.write(f"event: {event}\ndata: {json.dumps(data)}\n\n".encode())
            self.wfile.flush()
            return
        payload = json.dumps({"id": "msg_probe", "type": "message", "role": "assistant", "model": "probe",
                              "content": [{"type": "text", "text": "OK"}], "stop_reason": "end_turn",
                              "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
env = dict(os.environ)
env.update({
    "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_address[1]}",
    "ANTHROPIC_API_KEY": "sk-ant-probe-not-a-real-key",
    "CLAUDE_CONFIG_DIR": str(temp / "claude"),
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "DISABLE_AUTOUPDATER": "1",
})
run = subprocess.run([claude_exe, "-p", "Reply with OK.", "--permission-mode", "plan"],
                     cwd=ws, env=env, capture_output=True, text=True, encoding="utf-8",
                     errors="replace", timeout=180)
server.shutdown()

# The router's own inference, from this repository's code.
os.chdir(ws)
sys.path.insert(0, str(root))
import ciel_runtime  # noqa: E402

policy = ciel_runtime.conversation_turn_policy()
report: dict = {"claude_exit": run.returncode, "claude_stdout": run.stdout.strip()[-200:],
                "claude_stderr_tail": run.stderr.strip()[-400:], "requests": []}
main = None
for item in captured:
    body = item["body"] if isinstance(item["body"], dict) else {}
    messages = body.get("messages") or []
    summary = []
    for message in messages:
        content = message.get("content")
        blocks = content if isinstance(content, list) else [{"type": "text", "text": str(content)}]
        summary.append({
            "role": message.get("role"),
            "keys": sorted(message.keys()),
            "block_types": [b.get("type") for b in blocks if isinstance(b, dict)],
            "plan_reminder_text": [
                text[max(0, text.find("Plan mode") - 120):text.find("Plan mode") + 240]
                for text in (str(b.get("text", "")) for b in blocks if isinstance(b, dict))
                if "Plan mode" in text
            ],
        })
    entry = {"path": item["path"], "tools": len(body.get("tools") or []), "messages": summary}
    if body.get("tools"):
        main = body
        # Messages only (no system prompt or tool schemas), redacted to structure and the
        # plan-mode reminder: Claude Code also adds CLAUDE.md files found above the workspace.
        captured_path = out / "captured-main-messages.json"
        captured_path.write_text(json.dumps(messages, indent=2, ensure_ascii=False), encoding="utf-8")
        subprocess.run([sys.executable, str(Path(__file__).with_name("redact_capture.py")), str(captured_path)], check=True)
        entry["router_plan_mode_active"] = policy.plan_mode_active(body)
        name, _ = policy.plan_mode_tool_name_for_emit(body, "ExitPlanMode", {"plan": "x"})
        entry["router_emits_ExitPlanMode_as"] = name
    report["requests"].append(entry)

if main is not None:
    # Control: the same body with an EnterPlanMode call and result in history,
    # which is what the conversation held before the native compaction.
    control = dict(main)
    control["messages"] = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_enter", "name": "EnterPlanMode", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_enter", "content": "Entered plan mode."}]},
        *main["messages"],
    ]
    report["control_with_EnterPlanMode_history"] = {
        "router_plan_mode_active": policy.plan_mode_active(control),
        "router_emits_ExitPlanMode_as": policy.plan_mode_tool_name_for_emit(control, "ExitPlanMode", {"plan": "x"})[0],
    }

(out / "plan-wire-result.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps(report, indent=2, ensure_ascii=False))
