"""Owned loopback probe: does a session-socket user frame invoke Claude /btw?

It starts an interactive Claude Code process with an explicit messaging socket,
sends one authenticated type=user frame whose content is `/btw ...`, and records
what reaches the loopback Anthropic gateway. No live router, provider, or user
config is used.
"""
import json, os, re, secrets, socket, subprocess, sys, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[8]))
from ciel_runtime_support.windows_conpty import WindowsConPtySession

ROOT = Path(__file__).resolve().parent
RUN = ROOT / ("btw-native-" + uuid.uuid4().hex[:8]); RUN.mkdir()
CFG = RUN / "ccfg"; CFG.mkdir()
(CFG / ".claude.json").write_text(json.dumps({
    "hasCompletedOnboarding": True, "theme": "dark",
    "projects": {str(RUN / "workspace"): {"hasTrustDialogAccepted": True},
                 str(RUN / "workspace").replace("\\", "/"): {"hasTrustDialogAccepted": True}},
}), encoding="utf-8")
WS = RUN / "workspace"; WS.mkdir()
CLI = "C:/Users/djlov/AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe"
SOCKET = rf"\\.\pipe\LOCAL\ciel-btw-{uuid.uuid4().hex[:16]}"
PLUGIN = RUN / "plugin"; (PLUGIN / ".claude-plugin").mkdir(parents=True); (PLUGIN / "hooks").mkdir()
(PLUGIN / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "btw-probe", "version": "0.1.0"}), encoding="utf-8")
(PLUGIN / "hooks/hooks.json").write_text('{"modules":["./register.js"]}', encoding="utf-8")
(PLUGIN / "hooks/register.js").write_text(r'''
export function register(on) {
  on('prompt.submit', async ($, e, next) => {
    const origin = e.origin?.kind || ''
    if (origin !== 'composer' && /^\/btw(?:\s|$)/.test(e.text || '')) {
      const result = await $.command.run({ command: 'btw', args: e.text.replace(/^\/btw\s*/, '') })
      await $.fs.write('btw-command-result.json', JSON.stringify({ origin, text: e.text, result }))
      return result?.text ? { text: result.text } : result
    }
    return next(e)
  })
}
''', encoding="utf-8")
REQUESTS = []

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
        if "count_tokens" in self.path:
            raw = json.dumps({"input_tokens": 100}).encode()
            self.send_response(200); self.send_header("content-length", str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        messages = body.get("messages", [])
        text = json.dumps(messages, ensure_ascii=False)
        REQUESTS.append({
            "path": self.path,
            "messages": messages,
            "tools": [t.get("name") for t in body.get("tools", [])],
            "has_btw_text": "/btw" in text,
            "has_probe_question": "BTW-PROBE-QUESTION" in text,
        })
        msg = {"id": "msg_" + uuid.uuid4().hex[:10], "type": "message", "role": "assistant", "model": body.get("model"),
               "content": [{"type": "text", "text": "BTW-PROBE-ANSWER"}], "stop_reason": "end_turn",
               "usage": {"input_tokens": 100, "output_tokens": 10}}
        if not body.get("stream"):
            raw = json.dumps(msg).encode(); self.send_response(200); self.send_header("content-length", str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        ev = [("message_start", {"type": "message_start", "message": {**msg, "content": [], "stop_reason": None}}),
              ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
              ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "BTW-PROBE-ANSWER"}}),
              ("content_block_stop", {"type": "content_block_stop", "index": 0}),
              ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 10}}),
              ("message_stop", {"type": "message_stop"})]
        self.send_response(200); self.send_header("content-type", "text/event-stream"); self.send_header("connection", "close"); self.end_headers()
        for name, data in ev:
            self.wfile.write(("event: " + name + "\n" + "data: " + json.dumps(data) + "\n\n").encode()); self.wfile.flush()
        self.close_connection = True

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI"))}
env.update({"CLAUDE_CONFIG_DIR": str(CFG), "ANTHROPIC_BASE_URL": "http://127.0.0.1:" + str(server.server_port),
            "ANTHROPIC_AUTH_TOKEN": "fixture", "ANTHROPIC_API_KEY": "", "ANTHROPIC_MODEL": "fixture",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1", "ENABLE_TOOL_SEARCH": "false"})
logs = []
os.chdir(WS)
session = WindowsConPtySession(
    [CLI, "--permission-mode", "default", "--messaging-socket-path", SOCKET,
     "--plugin-dir", str(PLUGIN), "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}'],
    env, log=lambda level, message: logs.append((level, message)), mirror_output=False, forward_stdin=False)
result = {"run": str(RUN), "socket": SOCKET, "session_files": [], "sent": False, "requests": REQUESTS}
try:
    deadline = time.time() + 45
    key_path = None
    sessions = CFG / "sessions"
    while time.time() < deadline and session.poll() is None:
        candidates = list(sessions.glob("*.key")) if sessions.exists() else []
        if candidates:
            key_path = candidates[0]
            payload = json.loads(key_path.read_text(encoding="utf-8"))
            token = str(payload.get("peerToken") or "")
            if len(token) == 32:
                frames = (json.dumps({"type": "auth", "token": token}) + "\n" + json.dumps({
                    "type": "user", "uuid": str(uuid.uuid4()),
                    "message": {"role": "user", "content": "/btw BTW-PROBE-QUESTION what is already known?"},
                    "priority": "next"}, ensure_ascii=False) + "\n").encode()
                with open(SOCKET, "wb", buffering=0) as pipe:
                    pipe.write(frames)
                result["sent"] = True
                result["peer_token_length"] = len(token)
                break
        time.sleep(0.25)
    time.sleep(10)
    out = session.output_tail().decode("utf-8", errors="replace")
    (RUN / "stdout.txt").write_text(out, encoding="utf-8")
    (RUN / "stderr.txt").write_text("\n".join(str(x) for x in logs), encoding="utf-8")
    result["session_files"] = [str(p.relative_to(CFG)) for p in CFG.rglob("*") if p.is_file()]
    result["key_found"] = key_path is not None
    result["exit"] = session.poll()
    result["request_count"] = len(REQUESTS)
    result["saw_btw_in_model_messages"] = any(r["has_btw_text"] or r["has_probe_question"] for r in REQUESTS)
    result["model_message_text"] = [json.dumps(req["messages"], ensure_ascii=False) for req in REQUESTS]
    result["screen_contains_btw_answer"] = "BTW-PROBE-ANSWER" in out
    bridge = RUN / "workspace" / "btw-command-result.json"
    result["bridge_file_exists"] = bridge.exists()
    if bridge.exists():
        result["bridge"] = json.loads(bridge.read_text(encoding="utf-8"))
    result["screen_tail"] = out[-6000:]
finally:
    try:
        session.close()
    finally:
        server.shutdown()
(RUN / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({k: result.get(k) for k in ("run", "sent", "key_found", "exit", "request_count", "saw_btw_in_model_messages", "screen_contains_btw_answer", "bridge_file_exists")}, indent=2))
