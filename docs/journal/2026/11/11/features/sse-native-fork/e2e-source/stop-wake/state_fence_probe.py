"""Owned real-engine probe for two side-wake questions (Claude Code, isolated).

Q1 $.state compare-and-set across a same-process plugin hot reload:
    - generation A takes a fence value with ifVersion; after the module file is
      rewritten (generation B, same claude.exe), does B read A's value, and does
      a stale ifVersion write from B miss (isSet:false)?
    - two concurrent writers with one ifVersion inside one instance: exactly one
      isSet:true?
    - two concurrent writers across the reload: A's async task read before the
      reload and is held on a loopback request; B reads after the reload and is
      held too; both are released at once and write with ifVersion. Exactly one
      isSet:true? (If A's environment cannot act after the reload, that is
      recorded instead.)
Q2 SessionMessage uuid: after $.session.append while main is busy, does any
    $.session.messages() / messages({}) / messages({as:'api'}) row carry the
    append result's uuid? Is the uuid in the session transcript file?

Isolation: every inherited CIEL_RUNTIME_*/CLAUDE*/ANTHROPIC*/CODEX*/OPENAI*
variable is removed; CIEL_RUNTIME_TEST_ISOLATED=1, a temp CIEL_RUNTIME_CONFIG_DIR
and a temp CLAUDE_CONFIG_DIR are set; a loopback gateway is the provider. Only
processes descended from the claude.exe this script started are terminated.
Live profile files are hashed before and after and scanned for owned paths.
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
from urllib.parse import parse_qs, urlparse

SOURCE = Path(sys.argv[1]).resolve()
EVIDENCE_PARENT = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(SOURCE))
from ciel_runtime_support.windows_conpty import WindowsConPtySession  # noqa: E402

CLI = str(Path.home() / ".local" / "bin" / "claude.exe")
PROBE_ID = uuid.uuid4().hex[:8]
RUN = Path(tempfile.mkdtemp(prefix="ciel-state-fence-" + PROBE_ID + "-"))
EVIDENCE = EVIDENCE_PARENT / ("state-fence-" + PROBE_ID)
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
SESSION_ID = str(uuid.uuid4())
SOCKET = rf"\\.\pipe\LOCAL\ciel-state-fence-{uuid.uuid4().hex[:16]}"
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
            if child["created"] >= node["created"]:
                stack.append(child)
    return out


MODULE = r'''
const GEN = '__GEN__'
const BOOT = Math.random().toString(36).slice(2)
const GATE = 'http://127.0.0.1:__PORT__'
const FENCE = { plugin: 'fence-probe', key: 'legacyWakes' }
const RACE_SAME = { plugin: 'fence-probe', key: '__RACE_SAME__' }
const RACE_CROSS = { plugin: 'fence-probe', key: 'raceCross' }
let chain = Promise.resolve()
function note($, row) {
  const entry = { gen: GEN, boot: BOOT, at: Date.now(), ...row }
  chain = chain.then(async () => {
    let prior = ''
    try { prior = await $.fs.read('events-' + GEN + '.jsonl') } catch {}
    await $.fs.write('events-' + GEN + '.jsonl', prior + JSON.stringify(entry) + '\n')
  }).catch(() => {})
  return chain
}
async function report($, row) {
  try {
    await $.http.fetch(GATE + '/report', { method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ gen: GEN, boot: BOOT, at: Date.now(), ...row }) })
  } catch (error) { await note($, { event: 'report-threw', error: String(error && error.message || error).slice(0, 300) }) }
}
async function both($, row) { await note($, row); await report($, row) }
async function attempt(fn) {
  try { return { ok: true, value: await fn() } }
  catch (error) { return { ok: false, error: String(error && error.message || error).slice(0, 300) } }
}
let appended = null
let appendDone = false
export function register(on) {
  on('session.start', async ($, e, next) => {
    const result = await next(e)
    await both($, { event: 'session.start' })
    const fence0 = await attempt(() => $.state.get(FENCE))
    const cross0 = await attempt(() => $.state.get(RACE_CROSS))
    await both($, { event: 'initial-reads', fence: fence0, cross: cross0 })
    // Two concurrent writers inside this instance with one ifVersion.
    const same0 = await attempt(() => $.state.get(RACE_SAME))
    const v = same0.ok ? same0.value.version : 0
    const pair = await Promise.all([
      attempt(() => $.state.set(RACE_SAME, { by: GEN + '-1' }, { ifVersion: v })),
      attempt(() => $.state.set(RACE_SAME, { by: GEN + '-2' }, { ifVersion: v }))])
    const sameAfter = await attempt(() => $.state.get(RACE_SAME))
    await both($, { event: 'same-instance-race', ifVersion: v, results: pair, after: sameAfter })
    if (GEN === 'A') {
      // The fence: take attempt-X at the version read.
      const version = fence0.ok ? fence0.value.version : 0
      const list = fence0.ok && Array.isArray(fence0.value.value) ? fence0.value.value : []
      const take = await attempt(() => $.state.set(FENCE, [...list, 'attempt-X'], { ifVersion: version }))
      const after = await attempt(() => $.state.get(FENCE))
      await both($, { event: 'fence-take', had: list, ifVersion: version, take, after })
    } else {
      // After the reload: A's value is visible, and a write at the stale version misses.
      const stale = await attempt(() => $.state.set(FENCE, ['attempt-X', 'stale-B'], { ifVersion: 0 }))
      const after = await attempt(() => $.state.get(FENCE))
      await both($, { event: 'fence-after-reload', seen: fence0, stale, after })
    }
    // Cross-instance writer: read now, wait on the gateway, write with that version.
    void (async () => {
      const read = await attempt(() => $.state.get(RACE_CROSS))
      await both($, { event: 'cross-read', read })
      const held = await attempt(() => $.http.fetch(GATE + '/hold?gen=' + GEN + '&boot=' + BOOT))
      const write = await attempt(() => $.state.set(RACE_CROSS, { by: GEN, boot: BOOT },
        { ifVersion: read.ok ? read.value.version : 0 }))
      await both($, { event: 'cross-write', held: held.ok ? { ok: true, status: held.value.status } : held,
        ifVersion: read.ok ? read.value.version : 0, write })
      const final = await attempt(() => $.state.get(RACE_CROSS))
      const fence = await attempt(() => $.state.get(FENCE))
      await both($, { event: 'cross-final', final, fence })
    })()
    return result
  })
  on('turn.step', async function* ($, e, next) {
    if (e.agentId === undefined && __DO_APPEND__ && !appended) {
      appended = await attempt(() => $.session.append({ message: { type: 'user',
        content: [{ type: 'text', text: 'APPEND-PROBE-MARK ' + BOOT }] } }))
      await both($, { event: 'append', index: e.index, turnId: e.turnId, result: appended.ok
        ? { keys: Object.keys(appended.value || {}), uuid: appended.value?.uuid ?? null,
            deny: appended.value?.deny ?? null, message: appended.value?.message ?? null } : appended })
    }
    return yield* next(e)
  })
  on('turn.complete', async ($, e, next) => {
    const result = await next(e)
    if (e.agentId === undefined && __DO_APPEND__ && appended && !appendDone) {
      appendDone = true
      const uuid = appended.ok ? appended.value?.uuid : null
      const plain = await attempt(() => $.session.messages())
      const empty = await attempt(() => $.session.messages({}))
      const api = await attempt(() => $.session.messages({ as: 'api' }))
      const rows = r => r.ok && Array.isArray(r.value) ? r.value.map(m => ({ keys: Object.keys(m || {}).sort(),
        role: m?.role ?? null, uuid: m?.uuid ?? null,
        text: String(m?.text ?? JSON.stringify(m?.content ?? '')).slice(0, 160) })) : r
      const has = r => r.ok ? JSON.stringify(r.value).includes(String(uuid)) : null
      await both($, { event: 'messages', uuid, plain: rows(plain), empty: rows(empty), api: rows(api),
        uuidInPlain: has(plain), uuidInEmpty: has(empty), uuidInApi: has(api) })
    }
    return result
  })
  on('session.append', async ($, e, next) => {
    const result = await next(e)
    if (JSON.stringify(e.message?.content ?? '').includes('APPEND-PROBE-MARK'))
      await both($, { event: 'session.append-hook', door: e.door, uuid: e.uuid ?? null, resultUuid: result?.uuid ?? null })
    return result
  })
}
'''


def module(generation, port):
    return (MODULE.replace("__GEN__", generation).replace("__PORT__", str(port))
            .replace("__RACE_SAME__", "raceSame" + generation)
            .replace("__DO_APPEND__", "true" if generation == "A" else "false"))


GATE = {"holds": [], "reports": [], "release": threading.Event(), "requests": [], "lock": threading.Lock()}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/hold":
            self.send_error(404)
            return
        query = parse_qs(parsed.query)
        record = {"gen": query.get("gen", [""])[0], "boot": query.get("boot", [""])[0], "at": now(),
                  "released_at": None, "outcome": None}
        with GATE["lock"]:
            GATE["holds"].append(record)
        GATE["release"].wait(600)
        try:
            raw = b"go"
            self.send_response(200)
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            self.wfile.flush()
            record["outcome"] = "answered"
        except OSError as error:
            record["outcome"] = "connection closed: " + type(error).__name__
        record["released_at"] = now()

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length", 0)) or 0)
        if self.path.startswith("/report"):
            try:
                row = json.loads(body or b"{}")
            except ValueError:
                row = {"raw": body[:300].decode("utf-8", "replace")}
            row["received_at"] = now()
            with GATE["lock"]:
                GATE["reports"].append(row)
            self.send_response(204)
            self.end_headers()
            return
        data = json.loads(body or b"{}")
        if "count_tokens" in self.path:
            raw = json.dumps({"input_tokens": 50}).encode()
            self.send_response(200)
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        text = json.dumps(data.get("messages", []), ensure_ascii=False)
        GATE["requests"].append({"at": now(), "carries_append_mark": "APPEND-PROBE-MARK" in text,
                                 "message_count": len(data.get("messages", []))})
        message = {"id": "msg_" + uuid.uuid4().hex[:10], "type": "message", "role": "assistant",
                   "model": data.get("model"), "usage": {"input_tokens": 50, "output_tokens": 5}}
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


def reported(generation, event):
    with GATE["lock"]:
        return [r for r in GATE["reports"] if r.get("gen") == generation and r.get("event") == event]


def wait_for(predicate, seconds, step=0.25):
    end = now() + seconds
    while now() < end:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return predicate()


def send(token, content):
    frames = (json.dumps({"type": "auth", "token": token}) + "\n" + json.dumps({
        "type": "user", "uuid": str(uuid.uuid4()), "message": {"role": "user", "content": content},
        "priority": "next"}, ensure_ascii=False) + "\n").encode()
    with open(SOCKET, "wb", buffering=0) as pipe:
        pipe.write(frames)


def save(result):
    (EVIDENCE / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


result = {"probe": PROBE_ID, "run_dir": str(RUN), "cli": CLI, "session_id": SESSION_ID}
clean_env = {k: v for k, v in os.environ.items()
             if not k.upper().startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI"))}
result["cli_version"] = subprocess.run([CLI, "--version"], capture_output=True, text=True, timeout=60,
                                       env={**clean_env, "CLAUDE_CONFIG_DIR": str(CFG),
                                            "DISABLE_AUTOUPDATER": "1"}).stdout.strip()
live_before = {str(p): sha(p) for p in LIVE_FILES}
protected = [r for r in processes() if r["name"].lower() in PROTECTED_NAMES]
result["protected_count"] = len(protected)
server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
PORT = server.server_port
(PLUGIN / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "fence-probe", "version": "0.1.0"}), encoding="utf-8")
(PLUGIN / "hooks/hooks.json").write_text('{"modules":["./register.js"]}', encoding="utf-8")
(PLUGIN / "hooks/register.js").write_text(module("A", PORT), encoding="utf-8")
(CFG / ".claude.json").write_text(json.dumps({
    "hasCompletedOnboarding": True, "theme": "dark",
    "projects": {str(WS): {"hasTrustDialogAccepted": True},
                 str(WS).replace("\\", "/"): {"hasTrustDialogAccepted": True}}}), encoding="utf-8")
(CFG / "settings.json").write_text(json.dumps({"permissions": {"allow": []}}), encoding="utf-8")
os.chdir(WS)
env = dict(clean_env)
env.update({"CIEL_RUNTIME_TEST_ISOLATED": "1", "CIEL_RUNTIME_CONFIG_DIR": str(CIEL_CFG),
            "CLAUDE_CONFIG_DIR": str(CFG), "ANTHROPIC_BASE_URL": "http://127.0.0.1:" + str(PORT),
            "ANTHROPIC_AUTH_TOKEN": "fixture", "ANTHROPIC_API_KEY": "", "ANTHROPIC_MODEL": "fixture",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1",
            "ENABLE_TOOL_SEARCH": "false"})
result["inherited_ciel_after_strip"] = sorted(k for k in env if k.upper().startswith("CIEL_RUNTIME_")
                                              and k not in ("CIEL_RUNTIME_TEST_ISOLATED", "CIEL_RUNTIME_CONFIG_DIR"))
session = None
try:
    session = WindowsConPtySession(
        [CLI, "--permission-mode", "default", "--plugin-dir", str(PLUGIN), "--strict-mcp-config",
         "--mcp-config", '{"mcpServers":{}}', "--session-id", SESSION_ID, "--messaging-socket-path", SOCKET,
         "--debug-file", str(RUN / "debug.txt")],
        env, log=lambda level, message: None, mirror_output=False, forward_stdin=False)
    result["pid"] = session.pid
    result["gen_a_cross_read"] = bool(wait_for(lambda: any(r["event"] == "cross-read" for r in events("A")), 90))
    result["gen_a_hold_arrived"] = bool(wait_for(lambda: any(h["gen"] == "A" for h in GATE["holds"]), 30))
    root_created = next((r["created"] for r in processes() if r["pid"] == session.pid), None)
    result["root_created"] = root_created
    token = ""
    deadline = now() + 60
    while now() < deadline and not token:
        keys = list((CFG / "sessions").glob("*.key")) if (CFG / "sessions").exists() else []
        if keys:
            token = str(json.loads(keys[0].read_text(encoding="utf-8")).get("peerToken") or "")
        time.sleep(0.25)
    result["token_found"] = len(token) == 32
    if token:
        time.sleep(1.5)
        result["prompt_sent_at"] = now()
        send(token, "APPEND-PROBE say hi")
        result["messages_event"] = bool(wait_for(lambda: any(r["event"] == "messages" for r in events("A")), 90))
    (EVIDENCE / "screen-before-reload.txt").write_text(session.output_tail().decode("utf-8", errors="replace"),
                                                        encoding="utf-8")
    save(result)
    # Same-process hot reload of the module only.
    result["reload_written_at"] = now()
    (PLUGIN / "hooks/register.js").write_text(module("B", PORT), encoding="utf-8")
    result["gen_b_after_reload"] = bool(wait_for(lambda: any(r["event"] == "fence-after-reload" for r in events("B")), 120))
    result["gen_b_hold_arrived"] = bool(wait_for(lambda: any(h["gen"] == "B" for h in GATE["holds"]), 30))
    a_hold = next((h for h in GATE["holds"] if h["gen"] == "A"), {})
    result["a_hold_open_before_release"] = a_hold.get("released_at") is None
    time.sleep(1)
    result["released_at"] = now()
    GATE["release"].set()
    wait_for(lambda: reported("B", "cross-final") and (reported("A", "cross-final") or reported("A", "cross-write")), 30)
    time.sleep(3)
    result["same_process_after_reload"] = (session.poll() is None and any(
        r["pid"] == session.pid and r["created"] == root_created for r in processes()))
    (EVIDENCE / "screen-after-reload.txt").write_text(session.output_tail().decode("utf-8", errors="replace"),
                                                       encoding="utf-8")
    shutil.copyfile(RUN / "debug.txt", EVIDENCE / "debug.txt")
    transcripts = [p for p in CFG.rglob("*.jsonl")]
    result["transcripts"] = [str(p.relative_to(CFG)) for p in transcripts]
    result["events_a"] = events("A")
    result["events_b"] = events("B")
    result["reports"] = list(GATE["reports"])
    result["holds"] = list(GATE["holds"])
    result["model_requests"] = list(GATE["requests"])

    def first(rows, event):
        return next((r for r in rows if r.get("event") == event), None)
    a_fs, b_fs = events("A"), events("B")
    appended = first(a_fs, "append") or first(reported("A", "append"), "append")
    messages = first(a_fs, "messages") or first(reported("A", "messages"), "messages")
    append_uuid = ((appended or {}).get("result") or {}).get("uuid")
    in_transcript = [str(p.relative_to(CFG)) for p in transcripts
                     if append_uuid and append_uuid in p.read_text(encoding="utf-8", errors="replace")]
    for p in transcripts:
        shutil.copyfile(p, EVIDENCE / ("transcript-" + p.name))
    a_writes = reported("A", "cross-write") + [r for r in a_fs if r.get("event") == "cross-write"]
    b_writes = reported("B", "cross-write") + [r for r in b_fs if r.get("event") == "cross-write"]
    a_write = a_writes[0] if a_writes else None
    b_write = b_writes[0] if b_writes else None
    landed = [w for w in (a_write, b_write) if w and w.get("write", {}).get("ok") and w["write"]["value"].get("isSet")]
    b_start = first(b_fs, "session.start")
    result["analysis"] = {
        "same_instance_race": {gen: [(x.get("value") or {}).get("isSet") if x.get("ok") else x.get("error")
                                     for x in (first(rows, "same-instance-race") or {}).get("results", [])]
                               for gen, rows in (("A", a_fs), ("B", b_fs))},
        "fence_take_A": (first(a_fs, "fence-take") or {}).get("take"),
        "fence_seen_by_B_after_reload": (first(b_fs, "fence-after-reload") or {}).get("seen"),
        "stale_write_by_B": (first(b_fs, "fence-after-reload") or {}).get("stale"),
        "cross_read_A": (first(a_fs, "cross-read") or {}).get("read"),
        "cross_read_B": (first(b_fs, "cross-read") or {}).get("read"),
        "cross_write_A": a_write,
        "cross_write_B": b_write,
        "cross_write_A_after_B_session_start": bool(a_write and b_start and a_write.get("at", 0) > b_start.get("at", 0)),
        "cross_isSet_true_count": len(landed),
        "cross_final_B": (reported("B", "cross-final") or [{}])[0].get("final"),
        "append_result": (appended or {}).get("result"),
        "append_hook": first(a_fs, "session.append-hook"),
        "messages_uuid_in_plain": (messages or {}).get("uuidInPlain"),
        "messages_uuid_in_empty": (messages or {}).get("uuidInEmpty"),
        "messages_uuid_in_api": (messages or {}).get("uuidInApi"),
        "messages_row_keys": sorted({tuple(r.get("keys", [])) for r in ((messages or {}).get("empty") or [])
                                     if isinstance(r, dict)}),
        "append_mark_rows_empty": [r for r in ((messages or {}).get("empty") or [])
                                   if isinstance(r, dict) and "APPEND-PROBE-MARK" in str(r.get("text"))],
        "append_uuid_in_transcript_files": in_transcript,
        "model_request_carried_append": any(r["carries_append_mark"] for r in GATE["requests"]),
    }
    save(result)
finally:
    GATE["release"].set()
    if session is not None:
        try:
            rows = processes()
            root = next((r for r in rows if r["pid"] == session.pid), None)
            tree = descendants(rows, root["pid"], root["created"]) if root else []
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
    alive = {(r["pid"], r["created"]) for r in processes()}
    result["protected_missing_after"] = [r for r in protected if (r["pid"], r["created"]) not in alive]
    live_after = {str(p): sha(p) for p in LIVE_FILES}
    result["live_hash_changed"] = sorted(k for k in live_before if live_before[k] != live_after[k])
    markers = (str(RUN), str(RUN).replace("\\", "/"), str(RUN).replace("\\", "\\\\"), PROBE_ID)
    result["owned_path_in_live_files"] = sorted(
        str(p) for p in LIVE_FILES if p.exists()
        and any(m in p.read_text(encoding="utf-8", errors="replace") for m in markers))
    result["live_before"] = live_before
    result["live_after"] = live_after
    save(result)
    print(json.dumps({"probe": PROBE_ID, "evidence": str(EVIDENCE), "cli_version": result.get("cli_version"),
                      "analysis": result.get("analysis"), "live_hash_changed": result.get("live_hash_changed"),
                      "owned_path_in_live_files": result.get("owned_path_in_live_files"),
                      "protected_missing_after": result.get("protected_missing_after")}, indent=2, ensure_ascii=False))
