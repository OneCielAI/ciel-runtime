"""Owned probe: how a plugin's notice reaches a busy/idle main and a running fork.

On a real Claude Code process with a loopback model:

  append-busy   main is inside a running Bash tool; the plugin calls
                $.session.append({message:{type:'user', ...}}). Does main's next
                model request carry it, right after the tool result?
  append-idle   main has no turn; the same append. Does it start a turn, and
                does the next prompt's request carry it?
  send-fork     a background agent is inside a running Bash tool; the plugin
                calls $.session.send({to:{agentId}}). Is it delivered, and does
                the agent's next request carry it, without a new spawn?

No live router, provider or user config is used.
"""
import json, os, sys, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[8]))
from ciel_runtime_support.windows_conpty import WindowsConPtySession

ROOT = Path(__file__).resolve().parent
RUN = ROOT / ("delivery-" + uuid.uuid4().hex[:8]); RUN.mkdir()
CFG = RUN / "ccfg"; CFG.mkdir()
WS = RUN / "workspace"; WS.mkdir()
(CFG / ".claude.json").write_text(json.dumps({
    "hasCompletedOnboarding": True, "theme": "dark",
    "projects": {str(WS): {"hasTrustDialogAccepted": True},
                 str(WS).replace("\\", "/"): {"hasTrustDialogAccepted": True}},
}), encoding="utf-8")
(CFG / "settings.json").write_text(json.dumps({"permissions": {"allow": ["Bash"]}}), encoding="utf-8")
CLI = "C:/Users/djlov/AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe"
SOCKET = rf"\\.\pipe\LOCAL\ciel-deliver-{uuid.uuid4().hex[:16]}"
HOOK_LOG = WS / "hook-events.jsonl"


def busy(marker, seconds):
    path = (WS / marker).as_posix()
    return ("python -c \"import pathlib, time; pathlib.Path(r'" + path
            + "').write_text(str(time.time())); time.sleep(" + str(seconds) + "); print('busy-finished')\"")


PLUGIN = RUN / "plugin"; (PLUGIN / ".claude-plugin").mkdir(parents=True); (PLUGIN / "hooks").mkdir()
(PLUGIN / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "delivery-probe", "version": "0.1.0"}), encoding="utf-8")
(PLUGIN / "hooks/hooks.json").write_text('{"modules":["./register.js"]}', encoding="utf-8")
(PLUGIN / "hooks/register.js").write_text(r'''
let chain = Promise.resolve()
function note($, row) {
  chain = chain.then(async () => {
    let prior = ''
    try { prior = await $.fs.read('hook-events.jsonl') } catch {}
    await $.fs.write('hook-events.jsonl', prior + JSON.stringify({ ...row, at: Date.now() }) + '\n')
  }).catch(() => {})
  return chain
}
let fork = null
export function register(on) {
  on('session.start', async ($, e, next) => {
    const result = await next(e)
    await $.agent.register({ name: 'probe-worker', description: 'Delivery probe worker',
      prompt: 'Run the command you are given, then answer with one word.', model: 'inherit', effort: 'low',
      tools: ['Bash'], background: true, maxTurns: 4, omitClaudeMd: true })
    await note($, { event: 'registered' })
    return result
  })
  on('agent.spawn', async ($, e, next) => {
    await note($, { event: 'agent.spawn', subagentType: e.subagentType || '', description: e.description || '' })
    return next(e)
  })
  on('session.receive', async ($, e, next) => {
    const text = String(e.text || '').trim()
    if (e.agentId !== undefined || !text.startsWith('[delivery-probe] ')) return next(e)
    const command = text.slice('[delivery-probe] '.length)
    void (async () => {
      try {
        if (command === 'append-busy' || command === 'append-idle') {
          const result = await $.session.append({ message: { type: 'user', content: [{ type: 'text', text: 'APPEND-NOTE-' + command }] } })
          await note($, { event: command, result: result ?? null })
        } else if (command === 'spawn-fork') {
          const spawned = await $.agent.spawn({ subagentType: 'delivery-probe:probe-worker', description: 'Probe fork', prompt: 'FORK-START run the busy command' })
          fork = spawned?.agentId || null
          await note($, { event: 'spawn-fork', agentId: fork, deny: spawned?.deny || null })
        } else if (command === 'send-fork') {
          const result = await $.session.send({ to: { agentId: fork }, text: 'SEND-NOTE-to-fork' })
          await note($, { event: 'send-fork', agentId: fork, result: result ?? null })
        }
      } catch (error) { await note($, { event: command + '-threw', error: String(error).slice(0, 300) }) }
    })()
    return { consumed: 'delivery probe' }
  })
  on('turn.complete', async ($, e, next) => {
    await note($, { event: 'turn.complete', agentId: e.agentId || null, reason: e.reason || '' })
    return next(e)
  })
}
''', encoding="utf-8")

REQUESTS = []
STATE = {"main_tool": False, "fork_tool": False}


def sse(handler, message, blocks, reason):
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
    for name, data in events:
        handler.wfile.write(("event: " + name + "\n" + "data: " + json.dumps(data) + "\n\n").encode()); handler.wfile.flush()
    handler.close_connection = True


def shape(messages):
    """Role/block sequence only, to check tool_use/tool_result pairing."""
    rows = []
    for message in messages:
        content = message.get("content")
        kinds = [content and "text"] if isinstance(content, str) else [
            (block.get("type") + (":" + block.get("text", "")[:40] if block.get("type") == "text" else "")) for block in content or []]
        rows.append(message.get("role") + "[" + ", ".join(kinds) + "]")
    return rows


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
        if "count_tokens" in self.path:
            raw = json.dumps({"input_tokens": 100}).encode()
            self.send_response(200); self.send_header("content-length", str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        messages = body.get("messages", [])
        text = json.dumps(messages, ensure_ascii=False)
        is_fork = "FORK-START" in text
        REQUESTS.append({"at": time.time(), "fork": is_fork, "shape": shape(messages)[-6:],
                         "append_busy": "APPEND-NOTE-append-busy" in text, "append_idle": "APPEND-NOTE-append-idle" in text,
                         "send_note": "SEND-NOTE-to-fork" in text})
        message = {"id": "msg_" + uuid.uuid4().hex[:10], "type": "message", "role": "assistant",
                   "model": body.get("model"), "usage": {"input_tokens": 100, "output_tokens": 10}}
        if is_fork and not STATE["fork_tool"]:
            STATE["fork_tool"] = True
            return sse(self, message, [{"type": "tool_use", "id": "toolu_fork", "name": "Bash",
                                       "input": {"command": busy("fork-busy", 15), "description": "hold fork busy"}}], "tool_use")
        if not is_fork and "MAIN-TOOL-START" in text and not STATE["main_tool"] and "tool_result" not in text:
            STATE["main_tool"] = True
            return sse(self, message, [{"type": "tool_use", "id": "toolu_main", "name": "Bash",
                                       "input": {"command": busy("main-busy", 15), "description": "hold main busy"}}], "tool_use")
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
marks = {}


def send(token, content, priority="now"):
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
    if result["token_found"]:
        time.sleep(2)
        # 1. append while main is inside a running tool.
        send(token, "MAIN-TOOL-START please run the busy command", "next")
        result["main_busy_seen"] = wait_for((WS / "main-busy").exists, 150)
        time.sleep(1)
        marks["append_busy"] = send(token, "[delivery-probe] append-busy")
        wait_for(lambda: any(r["append_busy"] for r in REQUESTS), 60)
        time.sleep(4)
        screen("screen-append-busy.txt")
        # 2. append while main is idle: no turn expected; the next prompt carries it.
        marks["append_idle"] = send(token, "[delivery-probe] append-idle")
        before = len(REQUESTS)
        time.sleep(8)
        result["requests_after_idle_append"] = len(REQUESTS) - before
        screen("screen-append-idle.txt")
        marks["next_prompt"] = send(token, "NEXT-PROMPT after idle append", "next")
        wait_for(lambda: len(REQUESTS) > before, 30)
        time.sleep(3)
        # 3. message a running fork.
        marks["spawn_fork"] = send(token, "[delivery-probe] spawn-fork")
        result["fork_busy_seen"] = wait_for((WS / "fork-busy").exists, 150)
        time.sleep(1)
        marks["send_fork"] = send(token, "[delivery-probe] send-fork")
        wait_for(lambda: any(r["send_note"] for r in REQUESTS), 60)
        time.sleep(5)
        screen("screen-send-fork.txt")
    rows = hooks()
    result["hook_events"] = rows
    base = REQUESTS[0]["at"] if REQUESTS else 0
    result["marks"] = {k: round(v - base, 3) for k, v in marks.items()}
    result["requests"] = [{"t": round(r["at"] - base, 3), **{k: r[k] for k in ("fork", "append_busy", "append_idle", "send_note", "shape")}}
                          for r in REQUESTS]
    first = lambda key: next((r for r in REQUESTS if r[key]), None)
    busy_first = first("append_busy")
    result["append_busy"] = {"carried": bool(busy_first), "request_after_mark_s": round(busy_first["at"] - marks["append_busy"], 3) if busy_first and "append_busy" in marks else None,
                             "shape": busy_first["shape"] if busy_first else None}
    idle_first = first("append_idle")
    result["append_idle"] = {"started_turn": result.get("requests_after_idle_append", 0) > 0,
                             "carried_by_next_prompt": bool(idle_first) and idle_first["at"] >= marks.get("next_prompt", 0)}
    note = first("send_note")
    result["send_fork"] = {"delivered_to_fork_request": bool(note and note["fork"]),
                           "request_after_mark_s": round(note["at"] - marks["send_fork"], 3) if note and "send_fork" in marks else None,
                           "shape": note["shape"] if note else None,
                           "spawns": [h for h in rows if h.get("event") == "agent.spawn"]}
    result["exit"] = session.poll()
finally:
    try:
        session.close()
    finally:
        server.shutdown()
(RUN / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({k: result.get(k) for k in ("run", "token_found", "registered", "main_busy_seen", "fork_busy_seen",
                                              "append_busy", "append_idle", "send_fork", "marks", "exit")}, indent=2, ensure_ascii=False))
print(json.dumps([{k: h.get(k) for k in ("event", "result", "agentId", "deny", "error", "reason")} for h in result.get("hook_events", [])], ensure_ascii=False))
