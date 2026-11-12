"""Owned Claude Code probes for the prompt-FIFO review blockers.

Mode `append-turn-start`:
  The plugin submits prompt A and immediately starts prompt B as an ordinary
  queued plugin prompt. In A's main turn.start hook, before awaiting next(e),
  it appends B's text with $.session.append and awaits it. We record whether
  request 1 carries B, how many requests and turns carry B, and when B's own
  prompt promise resolves. This measures the batch path; it does not decide it.

Mode `reload-held-prompt`:
  Generation A submits SEED and then OLD while the provider holds SEED's first
  response. OLD is therefore held. The module file is rewritten in the same
  claude.exe. Generation B submits NEW. Old-instance promise settlement is sent
  through native globalThis.fetch to a loopback reporter, not through $.
  We record whether OLD resolves/rejects/never settles, whether OLD and NEW
  start turns, and whether NEW's engine submit is concurrent with held OLD.

Mode `append-clean` (rerun of design A with no second prompt at all):
  The plugin submits only prompt A. In A's main turn.start hook, before
  awaiting next(e), it appends B-APPEND and awaits the append. No B prompt is
  ever submitted. Question: does request 1 carry B-APPEND?

Mode `reload-turn-only` (control for `reload-held-prompt`):
  Generation A submits only SEED; the provider holds SEED's response, so main
  is inside a turn with no plugin prompt pending. The module is rewritten.
  Question: does generation B load while that turn is still running (before
  the held response is released)?

Isolation: every inherited CIEL_RUNTIME_*/CLAUDE*/ANTHROPIC*/CODEX*/OPENAI*
variable is removed; CIEL_RUNTIME_TEST_ISOLATED=1 and temp
CIEL_RUNTIME_CONFIG_DIR and CLAUDE_CONFIG_DIR are set. Live profile files are
hashed before/after. Only the owned claude.exe process tree is terminated.
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
from urllib.parse import urlparse

SOURCE = Path(sys.argv[1]).resolve()
EVIDENCE_PARENT = Path(sys.argv[2]).resolve()
MODE = sys.argv[3]
MODES = ("append-turn-start", "reload-held-prompt", "append-clean", "reload-turn-only")
if MODE not in MODES:
    raise SystemExit("mode must be one of " + ", ".join(MODES))
sys.path.insert(0, str(SOURCE))
from ciel_runtime_support.windows_conpty import WindowsConPtySession  # noqa: E402

CLI = str(Path.home() / ".local" / "bin" / "claude.exe")
PROBE_ID = uuid.uuid4().hex[:8]
RUN = Path(tempfile.mkdtemp(prefix="ciel-fifo-review-" + MODE + "-" + PROBE_ID + "-"))
EVIDENCE = EVIDENCE_PARENT / ("fifo-review-" + MODE + "-" + PROBE_ID)
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
MARK = {name: "FIFO-REVIEW-" + name.upper() + "-" + PROBE_ID for name in ("a", "b", "bappend", "seed", "old", "new")}
REPORTS = []
REQUESTS = []
HOLD = threading.Event()


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


COMMON = r'''
const MODE = __MODE__
const MARK = __MARK__
const PORT = __PORT__
const GEN = __GEN__
const BOOT = Math.random().toString(36).slice(2)
// Two channels: the plugin's own $.fs file (reliable while this instance's $
// works) and native globalThis.fetch to the loopback reporter (independent of
// $, so an old instance after a reload can still be heard if fetch works).
let fsChain = Promise.resolve()
let nativeStatus = null
function note($, row) {
  const entry = { gen:GEN, boot:BOOT, at:Date.now(), ...row }
  fsChain = fsChain.then(async () => {
    let prior = ''
    try { prior = await $.fs.read('events-' + GEN + '.jsonl') } catch {}
    await $.fs.write('events-' + GEN + '.jsonl', prior + JSON.stringify(entry) + '\n')
  }).catch(() => {})
  return Promise.all([fsChain, native(entry)])
}
async function native(entry) {
  try {
    if (typeof globalThis.fetch !== 'function') { nativeStatus = 'fetch:' + typeof globalThis.fetch; return }
    const response = await globalThis.fetch('http://127.0.0.1:' + PORT + '/report', { method:'POST',
      headers:{ 'content-type':'application/json' }, body:JSON.stringify(entry) })
    nativeStatus = 'status:' + response.status
  } catch (error) { nativeStatus = 'error:' + String(error && (error.message || error)).slice(0, 200) }
}
'''

APPEND_GEN = COMMON + r'''
let started = false
let appended = false
export function register(on) {
  on('session.start', async ($, e, next) => {
    const result = await next(e)
    await note($, { event:'session.start' }); await note($, { event:'native.status', nativeStatus, globals:Object.fromEntries(['fetch','WebSocket','XMLHttpRequest','EventSource','process','require','Deno','Bun','navigator','importScripts','postMessage'].map(name => [name, typeof globalThis[name]])) })
    if (!started) {
      started = true
      $.clock.after(2000, async () => {
        await note($, { event:'a.invoke' })
        const a = $.prompt.submit({ text:MARK.a }).then(
          value => note($, { event:'a.resolve', value }), error => note($, { event:'a.reject', error:String(error).slice(0, 300) }))
        await note($, { event:'b.invoke' })
        const b = $.prompt.submit({ text:MARK.b }).then(
          value => note($, { event:'b.resolve', value }), error => note($, { event:'b.reject', error:String(error).slice(0, 300) }))
        await Promise.allSettled([a, b])
        await note($, { event:'all.settled' })
      })
    }
    return result
  })
  on('turn.start', async ($, e, next) => {
    if (e.agentId === undefined) {
      await note($, { event:'turn.start.before-next', turnId:e.turnId, text:String(e.text || '').slice(0, 120) })
      if (!appended && String(e.text || '').includes(MARK.a)) {
        appended = true
        try {
          const value = await $.session.append({ message:{ type:'user', content:[{ type:'text', text:MARK.bappend }] } })
          await note($, { event:'append.resolve', uuid:value?.uuid ?? null, deny:value?.deny ?? null })
        } catch (error) { await note($, { event:'append.reject', error:String(error).slice(0, 300) }) }
      }
    }
    const result = await next(e)
    if (e.agentId === undefined) await note($, { event:'turn.start.after-next', turnId:e.turnId })
    return result
  })
  on('turn.step', async function* ($, e, next) {
    if (e.agentId === undefined) await note($, { event:'turn.step', turnId:e.turnId, index:e.index, messageCount:e.messageCount })
    return yield* next(e)
  })
  on('turn.complete', async ($, e, next) => {
    if (e.agentId === undefined) await note($, { event:'turn.complete', turnId:e.turnId, reason:e.reason || '' })
    return next(e)
  })
}
'''

CLEAN_GEN = COMMON + r'''
let started = false
let appended = false
export function register(on) {
  on('session.start', async ($, e, next) => {
    const result = await next(e)
    await note($, { event:'session.start' })
    if (!started) {
      started = true
      $.clock.after(2000, async () => {
        await note($, { event:'a.invoke' })
        const a = $.prompt.submit({ text:MARK.a }).then(
          value => note($, { event:'a.resolve', value }), error => note($, { event:'a.reject', error:String(error).slice(0, 300) }))
        await Promise.allSettled([a])
        await note($, { event:'all.settled' })
      })
    }
    return result
  })
  on('turn.start', async ($, e, next) => {
    if (e.agentId === undefined) {
      await note($, { event:'turn.start.before-next', turnId:e.turnId, text:String(e.text || '').slice(0, 120) })
      if (!appended && String(e.text || '').includes(MARK.a)) {
        appended = true
        try {
          const value = await $.session.append({ message:{ type:'user', content:[{ type:'text', text:MARK.bappend }] } })
          await note($, { event:'append.resolve', uuid:value?.uuid ?? null, deny:value?.deny ?? null })
        } catch (error) { await note($, { event:'append.reject', error:String(error).slice(0, 300) }) }
      }
    }
    const result = await next(e)
    if (e.agentId === undefined) await note($, { event:'turn.start.after-next', turnId:e.turnId })
    return result
  })
  on('turn.step', async function* ($, e, next) {
    if (e.agentId === undefined) await note($, { event:'turn.step', turnId:e.turnId, index:e.index, messageCount:e.messageCount })
    return yield* next(e)
  })
  on('turn.complete', async ($, e, next) => {
    if (e.agentId === undefined) await note($, { event:'turn.complete', turnId:e.turnId, reason:e.reason || '' })
    return next(e)
  })
}
'''

TURN_ONLY_A = COMMON + r'''
let started = false
export function register(on) {
  on('session.start', async ($, e, next) => {
    const result = await next(e)
    await note($, { event:'session.start' })
    if (!started) {
      started = true
      $.clock.after(2000, async () => {
        await note($, { event:'seed.invoke' })
        $.prompt.submit({ text:MARK.seed }).then(
          value => note($, { event:'seed.resolve', value }), error => note($, { event:'seed.reject', error:String(error).slice(0, 300) }))
      })
    }
    return result
  })
  on('turn.start', async ($, e, next) => {
    if (e.agentId === undefined) await note($, { event:'turn.start', turnId:e.turnId, text:String(e.text || '').slice(0, 120) })
    return next(e)
  })
  on('turn.complete', async ($, e, next) => {
    if (e.agentId === undefined) await note($, { event:'turn.complete', turnId:e.turnId, reason:e.reason || '' })
    return next(e)
  })
}
'''

RELOAD_A = COMMON + r'''
let started = false
export function register(on) {
  on('session.start', async ($, e, next) => {
    const result = await next(e)
    await note($, { event:'session.start' }); await note($, { event:'native.status', nativeStatus, globals:Object.fromEntries(['fetch','WebSocket','XMLHttpRequest','EventSource','process','require','Deno','Bun','navigator','importScripts','postMessage'].map(name => [name, typeof globalThis[name]])) })
    if (!started) {
      started = true
      $.clock.after(2000, async () => {
        await note($, { event:'seed.invoke' })
        const seed = $.prompt.submit({ text:MARK.seed }).then(
          value => note($, { event:'seed.resolve', value }), error => note($, { event:'seed.reject', error:String(error).slice(0, 300) }))
        await note($, { event:'old.invoke' })
        const old = $.prompt.submit({ text:MARK.old }).then(
          value => note($, { event:'old.resolve', value }), error => note($, { event:'old.reject', error:String(error).slice(0, 300) }))
        await note($, { event:'old.awaiting' })
        await Promise.allSettled([seed, old])
        await note($, { event:'a.all.settled' })
      })
    }
    return result
  })
  on('turn.start', async ($, e, next) => {
    if (e.agentId === undefined) await note($, { event:'turn.start', turnId:e.turnId, text:String(e.text || '').slice(0, 120) })
    return next(e)
  })
  on('turn.complete', async ($, e, next) => {
    if (e.agentId === undefined) await note($, { event:'turn.complete', turnId:e.turnId, reason:e.reason || '' })
    return next(e)
  })
}
'''

RELOAD_B = COMMON + r'''
let started = false
export function register(on) {
  on('session.start', async ($, e, next) => {
    const result = await next(e)
    await note($, { event:'session.start' }); await note($, { event:'native.status', nativeStatus, globals:Object.fromEntries(['fetch','WebSocket','XMLHttpRequest','EventSource','process','require','Deno','Bun','navigator','importScripts','postMessage'].map(name => [name, typeof globalThis[name]])) })
    if (!started) {
      started = true
      await note($, { event:'new.invoke' })
      $.prompt.submit({ text:MARK.new }).then(
        value => note($, { event:'new.resolve', value }), error => note($, { event:'new.reject', error:String(error).slice(0, 300) }))
    }
    return result
  })
  on('turn.start', async ($, e, next) => {
    if (e.agentId === undefined) await note($, { event:'turn.start', turnId:e.turnId, text:String(e.text || '').slice(0, 120) })
    return next(e)
  })
  on('turn.complete', async ($, e, next) => {
    if (e.agentId === undefined) await note($, { event:'turn.complete', turnId:e.turnId, reason:e.reason || '' })
    return next(e)
  })
}
'''


def module(text, generation):
    return (text.replace("__MODE__", json.dumps(MODE)).replace("__MARK__", json.dumps(MARK))
            .replace("__PORT__", str(SERVER.server_port)).replace("__GEN__", json.dumps(generation)))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length", 0)) or 0)
        if urlparse(self.path).path == "/report":
            try:
                row = json.loads(body or b"{}")
            except ValueError:
                row = {"raw": body[:300].decode("utf-8", "replace")}
            row["received_at"] = now()
            REPORTS.append(row)
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
        row = {"at": now(), "number": len(REQUESTS) + 1, "message_count": len(data.get("messages", []))}
        row.update({name: text.count(marker) for name, marker in MARK.items()})
        REQUESTS.append(row)
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
        emit("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "DONE"}})
        if MODE in ("reload-held-prompt", "reload-turn-only") and row["seed"] and not row["old"] and not row["new"]:
            # Hold SEED's response until generation B has been loaded and issued NEW.
            HOLD.wait(120)
        elif MODE in ("append-turn-start", "append-clean") and len(REQUESTS) == 1:
            time.sleep(2)
        emit("content_block_stop", {"type": "content_block_stop", "index": 0})
        emit("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                               "usage": {"output_tokens": 5}})
        emit("message_stop", {"type": "message_stop"})
        self.close_connection = True


def wait_for(predicate, seconds, step=0.1):
    end = now() + seconds
    while now() < end:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return predicate()


def fs_rows():
    rows = []
    for generation in ("A", "B"):
        path = WS / ("events-" + generation + ".jsonl")
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            row["channel"] = "fs"
            rows.append(row)
    return rows


def all_rows():
    """Plugin $.fs rows plus native-fetch reports, one per (boot, event, at)."""
    seen, rows = set(), []
    for row in fs_rows() + [dict(r, channel="native") for r in REPORTS]:
        key = (row.get("boot"), row.get("event"), row.get("at"))
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    return sorted(rows, key=lambda r: r.get("at") or 0)


def reported(event, generation=None):
    return [row for row in all_rows() if row.get("event") == event and (generation is None or row.get("gen") == generation)]


def submit_lines():
    path = RUN / "debug.txt"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "$.prompt.submit (fifo-review-probe)" not in line:
            continue
        stamp = line.split(" ", 1)[0]
        try:
            at = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000
        except ValueError:
            at = None
        out.append({"at": at, "line": line[:240]})
    return out


clean_env = {k: v for k, v in os.environ.items()
             if not k.upper().startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI"))}
result = {"probe": PROBE_ID, "mode": MODE, "run_dir": str(RUN), "cli": CLI, "markers": MARK,
          "script_sha256": sha(Path(__file__).resolve())}
result["cli_version"] = subprocess.run([CLI, "--version"], capture_output=True, text=True, timeout=60,
                                       env={**clean_env, "CLAUDE_CONFIG_DIR": str(CFG),
                                            "DISABLE_AUTOUPDATER": "1"}).stdout.strip()
live_before = {str(p): sha(p) for p in LIVE_FILES}
protected = [row for row in processes() if row["name"].lower() in PROTECTED_NAMES]
result["protected_count"] = len(protected)
SERVER = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=SERVER.serve_forever, daemon=True).start()
(PLUGIN / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "fifo-review-probe", "version": "0.1.0"}), encoding="utf-8")
(PLUGIN / "hooks/hooks.json").write_text('{"modules":["./register.js"]}', encoding="utf-8")
first_module = {"append-turn-start": APPEND_GEN, "append-clean": CLEAN_GEN,
                "reload-held-prompt": RELOAD_A, "reload-turn-only": TURN_ONLY_A}[MODE]
(PLUGIN / "hooks/register.js").write_text(module(first_module, "A"), encoding="utf-8")
(CFG / ".claude.json").write_text(json.dumps({
    "hasCompletedOnboarding": True, "theme": "dark",
    "projects": {str(WS): {"hasTrustDialogAccepted": True},
                 str(WS).replace("\\", "/"): {"hasTrustDialogAccepted": True}}}), encoding="utf-8")
(CFG / "settings.json").write_text(json.dumps({"permissions": {"allow": []}}), encoding="utf-8")
env = dict(clean_env)
env.update({"CIEL_RUNTIME_TEST_ISOLATED": "1", "CIEL_RUNTIME_CONFIG_DIR": str(CIEL_CFG),
            "CLAUDE_CONFIG_DIR": str(CFG), "ANTHROPIC_BASE_URL": "http://127.0.0.1:" + str(SERVER.server_port),
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
    result["started"] = bool(wait_for(lambda: reported("session.start", "A"), 90))
    if MODE == "append-clean":
        result["settled"] = bool(wait_for(lambda: reported("all.settled", "A"), 60))
        wait_for(lambda: reported("turn.complete", "A"), 30)
        time.sleep(3)
        first = REQUESTS[0] if REQUESTS else {}
        resolved = reported("append.resolve", "A")
        after_next = reported("turn.start.after-next", "A")
        result["analysis"] = {
            "append_resolved": resolved,
            "append_before_next_returned": bool(resolved and after_next and resolved[0]["at"] < after_next[0]["at"]),
            "request_count": len(REQUESTS),
            "request_1_has_a": bool(first.get("a")),
            "request_1_has_bappend": bool(first.get("bappend")),
            "bappend_by_request": [{"number": r["number"], "bappend": r["bappend"], "message_count": r["message_count"]}
                                   for r in REQUESTS],
            "first_step": (reported("turn.step", "A") or [None])[0],
            "submit_lines": submit_lines(),
        }
    elif MODE == "reload-turn-only":
        result["seed_held"] = bool(wait_for(lambda: any(r["seed"] for r in REQUESTS), 60))
        time.sleep(1)
        result["reload_written_at"] = now()
        (PLUGIN / "hooks/register.js").write_text(module(RELOAD_B, "B"), encoding="utf-8")
        result["b_loaded_while_turn_held"] = bool(wait_for(lambda: reported("session.start", "B"), 30))
        result["b_session_start_at"] = (reported("session.start", "B") or [{}])[0].get("at")
        held_until = now()
        HOLD.set()
        wait_for(lambda: reported("session.start", "B") and reported("new.resolve", "B"), 60)
        time.sleep(3)
        result["analysis"] = {
            "b_loaded_while_turn_held": result["b_loaded_while_turn_held"],
            "reload_to_b_session_start_ms": round(result["b_session_start_at"] - result["reload_written_at"] * 1000)
            if result["b_session_start_at"] else None,
            "held_released_at_ms_after_reload": round((held_until - result["reload_written_at"]) * 1000),
            "turn_events": [{k: r.get(k) for k in ("gen", "event", "at", "turnId", "text")}
                            for r in all_rows() if r.get("event") in ("turn.start", "turn.complete")],
            "submit_lines": submit_lines(),
            "requests": REQUESTS,
            "same_process_after_reload": session.poll() is None,
        }
    elif MODE == "append-turn-start":
        result["settled"] = bool(wait_for(lambda: reported("all.settled", "A"), 60))
        wait_for(lambda: len(REQUESTS) >= 2, 10)
        time.sleep(2)
        first = REQUESTS[0] if REQUESTS else {}
        result["analysis"] = {
            "append_resolved": reported("append.resolve", "A"),
            "request_1_has_a": bool(first.get("a")),
            "request_1_has_b": bool(first.get("b")),
            "b_occurrences_by_request": [{"number": r["number"], "b": r["b"]} for r in REQUESTS],
            "turn_starts": [{k: r.get(k) for k in ("at", "turnId", "text")} for r in reported("turn.start.before-next", "A")],
            "b_prompt_resolve": reported("b.resolve", "A") or reported("b.reject", "A"),
            "submit_lines": submit_lines(),
        }
    else:
        result["seed_held"] = bool(wait_for(lambda: any(r["seed"] for r in REQUESTS), 60))
        result["old_awaiting"] = bool(wait_for(lambda: reported("old.awaiting", "A"), 30))
        time.sleep(1)
        result["reload_written_at"] = now()
        (PLUGIN / "hooks/register.js").write_text(module(RELOAD_B, "B"), encoding="utf-8")
        result["new_invoked"] = bool(wait_for(lambda: reported("new.invoke", "B"), 90))
        time.sleep(3)
        lines_before_release = submit_lines()
        HOLD.set()
        wait_for(lambda: len(REQUESTS) >= 3 or reported("new.resolve", "B"), 30)
        time.sleep(4)
        result["analysis"] = {
            "submit_lines_before_release": lines_before_release,
            "submit_lines": submit_lines(),
            "seed": reported("seed.resolve", "A") + reported("seed.reject", "A"),
            "old": reported("old.resolve", "A") + reported("old.reject", "A"),
            "old_activity_after_reload": [r for r in all_rows() if r.get("gen") == "A"
                                          and (r.get("received_at") or (r.get("at") or 0) / 1000) > result["reload_written_at"]],
            "new": reported("new.resolve", "B") + reported("new.reject", "B"),
            "turn_starts": [{k: r.get(k) for k in ("gen", "at", "turnId", "text")}
                            for r in all_rows() if r.get("event") == "turn.start"],
            "requests": REQUESTS,
            "old_in_requests": [r["number"] for r in REQUESTS if r["old"]],
            "new_in_requests": [r["number"] for r in REQUESTS if r["new"]],
            "same_process_after_reload": session.poll() is None,
        }
    result["reports"] = all_rows()
    result["native_reports"] = len(REPORTS)
    result["native_status"] = [r.get("nativeStatus") for r in all_rows() if r.get("event") == "native.status"]
    result["requests"] = REQUESTS
    (EVIDENCE / "screen.txt").write_text(session.output_tail().decode("utf-8", errors="replace"), encoding="utf-8")
    shutil.copyfile(RUN / "debug.txt", EVIDENCE / "debug.txt")
finally:
    HOLD.set()
    if session is not None:
        try:
            rows = processes()
            root = next((row for row in rows if row["pid"] == session.pid), None)
            tree = descendants(rows, root["pid"], root["created"]) if root else []
            result["owned_tree"] = tree
            killed = []
            for row in sorted(tree, key=lambda value: value["created"], reverse=True):
                done = subprocess.run(["taskkill", "/F", "/PID", str(row["pid"])], capture_output=True, text=True)
                killed.append({"pid": row["pid"], "name": row["name"], "rc": done.returncode})
            result["killed"] = killed
        except Exception as error:  # noqa: BLE001
            result["kill_error"] = str(error)[:300]
        session.close()
    SERVER.shutdown()
    time.sleep(2)
    alive = {(row["pid"], row["created"]) for row in processes()}
    result["protected_missing_after"] = [row for row in protected if (row["pid"], row["created"]) not in alive]
    live_after = {str(p): sha(p) for p in LIVE_FILES}
    result["live_before"] = live_before
    result["live_after"] = live_after
    result["live_hash_changed"] = sorted(key for key in live_before if live_before[key] != live_after[key])
    markers = (str(RUN), str(RUN).replace("\\", "/"), str(RUN).replace("\\", "\\\\"), PROBE_ID)
    result["owned_path_in_live_files"] = sorted(
        str(path) for path in LIVE_FILES if path.exists()
        and any(marker in path.read_text(encoding="utf-8", errors="replace") for marker in markers))
    (EVIDENCE / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.rmtree(RUN, ignore_errors=True)
    print(json.dumps({"probe": PROBE_ID, "mode": MODE, "evidence": str(EVIDENCE), "cli_version": result.get("cli_version"),
                      "analysis": result.get("analysis"), "live_hash_changed": result.get("live_hash_changed"),
                      "owned_path_in_live_files": result.get("owned_path_in_live_files"),
                      "protected_missing_after": result.get("protected_missing_after")}, indent=2, ensure_ascii=False))
