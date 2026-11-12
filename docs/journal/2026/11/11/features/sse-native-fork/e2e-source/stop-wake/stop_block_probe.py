"""Owned probe: what a plugin can observe after its classic.Stop returns a block.

On a real Claude Code process (claude.exe) with a loopback model and an
isolated CLAUDE_CONFIG_DIR, a probe plugin blocks the first main Stop once
with a marker reason, and records every main turn.start / turn.step /
turn.complete / classic.Stop in order. The fake model records which request
carries the marker. Questions:

  1. Does main's next model request carry the Stop block reason?
  2. Which plugin-observable event, in the same lifecycle, precedes that
     request: a new turn.start ("" text) or a turn.step of the same turn?

No live router, provider, ciel-runtime launcher or user config is used.
"""
import json
import os
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WORKTREE = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(WORKTREE))
from ciel_runtime_support.windows_conpty import WindowsConPtySession  # noqa: E402

ROOT = Path(__file__).resolve().parent
RUN = ROOT / ("stopblock-" + uuid.uuid4().hex[:8])
RUN.mkdir()
CFG = RUN / "ccfg"
CFG.mkdir()
WS = RUN / "workspace"
WS.mkdir()
(CFG / ".claude.json").write_text(json.dumps({
    "hasCompletedOnboarding": True, "theme": "dark",
    "projects": {str(WS): {"hasTrustDialogAccepted": True},
                 str(WS).replace("\\", "/"): {"hasTrustDialogAccepted": True}},
}), encoding="utf-8")
(CFG / "settings.json").write_text(json.dumps({"permissions": {"allow": []}}), encoding="utf-8")
CLI = "C:/Users/djlov/AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe"
SOCKET = rf"\\.\pipe\LOCAL\ciel-stopblock-{uuid.uuid4().hex[:16]}"
HOOK_LOG = WS / "hook-events.jsonl"
MARKER = "STOP-BLOCK-PROBE-REASON"

PLUGIN = RUN / "plugin"
(PLUGIN / ".claude-plugin").mkdir(parents=True)
(PLUGIN / "hooks").mkdir()
(PLUGIN / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "stopblock-probe", "version": "0.1.0"}),
                                                   encoding="utf-8")
(PLUGIN / "hooks/hooks.json").write_text('{"modules":["./register.js"]}', encoding="utf-8")
(PLUGIN / "hooks/register.js").write_text(r'''
let chain = Promise.resolve()
let seq = 0
function note($, row) {
  const entry = { ...row, seq: ++seq, at: Date.now() }
  chain = chain.then(async () => {
    let prior = ''
    try { prior = await $.fs.read('hook-events.jsonl') } catch {}
    await $.fs.write('hook-events.jsonl', prior + JSON.stringify(entry) + '\n')
  }).catch(() => {})
  return chain
}
let armed = false
let blocked = 0
export function register(on) {
  on('session.start', async ($, e, next) => { const r = await next(e); await note($, { event: 'session.start' }); return r })
  on('turn.start', async ($, e, next) => {
    if (e.agentId === undefined) {
      if (String(e.text || '').includes('STOP-PROBE-ON')) armed = true
      await note($, { event: 'turn.start', turnId: e.turnId, text: String(e.text || '').slice(0, 60) })
    }
    return next(e)
  })
  on('turn.step', async function* ($, e, next) {
    if (e.agentId === undefined)
      await note($, { event: 'turn.step', turnId: e.turnId, index: e.index, messageCount: e.messageCount })
    return yield* next(e)
  })
  on('turn.complete', async ($, e, next) => {
    if (e.agentId === undefined)
      await note($, { event: 'turn.complete', turnId: e.turnId, reason: e.reason || '', answer: String(e.answer || '').slice(0, 40) })
    return next(e)
  })
  on('classic.Stop', async ($, e, next) => {
    const result = await next(e)
    if (e.agent_id) return result
    if (armed && blocked === 0 && !result?.block) {
      blocked++
      await note($, { event: 'classic.Stop', decision: 'block', stop_hook_active: e.stop_hook_active ?? null })
      return { ...result, block: 'STOP-BLOCK-PROBE-REASON: answer with the word STOPPED-OK.' }
    }
    await note($, { event: 'classic.Stop', decision: 'pass', stop_hook_active: e.stop_hook_active ?? null })
    return result
  })
}
''', encoding="utf-8")

REQUESTS = []


def sse(handler, message, text):
    events = [("message_start", {"type": "message_start", "message": {**message, "content": [], "stop_reason": None}}),
              ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
              ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}),
              ("content_block_stop", {"type": "content_block_stop", "index": 0}),
              ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                                 "usage": {"output_tokens": 10}}),
              ("message_stop", {"type": "message_stop"})]
    handler.send_response(200)
    handler.send_header("content-type", "text/event-stream")
    handler.send_header("connection", "close")
    handler.end_headers()
    for name, data in events:
        handler.wfile.write(("event: " + name + "\n" + "data: " + json.dumps(data) + "\n\n").encode())
        handler.wfile.flush()
    handler.close_connection = True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
        if "count_tokens" in self.path:
            raw = json.dumps({"input_tokens": 100}).encode()
            self.send_response(200)
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        messages = body.get("messages", [])
        text = json.dumps(messages, ensure_ascii=False)
        carries = MARKER in text
        REQUESTS.append({"at": time.time(), "carries_marker": carries, "message_count": len(messages),
                         "probe_prompt": "STOP-PROBE-ON" in text})
        message = {"id": "msg_" + uuid.uuid4().hex[:10], "type": "message", "role": "assistant",
                   "model": body.get("model"), "usage": {"input_tokens": 100, "output_tokens": 10}}
        sse(self, message, "STOPPED-OK" if carries else "PROBE-DONE")


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
env = {k: v for k, v in os.environ.items()
       if not k.upper().startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI"))}
env.update({"CLAUDE_CONFIG_DIR": str(CFG), "ANTHROPIC_BASE_URL": "http://127.0.0.1:" + str(server.server_port),
            "ANTHROPIC_AUTH_TOKEN": "fixture", "ANTHROPIC_API_KEY": "", "ANTHROPIC_MODEL": "fixture",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1", "ENABLE_TOOL_SEARCH": "false"})
os.chdir(WS)
session = WindowsConPtySession(
    [CLI, "--permission-mode", "default", "--messaging-socket-path", SOCKET,
     "--plugin-dir", str(PLUGIN), "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}'],
    env, log=lambda level, message: None, mirror_output=False, forward_stdin=False)
result = {"run": str(RUN), "cli": CLI}


def send(token, content):
    frames = (json.dumps({"type": "auth", "token": token}) + "\n" + json.dumps({
        "type": "user", "uuid": str(uuid.uuid4()), "message": {"role": "user", "content": content},
        "priority": "next"}, ensure_ascii=False) + "\n").encode()
    with open(SOCKET, "wb", buffering=0) as pipe:
        pipe.write(frames)
    return time.time()


def wait_for(predicate, seconds):
    limit = time.time() + seconds
    while time.time() < limit and not predicate():
        time.sleep(0.1)
    return bool(predicate())


def hooks():
    if not HOOK_LOG.exists():
        return []
    return [json.loads(line) for line in HOOK_LOG.read_text(encoding="utf-8").splitlines() if line.strip()]


try:
    token, deadline = "", time.time() + 60
    while time.time() < deadline and session.poll() is None and not token:
        keys = list((CFG / "sessions").glob("*.key")) if (CFG / "sessions").exists() else []
        if keys:
            token = str(json.loads(keys[0].read_text(encoding="utf-8")).get("peerToken") or "")
        time.sleep(0.25)
    result["token_found"] = len(token) == 32
    result["session_started"] = wait_for(lambda: any(h.get("event") == "session.start" for h in hooks()), 30)
    if result["token_found"]:
        time.sleep(2)
        result["prompt_sent_at"] = send(token, "STOP-PROBE-ON say hi")
        wait_for(lambda: any(r["carries_marker"] for r in REQUESTS), 60)
        wait_for(lambda: sum(1 for h in hooks() if h.get("event") == "classic.Stop") >= 2, 30)
        time.sleep(3)
        (RUN / "screen.txt").write_text(session.output_tail().decode("utf-8", errors="replace"), encoding="utf-8")
    rows = hooks()
    result["hook_events"] = rows
    result["requests"] = REQUESTS
    block = next((h for h in rows if h.get("event") == "classic.Stop" and h.get("decision") == "block"), None)
    marked = next((r for r in REQUESTS if r["carries_marker"]), None)
    after = [h for h in rows if block and h["seq"] > block["seq"]]
    result["analysis"] = {
        "blocked": bool(block),
        "block_turn": next((h.get("turnId") for h in reversed(rows) if block and h["seq"] < block["seq"]
                            and h.get("event") == "turn.start"), None),
        "next_request_carries_marker": bool(marked),
        "events_after_block": [{k: h.get(k) for k in ("event", "turnId", "index", "text", "decision",
                                                     "stop_hook_active", "reason", "messageCount")} for h in after],
        "marker_request_at_minus_block_ms": round((marked["at"] * 1000) - block["at"]) if block and marked else None,
        "first_step_after_block_ms_before_request": next(
            (round((marked["at"] * 1000) - h["at"]) for h in after if h.get("event") == "turn.step"), None)
        if marked else None,
    }
    result["exit"] = session.poll()
finally:
    try:
        session.close()
    finally:
        server.shutdown()
(RUN / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({k: result.get(k) for k in ("run", "token_found", "session_started", "analysis", "exit")},
                 indent=2, ensure_ascii=False))
