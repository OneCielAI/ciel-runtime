"""Owned real-claude.exe check of a session-socket input sent during a turn.

Betty (2026-11-23/24) runs SSE straight into main through Claude Code's
messaging socket. The router marked inputs that arrived during a turn
``unseen_failed`` 20 s after sending although the turn took every one of them.

This driver reproduces that on the installed Claude Code (2.1.331), with a
scripted Messages API and no real credentials:

1. the first turn runs a 35 s ``ping`` through Bash (Claude Code blocks a
   standalone ``sleep``);
2. 5 s into the sleep, the product socket client sends input id=901;
3. every 0.5 s the real transcript is read and the delivery verdict is
   computed twice, with the product code under test and with the code before
   the fix (``git show <base>:ciel_runtime_support/channel_transcript.py``),
   both driven through the product ``advance_channel_inflight``.

Read back: the record shapes Claude Code wrote, whether the scripted API saw
the input, and each variant's outcome (failed / completed, and when).

usage: python run_mid_turn_socket_real.py <tree under test> <evidence dir> <base commit> [prompt chars] [receive-mod]
"""
from __future__ import annotations

import hashlib
import json
import os
import re
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
OUT = Path(sys.argv[2]).resolve()
BASE = sys.argv[3]
OUT.mkdir(parents=True, exist_ok=True)
CLI = str(Path.home() / ".local/bin/claude.exe")
PREFIXES = ("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI", "MAX_MCP")
MESSAGE_ID = 901
SLEEP_SECONDS = 35
SEND_AFTER_TOOL_START = 5.0
MODEL = "fixture-main"


def sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def live_profile() -> dict:
    home = Path.home()
    return {name: sha(home / name) for name in (".claude/settings.json", ".claude.json")}


PROFILE_BEFORE = live_profile()
OWNED = Path(tempfile.mkdtemp(prefix="mid-turn-socket-"))
SNAPSHOT = OWNED / "snapshot"
shutil.copytree(SOURCE / "ciel_runtime_support", SNAPSHOT / "ciel_runtime_support",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
base_source = subprocess.run(["git", "-C", str(SOURCE), "show", f"{BASE}:ciel_runtime_support/channel_transcript.py"],
                             check=True, capture_output=True, text=True, encoding="utf-8").stdout
(SNAPSHOT / "ciel_runtime_support" / "channel_transcript_base.py").write_text(base_source, encoding="utf-8")
CFG, WS = OWNED / "claude", OWNED / "workspace"
for path in (CFG, WS):
    path.mkdir(parents=True, exist_ok=True)

for key in [k for k in os.environ if k.upper().startswith(PREFIXES)]:
    os.environ.pop(key, None)
env = dict(os.environ)
env.update(CLAUDE_CONFIG_DIR=str(CFG), ANTHROPIC_AUTH_TOKEN="fixture", ANTHROPIC_API_KEY="", ANTHROPIC_MODEL=MODEL,
           CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", DISABLE_AUTOUPDATER="1", ENABLE_TOOL_SEARCH="false",
           PYTHONIOENCODING="utf-8")
os.environ["CLAUDE_CONFIG_DIR"] = str(CFG)
sys.path.insert(0, str(SNAPSHOT))
from ciel_runtime_support import channel_transcript as current  # noqa: E402
from ciel_runtime_support import channel_transcript_base as before  # noqa: E402
from ciel_runtime_support.channel_inflight import (  # noqa: E402
    ChannelInflightEffects, ChannelInflightPolicy, ChannelInflightSnapshot, advance_channel_inflight)
from ciel_runtime_support.channel_runtime_environment import ChannelRuntimeEnvironmentPolicy  # noqa: E402
from ciel_runtime_support.channel_wake_claim_repository import (  # noqa: E402
    prompt_message_ids, prompt_references_message_id)
from ciel_runtime_support.claude_session_socket import ClaudeSessionSocketClient, generated_socket_path  # noqa: E402
from ciel_runtime_support.windows_conpty import WindowsConPtySession  # noqa: E402

LOCK = threading.Lock()
REQUESTS: list[dict] = []
TOOL_STARTED: list[float] = []
SESSION = str(uuid.uuid4())
PIPE = generated_socket_path("nt")
PROMPT = ("[ciel-runtime external channel message] channel=external:default room=external:default "
          f"from=https://walkie.invalid/conversations/c1 id={MESSAGE_ID} thread=c1:1\n"
          "Fixture support message: please acknowledge.")
# Betty's channel inputs are 58-5491 characters (median 3350); pad to a size on request.
BODY_CHARS = int(sys.argv[4]) if len(sys.argv) > 4 else 0
while len(PROMPT) < BODY_CHARS:
    PROMPT += "\nfixture line %04d: the support thread repeats this sentence to reach the size." % len(PROMPT)


def user_text(body: dict) -> str:
    parts = []
    for message in body.get("messages") or []:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
            continue
        for block in content or []:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
    return "\n".join(parts)


def stream(handler, body, blocks, stop_reason):
    msg = {"id": "msg_" + uuid.uuid4().hex[:12], "type": "message", "role": "assistant",
           "model": body.get("model") or MODEL, "content": [], "stop_reason": None,
           "usage": {"input_tokens": 20, "output_tokens": 5}}
    handler.send_response(200)
    handler.send_header("content-type", "text/event-stream")
    handler.send_header("connection", "close")
    handler.end_headers()

    def emit(kind, value):
        handler.wfile.write(("event: " + kind + "\ndata: " + json.dumps(value) + "\n\n").encode())
        handler.wfile.flush()

    emit("message_start", {"type": "message_start", "message": msg})
    for index, block in enumerate(blocks):
        if block["type"] == "text":
            emit("content_block_start", {"type": "content_block_start", "index": index,
                                         "content_block": {"type": "text", "text": ""}})
            emit("content_block_delta", {"type": "content_block_delta", "index": index,
                                         "delta": {"type": "text_delta", "text": block["text"]}})
        else:
            emit("content_block_start", {"type": "content_block_start", "index": index,
                                         "content_block": {"type": "tool_use", "id": block["id"],
                                                           "name": block["name"], "input": {}}})
            emit("content_block_delta", {"type": "content_block_delta", "index": index,
                                         "delta": {"type": "input_json_delta",
                                                   "partial_json": json.dumps(block["input"])}})
        emit("content_block_stop", {"type": "content_block_stop", "index": index})
    emit("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                           "usage": {"output_tokens": 5}})
    emit("message_stop", {"type": "message_stop"})


class Messages(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}"
        body = json.loads(raw)
        if "count_tokens" in self.path:
            data = b'{"input_tokens":20}'
            self.send_response(200)
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        text = user_text(body)
        raw_text = raw.decode("utf-8", errors="replace")
        has_tool_result = any(isinstance(block, dict) and block.get("type") == "tool_result"
                              for message in body.get("messages") or [] if isinstance(message.get("content"), list)
                              for block in message["content"])
        results = [str(block.get("content"))[:300] for message in body.get("messages") or []
                   if isinstance(message.get("content"), list) for block in message["content"]
                   if isinstance(block, dict) and block.get("type") == "tool_result"]
        with LOCK:
            REQUESTS.append({"at": time.time(), "has_input": f"id={MESSAGE_ID}" in raw_text, "tool_results": results[-1:],
                             "has_tool_result": has_tool_result, "start": "start the long task" in text,
                             "main": not self.headers.get("x-claude-code-agent-id")})
        if "start the long task" in text and not has_tool_result:
            with LOCK:
                TOOL_STARTED.append(time.time())
            return stream(self, body, [{"type": "tool_use", "id": "toolu_" + uuid.uuid4().hex[:20], "name": "Bash",
                                        "input": {"command": f"ping -n {SLEEP_SECONDS + 1} 127.0.0.1 > /dev/null && echo waited",
                                                  "description": "wait"}}],
                          "tool_use")
        reply = f"acknowledged {MESSAGE_ID}" if f"id={MESSAGE_ID}" in text else "done"
        return stream(self, body, [{"type": "text", "text": reply}], "end_turn")


def services():
    return current.ChannelWakeTranscriptServices(
        claim_prompt=lambda _message_id: "", prompt_references_message_id=prompt_references_message_id,
        prompt_message_ids=prompt_message_ids, now=time.time)


def transcript_path() -> Path | None:
    found = sorted(CFG.glob(f"projects/*/{SESSION}.jsonl"))
    return found[0] if found else None


class Variant:
    def __init__(self, name, module, holds):
        self.name, self.module, self.holds = name, module, holds
        self.message_id = MESSAGE_ID
        self.logged_at = 0.0
        self.outcome = None
        self.at = None
        self.states = []
        self.logs = []

    def step(self, text, sent_at, now):
        if self.outcome is not None:
            return
        state = self.module.wake_state_evidence_from_text(MESSAGE_ID, text, None, services()).state
        if not self.states or self.states[-1][1] != state:
            self.states.append((round(now - sent_at, 1), state))
        policy = ChannelInflightPolicy(
            unseen_retry_seconds=20.0, waiting_log_interval=30.0,
            is_stale=lambda s, started, current_time: ChannelRuntimeEnvironmentPolicy.inflight_is_stale(
                s, started, current_time, 180.0),
            commit_cursor_on_stale=False, log_namespace=f"channel_{self.name}", stale_event="stale_inflight_retry")
        holds = (lambda since: current.queue_holds_unattributed_input(text, since)) if self.holds else None
        effects = ChannelInflightEffects(
            commit_cursor=lambda _cursor: None, complete_wake=lambda _id: None, release_wake=lambda _id: None,
            ensure_cursor=lambda: MESSAGE_ID - 1, log=lambda level, message: self.logs.append(
                (round(now - sent_at, 1), level, message)),
            queue_holds_input=holds)
        update = advance_channel_inflight(
            ChannelInflightSnapshot(MESSAGE_ID, MESSAGE_ID, state, sent_at, self.logged_at, now, MESSAGE_ID - 1),
            policy, effects)
        self.logged_at = update.logged_at
        if update.action in {"completed", "failed"}:
            self.outcome, self.at = update.action, round(now - sent_at, 1)


def queue_shape(text: str, sent_at: float) -> list[dict]:
    rows = []
    for index, line in enumerate(text.splitlines(), start=1):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        kind = record.get("type")
        attachment = record.get("attachment") if isinstance(record.get("attachment"), dict) else {}
        timestamp = current.record_timestamp_seconds(record)
        if kind == "queue-operation":
            rows.append({"record": index, "kind": "queue-operation", "operation": record.get("operation"),
                         "has_content": bool(record.get("content")), "names_input": f"id={MESSAGE_ID}" in str(record.get("content") or ""),
                         "reason": record.get("reason"), "after_send_s": round(timestamp - sent_at, 2) if timestamp else None})
        elif attachment.get("type") == "queued_command":
            rows.append({"record": index, "kind": "attachment queued_command",
                         "names_input": f"id={MESSAGE_ID}" in str(attachment.get("prompt") or ""),
                         "stamped_after_send_s": round(timestamp - sent_at, 2) if timestamp else None})
        elif kind == "assistant":
            content = (record.get("message") or {}).get("content") or []
            text_out = "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
            if text_out:
                rows.append({"record": index, "kind": "assistant text", "text": text_out[:60]})
    return rows


api = ThreadingHTTPServer(("127.0.0.1", 0), Messages)
threading.Thread(target=api.serve_forever, daemon=True).start()
env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{api.server_port}"
(CFG / ".claude.json").write_text(json.dumps({"hasCompletedOnboarding": True, "theme": "dark", "projects": {
    str(WS): {"hasTrustDialogAccepted": True}, str(WS).replace("\\", "/"): {"hasTrustDialogAccepted": True}}}),
    encoding="utf-8")
(CFG / "settings.json").write_text(json.dumps({"permissions": {"allow": ["Bash"], "deny": []}}), encoding="utf-8")
# Betty loads the ciel-work mod, which hooks session.receive (peer input) and
# passes ordinary input on with next(e). "receive-mod" loads a mod with only
# that pass-through hook, to see whether the hook alone changes the records.
MOD_KIND = sys.argv[5] if len(sys.argv) > 5 else ""
WITH_MOD = MOD_KIND == "receive-mod"
# "ciel-work": the product mods themselves (no Work MCP backend is attached).
WITH_CIEL_WORK = MOD_KIND == "ciel-work"
MOD = OWNED / "plugin-receive-pass"
if WITH_MOD:
    (MOD / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (MOD / "hooks").mkdir(parents=True, exist_ok=True)
    (MOD / ".claude-plugin" / "plugin.json").write_text(json.dumps(
        {"name": "receive-pass", "version": "0.1.0", "description": "session.receive pass-through"}), encoding="utf-8")
    (MOD / "hooks" / "hooks.json").write_text(json.dumps({"modules": ["./register.js"]}), encoding="utf-8")
    (MOD / "hooks" / "register.js").write_text("\n".join((
        "export function register(on) {",
        "  on('session.receive', async ($, e, next) => next(e))",
        "}", "")), encoding="utf-8")
version = subprocess.run([CLI, "--version"], capture_output=True, text=True, timeout=60).stdout.strip()
result = {"kind": "mid-turn session-socket input, real Claude Code", "claude_version": version, "base": BASE,
          "prompt_chars": len(PROMPT),
          "mods": MOD_KIND or "none",
          "python_sha256": {name: sha(SNAPSHOT / "ciel_runtime_support" / name)
                            for name in ("channel_transcript.py", "channel_inflight.py", "channel_transcript_base.py")},
          "steps": {}}
DEBUG = OWNED / "claude-debug.txt"
proc = None
variants = [Variant("fixed", current, True), Variant("before_fix", before, False)]
sent_at = None
try:
    os.chdir(WS)
    plugin_args = ["--plugin-dir", str(MOD)] if WITH_MOD else []
    if WITH_CIEL_WORK:
        plugin_args = ["--plugin-dir", str(SNAPSHOT / "ciel_runtime_support/claude_mods/ciel-work"),
                       "--plugin-dir", str(SNAPSHOT / "ciel_runtime_support/claude_mods/ciel-work-gate")]
    proc = WindowsConPtySession([CLI, "--permission-mode", "default", "--messaging-socket-path", PIPE, *plugin_args,
                                 "--session-id", SESSION, "--debug-file", str(DEBUG), "start the long task"],
                                env, log=lambda *_: None, mirror_output=False, forward_stdin=False)
    end = time.time() + 90
    while time.time() < end and not TOOL_STARTED:
        time.sleep(0.2)
    result["steps"]["tool_use_served"] = bool(TOOL_STARTED)
    if WITH_MOD or WITH_CIEL_WORK:
        loaded = re.search(r"hooks module (?:receive-pass|ciel-work)@\S+ (loaded|failed to load)[^\n]*",
                           DEBUG.read_text(encoding="utf-8", errors="replace") if DEBUG.exists() else "")
        result["steps"]["mod_load_line"] = loaded.group(0)[:200] if loaded else None
    if not TOOL_STARTED:
        raise RuntimeError("the first turn never asked for the tool")
    time.sleep(SEND_AFTER_TOOL_START)
    client = ClaudeSessionSocketClient(Path.home(), lambda level, message: None, platform_name="nt")
    client.configure(PIPE)
    end = time.time() + 30
    while time.time() < end and not client.available():
        time.sleep(0.5)
    sent_at = time.time()
    result["steps"]["socket_sent"] = client.send(PROMPT, [{"id": MESSAGE_ID, "message": PROMPT}])
    result["steps"]["sent_after_tool_start_s"] = round(sent_at - TOOL_STARTED[0], 1)
    end = sent_at + 120
    while time.time() < end and any(v.outcome is None for v in variants):
        path = transcript_path()
        text = path.read_text(encoding="utf-8", errors="replace") if path else ""
        now = time.time()
        for variant in variants:
            variant.step(text, sent_at, now)
        time.sleep(0.5)
    time.sleep(2)
except Exception as error:
    result["failure"] = type(error).__name__ + ": " + str(error)[:500]
finally:
    path = transcript_path()
    text = path.read_text(encoding="utf-8", errors="replace") if path else ""
    if proc is not None:
        try:
            screen = proc.output_tail().decode("utf-8", errors="replace")
            screen = re.sub(r"\x1b\[[0-9;?<>=]*[ -/]*[@-~]", "", screen)
            (OUT / "console-tail.txt").write_text(screen[-4000:], encoding="utf-8")
        except Exception:
            pass
        killed = subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, text=True)
        result["kill"] = (killed.stdout + killed.stderr).strip()[:300]
        try:
            proc.close()
        except Exception:
            pass
    api.shutdown()
    api.server_close()
    with LOCK:
        requests = [{**r, "at": round(r["at"] - sent_at, 1) if sent_at else None} for r in REQUESTS]
    result["requests_after_send_s"] = requests
    result["transcript_shape"] = queue_shape(text, sent_at) if sent_at else []
    result["variants"] = {v.name: {"outcome": v.outcome, "at_s": v.at, "states": v.states,
                                   "logs": [entry for entry in v.logs if "waiting" not in entry[2]][:10]}
                          for v in variants}
    shape = result["transcript_shape"]
    queue_rows = [r for r in shape if r["kind"] == "queue-operation"]
    attach = next((r for r in shape if r["kind"] == "attachment queued_command" and r["names_input"]), None)
    absorb = next((r for r in queue_rows if r["operation"] == "remove" and r["names_input"]), None)
    fixed, base = result["variants"]["fixed"], result["variants"]["before_fix"]
    contentless = any(r["operation"] == "enqueue" and not r["has_content"]
                      and (r["after_send_s"] or -1) >= -2 for r in queue_rows)
    attachment_first = bool(attach and absorb and attach["record"] < absorb["record"]
                            and absorb["reason"] == "absorbed_mid_turn")
    result["observed_shape"] = {"contentless_enqueue_after_send": contentless,
                                "attachment_before_absorbing_remove": attachment_first,
                                "before_fix_outcome": base["outcome"]}
    checks = {
        "claude_2_1_331": version.startswith("2.1.331"),
        "input_sent_during_the_tool": bool(result["steps"].get("socket_sent")),
        "model_request_carried_the_input": any(r["has_input"] for r in requests),
        "fixed_completes_it": fixed["outcome"] == "completed",
    }
    if WITH_CIEL_WORK:
        # Betty's shape: the reproduction must show it and the old verdict.
        checks.update(mod_loaded="failed" not in str(result["steps"].get("mod_load_line") or "failed"),
                      contentless_enqueue_after_send=contentless,
                      attachment_before_absorbing_remove=attachment_first,
                      before_fix_marks_it_failed=base["outcome"] == "failed")
    result["checks"] = checks
    result["passed"] = all(checks.values()) and "failure" not in result
    profile_after = live_profile()
    # ~/.claude.json is rewritten by the operator's own live Claude session all
    # the time; the child here has its own CLAUDE_CONFIG_DIR. settings.json must
    # stay as it was.
    result["profile_changed"] = sorted(k for k in PROFILE_BEFORE if PROFILE_BEFORE[k] != profile_after.get(k))
    result["profile_unchanged"] = ".claude/settings.json" not in result["profile_changed"]
    result["passed"] = result["passed"] and result["profile_unchanged"]
    os.chdir(SOURCE)

    def writable(remove, target, _info):
        os.chmod(target, 0o600)
        remove(target)

    removed = False
    for _ in range(30):
        try:
            shutil.rmtree(OWNED, onerror=writable)
            removed = True
            break
        except OSError:
            time.sleep(2)
    result["scratch_removed"] = removed
    dumped = json.dumps(result, ensure_ascii=False, indent=2)
    for raw, label in ((str(OWNED), "<owned>"), (str(Path.home()), "<home>")):
        dumped = dumped.replace(json.dumps(raw)[1:-1], label).replace(raw, label)
    (OUT / "result.json").write_text(dumped, encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "checks": checks, "variants": {
        k: {"outcome": v["outcome"], "at_s": v["at_s"], "states": v["states"]} for k, v in result["variants"].items()},
        "failure": result.get("failure"), "scratch_removed": removed}, ensure_ascii=False, indent=1))
if not result["passed"]:
    raise SystemExit(1)
