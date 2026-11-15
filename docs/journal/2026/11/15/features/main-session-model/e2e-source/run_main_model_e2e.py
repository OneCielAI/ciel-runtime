"""Owned real-claude.exe E2E: the workspace main-session model applies to main from the next turn.

Real Claude Code engine + packaged ciel-work and ciel-work-gate plugins, against an
owned MCP backend (the real Work tools) and a scripted Messages stand-in that
records each request's ``model`` and ``x-claude-code-agent-id``.

Turns:
1. no main model set: main sends its launch model;
2. the main model is set through the shared settings path (work_live_settings):
   step 0 of the next turn sends the chosen model; the stand-in answers that step
   with a tool call and, before answering, resets the setting to inherit, so the
   turn's second step proves the model is held for the whole turn;
3. the following turn (setting now inherit) sends the launch model again.
The P0-P5/side fork table is stored in the same config and must be unchanged.
Evidence is structural only (kind, model, counts). Only the owned process tree is
killed (p0_common.Guard).
"""
from __future__ import annotations

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

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import p0_common as common  # noqa: E402

SOURCE = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
OUT.mkdir(parents=True, exist_ok=False)
CLI = str(Path.home() / ".local/bin/claude.exe")
GUARD = common.Guard(OUT)
OWNED = Path(tempfile.mkdtemp(prefix="ciel-main-model-e2e-"))
SNAPSHOT = common.snapshot_source(SOURCE, OWNED)
CFG, CIEL_CFG, WS = (OWNED / name for name in ("claude", "ciel", "workspace"))
for path in (CFG, CIEL_CFG, WS):
    path.mkdir(parents=True, exist_ok=True)
for args in (["init", "-q"], ["config", "user.name", "e2e"], ["config", "user.email", "e2e@localhost"]):
    subprocess.run(["git", "-C", str(WS), *args], check=True, capture_output=True)
(WS / "base.txt").write_text("base\n", encoding="utf-8")
subprocess.run(["git", "-C", str(WS), "add", "-A"], check=True, capture_output=True)
subprocess.run(["git", "-C", str(WS), "commit", "-q", "-m", "base"], check=True, capture_output=True)
SESSION = str(uuid.uuid4())
IDENTITY = "claude:" + SESSION
MAIN_MODEL = "fixture-main"
CHOSEN = "fixture-main-chosen"
FORKS = {"P2": "fixture-p2", "side": "fixture-side"}
env = common.stripped_env()
env.update(CIEL_RUNTIME_TEST_ISOLATED="1", CIEL_RUNTIME_CONFIG_DIR=str(CIEL_CFG), CLAUDE_CONFIG_DIR=str(CFG),
           ANTHROPIC_AUTH_TOKEN="fixture", ANTHROPIC_API_KEY="", ANTHROPIC_MODEL=MAIN_MODEL,
           CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", DISABLE_AUTOUPDATER="1", ENABLE_TOOL_SEARCH="false",
           PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
os.environ["CIEL_RUNTIME_CONFIG_DIR"] = str(CIEL_CFG)
os.environ["CIEL_RUNTIME_TEST_ISOLATED"] = "1"
sys.path.insert(0, str(SNAPSHOT))
from ciel_runtime_support.windows_conpty import WindowsConPtySession  # noqa: E402
from ciel_runtime_support.work_parallel_host import host_token  # noqa: E402
from ciel_runtime_support.work_mcp_tools import WORK_TOOL_NAMES, dispatch_work_tool, work_tool_schemas  # noqa: E402
from ciel_runtime_support.work_live_settings import settings as live_settings  # noqa: E402
from ciel_runtime_support.channel_mcp_http_controller import ChannelMcpHttpController, ChannelMcpHttpServices  # noqa: E402
from ciel_runtime_support.channel_mcp_tools import channel_mcp_tool_response  # noqa: E402
from ciel_runtime_support.http_response import HttpResponseAdapter  # noqa: E402

TOKEN = host_token()
env["CIEL_RUNTIME_WORK_PARALLEL_HOST_TOKEN"] = TOKEN
LOCK = threading.Lock()
CONFIG = {
    "work_mode": True, "work_auto_continue": False, "work_plan_approval_ask": False,
    "work_parallel_host": {"token": TOKEN, "capability_ready": True},
    "current_provider": "anthropic",
    "providers": {"anthropic": {"current_model": MAIN_MODEL,
                                "custom_models": [CHOSEN, *sorted(FORKS.values())]}},
    "work_parallel_models": {"anthropic": dict(FORKS)},
}


def snapshot_config():
    with LOCK:
        return json.loads(json.dumps(CONFIG))


def save_config(config):
    with LOCK:
        CONFIG.clear()
        CONFIG.update(json.loads(json.dumps(config)))


def set_main(value):
    """The same write the menu, web Settings tab, REST and ciel-runtimectl use."""
    result = live_settings(snapshot_config(), {"key": "work_main_model", "value": value}, save_config)
    row = next(r for r in result["settings"] if r["key"] == "work_main_model")
    return {"value": row["value"], "status": row["status"]}


MCP_LOG = []


def tool_response(request_id, params):
    name, args = params.get("name"), params.get("arguments") or {}
    MCP_LOG.append({"name": name, "event": args.get("event")})
    if name not in WORK_TOOL_NAMES:
        return channel_mcp_tool_response(request_id, "unknown", True)
    return dispatch_work_tool(request_id, name, args, load_config=snapshot_config,
                              state_dir=CIEL_CFG, respond=channel_mcp_tool_response)


CONTROLLER = ChannelMcpHttpController(ChannelMcpHttpServices(
    version="main-model-e2e", tool_schemas=lambda: work_tool_schemas(enabled=True),
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


REQUESTS = []           # main requests: {turn_marker, model, has_tool_result}
STATE = {"phase": "turn1", "reset_done": False}
TURN_SEEN = {key: threading.Event() for key in ("turn1", "turn2_step0", "turn2_step1", "turn3")}


def last_user_text(body):
    for message in reversed(body.get("messages") or []):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content, False
        texts = [b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text"]
        has_result = any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content or [])
        return "\n".join(texts), has_result
    return "", False


def emit_stream(handler, body, blocks, stop):
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
    for index, block in enumerate(blocks):
        if block["type"] == "text":
            emit("content_block_start", {"type": "content_block_start", "index": index,
                                         "content_block": {"type": "text", "text": ""}})
            emit("content_block_delta", {"type": "content_block_delta", "index": index,
                                         "delta": {"type": "text_delta", "text": block["text"]}})
        else:
            emit("content_block_start", {"type": "content_block_start", "index": index, "content_block": {
                "type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}})
            emit("content_block_delta", {"type": "content_block_delta", "index": index,
                                         "delta": {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}})
        emit("content_block_stop", {"type": "content_block_stop", "index": index})
    emit("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                           "usage": {"output_tokens": 5}})
    emit("message_stop", {"type": "message_stop"})


class Messages(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        if "count_tokens" in self.path:
            raw = b'{"input_tokens":20}'
            self.send_response(200); self.send_header("content-length", str(len(raw))); self.end_headers()
            self.wfile.write(raw)
            return
        agent = self.headers.get("x-claude-code-agent-id") or ""
        text, has_result = last_user_text(body)
        model = body.get("model")
        if agent:
            emit_stream(self, body, [{"type": "text", "text": "fork ok"}], "end_turn")
            return
        marker = next((m for m in ("TURN-ONE", "TURN-TWO", "TURN-THREE") if m in text), None)
        with LOCK:
            REQUESTS.append({"marker": marker, "tool_result": has_result, "model": model})
        if marker == "TURN-ONE":
            TURN_SEEN["turn1"].set()
            emit_stream(self, body, [{"type": "text", "text": "one done"}], "end_turn")
        elif marker == "TURN-TWO" and not has_result:
            # A remote reset lands while the turn is in its tool loop.
            STATE["reset"] = set_main("inherit")
            TURN_SEEN["turn2_step0"].set()
            emit_stream(self, body, [{"type": "tool_use", "id": "toolu_" + uuid.uuid4().hex[:12],
                                      "name": "mcp__ciel-runtime-router__work_status",
                                      "input": {"format": "text"}}], "tool_use")
        elif has_result and not marker:
            TURN_SEEN["turn2_step1"].set()
            emit_stream(self, body, [{"type": "text", "text": "two done"}], "end_turn")
        elif marker == "TURN-THREE":
            TURN_SEEN["turn3"].set()
            emit_stream(self, body, [{"type": "text", "text": "three done"}], "end_turn")
        else:
            emit_stream(self, body, [{"type": "text", "text": "ok"}], "end_turn")


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
result = {"kind": "main session model E2E", "claude_version": version, "launch_model": MAIN_MODEL,
          "chosen": CHOSEN, "snapshot_manifest": common.manifest(SNAPSHOT, SOURCE, "release-candidate"),
          "steps": {}}
proc = root_ident = None
kills = []
DEBUG = OWNED / "claude-debug.txt"
forks_before = json.loads(json.dumps(CONFIG["work_parallel_models"]))
try:
    os.chdir(WS)
    proc = WindowsConPtySession([CLI, "--permission-mode", "default", "--plugin-dir", str(PRODUCT),
                                 "--plugin-dir", str(GATE_PLUGIN), "--strict-mcp-config", "--mcp-config",
                                 json.dumps(mcp_config), "--session-id", SESSION, "--debug-file", str(DEBUG)],
                                env, log=lambda *_: None, mirror_output=False, forward_stdin=False)
    root_ident = GUARD.register_root(proc.pid)
    result["mod_load"] = common.wait_mod_loaded(DEBUG, 60)
    result["gate_load"] = common.plugin_load_verdict(DEBUG, "ciel-work-gate")
    if not result["mod_load"]["ok"]:
        raise common.ModNotLoaded("ciel-work did not load")
    proc.write(b"TURN-ONE reply briefly.\r")
    result["steps"]["turn1"] = TURN_SEEN["turn1"].wait(120)
    time.sleep(3)
    result["steps"]["set_main"] = set_main(CHOSEN)
    proc.write(b"TURN-TWO reply briefly.\r")
    result["steps"]["turn2_step0"] = TURN_SEEN["turn2_step0"].wait(120)
    result["steps"]["turn2_step1"] = TURN_SEEN["turn2_step1"].wait(120)
    result["steps"]["reset_during_turn2"] = STATE.get("reset")
    time.sleep(3)
    proc.write(b"TURN-THREE reply briefly.\r")
    result["steps"]["turn3"] = TURN_SEEN["turn3"].wait(120)
    time.sleep(2)
except Exception as error:
    result["failure"] = type(error).__name__ + ": " + str(error)[:500]
finally:
    if proc is not None:
        try:
            (OUT / "console.txt").write_text(common.screen_text(proc.output_tail())
                                             .replace(str(OWNED), "<owned>").replace(str(Path.home()), "<home>"),
                                             encoding="utf-8")
        except Exception:
            pass
    if root_ident is not None:
        kills = GUARD.kill_owned(root_ident)
    if proc is not None:
        proc.close()
    for server in (mcp_server, api_server):
        server.shutdown(); server.server_close()
    with LOCK:
        result["main_requests"] = list(REQUESTS)
        result["fork_table_after"] = CONFIG.get("work_parallel_models")
        result["main_table_after"] = CONFIG.get("work_main_models")

    def models(marker, tool_result):
        return [row["model"] for row in result["main_requests"]
                if row["marker"] == marker and row["tool_result"] == tool_result]
    turn2_step1 = [row["model"] for row in result["main_requests"] if row["marker"] is None and row["tool_result"]]
    checks = {
        "turn1_launch_model": models("TURN-ONE", False)[:1] == [MAIN_MODEL],
        "turn2_first_step_chosen": models("TURN-TWO", False)[:1] == [CHOSEN],
        "turn2_tool_step_still_chosen_after_reset": turn2_step1[:1] == [CHOSEN],
        "turn3_back_to_launch_model": models("TURN-THREE", False)[:1] == [MAIN_MODEL],
        "fork_table_untouched": result["fork_table_after"] == forks_before,
        "set_status_active": (result["steps"].get("set_main") or {}).get("status") == "active",
    }
    result["checks"] = checks
    result["passed"] = all(checks.values()) and "failure" not in result
    result["kills"] = len(kills)
    final = GUARD.final_report(kills)
    result["owned_survivors"] = len(final["owned_survivors"])
    result["killed_protected_overlap"] = len(final["killed_protected_overlap"])
    result["mcp_tail"] = MCP_LOG[-20:]
    text = json.dumps(result, ensure_ascii=False, indent=2)
    result["evidence_secret_hits"] = {"host_token": text.count(TOKEN)}
    (OUT / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "checks": checks, "main_requests": result["main_requests"],
                      "failure": result.get("failure")}))
if not result["passed"]:
    raise SystemExit(1)
