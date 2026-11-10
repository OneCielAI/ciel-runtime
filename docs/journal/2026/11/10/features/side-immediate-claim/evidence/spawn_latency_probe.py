"""Owned probe: when does a plugin's `$.agent.spawn` of a background actor resolve?

ciel-work's pollSide holds its global `busy` flag until `$.agent.spawn`
returns, so every other side event waits that long for a claim. This probe
measures, on a real Claude Code process with a loopback model:

  t_call      plugin calls $.agent.spawn
  t_resolved  the spawn promise resolves (agentId)
  t_request   the actor's first model request reaches the gateway
  t_closed    the gateway closes that held (20 s) response stream
  t_complete  turn.complete for that agentId

in two main-session states: idle, and busy inside a running Bash tool.
No live router, provider or user config is used.
"""
import json, os, sys, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[8]))
from ciel_runtime_support.windows_conpty import WindowsConPtySession

ROOT = Path(__file__).resolve().parent
RUN = ROOT / ("spawn-latency-" + uuid.uuid4().hex[:8]); RUN.mkdir()
CFG = RUN / "ccfg"; CFG.mkdir()
WS = RUN / "workspace"; WS.mkdir()
(CFG / ".claude.json").write_text(json.dumps({
    "hasCompletedOnboarding": True, "theme": "dark",
    "projects": {str(WS): {"hasTrustDialogAccepted": True},
                 str(WS).replace("\\", "/"): {"hasTrustDialogAccepted": True}},
}), encoding="utf-8")
(CFG / "settings.json").write_text(json.dumps({"permissions": {"allow": ["Bash"]}}), encoding="utf-8")
CLI = "C:/Users/djlov/AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe"
SOCKET = rf"\\.\pipe\LOCAL\ciel-spawn-{uuid.uuid4().hex[:16]}"
HOOK_LOG = WS / "hook-events.jsonl"
BUSY_MARKER = WS / "busy-started"
BUSY_COMMAND = ("python -c \"import pathlib, time; pathlib.Path(r'" + BUSY_MARKER.as_posix()
                + "').write_text(str(time.time())); time.sleep(40); print('busy-finished')\"")
PLUGIN = RUN / "plugin"; (PLUGIN / ".claude-plugin").mkdir(parents=True); (PLUGIN / "hooks").mkdir()
(PLUGIN / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "spawn-probe", "version": "0.1.0"}), encoding="utf-8")
(PLUGIN / "hooks/hooks.json").write_text('{"modules":["./register.js"]}', encoding="utf-8")
(PLUGIN / "hooks/register.js").write_text(r'''
let chain = Promise.resolve()
function note($, row) {
  chain = chain.then(async () => {
    let prior = ''
    try { prior = await $.fs.read('hook-events.jsonl') } catch {}
    await $.fs.write('hook-events.jsonl', prior + JSON.stringify(row) + '\n')
  }).catch(() => {})
  return chain
}
const SPAWN = /^\[spawn-probe\] (idle|busy)$/
export function register(on) {
  on('session.start', async ($, e, next) => {
    const result = await next(e)
    await $.agent.register({ name: 'probe-actor', description: 'Spawn latency probe actor',
      prompt: 'Answer with one word.', model: 'inherit', effort: 'low', tools: [],
      background: true, maxTurns: 1, omitClaudeMd: true })
    await note($, { event: 'registered', at: Date.now() })
    return result
  })
  on('session.receive', async ($, e, next) => {
    const match = SPAWN.exec(String(e.text || '').trim())
    if (!match || e.agentId !== undefined) return next(e)
    const phase = match[1]
    const call = Date.now()
    void (async () => {
      try {
        const spawned = await $.agent.spawn({ subagentType: 'spawn-probe:probe-actor',
          description: 'Probe actor ' + phase, prompt: 'SPAWN-PROBE-' + phase.toUpperCase() })
        await note($, { event: 'spawn-resolved', phase, call, resolved: Date.now(),
          agentId: spawned?.agentId || null, deny: spawned?.deny || null })
      } catch (error) {
        await note($, { event: 'spawn-threw', phase, call, at: Date.now(), error: String(error).slice(0, 300) })
      }
    })()
    return { consumed: 'spawn probe' }
  })
  on('turn.complete', async ($, e, next) => {
    if (e.agentId) await note($, { event: 'turn.complete', agentId: e.agentId, reason: e.reason || '', at: Date.now() })
    return next(e)
  })
}
''', encoding="utf-8")

REQUESTS = []
STATE = {"tool_sent": False, "actor": {}}


def sse(handler, message, blocks, reason, hold_key=None):
    events = [("message_start", {"type": "message_start", "message": {**message, "content": [], "stop_reason": None}})]
    for index, block in enumerate(blocks):
        if block["type"] == "text":
            events += [("content_block_start", {"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}}),
                       ("content_block_delta", {"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": block["text"]}})]
        else:
            events += [("content_block_start", {"type": "content_block_start", "index": index, "content_block": {**block, "input": {}}}),
                       ("content_block_delta", {"type": "content_block_delta", "index": index, "delta": {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}})]
        events.append(("content_block_stop", {"type": "content_block_stop", "index": index}))
    events += [("message_delta", {"type": "message_delta", "delta": {"stop_reason": reason, "stop_sequence": None}, "usage": {"output_tokens": 10}}),
               ("message_stop", {"type": "message_stop"})]
    handler.send_response(200); handler.send_header("content-type", "text/event-stream"); handler.send_header("connection", "close"); handler.end_headers()
    for index, (name, data) in enumerate(events):
        handler.wfile.write(("event: " + name + "\n" + "data: " + json.dumps(data) + "\n\n").encode()); handler.wfile.flush()
        if hold_key and index == 2:
            time.sleep(20)
            STATE["actor"][hold_key]["closed"] = time.time()
    handler.close_connection = True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
        if "count_tokens" in self.path:
            raw = json.dumps({"input_tokens": 100}).encode()
            self.send_response(200); self.send_header("content-length", str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        # Match the whole request: the prompt is not always the last block
        # (run cd21debd answered the actors without holding them).
        text = json.dumps(body.get("messages", []), ensure_ascii=False)
        REQUESTS.append({"at": time.time(), "last": text[-200:]})
        message = {"id": "msg_" + uuid.uuid4().hex[:10], "type": "message", "role": "assistant",
                   "model": body.get("model"), "usage": {"input_tokens": 100, "output_tokens": 10}}
        for phase in ("IDLE", "BUSY"):
            if "SPAWN-PROBE-" + phase in text and phase not in STATE["actor"]:
                STATE["actor"][phase] = {"request": time.time()}
                return sse(self, message, [{"type": "text", "text": "ACTOR-DONE"}], "end_turn", hold_key=phase)
        if "MAIN-TOOL-START" in text and not STATE["tool_sent"] and "tool_result" not in text:
            STATE["tool_sent"] = True
            return sse(self, message, [{"type": "tool_use", "id": "toolu_busy", "name": "Bash",
                                       "input": {"command": BUSY_COMMAND, "description": "hold main busy"}}], "tool_use")
        return sse(self, message, [{"type": "text", "text": "PROBE-DONE"}], "end_turn")


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI"))}
env.update({"CLAUDE_CONFIG_DIR": str(CFG), "ANTHROPIC_BASE_URL": "http://127.0.0.1:" + str(server.server_port),
            "ANTHROPIC_AUTH_TOKEN": "fixture", "ANTHROPIC_API_KEY": "", "ANTHROPIC_MODEL": "fixture",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1", "ENABLE_TOOL_SEARCH": "false"})
os.chdir(WS)
session = WindowsConPtySession(
    [CLI, "--permission-mode", "default", "--messaging-socket-path", SOCKET,
     "--plugin-dir", str(PLUGIN), "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}'],
    env, log=lambda level, message: None, mirror_output=False, forward_stdin=False)
result = {"run": str(RUN)}


def send(token, content, priority):
    frames = (json.dumps({"type": "auth", "token": token}) + "\n" + json.dumps({
        "type": "user", "uuid": str(uuid.uuid4()), "message": {"role": "user", "content": content},
        "priority": priority}, ensure_ascii=False) + "\n").encode()
    with open(SOCKET, "wb", buffering=0) as pipe:
        pipe.write(frames)
    return time.time()


def wait_for(predicate, seconds):
    limit = time.time() + seconds
    while time.time() < limit and not predicate():
        time.sleep(0.1)
    return bool(predicate())


def hooks():
    return [json.loads(line) for line in HOOK_LOG.read_text(encoding="utf-8").splitlines()] if HOOK_LOG.exists() else []


def screen(name):
    (RUN / name).write_text(session.output_tail().decode("utf-8", errors="replace"), encoding="utf-8")


try:
    token, deadline = "", time.time() + 60
    while time.time() < deadline and session.poll() is None and not token:
        keys = list((CFG / "sessions").glob("*.key")) if (CFG / "sessions").exists() else []
        if keys:
            token = str(json.loads(keys[0].read_text(encoding="utf-8")).get("peerToken") or "")
        time.sleep(0.25)
    result["token_found"] = len(token) == 32
    result["registered"] = wait_for(lambda: any(h.get("event") == "registered" for h in hooks()), 30)
    sent = {}
    if result["token_found"]:
        time.sleep(2)
        # Phase idle.
        sent["idle"] = send(token, "[spawn-probe] idle", "now")
        wait_for(lambda: "closed" in STATE["actor"].get("IDLE", {}), 60)
        time.sleep(4)
        screen("screen-idle.txt")
        # Phase busy: main inside a running Bash tool.
        send(token, "MAIN-TOOL-START please run the busy command", "next")
        result["tool_marker_seen"] = wait_for(BUSY_MARKER.exists, 150)
        time.sleep(1)
        sent["busy"] = send(token, "[spawn-probe] busy", "now")
        time.sleep(3)
        screen("screen-busy.txt")
        wait_for(lambda: "closed" in STATE["actor"].get("BUSY", {}), 60)
        time.sleep(4)
        screen("screen-busy-done.txt")
    rows = hooks()
    result["hook_events"] = rows
    phases = {}
    for phase in ("idle", "busy"):
        resolved = next((h for h in rows if h.get("event") in ("spawn-resolved", "spawn-threw") and h.get("phase") == phase), None)
        actor = STATE["actor"].get(phase.upper(), {})
        agent = resolved.get("agentId") if resolved else None
        complete = next((h for h in rows if h.get("event") == "turn.complete" and agent and h.get("agentId") == agent), None)
        base = sent.get(phase)
        rel = lambda value: round(value - base, 3) if value and base else None
        phases[phase] = {
            "spawn": resolved and {k: resolved.get(k) for k in ("event", "agentId", "deny", "error")},
            "call_s": rel(resolved["call"] / 1000) if resolved else None,
            "resolved_s": rel(resolved["resolved"] / 1000) if resolved and resolved.get("resolved") else None,
            "actor_request_s": rel(actor.get("request")),
            "actor_stream_closed_s": rel(actor.get("closed")),
            "turn_complete_s": rel(complete["at"] / 1000) if complete else None,
        }
    result["phases"] = phases
    result["tool_result_requests_after_busy_signal"] = sum(
        1 for r in REQUESTS if sent.get("busy") and r["at"] > sent["busy"] and "tool_result" in r["last"])
    result["exit"] = session.poll()
finally:
    try:
        session.close()
    finally:
        server.shutdown()
(RUN / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({k: result.get(k) for k in ("run", "token_found", "registered", "tool_marker_seen", "phases",
                                              "tool_result_requests_after_busy_signal", "exit")}, indent=2))
