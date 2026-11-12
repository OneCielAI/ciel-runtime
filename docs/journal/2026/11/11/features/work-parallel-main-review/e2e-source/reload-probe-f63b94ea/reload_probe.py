"""Owned Claude Code 2.1.322 probe: is a live native fork in agent.list after a
same-process plugin hot reload, and does it survive a process restart?

(a) One claude.exe loads a --plugin-dir mod (generation A). A spawns one native
    fork whose model stream the loopback gateway holds open, so the fork stays
    running. The module file is then rewritten (generation B), which the engine
    hot-reloads in the same process. B's session.start records $.agent.list().
(b) After (a)'s evidence is written, the owned claude.exe tree is killed and
    relaunched with --continue; the new process's session.start records
    $.agent.list() and the owned process tree is recorded.

Isolation: every inherited CIEL_RUNTIME_*/CLAUDE*/ANTHROPIC*/CODEX*/OPENAI*
variable is removed; CIEL_RUNTIME_TEST_ISOLATED=1, a temp CIEL_RUNTIME_CONFIG_DIR
and a temp CLAUDE_CONFIG_DIR are set. Only processes descended from the
claude.exe this script started are ever terminated. Live profile files are
hashed before and after and scanned for any owned path.
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
RUN = Path(tempfile.mkdtemp(prefix="ciel-reload-probe-" + PROBE_ID + "-"))
EVIDENCE = EVIDENCE_PARENT / ("reload-probe-" + PROBE_ID)
EVIDENCE.mkdir(parents=True)
CFG = RUN / "ccfg"; CFG.mkdir()
CIEL_CFG = RUN / "ciel-config"; CIEL_CFG.mkdir()
WS = RUN / "workspace"; WS.mkdir()
PLUGIN = RUN / "plugin"
(PLUGIN / ".claude-plugin").mkdir(parents=True); (PLUGIN / "hooks").mkdir()
SESSION_ID = str(uuid.uuid4())
LIVE_FILES = [Path.home() / ".claude.json"] + [Path.home() / ".claude" / name for name in (
    "settings.json", "settings.local.json", "CLAUDE.md", "keybindings.json", "daemon.status.json",
    "daemon.lock", "plugins/installed_plugins.json", "plugins/known_marketplaces.json")] + [
    Path(os.environ.get("APPDATA", "")) / "ciel-runtime" / "config.json"]
PROTECTED_NAMES = {"claude.exe", "python.exe", "py.exe", "node.exe", "pythonw.exe"}


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
            # A child must be created after its parent: guards against PID reuse.
            if child["created"] >= node["created"]:
                stack.append(child)
    return out


def module(generation, spawn):
    return r'''
const GEN = %s
const BOOT = Math.random().toString(36).slice(2)
let chain = Promise.resolve()
function note($, row) {
  chain = chain.then(async () => {
    let prior = ''
    try { prior = await $.fs.read('events-' + GEN + '.jsonl') } catch {}
    await $.fs.write('events-' + GEN + '.jsonl', prior + JSON.stringify({ gen: GEN, boot: BOOT, at: Date.now(), ...row }) + '\n')
  }).catch(() => {})
  return chain
}
async function list($, label) {
  try {
    const rows = await $.agent.list()
    await note($, { event: 'agent.list', label, agents: rows.map(a => ({ id: a.id, status: a.status, name: a.name ?? null,
      type: a.type, spawnedBy: a.spawnedBy ?? null, parentId: a.parentId ?? null })) })
  } catch (error) { await note($, { event: 'agent.list-threw', label, error: String(error).slice(0, 300) }) }
}
export function register(on) {
  on('session.start', async ($, e, next) => {
    await list($, 'session.start-before-next')
    const result = await next(e)
    await list($, 'session.start-after-next')
    await note($, { event: 'session.start', isInteractive: e.isInteractive === true })
    if (%s) {
      $.clock.after(4000, async () => {
        let spawned = null
        try {
          spawned = await $.agent.spawn({ subagentType: 'fork', name: 'reload-probe-fork',
            description: 'Reload probe fork', prompt: 'RELOAD-PROBE-FORK hold this turn open.' })
          await note($, { event: 'spawn', agentId: spawned?.agentId ?? null, deny: spawned?.deny ?? null })
        } catch (error) { await note($, { event: 'spawn-threw', error: String(error).slice(0, 300) }) }
        for (let i = 0; i < 6; i++) { await $.clock.sleep(2000); await list($, 'after-spawn-' + i) }
      })
    } else {
      $.clock.after(3000, async () => { await list($, 'reloaded+3s') })
      $.clock.after(8000, async () => { await list($, 'reloaded+8s') })
    }
    return result
  })
  on('turn.complete', async ($, e, next) => {
    if (e.agentId) await note($, { event: 'turn.complete', agentId: e.agentId, reason: e.reason || '' })
    return next(e)
  })
}
''' % (json.dumps(generation), "true" if spawn else "false")


(PLUGIN / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "reload-probe", "version": "0.1.0"}), encoding="utf-8")
(PLUGIN / "hooks/hooks.json").write_text('{"modules":["./register.js"]}', encoding="utf-8")
(PLUGIN / "hooks/register.js").write_text(module("A", True), encoding="utf-8")
(CFG / ".claude.json").write_text(json.dumps({
    "hasCompletedOnboarding": True, "theme": "dark",
    "projects": {str(WS): {"hasTrustDialogAccepted": True},
                 str(WS).replace("\\", "/"): {"hasTrustDialogAccepted": True}}}), encoding="utf-8")
(CFG / "settings.json").write_text(json.dumps({"permissions": {"allow": []}}), encoding="utf-8")

GATE = {"fork_requests": [], "requests": 0, "release": threading.Event(), "hold": {}}


def sse_head(handler, message):
    handler.send_response(200)
    handler.send_header("content-type", "text/event-stream")
    handler.send_header("connection", "close")
    handler.end_headers()

    def emit(name, data):
        handler.wfile.write(("event: " + name + "\ndata: " + json.dumps(data) + "\n\n").encode())
        handler.wfile.flush()
    emit("message_start", {"type": "message_start", "message": {**message, "content": [], "stop_reason": None}})
    return emit


def finish(emit, index, text):
    emit("content_block_start", {"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}})
    emit("content_block_delta", {"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": text}})
    emit("content_block_stop", {"type": "content_block_stop", "index": index})
    emit("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                           "usage": {"output_tokens": 5}})
    emit("message_stop", {"type": "message_stop"})


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        if "count_tokens" in self.path:
            raw = json.dumps({"input_tokens": 50}).encode()
            self.send_response(200); self.send_header("content-length", str(len(raw))); self.end_headers()
            self.wfile.write(raw); return
        GATE["requests"] += 1
        text = json.dumps(body.get("messages", []), ensure_ascii=False)
        message = {"id": "msg_" + uuid.uuid4().hex[:10], "type": "message", "role": "assistant",
                   "model": body.get("model"), "usage": {"input_tokens": 50, "output_tokens": 5}}
        if "RELOAD-PROBE-FORK" in text:
            record = {"at": now(), "closed_at": None, "outcome": None}
            GATE["fork_requests"].append(record)
            emit = sse_head(self, message)
            if len(GATE["fork_requests"]) == 1:
                # The first fork request is held open: the fork is live and running.
                GATE["hold"] = record
                emit("content_block_start", {"type": "content_block_start", "index": 0,
                                             "content_block": {"type": "text", "text": ""}})
                emit("content_block_delta", {"type": "content_block_delta", "index": 0,
                                             "delta": {"type": "text_delta", "text": "holding"}})
                try:
                    deadline = now() + 900
                    while not GATE["release"].is_set() and now() < deadline:
                        emit("ping", {"type": "ping"})
                        GATE["release"].wait(3)
                    emit("content_block_stop", {"type": "content_block_stop", "index": 0})
                    emit("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn",
                                           "stop_sequence": None}, "usage": {"output_tokens": 5}})
                    emit("message_stop", {"type": "message_stop"})
                    record["outcome"] = "released"
                except OSError as error:
                    record["outcome"] = "connection closed: " + type(error).__name__
                record["closed_at"] = now()
                self.close_connection = True
                return
            finish(emit, 0, "FORK-AGAIN")
            record.update(outcome="answered", closed_at=now())
            self.close_connection = True
            return
        emit = sse_head(self, message)
        finish(emit, 0, "OK")
        self.close_connection = True


def events(generation):
    path = WS / ("events-" + generation + ".jsonl")
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows


def wait_for(predicate, seconds, step=0.25):
    end = now() + seconds
    while now() < end:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return predicate()


def launch(extra, debug_name):
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI"))}
    env.update({"CIEL_RUNTIME_TEST_ISOLATED": "1", "CIEL_RUNTIME_CONFIG_DIR": str(CIEL_CFG),
                "CLAUDE_CONFIG_DIR": str(CFG), "ANTHROPIC_BASE_URL": "http://127.0.0.1:" + str(server.server_port),
                "ANTHROPIC_AUTH_TOKEN": "fixture", "ANTHROPIC_API_KEY": "", "ANTHROPIC_MODEL": "fixture",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1",
                "ENABLE_TOOL_SEARCH": "false"})
    inherited = sorted(k for k in env if k.upper().startswith("CIEL_RUNTIME_")
                       and k not in ("CIEL_RUNTIME_TEST_ISOLATED", "CIEL_RUNTIME_CONFIG_DIR"))
    session = WindowsConPtySession(
        [CLI, "--permission-mode", "default", "--plugin-dir", str(PLUGIN), "--strict-mcp-config",
         "--mcp-config", '{"mcpServers":{}}', "--debug-file", str(RUN / debug_name), *extra],
        env, log=lambda level, message: None, mirror_output=False, forward_stdin=False)
    return session, inherited


def owned_tree(session):
    rows = processes()
    root = next((r for r in rows if r["pid"] == session.pid), None)
    return rows, (descendants(rows, root["pid"], root["created"]) if root else [])


def kill_owned(tree):
    killed = []
    for row in sorted(tree, key=lambda r: r["created"], reverse=True):
        done = subprocess.run(["taskkill", "/F", "/PID", str(row["pid"])], capture_output=True, text=True)
        killed.append({"pid": row["pid"], "name": row["name"], "rc": done.returncode})
    return killed


def screen(session, name):
    (EVIDENCE / name).write_text(session.output_tail().decode("utf-8", errors="replace"), encoding="utf-8")


def save(result):
    (EVIDENCE / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


result = {"probe": PROBE_ID, "run_dir": str(RUN), "cli": CLI, "session_id": SESSION_ID}
result["cli_version"] = subprocess.run([CLI, "--version"], capture_output=True, text=True, timeout=60,
                                       env={**{k: v for k, v in os.environ.items() if not k.upper().startswith(
                                           ("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC"))},
                                            "CLAUDE_CONFIG_DIR": str(CFG), "DISABLE_AUTOUPDATER": "1"}).stdout.strip()
live_before = {str(p): sha(p) for p in LIVE_FILES}
procs_before = processes()
protected = [r for r in procs_before if r["name"].lower() in PROTECTED_NAMES]
result["protected_count"] = len(protected)
server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
os.chdir(WS)
session = None
try:
    # ---- (a) same-process plugin hot reload ----------------------------------
    session, inherited = launch(["--session-id", SESSION_ID], "phase1-debug.txt")
    result["inherited_ciel_after_strip"] = inherited
    result["phase1_pid"] = session.pid
    result["gen_a_started"] = bool(wait_for(lambda: any(r["event"] == "session.start" for r in events("A")), 90))
    spawn = wait_for(lambda: next((r for r in events("A") if r["event"] in ("spawn", "spawn-threw")), None), 90)
    result["spawn"] = spawn
    agent_id = (spawn or {}).get("agentId")
    result["fork_request_held"] = bool(wait_for(lambda: GATE["hold"].get("at"), 60))

    def live_in_a():
        for row in events("A"):
            if row["event"] == "agent.list":
                match = next((a for a in row["agents"] if a["id"] == agent_id), None)
                if match and match["status"] in ("pending", "running"):
                    return {"label": row["label"], "at": row["at"], "row": match}
        return None
    result["a_list_live_fork"] = wait_for(live_in_a, 30)
    screen(session, "phase1-before-reload.txt")
    procs, tree = owned_tree(session)
    result["phase1_tree_before_reload"] = tree
    root_created = next(r["created"] for r in procs if r["pid"] == session.pid)
    # Hot reload only the module, in the same claude.exe.
    result["reload_written_at"] = now()
    (PLUGIN / "hooks/register.js").write_text(module("B", False), encoding="utf-8")
    result["gen_b_started"] = bool(wait_for(lambda: any(r["event"] == "session.start" for r in events("B")), 120))
    wait_for(lambda: any(r.get("label") == "reloaded+8s" for r in events("B")), 30)
    result["same_process_after_reload"] = (session.poll() is None and any(
        r["pid"] == session.pid and r["created"] == root_created for r in processes()))
    result["hold_open_after_reload"] = GATE["hold"].get("closed_at") is None
    b_boot = next((r["boot"] for r in events("B") if r["event"] == "session.start"), None)
    result["gen_b_lists"] = [{"label": r["label"], "at": r["at"],
                              "fork": next((a for a in r["agents"] if a["id"] == agent_id), None),
                              "agents": r["agents"]} for r in events("B")
                             if r["event"] == "agent.list" and r["boot"] == b_boot]
    result["events_a"] = events("A"); result["events_b_phase1"] = [r for r in events("B") if r["boot"] == b_boot]
    screen(session, "phase1-after-reload.txt")
    shutil.copyfile(RUN / "phase1-debug.txt", EVIDENCE / "phase1-debug.txt")
    result["question_a"] = {
        "live_fork_before_reload": result["a_list_live_fork"],
        "same_claude_process": result["same_process_after_reload"],
        "fork_stream_still_held": result["hold_open_after_reload"],
        "listed_in_reloaded_session_start": [
            {"label": r["label"], "status": (r["fork"] or {}).get("status")} for r in result["gen_b_lists"]],
    }
    save(result)  # Evidence for (a) is on disk before any kill.

    # ---- (b) kill the owned tree and relaunch with --continue ---------------
    procs, tree = owned_tree(session)
    result["phase1_tree_at_kill"] = tree
    result["killed"] = kill_owned(tree)
    session.close()
    time.sleep(3)
    survivors = [r for r in processes() if any(r["pid"] == t["pid"] and r["created"] == t["created"] for t in tree)]
    result["phase1_survivors_after_kill"] = survivors
    result["hold_after_kill"] = dict(GATE["hold"])
    fork_requests_before = len(GATE["fork_requests"])
    session, _ = launch(["--continue"], "phase2-debug.txt")
    result["phase2_pid"] = session.pid
    wait_for(lambda: len({r["boot"] for r in events("B") if r["event"] == "session.start"}) >= 2, 120)
    boots = []
    for row in events("B"):
        if row["event"] == "session.start" and row["boot"] not in boots:
            boots.append(row["boot"])
    p2_boot = boots[1] if len(boots) > 1 else None
    wait_for(lambda: any(r.get("label") == "reloaded+8s" and r["boot"] == p2_boot for r in events("B")), 30)
    time.sleep(5)
    result["phase2_lists"] = [{"label": r["label"], "fork": next((a for a in r["agents"] if a["id"] == agent_id), None),
                               "agents": r["agents"]} for r in events("B")
                              if r["event"] == "agent.list" and r["boot"] == p2_boot]
    result["phase2_new_fork_requests"] = len(GATE["fork_requests"]) - fork_requests_before
    procs, tree2 = owned_tree(session)
    result["phase2_tree"] = tree2
    result["phase1_rows_alive_in_phase2"] = [r for r in procs if any(
        r["pid"] == t["pid"] and r["created"] == t["created"] for t in result["phase1_tree_at_kill"])]
    screen(session, "phase2.txt")
    shutil.copyfile(RUN / "phase2-debug.txt", EVIDENCE / "phase2-debug.txt")
    debug2 = (RUN / "phase2-debug.txt").read_text(encoding="utf-8", errors="replace")
    result["phase2_orphan_lines"] = [line[:300] for line in debug2.splitlines()
                                     if "orphan" in line.lower() or "background agent" in line.lower()]
    result["question_b"] = {
        "old_process_rows_alive": result["phase1_rows_alive_in_phase2"],
        "old_fork_listed_after_continue": [
            {"label": r["label"], "status": (r["fork"] or {}).get("status")} for r in result["phase2_lists"]],
        "fork_model_requests_after_continue": result["phase2_new_fork_requests"],
        "engine_orphan_lines": result["phase2_orphan_lines"],
    }
    result["events_b_all"] = events("B")
    save(result)
finally:
    GATE["release"].set()
    if session is not None:
        try:
            _, tree = owned_tree(session)
            result["final_killed"] = kill_owned(tree)
        except Exception as error:  # noqa: BLE001
            result["final_kill_error"] = str(error)[:300]
        session.close()
    server.shutdown()
    time.sleep(2)
    procs_after = processes()
    alive = {(r["pid"], r["created"]) for r in procs_after}
    result["protected_missing_after"] = [r for r in protected if (r["pid"], r["created"]) not in alive]
    live_after = {str(p): sha(p) for p in LIVE_FILES}
    result["live_hash_changed"] = sorted(k for k in live_before if live_before[k] != live_after[k])
    owned_markers = (str(RUN), str(RUN).replace("\\", "/"), str(RUN).replace("\\", "\\\\"), PROBE_ID)
    result["owned_path_in_live_files"] = sorted(
        str(p) for p in LIVE_FILES if p.exists()
        and any(m in p.read_text(encoding="utf-8", errors="replace") for m in owned_markers))
    result["live_before"] = live_before
    result["live_after"] = live_after
    save(result)
    print(json.dumps({"probe": PROBE_ID, "evidence": str(EVIDENCE), "cli_version": result.get("cli_version"),
                      "question_a": result.get("question_a"), "question_b": result.get("question_b"),
                      "protected_missing_after": result.get("protected_missing_after"),
                      "live_hash_changed": result.get("live_hash_changed"),
                      "owned_path_in_live_files": result.get("owned_path_in_live_files")}, indent=2))
