"""Owned real-claude.exe E2E: per-priority and side fork models from the first request.

Real Claude Code engine + packaged ciel-work and ciel-work-gate plugins, against an
owned MCP backend (the real Work tools) and a scripted Messages stand-in that
records each request's ``model`` and ``x-claude-code-agent-id``. Three forks:

1. an ordinary P2 Work fork (policy enabled),
2. a P0 fork activated while parallel is disabled+paused and its goal is planning,
3. an SSE side fork.

Each fork's FIRST request must carry its configured model; main keeps its model.
Evidence is structural only (kind, model, counts); no prompt text is persisted.
Only the owned process tree is killed (p0_common.Guard).
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
OWNED = Path(tempfile.mkdtemp(prefix="ciel-fork-models-e2e-"))
SNAPSHOT = common.snapshot_source(SOURCE, OWNED)
CFG, CIEL_CFG, WS = (OWNED / name for name in ("claude", "ciel", "workspace"))
for path in (CFG, CIEL_CFG, WS):
    path.mkdir(parents=True, exist_ok=True)
# The workspace is its own git repository (as a real project is), so Work
# forks get a managed worktree; it must never resolve to a parent repository.
for args in (["init", "-q"], ["config", "user.name", "e2e"], ["config", "user.email", "e2e@localhost"]):
    subprocess.run(["git", "-C", str(WS), *args], check=True, capture_output=True)
(WS / "base.txt").write_text("base\n", encoding="utf-8")
subprocess.run(["git", "-C", str(WS), "add", "-A"], check=True, capture_output=True)
subprocess.run(["git", "-C", str(WS), "commit", "-q", "-m", "base"], check=True, capture_output=True)
SESSION = str(uuid.uuid4())
IDENTITY = "claude:" + SESSION
MAIN_MODEL = "fixture-main"
MODELS = {"P2": "fixture-p2", "P0": "fixture-p0", "side": "fixture-side"}
env = common.stripped_env()
env.update(CIEL_RUNTIME_TEST_ISOLATED="1", CIEL_RUNTIME_CONFIG_DIR=str(CIEL_CFG), CLAUDE_CONFIG_DIR=str(CFG),
           ANTHROPIC_AUTH_TOKEN="fixture", ANTHROPIC_API_KEY="", ANTHROPIC_MODEL=MAIN_MODEL,
           CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", DISABLE_AUTOUPDATER="1", ENABLE_TOOL_SEARCH="false",
           PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
os.environ["CIEL_RUNTIME_CONFIG_DIR"] = str(CIEL_CFG)
os.environ["CIEL_RUNTIME_TEST_ISOLATED"] = "1"
from ciel_runtime_support.windows_conpty import WindowsConPtySession  # noqa: E402
from ciel_runtime_support import work_parallel_mcp as parallel  # noqa: E402
from ciel_runtime_support import work_plan_tree as tree  # noqa: E402
from ciel_runtime_support.work_parallel_host import host_token  # noqa: E402
from ciel_runtime_support.work_plan_store import session_work_plan_store  # noqa: E402
from ciel_runtime_support.work_mcp_tools import WORK_TOOL_NAMES, dispatch_work_tool, work_tool_schemas  # noqa: E402
from ciel_runtime_support.channel_mcp_http_controller import ChannelMcpHttpController, ChannelMcpHttpServices  # noqa: E402
from ciel_runtime_support.channel_mcp_tools import channel_mcp_tool_response  # noqa: E402
from ciel_runtime_support.http_response import HttpResponseAdapter  # noqa: E402

TOKEN = host_token()
env["CIEL_RUNTIME_WORK_PARALLEL_HOST_TOKEN"] = TOKEN
BOARD = session_work_plan_store(CIEL_CFG, IDENTITY)
CONFIG = {
    "work_mode": True, "work_auto_continue": False, "work_plan_approval_ask": False,
    "work_parallel_host": {"token": TOKEN, "capability_ready": True},
    "current_provider": "anthropic",
    "providers": {"anthropic": {"current_model": MAIN_MODEL, "custom_models": sorted(MODELS.values())}},
    "work_parallel_models": {"anthropic": dict(MODELS)},
    "work_side_intake": {"enabled": True, "type": "btw_with_mcp"},
}


def host_run(tool, args, host=None):
    payload = dict(args)
    payload["_host"] = {"token": TOKEN, **(host or {})}
    payload.setdefault("session_id", IDENTITY)
    if tool == "work_parallel_event" and "expected_version" not in payload:
        payload["expected_version"] = parallel.ledger().read()["version"]
    return json.loads(parallel.run(tool, payload, BOARD, IDENTITY, CONFIG, None))


def seed_root(title, priority, phase):
    doc = BOARD.read()
    doc, root = BOARD.transaction(lambda d: tree.set_plan(d, {
        "title": title, "priority": priority,
        "children": [{"title": title + " item", "note": "Capture only; end the turn."}]}, None),
        expected_version=doc["version"])
    if phase == tree.PHASE_EXECUTING:
        BOARD.transaction(lambda d: tree.set_phase(d, phase, root), expected_version=doc["version"])
    return root


host_run("work_parallel_configure", {"changes": {"enabled": True, "paused": False},
                                     "expected_version": parallel.ledger().read()["version"]})
P2_ROOT = seed_root("P2 capture", "P2", tree.PHASE_EXECUTING)
MCP_LOG = []


def tool_response(request_id, params):
    name, args = params.get("name"), params.get("arguments") or {}
    MCP_LOG.append({"name": name, "event": args.get("event")})
    if name not in WORK_TOOL_NAMES:
        return channel_mcp_tool_response(request_id, "unknown", True)
    response = dispatch_work_tool(request_id, name, args, load_config=lambda: json.loads(json.dumps(CONFIG)),
                                  state_dir=CIEL_CFG, respond=channel_mcp_tool_response)
    result_part = (response or {}).get("result") or {}
    if result_part.get("isError"):
        # Refusal text only (never a success payload, which can carry a worker token).
        MCP_LOG[-1]["error"] = "".join(block.get("text", "") for block in result_part.get("content") or [])[:300]
    return response


CONTROLLER = ChannelMcpHttpController(ChannelMcpHttpServices(
    version="fork-models-e2e", tool_schemas=lambda: work_tool_schemas(enabled=True),
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


LOCK = threading.Lock()
REQUESTS = []          # every Messages request: {agent, kind, model, n}
FIRST = {}             # agent id -> first request {kind, model}
SEEN = {"work": threading.Event(), "p0": threading.Event(), "side": threading.Event()}


def last_user_text(body):
    for message in reversed(body.get("messages") or []):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        return "\n".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")
    return ""


def classify(body):
    text = last_user_text(body)
    if "Execute exactly this P0 Work assignment immediately" in text:
        return "p0"
    if "Execute exactly this approved Work assignment" in text:
        return "work"
    if '"claim_generation"' in text and '"event_id"' in text:
        return "side"
    return "other"


def stream(handler, body, text):
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
    emit("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}})
    emit("content_block_stop", {"type": "content_block_stop", "index": 0})
    emit("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
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
        kind = classify(body) if agent else "main"
        with LOCK:
            row = {"agent": agent[:12] if agent else None, "kind": kind, "model": body.get("model")}
            REQUESTS.append(row)
            if agent and agent not in FIRST:
                FIRST[agent] = {"kind": kind, "model": body.get("model"), "order": len(REQUESTS)}
                if kind in SEEN:
                    SEEN[kind].set()
        stream(self, body, "captured" if agent else "ok")


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
result = {"kind": "fork models first-request E2E", "claude_version": version, "configured": MODELS,
          "main_model": MAIN_MODEL, "snapshot_manifest": common.manifest(SNAPSHOT, SOURCE, "release-candidate"),
          "guard": {"owner": GUARD.report["owner"], "static_present": len(GUARD.report["static_present"])},
          "steps": {}}
proc = root_ident = None
kills = []
DEBUG = OWNED / "claude-debug.txt"
try:
    os.chdir(WS)
    proc = WindowsConPtySession([CLI, "--permission-mode", "default", "--plugin-dir", str(PRODUCT),
                                 "--plugin-dir", str(GATE_PLUGIN), "--strict-mcp-config", "--mcp-config",
                                 json.dumps(mcp_config), "--session-id", SESSION, "--debug-file", str(DEBUG)],
                                env, log=lambda *_: None, mirror_output=False, forward_stdin=False)
    root_ident = GUARD.register_root(proc.pid)
    result["mod_load"] = common.wait_mod_loaded(DEBUG, 60)
    gate_load = common.plugin_load_verdict(DEBUG, "ciel-work-gate")
    result["gate_load"] = gate_load
    if not result["mod_load"]["ok"]:
        raise common.ModNotLoaded("ciel-work did not load")
    proc.write(b"Start the approved Work item now.\r")
    result["steps"]["p2_fork_seen"] = SEEN["work"].wait(120)
    if not result["steps"]["p2_fork_seen"]:
        raise RuntimeError("P2 Work fork request not observed")
    # P0 while disabled + paused, on a goal still in planning (not approved).
    host_run("work_parallel_configure", {"changes": {"enabled": False, "paused": True},
                                         "expected_version": parallel.ledger().read()["version"]})
    p0_root = seed_root("P0 capture", "P1", tree.PHASE_PLANNING)
    answer = dispatch_work_tool(1, "work_priority_command", {
        "session_id": IDENTITY, "command": "work_node_update",
        "payload": {"id": p0_root, "priority": "P0", "expected_version": BOARD.read()["version"]},
        "_host": {"token": TOKEN, "internal": True}}, load_config=lambda: json.loads(json.dumps(CONFIG)),
        state_dir=CIEL_CFG, respond=lambda _id, text, error=False: {"text": text, "error": error})
    result["steps"]["p0_priority"] = {"error": answer["error"], **({} if answer["error"] else {
        key: json.loads(answer["text"]).get(key) for key in ("canonical_priority", "effective_priority")})}
    policy = parallel.ledger().read()["policy"]
    result["steps"]["policy_when_p0_set"] = {"enabled": policy["enabled"], "paused": policy["paused"]}
    result["steps"]["p0_root_phase"] = tree.root_phase(BOARD.read(), p0_root)
    proc.write(b"Continue.\r")
    result["steps"]["p0_fork_seen"] = SEEN["p0"].wait(120)
    # SSE side event: the mod's side poll claims it and starts a native side fork.
    from ciel_runtime_support.side_intake_service import receive
    receive(CIEL_CFG, IDENTITY, json.dumps({"data": {"text": "status of the capture?"}}), "e2e:side:1", admit=False)
    proc.write(b"Continue.\r")
    result["steps"]["side_fork_seen"] = SEEN["side"].wait(150)
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
        firsts = list(FIRST.values())
        result["first_requests"] = sorted(firsts, key=lambda row: row["order"])
        result["request_count"] = len(REQUESTS)
        result["main_models"] = sorted({row["model"] for row in REQUESTS if row["kind"] == "main"})
        result["fork_models_seen"] = {kind: sorted({row["model"] for row in REQUESTS if row["kind"] == kind})
                                      for kind in ("work", "p0", "side", "other")}
    expect = {"work": MODELS["P2"], "p0": MODELS["P0"], "side": MODELS["side"]}
    checks = {kind: [row["model"] for row in result["first_requests"] if row["kind"] == kind][:1] == [model]
              for kind, model in expect.items()}
    checks["main_unchanged"] = result["main_models"] == [MAIN_MODEL]
    checks["p0_while_disabled_paused_planning"] = (result["steps"].get("policy_when_p0_set") == {
        "enabled": False, "paused": True} and result["steps"].get("p0_root_phase") == tree.PHASE_PLANNING
        and bool(result["steps"].get("p0_fork_seen")))
    result["checks"] = checks
    result["passed"] = all(checks.values()) and "failure" not in result
    result["kills"] = len(kills)
    final = GUARD.final_report(kills)
    result["owned_survivors"] = len(final["owned_survivors"])
    result["killed_protected_overlap"] = len(final["killed_protected_overlap"])
    result["protected_roots_absent_at_end"] = len(final["protected_roots_absent_at_end"])
    result["mcp_tail"] = MCP_LOG[-30:]
    text = json.dumps(result, ensure_ascii=False, indent=2)
    result["evidence_secret_hits"] = {"host_token": text.count(TOKEN)}
    (OUT / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "checks": checks, "first_requests": result["first_requests"],
                      "failure": result.get("failure")}))
if not result["passed"]:
    raise SystemExit(1)
