"""Owned probe: how does a session-socket user frame reach plugin hooks?

A real interactive Claude Code process runs with a loopback model and an owned
messaging socket. One authenticated `type=user` frame carrying the exact host
signal `[ciel-side-receipt-ready] <64 hex>` is written to the socket in each of
three main-session states:

  tool    main is inside a running Bash tool call (marker file proves it runs)
  stream  main is inside an open model response stream
  idle    main has no turn

A probe plugin records the time, hook, origin and text of every
`session.receive` / `prompt.submit` that carries the signal and consumes /
drops it. The loopback gateway records whether the text ever reached the model.
No live router, provider or user config is used.
"""
import json, os, sys, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[8]))
from ciel_runtime_support.windows_conpty import WindowsConPtySession

ROOT = Path(__file__).resolve().parent
RUN = ROOT / ("receipt-ready-" + uuid.uuid4().hex[:8]); RUN.mkdir()
CFG = RUN / "ccfg"; CFG.mkdir()
WS = RUN / "workspace"; WS.mkdir()
(CFG / ".claude.json").write_text(json.dumps({
    "hasCompletedOnboarding": True, "theme": "dark",
    "projects": {str(WS): {"hasTrustDialogAccepted": True},
                 str(WS).replace("\\", "/"): {"hasTrustDialogAccepted": True}},
}), encoding="utf-8")
(CFG / "settings.json").write_text(json.dumps({"permissions": {"allow": ["Bash"]}}), encoding="utf-8")
CLI = "C:/Users/djlov/AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe"
SOCKET = rf"\\.\pipe\LOCAL\ciel-ready-{uuid.uuid4().hex[:16]}"
SIGNALS = {"tool": "[ciel-side-receipt-ready] " + "a" * 64,
           "stream": "[ciel-side-receipt-ready] " + "b" * 64,
           "idle": "[ciel-side-receipt-ready] " + "c" * 64}
HOOK_LOG = WS / "hook-events.jsonl"
BUSY_MARKER = WS / "busy-started"
# A plain `sleep` is refused by the Bash tool, so hold the turn with Python and
# leave a marker at an absolute path proving the tool process is running.
BUSY_COMMAND = ("python -c \"import pathlib, time; pathlib.Path(r'" + BUSY_MARKER.as_posix()
                + "').write_text(str(time.time())); time.sleep(20); print('busy-finished')\"")
PLUGIN = RUN / "plugin"; (PLUGIN / ".claude-plugin").mkdir(parents=True); (PLUGIN / "hooks").mkdir()
(PLUGIN / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "ready-probe", "version": "0.1.0"}), encoding="utf-8")
(PLUGIN / "hooks/hooks.json").write_text('{"modules":["./register.js"]}', encoding="utf-8")
(PLUGIN / "hooks/register.js").write_text(r'''
const SIGNAL = /^\[ciel-side-receipt-ready\] [0-9a-f]{64}$/
let chain = Promise.resolve()
function note($, row) {
  chain = chain.then(async () => {
    let prior = ''
    try { prior = await $.fs.read('hook-events.jsonl') } catch {}
    await $.fs.write('hook-events.jsonl', prior + JSON.stringify(row) + '\n')
  }).catch(() => {})
  return chain
}
export function register(on) {
  on('session.receive', async ($, e, next) => {
    const text = String(e.text || '').trim()
    if (SIGNAL.test(text)) {
      const at = Date.now()
      await note($, { hook: 'session.receive', origin: e.origin?.kind || '', agentId: e.agentId || null, text, at })
      return { consumed: 'probe consumed' }
    }
    return next(e)
  })
  on('prompt.submit', async ($, e, next) => {
    const text = String(e.text || '').trim()
    if (SIGNAL.test(text)) {
      const at = Date.now()
      await note($, { hook: 'prompt.submit', origin: e.origin?.kind || '', text, at })
      return { drop: 'probe dropped' }
    }
    return next(e)
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
            # Main is now mid-stream: the first text delta is out, the response is still open.
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
        last = json.dumps((body.get("messages") or [{}])[-1], ensure_ascii=False)
        REQUESTS.append({"at": time.time(), "has_signal": "ciel-side-receipt-ready" in text,
                         "has_tool_result": "tool_result" in text, "has_stream_start": "MAIN-STREAM-START" in text})
        message = {"id": "msg_" + uuid.uuid4().hex[:10], "type": "message", "role": "assistant",
                   "model": body.get("model"), "usage": {"input_tokens": 100, "output_tokens": 10}}
        # The peer prompt is not always the last message block, so match the
        # whole request for the first tool turn (run e5be2181 missed it).
        if "MAIN-TOOL-START" in text and not STATE["tool_sent"] and "tool_result" not in text:
            STATE["tool_sent"] = True
            return sse(self, message, [{"type": "tool_use", "id": "toolu_busy", "name": "Bash",
                                       "input": {"command": BUSY_COMMAND, "description": "hold main busy"}}], "tool_use")
        if "MAIN-STREAM-START" in last and STATE["stream_open"] is None:
            return sse(self, message, [{"type": "text", "text": "STREAM-DONE"}], "end_turn", hold=15.0)
        return sse(self, message, [{"type": "text", "text": "PROBE-DONE"}], "end_turn")


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
    sent = {}
    if result["token_found"]:
        time.sleep(3)
        # Phase 1: main busy inside a running Bash tool.
        send(token, "MAIN-TOOL-START please run the busy command", "next")
        result["tool_marker_seen"] = wait_for(BUSY_MARKER.exists, 150)
        time.sleep(1)
        sent["tool"] = send(token, SIGNALS["tool"], "now")
        time.sleep(1)
        screen("screen-phase1-tool-busy.txt")
        wait_for(lambda: any(r["has_tool_result"] for r in REQUESTS), 150)
        time.sleep(3)
        screen("screen-phase1-tool-done.txt")
        # Phase 2: main busy inside an open model response stream.
        send(token, "MAIN-STREAM-START keep streaming", "next")
        result["stream_open_seen"] = wait_for(lambda: STATE["stream_open"] is not None, 60)
        time.sleep(1)
        sent["stream"] = send(token, SIGNALS["stream"], "now")
        time.sleep(1)
        screen("screen-phase2-stream-busy.txt")
        wait_for(lambda: STATE["stream_closed"] is not None, 30)
        time.sleep(3)
        screen("screen-phase2-stream-done.txt")
        # Phase 3: main idle.
        sent["idle"] = send(token, SIGNALS["idle"], "now")
        time.sleep(6)
    screen("screen.txt")
    hooks = [json.loads(line) for line in HOOK_LOG.read_text(encoding="utf-8").splitlines()] if HOOK_LOG.exists() else []
    result["sent_at"] = sent
    result["hook_events"] = hooks
    phases = {}
    for phase, text in SIGNALS.items():
        seen = [h for h in hooks if h.get("text") == text]
        phases[phase] = {"hooks": [{"hook": h["hook"], "origin": h["origin"], "agentId": h.get("agentId"),
                                    "latency_s": round(h["at"] / 1000 - sent[phase], 3) if phase in sent else None}
                                   for h in seen]}
    tool_results = [r["at"] for r in REQUESTS if r["has_tool_result"]]
    if "tool" in sent:
        phases["tool"]["main_busy_at_signal"] = bool(tool_results) and min(tool_results) > sent["tool"]
    if "stream" in sent:
        opened, closed = STATE["stream_open"], STATE["stream_closed"]
        phases["stream"]["main_busy_at_signal"] = bool(opened and closed and opened < sent["stream"] < closed)
        phases["stream"]["every_hook_before_stream_closed"] = bool(phases["stream"]["hooks"]) and bool(closed) and all(
            sent["stream"] + h["latency_s"] < closed for h in phases["stream"]["hooks"])
    result["phases"] = phases
    result["model_saw_any_signal"] = any(r["has_signal"] for r in REQUESTS)
    base = REQUESTS[0]["at"] if REQUESTS else 0
    result["requests"] = [{"t_from_first": round(r["at"] - base, 3), "has_signal": r["has_signal"],
                           "has_tool_result": r["has_tool_result"], "has_stream_start": r["has_stream_start"]}
                          for r in REQUESTS]
    result["sent_from_first"] = {k: round(v - base, 3) for k, v in sent.items()}
    result["exit"] = session.poll()
finally:
    try:
        session.close()
    finally:
        server.shutdown()
(RUN / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({k: result.get(k) for k in ("run", "token_found", "tool_marker_seen", "stream_open_seen", "phases",
                                              "model_saw_any_signal", "sent_from_first", "requests", "exit")}, indent=2))
