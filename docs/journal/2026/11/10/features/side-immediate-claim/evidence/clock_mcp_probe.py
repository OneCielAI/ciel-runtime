"""Owned probe: do a plugin's 2 s clock and its MCP calls stall while main is busy?

ciel-work's side pump runs from `$.clock.after(2000)` and claims through
`$.mcp.call` to the router. This probe runs the same pattern on a real Claude
Code process with a loopback model and a local stdio MCP server, and records
every tick time and every MCP round trip in three main states:

  idle    no main turn (10 s)
  tool    main inside a running Bash tool (30 s)
  stream  main inside an open model response stream (15 s)

No live router, provider or user config is used.
"""
import json, os, sys, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[8]))
from ciel_runtime_support.windows_conpty import WindowsConPtySession

ROOT = Path(__file__).resolve().parent
RUN = ROOT / ("clock-mcp-" + uuid.uuid4().hex[:8]); RUN.mkdir()
CFG = RUN / "ccfg"; CFG.mkdir()
WS = RUN / "workspace"; WS.mkdir()
(CFG / ".claude.json").write_text(json.dumps({
    "hasCompletedOnboarding": True, "theme": "dark",
    "projects": {str(WS): {"hasTrustDialogAccepted": True},
                 str(WS).replace("\\", "/"): {"hasTrustDialogAccepted": True}},
}), encoding="utf-8")
(CFG / "settings.json").write_text(json.dumps({"permissions": {"allow": ["Bash"]}}), encoding="utf-8")
CLI = "C:/Users/djlov/AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe"
SOCKET = rf"\\.\pipe\LOCAL\ciel-clock-{uuid.uuid4().hex[:16]}"
HOOK_LOG = WS / "hook-events.jsonl"
BUSY_MARKER = WS / "busy-started"
BUSY_COMMAND = ("python -c \"import pathlib, time; pathlib.Path(r'" + BUSY_MARKER.as_posix()
                + "').write_text(str(time.time())); time.sleep(30); print('busy-finished')\"")
SERVER = (ROOT / "probe_mcp_server.py").as_posix()
MCP_CONFIG = json.dumps({"mcpServers": {"probe": {"command": sys.executable, "args": [SERVER]}}})
PLUGIN = RUN / "plugin"; (PLUGIN / ".claude-plugin").mkdir(parents=True); (PLUGIN / "hooks").mkdir()
(PLUGIN / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "clock-probe", "version": "0.1.0"}), encoding="utf-8")
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
let started = false
export function register(on) {
  on('session.start', async ($, e, next) => {
    const result = await next(e)
    if (started) return result
    started = true
    try { await $.mcp.connect('probe'); await note($, { event: 'connected', at: Date.now() }) }
    catch (error) { await note($, { event: 'connect-failed', at: Date.now(), error: String(error).slice(0, 300) }) }
    const tick = () => $.clock.after(2000, async () => {
      const fired = Date.now()
      let row = { event: 'tick', fired }
      try {
        const answer = await $.mcp.call('probe', 'now', {})
        const text = (answer?.content || []).map(c => c.text || '').join('')
        row = { ...row, returned: Date.now(), server_at: JSON.parse(text).server_at }
      } catch (error) { row = { ...row, returned: Date.now(), error: String(error).slice(0, 200) } }
      await note($, row)
      tick()
    })
    tick()
    return result
  })
}
''', encoding="utf-8")

REQUESTS = []
STATE = {"tool_sent": False, "stream_open": None, "stream_closed": None}


def sse(handler, message, blocks, reason, hold=0.0):
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
        if hold and index == 2:
            STATE["stream_open"] = time.time()
            time.sleep(hold)
            STATE["stream_closed"] = time.time()
    handler.close_connection = True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
        if "count_tokens" in self.path:
            raw = json.dumps({"input_tokens": 100}).encode()
            self.send_response(200); self.send_header("content-length", str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        text = json.dumps(body.get("messages", []), ensure_ascii=False)
        REQUESTS.append({"at": time.time(), "has_tool_result": "tool_result" in text})
        message = {"id": "msg_" + uuid.uuid4().hex[:10], "type": "message", "role": "assistant",
                   "model": body.get("model"), "usage": {"input_tokens": 100, "output_tokens": 10}}
        if "MAIN-TOOL-START" in text and not STATE["tool_sent"] and "tool_result" not in text:
            STATE["tool_sent"] = True
            return sse(self, message, [{"type": "tool_use", "id": "toolu_busy", "name": "Bash",
                                       "input": {"command": BUSY_COMMAND, "description": "hold main busy"}}], "tool_use")
        if "MAIN-STREAM-START" in text and STATE["stream_open"] is None:
            return sse(self, message, [{"type": "text", "text": "STREAM-DONE"}], "end_turn", hold=15.0)
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
     "--plugin-dir", str(PLUGIN), "--strict-mcp-config", "--mcp-config", MCP_CONFIG],
    env, log=lambda level, message: None, mirror_output=False, forward_stdin=False)
result = {"run": str(RUN)}
marks = {}


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


try:
    token, deadline = "", time.time() + 60
    while time.time() < deadline and session.poll() is None and not token:
        keys = list((CFG / "sessions").glob("*.key")) if (CFG / "sessions").exists() else []
        if keys:
            token = str(json.loads(keys[0].read_text(encoding="utf-8")).get("peerToken") or "")
        time.sleep(0.25)
    result["token_found"] = len(token) == 32
    if result["token_found"]:
        time.sleep(2)
        marks["idle"] = [time.time()]
        time.sleep(10)
        marks["idle"].append(time.time())
        send(token, "MAIN-TOOL-START please run the busy command", "next")
        result["tool_marker_seen"] = wait_for(BUSY_MARKER.exists, 150)
        marks["tool"] = [time.time()]
        wait_for(lambda: any(r["has_tool_result"] for r in REQUESTS), 60)
        marks["tool"].append(time.time())
        time.sleep(3)
        send(token, "MAIN-STREAM-START keep streaming", "next")
        result["stream_open_seen"] = wait_for(lambda: STATE["stream_open"] is not None, 60)
        wait_for(lambda: STATE["stream_closed"] is not None, 30)
        marks["stream"] = [STATE["stream_open"], STATE["stream_closed"]]
        time.sleep(3)
    (RUN / "screen.txt").write_text(session.output_tail().decode("utf-8", errors="replace"), encoding="utf-8")
    rows = [json.loads(line) for line in HOOK_LOG.read_text(encoding="utf-8").splitlines()] if HOOK_LOG.exists() else []
    result["setup_events"] = [r for r in rows if r.get("event") != "tick"]
    ticks = [r for r in rows if r.get("event") == "tick"]
    phases = {}
    for phase, (start, end) in marks.items():
        if not start or not end:
            continue
        inside = [r for r in ticks if start <= r["fired"] / 1000 <= end]
        fired = [r["fired"] / 1000 for r in inside]
        gaps = [round(b - a, 3) for a, b in zip(fired, fired[1:])]
        rtt = [round((r["returned"] - r["fired"]) / 1000, 3) for r in inside if "returned" in r and "error" not in r]
        phases[phase] = {"window_s": round(end - start, 3), "ticks": len(inside), "max_gap_s": max(gaps) if gaps else None,
                         "gaps_s": gaps, "mcp_rtt_s": rtt, "errors": [r["error"] for r in inside if "error" in r]}
    result["phases"] = phases
    result["exit"] = session.poll()
finally:
    try:
        session.close()
    finally:
        server.shutdown()
(RUN / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({k: result.get(k) for k in ("run", "token_found", "tool_marker_seen", "stream_open_seen",
                                              "setup_events", "phases", "exit")}, indent=2))
