"""Owned Claude Code probe: concurrent plugin prompts and pre-turn append.

One isolated plugin, at session.start, calls $.prompt.submit(P1) and
$.prompt.submit(P2) back-to-back without awaiting either, then calls
$.session.append(APPEND) before either plugin prompt's turn.start. A loopback
provider holds P1's model response for four seconds so P2 cannot wait on a
brief turn. The plugin records invocation/resolution and main turn events; the
gateway records which text each request carries.

Questions:
(a) When does P2 resolve, and in which model request does its text enter?
(b) Does the append issued after prompt submission but before P1 turn.start
    enter P1's first model request?

Isolation: inherited CIEL_RUNTIME_*/CLAUDE*/ANTHROPIC*/CODEX*/OPENAI* are
removed; CIEL_RUNTIME_TEST_ISOLATED=1 and temp CIEL_RUNTIME_CONFIG_DIR and
CLAUDE_CONFIG_DIR are set. Live profile files are hashed before/after. Only the
owned claude.exe process tree is terminated.
"""
import hashlib
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

SOURCE = Path(sys.argv[1]).resolve()
EVIDENCE_PARENT = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(SOURCE))
from ciel_runtime_support.windows_conpty import WindowsConPtySession  # noqa: E402

CLI = str(Path.home() / ".local" / "bin" / "claude.exe")
PROBE_ID = uuid.uuid4().hex[:8]
RUN = Path(tempfile.mkdtemp(prefix="ciel-prompt-gate-" + PROBE_ID + "-"))
EVIDENCE = EVIDENCE_PARENT / ("prompt-gate-" + PROBE_ID)
EVIDENCE.mkdir(parents=True)
CFG = RUN / "ccfg"
CFG.mkdir()
CIEL_CFG = RUN / "ciel-config"
CIEL_CFG.mkdir()
WS = RUN / "workspace"
WS.mkdir()
PLUGIN = RUN / "plugin"
(PLUGIN / ".claude-plugin").mkdir(parents=True)
(PLUGIN / "hooks").mkdir()
LIVE_FILES = [Path.home() / ".claude.json"] + [Path.home() / ".claude" / name for name in (
    "settings.json", "settings.local.json", "CLAUDE.md", "keybindings.json", "daemon.status.json",
    "daemon.lock", "plugins/installed_plugins.json", "plugins/known_marketplaces.json")] + [
    Path(os.environ.get("APPDATA", "")) / "ciel-runtime" / "config.json"]
PROTECTED_NAMES = {"claude.exe", "python.exe", "py.exe", "node.exe", "pythonw.exe"}
P1 = "PROMPT-GATE-P1-" + PROBE_ID
P2 = "PROMPT-GATE-P2-" + PROBE_ID
APPEND = "PROMPT-GATE-APPEND-" + PROBE_ID


def now():
    return time.time()


def sha(path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def processes():
    script = ("Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,"
              "@{n='Created';e={$_.CreationDate.ToUniversalTime().ToString('o')}} | ConvertTo-Json -Compress")
    done = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, timeout=120)
    rows = json.loads(done.stdout or "[]")
    return [{"pid": r["ProcessId"], "ppid": r["ParentProcessId"], "name": r["Name"], "created": r["Created"]}
            for r in rows]


def descendants(rows, root_pid, root_created):
    by_parent = {}
    for row in rows:
        by_parent.setdefault(row["ppid"], []).append(row)
    root = next((r for r in rows if r["pid"] == root_pid and r["created"] == root_created), None)
    if root is None:
        return []
    out, stack = [], [root]
    while stack:
        node = stack.pop()
        out.append(node)
        for child in by_parent.get(node["pid"], []):
            if child["created"] >= node["created"]:
                stack.append(child)
    return out


plugin_source = r'''
const P1 = __P1__
const P2 = __P2__
const APPEND = __APPEND__
let seq = 0
let chain = Promise.resolve()
function note($, row) {
  const entry = { seq: ++seq, at: Date.now(), ...row }
  chain = chain.then(async () => {
    let prior = ''
    try { prior = await $.fs.read('events.jsonl') } catch {}
    await $.fs.write('events.jsonl', prior + JSON.stringify(entry) + '\n')
  }).catch(() => {})
  return chain
}
export function register(on) {
  on('session.start', async ($, e, next) => {
    const result = await next(e)
    await note($, { event:'session.start' })
    $.clock.after(2000, async () => {
      await note($, { event:'p1.invoke' })
      const p1 = $.prompt.submit({ text:P1 }).then(
        value => note($, { event:'p1.resolve', value }),
        error => note($, { event:'p1.reject', error:String(error).slice(0, 300) }))
      await note($, { event:'p2.invoke' })
      const p2 = $.prompt.submit({ text:P2 }).then(
        value => note($, { event:'p2.resolve', value }),
        error => note($, { event:'p2.reject', error:String(error).slice(0, 300) }))
      await note($, { event:'append.invoke' })
      try {
        const value = await $.session.append({ message:{ type:'user', content:[{ type:'text', text:APPEND }] } })
        await note($, { event:'append.resolve', uuid:value?.uuid ?? null, deny:value?.deny ?? null })
      } catch (error) { await note($, { event:'append.reject', error:String(error).slice(0, 300) }) }
      await Promise.allSettled([p1, p2])
      await note($, { event:'all.settled' })
    })
    return result
  })
  on('turn.start', async ($, e, next) => {
    if (e.agentId === undefined) await note($, { event:'turn.start', turnId:e.turnId, text:String(e.text || '').slice(0, 100) })
    return next(e)
  })
  on('turn.step', async function* ($, e, next) {
    if (e.agentId === undefined) await note($, { event:'turn.step', turnId:e.turnId, index:e.index, messageCount:e.messageCount })
    return yield* next(e)
  })
  on('turn.complete', async ($, e, next) => {
    if (e.agentId === undefined) await note($, { event:'turn.complete', turnId:e.turnId, reason:e.reason || '' })
    return next(e)
  })
  on('session.append', async ($, e, next) => {
    const result = await next(e)
    if (JSON.stringify(e.message?.content ?? '').includes(APPEND))
      await note($, { event:'session.append', uuid:e.uuid ?? null, resultUuid:result?.uuid ?? null, door:e.door })
    return result
  })
}
'''.replace("__P1__", json.dumps(P1)).replace("__P2__", json.dumps(P2)).replace("__APPEND__", json.dumps(APPEND))

(PLUGIN / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "prompt-gate-probe", "version": "0.1.0"}), encoding="utf-8")
(PLUGIN / "hooks/hooks.json").write_text('{"modules":["./register.js"]}', encoding="utf-8")
(PLUGIN / "hooks/register.js").write_text(plugin_source, encoding="utf-8")
(CFG / ".claude.json").write_text(json.dumps({
    "hasCompletedOnboarding": True, "theme": "dark",
    "projects": {str(WS): {"hasTrustDialogAccepted": True},
                 str(WS).replace("\\", "/"): {"hasTrustDialogAccepted": True}}}), encoding="utf-8")
(CFG / "settings.json").write_text(json.dumps({"permissions": {"allow": []}}), encoding="utf-8")

REQUESTS = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        if "count_tokens" in self.path:
            raw = json.dumps({"input_tokens": 50}).encode()
            self.send_response(200)
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        text = json.dumps(body.get("messages", []), ensure_ascii=False)
        row = {"at": now(), "number": len(REQUESTS) + 1, "message_count": len(body.get("messages", [])),
               "p1": P1 in text, "p2": P2 in text, "append": APPEND in text}
        REQUESTS.append(row)
        message = {"id": "msg_" + uuid.uuid4().hex[:10], "type": "message", "role": "assistant",
                   "model": body.get("model"), "usage": {"input_tokens": 50, "output_tokens": 5}}
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("connection", "close")
        self.end_headers()

        def emit(name, payload):
            self.wfile.write(("event: " + name + "\ndata: " + json.dumps(payload) + "\n\n").encode())
            self.wfile.flush()
        emit("message_start", {"type": "message_start", "message": {**message, "content": [], "stop_reason": None}})
        emit("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
        emit("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "DONE"}})
        # Hold the first request open long enough to measure whether P2 resolves
        # or enters while P1's turn is running.
        if len(REQUESTS) == 1:
            time.sleep(4)
        emit("content_block_stop", {"type": "content_block_stop", "index": 0})
        emit("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                               "usage": {"output_tokens": 5}})
        emit("message_stop", {"type": "message_stop"})
        self.close_connection = True


def events():
    path = WS / "events.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows


def wait_for(predicate, seconds, step=0.1):
    end = now() + seconds
    while now() < end:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return predicate()


def event(name):
    return next((row for row in events() if row.get("event") == name), None)


clean_env = {k: v for k, v in os.environ.items()
             if not k.upper().startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI"))}
result = {"probe": PROBE_ID, "run_dir": str(RUN), "cli": CLI, "markers": {"p1": P1, "p2": P2, "append": APPEND}}
result["cli_version"] = subprocess.run([CLI, "--version"], capture_output=True, text=True, timeout=60,
                                       env={**clean_env, "CLAUDE_CONFIG_DIR": str(CFG),
                                            "DISABLE_AUTOUPDATER": "1"}).stdout.strip()
live_before = {str(p): sha(p) for p in LIVE_FILES}
protected = [row for row in processes() if row["name"].lower() in PROTECTED_NAMES]
result["protected_count"] = len(protected)
server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
env = dict(clean_env)
env.update({"CIEL_RUNTIME_TEST_ISOLATED": "1", "CIEL_RUNTIME_CONFIG_DIR": str(CIEL_CFG),
            "CLAUDE_CONFIG_DIR": str(CFG), "ANTHROPIC_BASE_URL": "http://127.0.0.1:" + str(server.server_port),
            "ANTHROPIC_AUTH_TOKEN": "fixture", "ANTHROPIC_API_KEY": "", "ANTHROPIC_MODEL": "fixture",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1",
            "ENABLE_TOOL_SEARCH": "false"})
result["inherited_ciel_after_strip"] = sorted(k for k in env if k.upper().startswith("CIEL_RUNTIME_")
                                              and k not in ("CIEL_RUNTIME_TEST_ISOLATED", "CIEL_RUNTIME_CONFIG_DIR"))
os.chdir(WS)
session = None
try:
    session = WindowsConPtySession(
        [CLI, "--permission-mode", "default", "--plugin-dir", str(PLUGIN), "--strict-mcp-config",
         "--mcp-config", '{"mcpServers":{}}', "--debug-file", str(RUN / "debug.txt")],
        env, log=lambda level, message: None, mirror_output=False, forward_stdin=False)
    result["pid"] = session.pid
    result["started"] = bool(wait_for(lambda: event("session.start"), 90))
    result["all_settled"] = bool(wait_for(lambda: event("all.settled"), 90))
    wait_for(lambda: len(REQUESTS) >= 2, 20)
    time.sleep(2)
    rows = events()
    result["events"] = rows
    result["requests"] = REQUESTS
    by = {}
    for row in rows:
        by.setdefault(row["event"], row)
    p1_req = next((r for r in REQUESTS if r["p1"]), None)
    p2_req = next((r for r in REQUESTS if r["p2"]), None)
    first_start = next((r for r in rows if r["event"] == "turn.start"), None)
    first_complete = next((r for r in rows if r["event"] == "turn.complete"), None)
    result["analysis"] = {
        "p1_invoke_to_turn_start_ms": round(first_start["at"] - by["p1.invoke"]["at"]) if first_start else None,
        "p1_invoke_to_resolve_ms": round(by["p1.resolve"]["at"] - by["p1.invoke"]["at"]) if "p1.resolve" in by else None,
        "p2_invoke_to_resolve_ms": round(by["p2.resolve"]["at"] - by["p2.invoke"]["at"]) if "p2.resolve" in by else None,
        "p2_resolved_before_first_complete": bool(by.get("p2.resolve") and first_complete
                                                   and by["p2.resolve"]["at"] < first_complete["at"]),
        "p1_request": p1_req,
        "p2_request": p2_req,
        "append_in_p1_request": bool(p1_req and p1_req["append"]),
        "append_in_p2_request": bool(p2_req and p2_req["append"]),
        "p2_in_p1_request": bool(p1_req and p1_req["p2"]),
        "turn_starts": [{k: r.get(k) for k in ("at", "turnId", "text")} for r in rows if r["event"] == "turn.start"],
        "turn_completes": [{k: r.get(k) for k in ("at", "turnId", "reason")} for r in rows if r["event"] == "turn.complete"],
        "append_invoke_before_first_turn_start": bool(by.get("append.invoke") and first_start
                                                       and by["append.invoke"]["at"] < first_start["at"]),
    }
    (EVIDENCE / "screen.txt").write_text(session.output_tail().decode("utf-8", errors="replace"), encoding="utf-8")
    shutil.copyfile(RUN / "debug.txt", EVIDENCE / "debug.txt")
    (EVIDENCE / "events.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
finally:
    if session is not None:
        try:
            rows = processes()
            root = next((r for r in rows if r["pid"] == session.pid), None)
            tree = descendants(rows, root["pid"], root["created"]) if root else []
            result["owned_tree"] = tree
            killed = []
            for row in sorted(tree, key=lambda r: r["created"], reverse=True):
                done = subprocess.run(["taskkill", "/F", "/PID", str(row["pid"])], capture_output=True, text=True)
                killed.append({"pid": row["pid"], "name": row["name"], "rc": done.returncode})
            result["killed"] = killed
        except Exception as error:  # noqa: BLE001
            result["kill_error"] = str(error)[:300]
        session.close()
    server.shutdown()
    time.sleep(2)
    alive = {(row["pid"], row["created"]) for row in processes()}
    result["protected_missing_after"] = [row for row in protected if (row["pid"], row["created"]) not in alive]
    live_after = {str(p): sha(p) for p in LIVE_FILES}
    result["live_before"] = live_before
    result["live_after"] = live_after
    result["live_hash_changed"] = sorted(k for k in live_before if live_before[k] != live_after[k])
    markers = (str(RUN), str(RUN).replace("\\", "/"), str(RUN).replace("\\", "\\\\"), PROBE_ID)
    result["owned_path_in_live_files"] = sorted(
        str(p) for p in LIVE_FILES if p.exists()
        and any(marker in p.read_text(encoding="utf-8", errors="replace") for marker in markers))
    (EVIDENCE / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"probe": PROBE_ID, "evidence": str(EVIDENCE), "cli_version": result.get("cli_version"),
                      "started": result.get("started"), "all_settled": result.get("all_settled"),
                      "analysis": result.get("analysis"), "live_hash_changed": result.get("live_hash_changed"),
                      "owned_path_in_live_files": result.get("owned_path_in_live_files"),
                      "protected_missing_after": result.get("protected_missing_after")}, indent=2, ensure_ascii=False))
