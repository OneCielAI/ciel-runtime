"""Owned real-engine probe of the per-key prompt registry (Claude Code, isolated).

The plugin is named ciel-work so it owns the `promptPending` $.state key. Its
hooks/side-intake.js is the exact draft file under test (sha256 recorded); a
small driver hooks/register.js imports only the registry exports from it.

Phase F (state freshness): dispatch D1 reads `legacyWakes`, sleeps 3 s with
$.clock.sleep, and reads again; dispatch D2 (a timer) writes it in between.
Does a long-lived dispatch see another dispatch's write? (The registry
finalizer and the idle-window callback read through a captured $.)

Phase 1 (idle path, unresolved head): while main is idle the driver issues
key parallel:A, key side:B, and parallel:A again. The driver's own
prompt.submit hook holds A's prompt for HOLD_MS (longer than the 5 s idle
window). Measured: both keys issued at once; one A submission; whether B
waits behind A in the engine; registry rows; A released as uncertain by the
idle window and the registry row removed; whether A's prompt still enters.

Phase 2 (hot reload with a registered unresolved key): generation A issues
parallel:R, held in prompt.submit for RELOAD_HOLD_MS, and the driver file is
rewritten (generation B). Measured: whether the reload happens while that hook
is held; whether B's side-intake.js instance is fresh; hydration; B's own
sidePromptSubmit(parallel:R) is not submitted; B's idle window removes the row;
whether R's prompt enters.

Isolation: every inherited CIEL_RUNTIME_*/CLAUDE*/ANTHROPIC*/CODEX*/OPENAI*
variable is removed; CIEL_RUNTIME_TEST_ISOLATED=1 and temp
CIEL_RUNTIME_CONFIG_DIR and CLAUDE_CONFIG_DIR are set; a loopback gateway is
the provider. Live profile files are hashed before and after. Only the owned
claude.exe process tree is terminated.
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
SIDE_INTAKE = Path(sys.argv[2]).resolve()
EVIDENCE_PARENT = Path(sys.argv[3]).resolve()
sys.path.insert(0, str(SOURCE))
from ciel_runtime_support.windows_conpty import WindowsConPtySession  # noqa: E402

CLI = str(Path.home() / ".local" / "bin" / "claude.exe")
PROBE_ID = uuid.uuid4().hex[:8]
RUN = Path(tempfile.mkdtemp(prefix="ciel-prompt-registry-" + PROBE_ID + "-"))
EVIDENCE = EVIDENCE_PARENT / ("prompt-registry-" + PROBE_ID)
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
HOLD_MS = 8000
RELOAD_HOLD_MS = 9000
MARK = {name: "PROMPT-REGISTRY-" + name + "-" + PROBE_ID for name in ("A", "A2", "B", "R", "R2")}


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


DRIVER = r'''
import { sidePromptSubmit, sidePromptPending, sidePromptHydrate, sidePromptTurnStart, sidePromptTurnComplete,
  sideMainBusy } from './side-intake.js'
const GEN = __GEN__
const M = __MARK__
const HOLD_MS = __HOLD__
const RELOAD_HOLD_MS = __RELOAD_HOLD__
const REG = { plugin:'ciel-work', key:'promptPending' }
const SCRATCH = { plugin:'ciel-work', key:'legacyWakes' }
let working = false
sideMainBusy(() => working)
let seq = 0
let chain = Promise.resolve()
function note($, row) {
  const entry = { gen:GEN, seq:++seq, at:Date.now(), ...row }
  chain = chain.then(async () => {
    let prior = ''
    try { prior = await $.fs.read('events-' + GEN + '.jsonl') } catch {}
    await $.fs.write('events-' + GEN + '.jsonl', prior + JSON.stringify(entry) + '\n')
  }).catch(() => {})
  return chain
}
async function registry($) {
  try {
    const held = await $.state.get(REG)
    return { version:held.version, keys:(Array.isArray(held.value) ? held.value : []).map(row => row.key) }
  } catch (error) { return { error:String(error).slice(0, 200) } }
}
const pendingView = () => ({ A:sidePromptPending('parallel:A'), B:sidePromptPending('side:B', 'b'.repeat(16)),
  R:sidePromptPending('parallel:R') })
function marker(text) { return Object.keys(M).find(name => String(text || '').includes(M[name])) || null }
let held = { A:false, R:false }
export function register(on) {
  on('session.start', async ($, e, next) => {
    const result = await next(e)
    const before = pendingView()
    await sidePromptHydrate($)
    await note($, { event:'session.start', pendingBeforeHydrate:before, pendingAfterHydrate:pendingView(),
      registry:await registry($) })
    if (GEN === 'A') {
      // Phase F: D1 long-lived read, D2 write in between.
      $.clock.after(1500, async () => {
        let first = null, second = null
        try { first = await $.state.get(SCRATCH) } catch (error) { first = { error:String(error).slice(0, 200) } }
        await note($, { event:'fresh.d1.first', read:first })
        try { await $.clock.sleep(3000) } catch (error) { await note($, { event:'fresh.d1.sleep-error', error:String(error).slice(0, 200) }) }
        try { second = await $.state.get(SCRATCH) } catch (error) { second = { error:String(error).slice(0, 200) } }
        await note($, { event:'fresh.d1.second', read:second })
      })
      $.clock.after(2500, async () => {
        let wrote = null
        try { wrote = await $.state.set(SCRATCH, ['fresh-' + GEN], { ifVersion:0 }) } catch (error) { wrote = { error:String(error).slice(0, 200) } }
        await note($, { event:'fresh.d2.write', wrote })
      })
      // Phase 1 inside this hook dispatch (run 35a040a8: the plugin's own
      // prompt.submit hook was not raised for prompts issued from a timer).
      // The three calls are back to back, with no await between them.
      const invokedAt = Date.now()
      const a = sidePromptSubmit($, 'parallel:A', M.A)
      const b = sidePromptSubmit($, 'side:B', M.B, { ref:'b'.repeat(16) })
      const a2 = sidePromptSubmit($, 'parallel:A', M.A2)
      const pendingNow = pendingView()
      await note($, { event:'invoked', invokedAt, sameAPromise:a === a2, distinctB:a !== b, pending:pendingNow })
      a.then(value => note($, { event:'a.resolve', value, resolvedAt:Date.now(), pending:pendingView() }))
      a2.then(value => note($, { event:'a2.resolve', value, resolvedAt:Date.now() }))
      b.then(value => note($, { event:'b.resolve', value, resolvedAt:Date.now(), pending:pendingView() }))
      $.clock.after(300, async () => note($, { event:'registry.after-issue', registry:await registry($), pending:pendingView() }))
      $.clock.after(5600, async () => note($, { event:'registry.after-window', registry:await registry($), pending:pendingView() }))
    } else {
      // Generation B: the same key again is never submitted while registered.
      const r2 = sidePromptSubmit($, 'parallel:R', M.R2)
      await note($, { event:'r2.invoke', pending:pendingView() })
      r2.then(async value => note($, { event:'r2.resolve', value, pending:pendingView(), registry:await registry($) }))
      $.clock.after(300, async () => note($, { event:'b.registry.after-start', registry:await registry($), pending:pendingView() }))
    }
    return result
  })
  on('prompt.submit', async ($, e, next) => {
    const name = marker(e.text)
    await note($, { event:'hook.prompt.submit', name, working, head:String(e.text || '').slice(0, 60),
      origin:e.origin ? JSON.stringify(e.origin).slice(0, 120) : null })
    const wait = name === 'A' && !held.A ? HOLD_MS : name === 'R' && !held.R ? RELOAD_HOLD_MS : 0
    if (wait) {
      held[name] = true
      await note($, { event:'hold.begin', name, ms:wait })
      try { await $.clock.sleep(wait, { signal:next.signal }) }
      catch (error) { await note($, { event:'hold.error', name, error:String(error).slice(0, 200) }) }
      await note($, { event:'hold.end', name })
      // If A's prompt never starts a turn, phase 2 still runs once main is idle.
      if (name === 'A' && GEN === 'A') $.clock.after(12000, () => { if (!working) startPhase2($, 'a-fallback') })
    }
    const result = await next(e)
    if (name) await note($, { event:'hook.prompt.result', name, drop:result?.drop ?? null })
    return result
  })
  on('turn.start', async ($, e, next) => {
    if (e.agentId === undefined) {
      working = true; sidePromptTurnStart(); turnName = marker(e.text)
      await note($, { event:'turn.start', turnId:e.turnId, name:turnName })
    }
    return next(e)
  })
  on('turn.complete', async ($, e, next) => {
    const result = await next(e)
    if (e.agentId === undefined) {
      working = false; sidePromptTurnComplete($)
      await note($, { event:'turn.complete', turnId:e.turnId, reason:e.reason || '', name:turnName })
      if (GEN === 'A' && turnName === 'A') await startPhase2($, 'a-turn-complete')
    }
    return result
  })
}
let phase2 = false
let turnName = null
// Issued inside a hook dispatch so the plugin's own prompt.submit hook can hold it.
async function startPhase2($, why) {
  if (phase2) return
  phase2 = true
  const r = sidePromptSubmit($, 'parallel:R', M.R)
  await note($, { event:'r.invoke', why, pending:pendingView() })
  r.then(value => note($, { event:'r.resolve', value, resolvedAt:Date.now() }))
  $.clock.after(400, async () => note($, { event:'phase2.ready', registry:await registry($), pending:pendingView() }))
}
'''


def driver(gen):
    return (DRIVER.replace("__GEN__", json.dumps(gen)).replace("__MARK__", json.dumps(MARK))
            .replace("__HOLD__", str(HOLD_MS)).replace("__RELOAD_HOLD__", str(RELOAD_HOLD_MS)))


(PLUGIN / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "ciel-work", "version": "0.0.1-probe"}),
                                                   encoding="utf-8")
(PLUGIN / "hooks/hooks.json").write_text('{"modules":["./register.js"]}', encoding="utf-8")
shutil.copyfile(SIDE_INTAKE, PLUGIN / "hooks/side-intake.js")
(PLUGIN / "hooks/register.js").write_text(driver("A"), encoding="utf-8")
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
        REQUESTS.append({"at": now(), "number": len(REQUESTS) + 1, "message_count": len(body.get("messages", [])),
                         **{name: text.count(value) for name, value in MARK.items()}})
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
        emit("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "OK"}})
        emit("content_block_stop", {"type": "content_block_stop", "index": 0})
        emit("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                               "usage": {"output_tokens": 5}})
        emit("message_stop", {"type": "message_stop"})
        self.close_connection = True


def rows(gen=None):
    out = []
    for path in sorted(WS.glob("events-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if gen is None or row.get("gen") == gen:
                out.append(row)
    return sorted(out, key=lambda r: (r.get("at") or 0, r.get("gen"), r.get("seq") or 0))


def first(event, gen=None):
    return next((r for r in rows(gen) if r.get("event") == event), None)


def wait_for(predicate, seconds, step=0.1):
    end = now() + seconds
    while now() < end:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return predicate()


clean_env = {k: v for k, v in os.environ.items()
             if not k.upper().startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI"))}
result = {"probe": PROBE_ID, "run_dir": str(RUN), "cli": CLI, "markers": MARK, "hold_ms": HOLD_MS,
          "reload_hold_ms": RELOAD_HOLD_MS, "side_intake": str(SIDE_INTAKE), "side_intake_sha256": sha(SIDE_INTAKE),
          "driver_a_sha256": hashlib.sha256(driver("A").encode()).hexdigest(),
          "driver_b_sha256": hashlib.sha256(driver("B").encode()).hexdigest(),
          "probe_script_sha256": sha(Path(__file__))}
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
    result["started"] = bool(wait_for(lambda: first("session.start", "A"), 90))
    result["phase1_invoked"] = bool(wait_for(lambda: first("invoked", "A"), 60))
    result["a_settled"] = bool(wait_for(lambda: first("a.resolve", "A"), 40))
    result["phase2_ready"] = bool(wait_for(lambda: first("phase2.ready", "A"), 60))
    if result["phase2_ready"]:
        result["reload_written_at"] = now()
        (PLUGIN / "hooks/register.js").write_text(driver("B"), encoding="utf-8")
        result["b_started"] = bool(wait_for(lambda: first("session.start", "B"), 40))
        wait_for(lambda: first("r2.resolve", "B"), 40)
        wait_for(lambda: any(r["R"] for r in REQUESTS), 15)
    time.sleep(3)
    all_rows = rows()
    result["events"] = all_rows
    result["requests"] = REQUESTS

    a_invoke = (first("invoked", "A") or {}).get("invokedAt")

    def resolved_ms(event, gen):
        row = first(event, gen)
        return row["resolvedAt"] - a_invoke if row and a_invoke and row.get("resolvedAt") else None
    result["analysis"] = {
        "fresh_first": (first("fresh.d1.first", "A") or {}).get("read"),
        "fresh_write": (first("fresh.d2.write", "A") or {}).get("wrote"),
        "fresh_second": (first("fresh.d1.second", "A") or {}).get("read"),
        "invoked": first("invoked", "A"),
        "hook_arrivals_ms": [{"name": r["name"], "ms": r["at"] - a_invoke, "working": r.get("working")}
                             for r in all_rows if r.get("event") == "hook.prompt.submit" and a_invoke],
        "holds": [{k: r.get(k) for k in ("gen", "event", "name", "at", "error")} for r in all_rows
                  if str(r.get("event", "")).startswith("hold.")],
        "registry_after_issue": first("registry.after-issue", "A"),
        "registry_after_window": first("registry.after-window", "A"),
        "a_resolve": first("a.resolve", "A"),
        "a_resolve_ms": resolved_ms("a.resolve", "A"),
        "a2_resolve": first("a2.resolve", "A"),
        "b_resolve": first("b.resolve", "A"),
        "b_resolve_ms": resolved_ms("b.resolve", "A"),
        "turn_start_ms": [{"name": r.get("name"), "gen": r["gen"], "ms": r["at"] - a_invoke} for r in all_rows
                          if r.get("event") == "turn.start" and a_invoke],
        "turns": [{k: r.get(k) for k in ("gen", "event", "at", "turnId", "name", "reason")} for r in all_rows
                  if r.get("event") in ("turn.start", "turn.complete")],
        "prompt_results": [{k: r.get(k) for k in ("gen", "name", "at", "drop")} for r in all_rows
                           if r.get("event") == "hook.prompt.result"],
        "phase2_ready": first("phase2.ready", "A"),
        "r_resolve": first("r.resolve", "A"),
        "b_session_start": first("session.start", "B"),
        "b_registry_after_start": first("b.registry.after-start", "B"),
        "r2_invoke": first("r2.invoke", "B"),
        "r2_resolve": first("r2.resolve", "B"),
        "old_activity_after_reload": [r for r in all_rows if r.get("gen") == "A" and result.get("reload_written_at")
                                      and r["at"] / 1000 > result["reload_written_at"]],
        "requests_by_marker": {name: [r["number"] for r in REQUESTS if r[name]] for name in MARK},
        "same_process_after_reload": session.poll() is None,
    }
    (EVIDENCE / "screen.txt").write_text(session.output_tail().decode("utf-8", errors="replace"), encoding="utf-8")
    shutil.copyfile(RUN / "debug.txt", EVIDENCE / "debug.txt")
    shutil.copyfile(PLUGIN / "hooks/side-intake.js", EVIDENCE / "side-intake.js")
    (EVIDENCE / "driver-A.js").write_text(driver("A"), encoding="utf-8")
    (EVIDENCE / "driver-B.js").write_text(driver("B"), encoding="utf-8")
finally:
    if session is not None:
        try:
            procs = processes()
            root = next((r for r in procs if r["pid"] == session.pid), None)
            tree = descendants(procs, root["pid"], root["created"]) if root else []
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
    shutil.rmtree(RUN, ignore_errors=True)
    print(json.dumps({"probe": PROBE_ID, "evidence": str(EVIDENCE), "cli_version": result.get("cli_version"),
                      "started": result.get("started"), "analysis": result.get("analysis"),
                      "live_hash_changed": result.get("live_hash_changed"),
                      "owned_path_in_live_files": result.get("owned_path_in_live_files"),
                      "protected_missing_after": result.get("protected_missing_after")}, indent=2, ensure_ascii=False))
