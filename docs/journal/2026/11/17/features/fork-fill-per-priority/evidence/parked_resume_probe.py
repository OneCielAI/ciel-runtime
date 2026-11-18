"""Owned probe: can a plugin resume a PARKED native fork with $.session.send?

Stage 3 (2026-11-17) needs the Work host to resume a worker that asked a
question and ended its turn (waiting_input, slot given back) once its wait is
cleared, on the same lease, fork and worktree. Questions measured on a real
Claude Code (machine install) with a loopback model:

  1. After the fork's turn ended, does $.session.send({to:{agentId}}) deliver?
  2. Does the engine raise agent.spawn with resume.agentId equal to the fork's
     id (a deferred resume), and does the run continue under that same id?
  3. Does the resumed fork's next model request carry the sent message and the
     fork's earlier history (same conversation, not a fresh agent)?
  4. Where does the resumed fork run: the fork's own cwd (a worktree path the
     spawn set) or the parent's? Measured by a Bash `cd` probe the fork runs.
  5. A denied resume spawn: does it leave the fork parked (no run)?

No live router, provider or user config is used. Scratch is under PROBE_ROOT
(default D:\\Temp); only the redacted result.json is kept as evidence.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[8]
sys.path.insert(0, str(REPO))
from ciel_runtime_support.windows_conpty import WindowsConPtySession  # noqa: E402

OUT = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("PROBE_ROOT") or "D:/Temp")
RUN = Path(tempfile.mkdtemp(prefix="ciel-parked-resume-", dir=str(ROOT)))
CFG = RUN / "ccfg"; CFG.mkdir()
WS = RUN / "workspace"; WS.mkdir()
WT = RUN / "worktree-a"; WT.mkdir()
for folder in (WS, WT):
    subprocess.run(["git", "init", "-q", str(folder)], check=True)
(CFG / ".claude.json").write_text(json.dumps({
    "hasCompletedOnboarding": True, "theme": "dark",
    # Claude Code keys the folder by its own resolved spelling (D:/temp/... was
    # observed); every spelling is written so no trust dialog stops the probe.
    "projects": {key: {"hasTrustDialogAccepted": True} for p in (WS, WT)
                 for raw in (str(p), str(p.resolve()), os.path.realpath(p))
                 for key in {raw, raw.replace("\\", "/"), raw.replace("\\", "/").lower(),
                             raw.replace("\\", "/")[:1].upper() + raw.replace("\\", "/")[1:].lower()}},
}), encoding="utf-8")
(CFG / "settings.json").write_text(json.dumps({"permissions": {"allow": ["Bash"]}}), encoding="utf-8")
CLI = shutil.which("claude") or "claude"
SOCKET = rf"\\.\pipe\LOCAL\ciel-parked-{uuid.uuid4().hex[:16]}"
HOOK_LOG = WS / "hook-events.jsonl"

PLUGIN = RUN / "plugin"; (PLUGIN / ".claude-plugin").mkdir(parents=True); (PLUGIN / "hooks").mkdir()
(PLUGIN / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "parked-probe", "version": "0.1.0"}),
                                                   encoding="utf-8")
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
let denyResume = false
export function register(on) {
  on('session.start', async ($, e, next) => { const r = await next(e); await note($, { event: 'started' }); return r })
  on('agent.spawn', async ($, e, next) => {
    const row = { event: 'agent.spawn', resume: e.resume?.agentId || null, name: e.name || null,
      fork: e.fork === true, subagentType: e.subagentType || '', cwd: e.cwd || null,
      prompt: String(e.prompt || '').slice(0, 120) }
    if (e.resume && denyResume) { await note($, { ...row, denied: true }); return { deny: 'probe: parked stays parked' } }
    const started = await next(e)
    await note($, { ...row, started: started?.agentId || null, deny: started?.deny || null,
      origin: next.origin?.plugin || null })
    return started
  })
  on('session.receive', async ($, e, next) => {
    const text = String(e.text || '').trim()
    if (e.agentId !== undefined || !text.startsWith('[parked-probe] ')) return next(e)
    const command = text.slice('[parked-probe] '.length)
    void (async () => {
      try {
        if (command === 'spawn') {
          const spawned = await $.agent.spawn({ subagentType: 'fork', name: 'probe-fork', description: 'Parked probe fork',
            prompt: 'FORK-START: run the cwd command, then answer ASKED', cwd: WT_PATH })
          fork = spawned?.agentId || null
          await note($, { event: 'spawn', agentId: fork, deny: spawned?.deny || null })
        } else if (command === 'list') {
          const rows = await $.agent.list()
          await note($, { event: 'list', rows: rows.map(r => ({ id: r.id, status: r.status, type: r.type })) })
        } else if (command === 'send') {
          const result = await $.session.send({ to: { agentId: fork }, text: 'RESUME-NOTE: your wait is cleared, continue' })
          await note($, { event: 'send', agentId: fork, result: result ?? null })
        } else if (command === 'deny-on') {
          denyResume = true; await note($, { event: 'deny-on' })
        } else if (command === 'send-denied') {
          const result = await $.session.send({ to: { agentId: fork }, text: 'DENIED-NOTE: should not run' })
          await note($, { event: 'send-denied', agentId: fork, result: result ?? null })
        }
      } catch (error) { await note($, { event: command + '-threw', error: String(error).slice(0, 300) }) }
    })()
    return { consumed: 'parked probe' }
  })
  on('turn.complete', async ($, e, next) => {
    await note($, { event: 'turn.complete', agentId: e.agentId || null, reason: e.reason || '' })
    return next(e)
  })
}
'''.replace("WT_PATH", json.dumps(str(WT))), encoding="utf-8")

REQUESTS = []
STATE = {"fork_turns": 0}


def sse(handler, message, blocks, reason):
    events = [("message_start", {"type": "message_start", "message": {**message, "content": [], "stop_reason": None}})]
    for index, block in enumerate(blocks):
        if block["type"] == "text":
            events += [("content_block_start", {"type": "content_block_start", "index": index,
                                                "content_block": {"type": "text", "text": ""}}),
                       ("content_block_delta", {"type": "content_block_delta", "index": index,
                                                "delta": {"type": "text_delta", "text": block["text"]}})]
        else:
            events += [("content_block_start", {"type": "content_block_start", "index": index,
                                                "content_block": {**block, "input": {}}}),
                       ("content_block_delta", {"type": "content_block_delta", "index": index,
                                                "delta": {"type": "input_json_delta",
                                                          "partial_json": json.dumps(block["input"])}})]
        events.append(("content_block_stop", {"type": "content_block_stop", "index": index}))
    events += [("message_delta", {"type": "message_delta", "delta": {"stop_reason": reason, "stop_sequence": None},
                                  "usage": {"output_tokens": 10}}), ("message_stop", {"type": "message_stop"})]
    handler.send_response(200); handler.send_header("content-type", "text/event-stream")
    handler.send_header("connection", "close"); handler.end_headers()
    for name, data in events:
        handler.wfile.write(("event: " + name + "\n" + "data: " + json.dumps(data) + "\n\n").encode())
        handler.wfile.flush()
    handler.close_connection = True


def last_user_text(messages):
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        return " ".join(str(b.get("text") or b.get("content") or "") for b in content or [] if isinstance(b, dict))
    return ""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
        if "count_tokens" in self.path:
            raw = json.dumps({"input_tokens": 100}).encode()
            self.send_response(200); self.send_header("content-length", str(len(raw))); self.end_headers()
            self.wfile.write(raw); return
        messages = body.get("messages", [])
        text = json.dumps(messages, ensure_ascii=False)
        is_fork = "FORK-START" in text
        last = last_user_text(messages)
        REQUESTS.append({"at": time.time(), "fork": is_fork, "messages": len(messages),
                         "resume_note": "RESUME-NOTE" in text, "denied_note": "DENIED-NOTE" in text,
                         "has_fork_start": is_fork, "last_has_resume": "RESUME-NOTE" in last,
                         "cwd_results": [line for line in text.split("\\n") if "CWD=" in line][-3:]})
        message = {"id": "msg_" + uuid.uuid4().hex[:10], "type": "message", "role": "assistant",
                   "model": body.get("model"), "usage": {"input_tokens": 100, "output_tokens": 10}}
        if is_fork and "tool_result" not in last and ("FORK-START" in last or "RESUME-NOTE" in last):
            STATE["fork_turns"] += 1
            tag = "first" if STATE["fork_turns"] == 1 else "resumed"
            command = ("python -c \"import os,pathlib; p=pathlib.Path(os.getcwd()); print('CWD=' + p.as_posix()); "
                       "(pathlib.Path(r'" + (WS / ("cwd-" + tag + ".txt")).as_posix()
                       + "')).write_text(p.as_posix())\"")
            return sse(self, message, [{"type": "tool_use", "id": "toolu_" + tag, "name": "Bash",
                                       "input": {"command": command, "description": "print cwd"}}], "tool_use")
        reply = "ASKED" if is_fork and STATE["fork_turns"] <= 1 else ("RESUMED-DONE" if is_fork else "PROBE-DONE")
        return sse(self, message, [{"type": "text", "text": reply}], "end_turn")


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
result = {"claude": subprocess.run([CLI, "--version"], capture_output=True, text=True).stdout.strip()}


def send(token, content, priority="now"):
    frames = (json.dumps({"type": "auth", "token": token}) + "\n" + json.dumps({
        "type": "user", "uuid": str(uuid.uuid4()), "message": {"role": "user", "content": content},
        "priority": priority}, ensure_ascii=False) + "\n").encode()
    with open(SOCKET, "wb", buffering=0) as pipe:
        pipe.write(frames)


def wait_for(predicate, seconds):
    limit = time.time() + seconds
    while time.time() < limit and not predicate():
        time.sleep(0.1)
    return bool(predicate())


def hooks():
    return [json.loads(line) for line in HOOK_LOG.read_text(encoding="utf-8").splitlines()] if HOOK_LOG.exists() else []


def events(name):
    return [h for h in hooks() if h.get("event") == name]


try:
    token, deadline = "", time.time() + 60
    while time.time() < deadline and session.poll() is None and not token:
        keys = list((CFG / "sessions").glob("*.key")) if (CFG / "sessions").exists() else []
        if keys:
            token = str(json.loads(keys[0].read_text(encoding="utf-8")).get("peerToken") or "")
        time.sleep(0.25)
    result["token_found"] = len(token) == 32
    result["started"] = wait_for(lambda: bool(events("started")), 30)
    (RUN / "screen-start.txt").write_text(session.output_tail().decode("utf-8", errors="replace"), encoding="utf-8")
    if result["token_found"]:
        time.sleep(2)
        send(token, "[parked-probe] spawn")
        fork_id = None
        if wait_for(lambda: bool(events("spawn")), 60):
            fork_id = events("spawn")[0].get("agentId")
        result["fork_id_present"] = bool(fork_id)
        # The fork runs its cwd command and answers ASKED: its turn ends (parked).
        result["fork_turn_ended"] = wait_for(lambda: any(h.get("agentId") == fork_id for h in events("turn.complete")), 90)
        time.sleep(3)
        send(token, "[parked-probe] list")
        wait_for(lambda: bool(events("list")), 20)
        before_send = len(REQUESTS)
        send(token, "[parked-probe] send")
        wait_for(lambda: bool(events("send")), 30)
        result["resumed_turn_ended"] = wait_for(
            lambda: sum(h.get("agentId") == fork_id for h in events("turn.complete")) >= 2, 90)
        time.sleep(3)
        send(token, "[parked-probe] deny-on")
        wait_for(lambda: bool(events("deny-on")), 20)
        before_denied = len(REQUESTS)
        send(token, "[parked-probe] send-denied")
        wait_for(lambda: bool(events("send-denied")), 30)
        time.sleep(10)
        spawns = events("agent.spawn")
        resume_spawns = [h for h in spawns if h.get("resume")]
        after = REQUESTS[before_send:before_denied]
        result.update({
            "send_result": (events("send") or [{}])[0].get("result"),
            "list_before_send": (events("list") or [{}])[0].get("rows"),
            "spawn_events": [{k: h.get(k) for k in ("resume", "name", "fork", "subagentType", "cwd", "started",
                                                    "deny", "denied")} for h in spawns],
            "resume_spawn_same_id": bool(resume_spawns) and resume_spawns[0].get("resume") == fork_id
                and resume_spawns[0].get("started") in (fork_id, None),
            "resumed_request_carried_note": any(r["fork"] and r["resume_note"] for r in after),
            "resumed_request_kept_history": any(r["fork"] and r["resume_note"] and r["has_fork_start"] for r in after),
            "cwd_first": (WS / "cwd-first.txt").read_text(encoding="utf-8") if (WS / "cwd-first.txt").exists() else None,
            "cwd_resumed": (WS / "cwd-resumed.txt").read_text(encoding="utf-8") if (WS / "cwd-resumed.txt").exists() else None,
            "denied_send_result": (events("send-denied") or [{}])[0].get("result"),
            "denied_spawn_recorded": any(h.get("denied") for h in spawns),
            "denied_run_requests": sum(1 for r in REQUESTS[before_denied:] if r["denied_note"]),
            "turn_completes": [{k: h.get(k) for k in ("agentId", "reason")} for h in events("turn.complete")],
            "fork_id": fork_id, "worktree": WT.as_posix(), "workspace": WS.as_posix(),
        })
        result["cwd_resumed_is_fork_worktree"] = result["cwd_resumed"] == WT.as_posix()
    result["exit"] = session.poll()
    (RUN / "screen-final.txt").write_text(session.output_tail().decode("utf-8", errors="replace"), encoding="utf-8")
finally:
    try:
        session.close()
    finally:
        server.shutdown()
text = json.dumps(result, ensure_ascii=False, indent=2).replace(str(RUN).replace("\\", "/"), "<scratch>").replace(
    str(RUN), "<scratch>")
(OUT / "parked-resume-result.json").write_text(text + "\n", encoding="utf-8")
print(text)
