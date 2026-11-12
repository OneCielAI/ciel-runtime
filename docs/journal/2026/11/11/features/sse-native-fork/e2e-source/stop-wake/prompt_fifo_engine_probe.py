"""Owned Claude Code probe: the shipped side-intake.js prompt FIFO on the real engine.

The plugin imports the repository's side-intake.js (argv[3], hashed) and, while
main is idle, calls sidePromptSubmit for 'parallel:A' (P1) and 'side:B' (P2)
back-to-back, then 'side:B' again with other text. The provider holds P1's
response four seconds. Recorded: every engine $.prompt.submit call (debug log),
main turn events, sidePromptPending at each step, and which request carries
which text.

Questions:
(a) Is only one $.prompt.submit in progress, the second called after P1's turn
    started, and does P2 still start its own turn after P1's turn ends?
(b) Is the duplicate 'side:B' call shared (never submitted)?
(c) Is 'side:B' pending at P1's Stop (so the review gate would skip it)?

Isolation: inherited CIEL_RUNTIME_*/CLAUDE*/ANTHROPIC*/CODEX*/OPENAI* are
removed; CIEL_RUNTIME_TEST_ISOLATED=1 and temp CIEL_RUNTIME_CONFIG_DIR and
CLAUDE_CONFIG_DIR are set. Live profile files are hashed before/after. Only the
owned claude.exe process tree is terminated.
"""
import datetime
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
RUN = Path(tempfile.mkdtemp(prefix="ciel-prompt-fifo-" + PROBE_ID + "-"))
SIDE_INTAKE = Path(sys.argv[3]).resolve()
EVIDENCE = EVIDENCE_PARENT / ("prompt-fifo-" + PROBE_ID)
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
P1 = "PROMPT-FIFO-P1-" + PROBE_ID
P2 = "PROMPT-FIFO-P2-" + PROBE_ID
APPEND = "PROMPT-FIFO-UNUSED-" + PROBE_ID


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
import { sidePromptSubmit, sidePromptPending } from './side-intake.js'
const P1 = __P1__
const P2 = __P2__
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
const pending = () => ({ a:sidePromptPending('parallel:A'), b:sidePromptPending('side:B') })
export function register(on) {
  on('session.start', async ($, e, next) => {
    const result = await next(e)
    await note($, { event:'session.start' })
    $.clock.after(2000, async () => {
      await note($, { event:'p1.invoke' })
      const p1 = sidePromptSubmit($, 'parallel:A', P1).then(value => note($, { event:'p1.resolve', value, pending:pending() }))
      await note($, { event:'p2.invoke' })
      const p2 = sidePromptSubmit($, 'side:B', P2).then(value => note($, { event:'p2.resolve', value, pending:pending() }))
      const dup = sidePromptSubmit($, 'side:B', P2 + '-DUPLICATE')
      await note($, { event:'after.invoke', pending:pending(), duplicateShared:dup === p2 ? null : 'distinct-promise' })
      await Promise.allSettled([p1, p2, dup])
      await note($, { event:'all.settled', pending:pending(), duplicate:await dup })
    })
    return result
  })
  on('turn.start', async ($, e, next) => {
    if (e.agentId === undefined) await note($, { event:'turn.start', turnId:e.turnId, text:String(e.text || '').slice(0, 100), pending:pending() })
    return next(e)
  })
  on('turn.step', async function* ($, e, next) {
    if (e.agentId === undefined) await note($, { event:'turn.step', turnId:e.turnId, index:e.index, messageCount:e.messageCount })
    return yield* next(e)
  })
  on('classic.Stop', async ($, e, next) => {
    const result = await next(e)
    await note($, { event:'stop', stop_hook_active:e.stop_hook_active ?? null, pending:pending() })
    return result
  })
  on('turn.complete', async ($, e, next) => {
    if (e.agentId === undefined) await note($, { event:'turn.complete', turnId:e.turnId, reason:e.reason || '', pending:pending() })
    return next(e)
  })
}
'''.replace("__P1__", json.dumps(P1)).replace("__P2__", json.dumps(P2))

(PLUGIN / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "prompt-fifo-probe", "version": "0.1.0"}), encoding="utf-8")
(PLUGIN / "hooks/hooks.json").write_text('{"modules":["./register.js"]}', encoding="utf-8")
(PLUGIN / "hooks/register.js").write_text(plugin_source, encoding="utf-8")
shutil.copyfile(SIDE_INTAKE, PLUGIN / "hooks/side-intake.js")
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
               "p1": P1 in text, "p2": P2 in text, "dup": (P2 + "-DUPLICATE") in text}
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
result = {"probe": PROBE_ID, "run_dir": str(RUN), "cli": CLI, "markers": {"p1": P1, "p2": P2},
          "side_intake_sha256": sha(SIDE_INTAKE)}
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
    debug = (RUN / "debug.txt").read_text(encoding="utf-8", errors="replace").splitlines()
    submit_lines = [line[:200] for line in debug if "$.prompt.submit (prompt-fifo-probe)" in line]
    result["submit_lines"] = submit_lines
    p2_submit_at = None
    if len(submit_lines) >= 2:
        stamp = submit_lines[1].split(" ")[0]
        p2_submit_at = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000
    result["analysis"] = {
        "engine_submit_calls": len(submit_lines),
        "second_submit_after_first_turn_start_ms": round(p2_submit_at - first_start["at"]) if p2_submit_at and first_start else None,
        "p1_invoke_to_resolve_ms": round(by["p1.resolve"]["at"] - by["p1.invoke"]["at"]) if "p1.resolve" in by else None,
        "p2_invoke_to_resolve_ms": round(by["p2.resolve"]["at"] - by["p2.invoke"]["at"]) if "p2.resolve" in by else None,
        "p2_resolved_after_first_complete": bool(by.get("p2.resolve") and first_complete
                                                 and by["p2.resolve"]["at"] > first_complete["at"]),
        "pending_after_invoke": by.get("after.invoke", {}).get("pending"),
        "pending_at_first_stop": next((r.get("pending") for r in rows if r["event"] == "stop"), None),
        "pending_all_settled": by.get("all.settled", {}).get("pending"),
        "duplicate_result": by.get("all.settled", {}).get("duplicate"),
        "p1_request": p1_req,
        "p2_request": p2_req,
        "p2_in_p1_request": bool(p1_req and p1_req["p2"]),
        "duplicate_text_in_any_request": any(r.get("dup") for r in REQUESTS),
        "turn_starts": [{k: r.get(k) for k in ("at", "turnId", "text")} for r in rows if r["event"] == "turn.start"],
        "turn_completes": [{k: r.get(k) for k in ("at", "turnId", "reason")} for r in rows if r["event"] == "turn.complete"],
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
