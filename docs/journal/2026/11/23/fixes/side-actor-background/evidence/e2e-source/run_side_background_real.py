"""Owned real-claude.exe check of the side actor lease and background fixes (2026-11-23).

The installed claude.exe loads ciel-work and ciel-work-gate from a snapshot of
the tree under test. The MCP backend is the real Work and side dispatch
(dispatch_work_tool) on an owned state directory; the Messages API is a
scripted stand-in. No real credentials; every directory is owned scratch under
D:/Temp, removed at the end. Side session "fresh".

Board: one goal with a group node whose wait names the event's approval, a
free leaf under it, and a leaf linked to the event through its refs that holds
main's recorded decision in progress.

Flow:
1. Event 1 enters the real side inbox; the stand-in holds its actor's reply,
   so the actor stays listed live.
2. The driver stalls every work_side_claim / work_side_renew call (the request
   reaches the MCP server and waits) until 140 s after the claim, past the
   120 s lease: the Andy 22:01-22:06Z pattern, where the router answered the
   side pump only after minutes.
3. The stall ends, the stand-in releases the actor's answer: an update that
   releases the group's wait.
Read back: one actor for the event, its stream never dropped (it was not
stopped), the event processed once, the spawn prompt's session_mode,
main_transcript and the linked leaf's records, and the group's wait released
with the leaves' own state unchanged.

usage: python run_side_background_real.py <tree under test> <evidence dir>
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
OUT.mkdir(parents=True, exist_ok=True)
CLI = str(Path.home() / ".local/bin/claude.exe")
PREFIXES = ("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI", "MAX_MCP")
STALL_PAST_CLAIM_S = 140
WAIT = "approval of the release group from the isolated check"
DECISION_TEXT = "MAIN DECISION: only the 120px part is approved; the rest stays held"


def sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def live_profile() -> dict:
    home = Path.home()
    paths = [home / ".claude" / "settings.json", home / ".claude.json"]
    bin_dir = home / "AppData/Local/ciel-runtime/bin"
    if bin_dir.is_dir():
        paths += sorted(p for p in bin_dir.iterdir() if p.is_file())
    return {p.name if p.parent != home else "~/" + p.name: sha(p) for p in paths}


PROFILE_BEFORE = live_profile()
OWNED = Path(tempfile.mkdtemp(prefix="side-bg-", dir="D:/Temp"))
SNAPSHOT = OWNED / "snapshot"
shutil.copytree(SOURCE / "ciel_runtime_support", SNAPSHOT / "ciel_runtime_support",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
CFG, CIEL_CFG, WS = (OWNED / name for name in ("claude", "ciel", "workspace"))
for path in (CFG, CIEL_CFG, WS):
    path.mkdir(parents=True, exist_ok=True)
for args in (["init", "-q"], ["config", "user.name", "e2e"], ["config", "user.email", "e2e@localhost"]):
    subprocess.run(["git", "-C", str(WS), *args], check=True, capture_output=True)
(WS / "base.txt").write_text("base\n", encoding="utf-8")
subprocess.run(["git", "-C", str(WS), "add", "-A"], check=True, capture_output=True)
subprocess.run(["git", "-C", str(WS), "commit", "-q", "-m", "base"], check=True, capture_output=True)

env = {k: v for k, v in os.environ.items() if not k.upper().startswith(PREFIXES)}
for key in [k for k in os.environ if k.upper().startswith(PREFIXES)]:
    os.environ.pop(key, None)
SESSION = str(uuid.uuid4())
IDENTITY = "claude:" + SESSION
MAIN_MODEL = "fixture-main"
env.update(CIEL_RUNTIME_TEST_ISOLATED="1", CIEL_RUNTIME_CONFIG_DIR=str(CIEL_CFG), CLAUDE_CONFIG_DIR=str(CFG),
           ANTHROPIC_AUTH_TOKEN="fixture", ANTHROPIC_API_KEY="", ANTHROPIC_MODEL=MAIN_MODEL,
           CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", DISABLE_AUTOUPDATER="1", ENABLE_TOOL_SEARCH="false",
           PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
os.environ["CIEL_RUNTIME_CONFIG_DIR"] = str(CIEL_CFG)
os.environ["CIEL_RUNTIME_TEST_ISOLATED"] = "1"
sys.path.insert(0, str(SNAPSHOT))
from ciel_runtime_support import work_plan_tree as tree  # noqa: E402
from ciel_runtime_support.windows_conpty import WindowsConPtySession  # noqa: E402
from ciel_runtime_support.work_parallel_host import host_token  # noqa: E402
from ciel_runtime_support.work_mcp_tools import WORK_TOOL_NAMES, dispatch_work_tool, work_tool_schemas  # noqa: E402
from ciel_runtime_support.work_plan_store import session_work_plan_store  # noqa: E402
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox  # noqa: E402
from ciel_runtime_support.channel_mcp_http_controller import ChannelMcpHttpController, ChannelMcpHttpServices  # noqa: E402
from ciel_runtime_support.channel_mcp_tools import channel_mcp_tool_response  # noqa: E402
from ciel_runtime_support.http_response import HttpResponseAdapter  # noqa: E402

TOKEN = host_token()
env["CIEL_RUNTIME_WORK_PARALLEL_HOST_TOKEN"] = TOKEN
LOCK = threading.Lock()
CONFIG = {
    "work_mode": True, "work_auto_continue": False, "work_plan_approval_ask": False,
    "work_parallel_host": {"token": TOKEN, "capability_ready": True},
    "work_side_intake": {"enabled": True, "type": "btw_with_mcp", "session": "fresh"},
    # The periodic blocker check would add its own events; this run has one.
    "work_gate_check": {"minutes": 0},
    "current_provider": "anthropic",
    "providers": {"anthropic": {"current_model": MAIN_MODEL, "custom_models": []}},
}
STORE = session_work_plan_store(CIEL_CFG, IDENTITY)
_, GOAL = STORE.transaction(lambda d: tree.add_node(d, None, "Side background check"))
_, GROUP = STORE.transaction(lambda d: tree.add_node(d, GOAL, "release group", waiting_on=WAIT))
_, FREE = STORE.transaction(lambda d: tree.add_node(d, GROUP, "free leaf"))
_, LINKED = STORE.transaction(lambda d: tree.add_node(d, GROUP, "linked leaf", refs=["sidebg-ref-77"]))
STORE.transaction(lambda d: tree.update_node(d, LINKED, progress=DECISION_TEXT, note="scope note for the linked leaf"))
BOARD_BEFORE = {n["id"]: dict(n) for n in STORE.read()["nodes"]}
INBOX = SideIntakeInbox(CIEL_CFG, IDENTITY)


def snapshot_config():
    with LOCK:
        return json.loads(json.dumps(CONFIG))


def save_config(config):
    with LOCK:
        CONFIG.clear()
        CONFIG.update(json.loads(json.dumps(config)))


MCP_LOG = []
STALL = threading.Event()


def tool_response(request_id, params):
    name, args = params.get("name"), params.get("arguments") or {}
    if name not in WORK_TOOL_NAMES:
        return channel_mcp_tool_response(request_id, "unknown", True)
    arrived = time.time()
    stalled = False
    if name in ("work_side_claim", "work_side_renew"):
        while STALL.is_set():
            stalled = True
            time.sleep(0.2)
    started = time.time()
    response = dispatch_work_tool(request_id, name, args, load_config=snapshot_config, save_config=save_config,
                                  state_dir=CIEL_CFG, respond=channel_mcp_tool_response)
    entry = {"arrived": round(arrived, 2), "at": round(started, 2), "name": name, "stalled": stalled,
             "error": bool(response["result"].get("isError"))}
    if name == "work_side_fail":
        entry["category"] = str(args.get("category") or "")[:80]
        entry["reason"] = str(args.get("reason") or "")[:300]
    if name == "work_side_renew":
        entry["generation"] = str((args.get("_host") or {}).get("generation") or "")[:8]
        if entry["error"]:
            entry["refusal"] = response["result"]["content"][0]["text"][:200]
    if name == "work_side_claim":
        try:
            payload = json.loads(response["result"]["content"][0]["text"])
            if isinstance(payload, dict) and isinstance(payload.get("paged"), dict):
                paged = payload["paged"]
                parts = [json.loads(dispatch_work_tool(request_id, "work_host_page",
                                                       {**args, "id": paged["id"], "part": part},
                                                       load_config=snapshot_config, save_config=save_config,
                                                       state_dir=CIEL_CFG, respond=channel_mcp_tool_response
                                                       )["result"]["content"][0]["text"])["text"]
                         for part in range(paged["parts"])]
                payload = json.loads("".join(parts))
            claim = payload.get("claim") if isinstance(payload, dict) else None
            if isinstance(claim, dict):
                entry["claim"] = claim.get("receipt_id", "")[:12]
                entry["generation"] = claim.get("generation", "")[:8]
        except Exception:
            entry["parse"] = "unparsed"
    with LOCK:
        MCP_LOG.append(entry)
    return response


CONTROLLER = ChannelMcpHttpController(ChannelMcpHttpServices(
    version="side-bg-real", tool_schemas=lambda: work_tool_schemas(enabled=True),
    tool_call_response=tool_response, write_json=HttpResponseAdapter.write_json,
    write_accepted=HttpResponseAdapter.write_accepted, log=lambda *_: None))


class Mcp(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def do_GET(self):
        if not CONTROLLER.get(self, self.path.split("?")[0]):
            self.send_response(404); self.send_header("content-length", "0"); self.end_headers()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        if not CONTROLLER.post(self, self.path.split("?")[0], body):
            self.send_response(404); self.send_header("content-length", "0"); self.end_headers()


SIDE = []
PROMPTS = []
HELD = threading.Event()
RELEASE = threading.Event()
HOLD_MAX_SECONDS = 400


def all_user_text(body):
    parts = []
    for message in body.get("messages") or []:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        else:
            parts.extend(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")
    return "\n".join(parts)


def spawn_prompt(text):
    start = text.find('{"event_id"')
    if start < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(text[start:])[0]
    except ValueError:
        return None


def emit_text(handler, body, text, hold=False):
    msg = {"id": "msg_" + uuid.uuid4().hex[:12], "type": "message", "role": "assistant",
           "model": body.get("model") or MAIN_MODEL, "content": [], "stop_reason": None,
           "usage": {"input_tokens": 20, "output_tokens": 5}}
    handler.send_response(200)
    handler.send_header("content-type", "text/event-stream")
    handler.send_header("connection", "close")
    handler.end_headers()

    def emit(kind, value):
        handler.wfile.write(("event: " + kind + "\ndata: " + json.dumps(value) + "\n\n").encode())
        handler.wfile.flush()

    emit("message_start", {"type": "message_start", "message": msg})
    emit("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
    if hold:
        HELD.set()
        end = time.time() + HOLD_MAX_SECONDS
        try:
            while time.time() < end and not RELEASE.is_set():
                emit("ping", {"type": "ping"})
                time.sleep(2)
        except OSError:
            with LOCK:
                SIDE.append({"at": round(time.time(), 2), "agent": True, "label": "held-stream-dropped"})
            return
    emit("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}})
    emit("content_block_stop", {"type": "content_block_stop", "index": 0})
    emit("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
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
            self.send_response(200); self.send_header("content-length", str(len(data))); self.end_headers()
            self.wfile.write(data)
            return
        agent = self.headers.get("x-claude-code-agent-id") or ""
        text = all_user_text(body)
        label = "SIDE-BG-EVENT-1" if "SIDE-BG-EVENT-1" in text else None
        with LOCK:
            SIDE.append({"at": round(time.time(), 2), "agent": agent[:16], "label": label})
        if agent and label:
            prompt = spawn_prompt(text)
            with LOCK:
                PROMPTS.append({"agent": agent[:16], "prompt": prompt})
            decision = {"action": "update", "kind": "request", "item_id": GROUP,
                        "summary": "release group approval verified in the isolated check",
                        "reply": "release group approved", "refs": ["sidebg-ref-77"],
                        "release_waits": [{"id": GROUP, "waiting_on": WAIT}]}
            return emit_text(self, body, json.dumps({"understanding": "the release group is approved",
                                                     "decision": decision,
                                                     "expected_version": STORE.read()["version"]}),
                             hold=not HELD.is_set())
        emit_text(self, body, "ack")


mcp_server = ThreadingHTTPServer(("127.0.0.1", 0), Mcp)
api_server = ThreadingHTTPServer(("127.0.0.1", 0), Messages)
for server in (mcp_server, api_server):
    threading.Thread(target=server.serve_forever, daemon=True).start()
env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{api_server.server_port}"
mcp_config = {"mcpServers": {"ciel-runtime-router": {"type": "http",
                                                    "url": f"http://127.0.0.1:{mcp_server.server_port}/ca/mcp"}}}
PRODUCT = OWNED / "plugin-ciel-work"
shutil.copytree(SNAPSHOT / "ciel_runtime_support/claude_mods/ciel-work", PRODUCT)
GATE_PLUGIN = OWNED / "plugin-ciel-work-gate"
shutil.copytree(SNAPSHOT / "ciel_runtime_support/claude_mods/ciel-work-gate", GATE_PLUGIN)
(CFG / ".claude.json").write_text(json.dumps({"hasCompletedOnboarding": True, "theme": "dark", "projects": {
    str(WS): {"hasTrustDialogAccepted": True}, str(WS).replace("\\", "/"): {"hasTrustDialogAccepted": True}}}),
    encoding="utf-8")
(CFG / "settings.json").write_text(json.dumps({"permissions": {"allow": ["mcp__ciel-runtime-router"], "deny": []}}),
                                   encoding="utf-8")
version = subprocess.run([CLI, "--version"], capture_output=True, text=True, timeout=60).stdout.strip()
mod_digests = {name: sha(PRODUCT / "hooks" / name) for name in ("register.js", "side-intake.js", "texts.generated.js")}
result = {"kind": "side actor lease and background, real load", "claude_version": version,
          "source": str(SOURCE), "mod_sha256": mod_digests,
          "python_sha256": {name: sha(SNAPSHOT / "ciel_runtime_support" / name) for name in
                            ("side_intake_processing.py", "side_work_decision.py", "work_gate_check.py",
                             "work_mcp_tools.py", "side_intake_settings.py")},
          "board": {"goal": GOAL, "group": GROUP, "free": FREE, "linked": LINKED}, "steps": {}}
DEBUG = OWNED / "claude-debug.txt"
proc = None
t0 = time.time()
receipts = {}
processing: dict = {}


def debug_text() -> str:
    return DEBUG.read_text(encoding="utf-8", errors="replace") if DEBUG.exists() else ""


def wait_for(pattern: str, timeout: float):
    end = time.time() + timeout
    while time.time() < end:
        match = re.search(pattern, debug_text())
        if match:
            return match
        time.sleep(0.5)
    return None


def stage(receipt_id: str) -> str | None:
    row = INBOX._load(receipt_id)
    return None if row is None else SideIntakeInbox._stage(SideIntakeInbox._stage_inputs(row), time.time())


def wait_stage(receipt_id: str, wanted: tuple, timeout: float) -> str | None:
    end = time.time() + timeout
    current = None
    while time.time() < end:
        current = stage(receipt_id)
        if current in wanted:
            return current
        time.sleep(0.5)
    return current


def own_tree_kill(pid: int) -> str:
    done = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)
    return (done.stdout + done.stderr).strip()[:400]


try:
    os.chdir(WS)
    proc = WindowsConPtySession([CLI, "--permission-mode", "default", "--plugin-dir", str(PRODUCT),
                                 "--plugin-dir", str(GATE_PLUGIN), "--strict-mcp-config", "--mcp-config",
                                 json.dumps(mcp_config), "--session-id", SESSION, "--debug-file", str(DEBUG)],
                                env, log=lambda *_: None, mirror_output=False, forward_stdin=False)
    loaded = wait_for(r"hooks module ciel-work@\S+ (loaded|failed to load)[^\n]*", 90)
    result["steps"]["mod_load_line"] = loaded.group(0)[:200] if loaded else None
    settled = wait_for(r"ciel-work@\w+[^\n]*?session\.start settled in ([\d.]+)ms", 90)
    result["steps"]["session_start_settled_ms"] = float(settled.group(1)) if settled else None
    time.sleep(5)
    receipts["e1"] = INBOX.receive("SIDE-BG-EVENT-1 sidebg-ref-77: " + WAIT + " is granted", "side-bg-e1")["receipt_id"]
    result["steps"]["e1_sent_s"] = round(time.time() - t0, 1)
    result["steps"]["actor1_held"] = HELD.wait(120)
    result["steps"]["actor1_held_s"] = round(time.time() - t0, 1)
    # The router stops answering the side pump (claims and renews wait at the
    # MCP server) until well past the 120 s lease of event 1's claim.
    processing = (INBOX._load(receipts["e1"]) or {}).get("processing") or {}
    result["steps"]["claim_generation"] = str(processing.get("generation") or "")[:8]
    result["steps"]["claim_lease_until_s"] = round(float(processing.get("lease_until") or 0) - t0, 1)
    STALL.set()
    result["steps"]["stall_started_s"] = round(time.time() - t0, 1)
    stall_end = float(processing.get("started_at") or time.time()) + STALL_PAST_CLAIM_S
    while time.time() < stall_end:
        time.sleep(0.5)
    STALL.clear()
    result["steps"]["stall_ended_s"] = round(time.time() - t0, 1)
    # A few pump ticks after the stall, then the actor answers.
    time.sleep(15)
    RELEASE.set()
    result["steps"]["released_s"] = round(time.time() - t0, 1)
    result["steps"]["e1_stage"] = wait_stage(receipts["e1"], ("processed", "quarantined"), 120)
    result["steps"]["e1_done_s"] = round(time.time() - t0, 1)
    time.sleep(10)
except Exception as error:
    result["failure"] = type(error).__name__ + ": " + str(error)[:500]
finally:
    RELEASE.set()
    STALL.clear()
    if proc is not None:
        try:
            screen = proc.output_tail().decode("utf-8", errors="replace")
            screen = re.sub(r"\x1b\[[0-9;?<>=]*[ -/]*[@-~]", "", screen)
            (OUT / "console-tail.txt").write_text(screen[-6000:].replace(str(OWNED), "<owned>")
                                                  .replace(str(Path.home()), "<home>"), encoding="utf-8")
        except Exception:
            pass
        result["kill"] = own_tree_kill(proc.pid) if proc.poll() is None else "exited"
        try:
            proc.close()
        except Exception:
            pass
    for server in (mcp_server, api_server):
        server.shutdown(); server.server_close()
    text = debug_text()
    mask = lambda line: re.sub(r"[A-Za-z]:[\\/][^\s\"']+", "<path>", line)
    side_lines = [mask(line)[:400] for line in text.splitlines()
                  if "Side SSE" in line or "side-fresh" in line or "TaskStop" in line or "agent_completion" in line
                  or "Ciel side" in line or re.search(r"ciel-work@\S+ (loaded|failed|session\.start|unloaded)", line)]
    (OUT / "debug-side-lines.txt").write_text("\n".join(side_lines[:600]), encoding="utf-8")
    actors = sorted({r["agent"] for r in SIDE if r["label"] == "SIDE-BG-EVENT-1" and r["agent"]})
    dropped = [r for r in SIDE if r["label"] == "held-stream-dropped"]
    e1 = receipts.get("e1", "")[:12]
    claims_e1 = [e for e in MCP_LOG if e["name"] == "work_side_claim" and e.get("claim") == e1]
    stalled = [e for e in MCP_LOG if e["stalled"]]
    first = (PROMPTS[0]["prompt"] if PROMPTS else None) or {}
    referenced = {row.get("id"): row for row in ((first.get("workload") or {}).get("referenced") or [])}
    board_after = {n["id"]: n for n in STORE.read()["nodes"]}
    candidates = [leaf["id"] for rows in tree._runnable_by_root(STORE.read(), tree._index(STORE.read()))[1]
                  for leaf in rows]
    transcript = str(first.get("main_transcript") or "")
    result["mcp_calls"] = [{"arrived_s": round(e["arrived"] - t0, 1), "dispatched_s": round(e["at"] - t0, 1),
                            **{k: v for k, v in e.items() if k not in ("arrived", "at")}}
                           for e in MCP_LOG if e["name"] != "work_parallel_status"][:300]
    result["model_requests"] = [{"after_s": round(r["at"] - t0, 1), "agent": r["agent"], "label": r["label"]}
                                for r in SIDE][:100]
    result["event1_actors"] = actors
    result["event1_claims"] = [{"dispatched_s": round(e["at"] - t0, 1), "generation": e.get("generation")}
                               for e in claims_e1]
    result["spawn_prompt"] = {"session_mode": first.get("session_mode"),
                              "main_transcript": mask(transcript),
                              "main_transcript_names_session": transcript.endswith(SESSION + ".jsonl"),
                              "referenced": {k: {f: v.get(f) for f in ("refs", "progress", "note", "waiting_on")
                                                 if v.get(f) is not None} for k, v in referenced.items()},
                              "instruction_head": str(first.get("instruction") or "")[:400]}
    result["board_after"] = {k: {f: board_after[k].get(f) for f in ("status", "waiting_on", "progress")}
                             for k in (GROUP, FREE, LINKED) if k in board_after}
    result["candidates_after"] = candidates
    result["final_stage"] = stage(receipts["e1"]) if receipts.get("e1") else None
    result["receipt_row"] = {k: v for k, v in ((INBOX._load(receipts["e1"]) or {}).get("result") or {}).items()
                             if k in ("action", "released_waits", "unreleased_waits", "unblocked_waits")} \
        if receipts.get("e1") else None
    checks = {
        "mod_loaded": bool(result["steps"].get("mod_load_line")) and "failed" not in str(result["steps"].get("mod_load_line")),
        "actor_held_live": bool(result["steps"].get("actor1_held")),
        "pump_calls_stalled_past_lease": any(e["at"] > float(processing.get("lease_until") or 1e18) for e in stalled),
        "one_actor_for_the_event": len(actors) == 1,
        "actor_never_stopped": not dropped,
        "claim_kept_its_generation": all(c.get("generation") == result["steps"].get("claim_generation")
                                         for c in claims_e1) and bool(claims_e1),
        "event_processed": result["final_stage"] == "processed",
        "prompt_session_mode_fresh": first.get("session_mode") == "fresh",
        "prompt_names_main_transcript": result["spawn_prompt"]["main_transcript_names_session"],
        "prompt_linked_leaf_records": (referenced.get(LINKED) or {}).get("progress") == DECISION_TEXT,
        "group_wait_released": (board_after.get(GROUP) or {}).get("waiting_on") == "",
        "leaves_unchanged": all(board_after[k].get(f) == BOARD_BEFORE[k].get(f) for k in (FREE, LINKED)
                                for f in ("status", "waiting_on", "progress", "note")),
        "free_leaf_now_a_candidate": FREE in candidates,
    }
    result["checks"] = checks
    result["passed"] = all(checks.values()) and "failure" not in result
    profile_after = live_profile()
    result["profile_changed"] = sorted(k for k in set(PROFILE_BEFORE) | set(profile_after)
                                       if PROFILE_BEFORE.get(k) != profile_after.get(k))
    result["profile_unchanged"] = not [k for k in result["profile_changed"] if k != "~/.claude.json"]
    result["passed"] = result["passed"] and result["profile_unchanged"]
    os.chdir(SOURCE)
    removed = False

    def writable(remove, path, _info):
        os.chmod(path, 0o600)
        remove(path)

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
        # Paths appear both as written and JSON-escaped (doubled backslashes).
        dumped = dumped.replace(json.dumps(raw)[1:-1], label).replace(raw, label)
    result_text = dumped if TOKEN not in dumped else dumped.replace(TOKEN, "<token>")
    (OUT / "result.json").write_text(result_text, encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "checks": checks, "steps": result["steps"],
                      "event1_actors": actors, "event1_claims": result["event1_claims"],
                      "final_stage": result["final_stage"], "failure": result.get("failure"),
                      "scratch_removed": removed, "profile_unchanged": result["profile_unchanged"]},
                     ensure_ascii=False, indent=1))
if not result["passed"]:
    raise SystemExit(1)
