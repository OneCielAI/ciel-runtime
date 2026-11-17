"""Owned real-claude.exe E2E: a Work fork in a repository another account owns.

Celly (2026-11-17): the router runs as a sandbox account; the repository and
``.git`` belong to the machine owner, so git refuses it ("detected dubious
ownership") and no Work fork ever started. Here git is told every repository
is owned by another account (``GIT_TEST_ASSUME_DIFFERENT_OWNER=1``) for both
the Work backend (this process) and claude.exe, and the router's launch folder
is the workspace repository, as on Celly.

Real Claude Code + packaged ciel-work and ciel-work-gate plugins, an owned MCP
backend (the real Work tools) and a scripted Messages stand-in. One executing
P2 item. Pass = a Work fork's first request is observed, its lease is bound to
that native agent with a worktree on disk, and no coordination run is left
reserved. Evidence is structural only. Only the owned process tree is killed.

    python run_foreign_owner_e2e.py <source tree> <out dir> [wait seconds]
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
WAIT = float(sys.argv[3]) if len(sys.argv) > 3 else 150.0
OUT.mkdir(parents=True, exist_ok=False)
CLI = str(Path.home() / ".local/bin/claude.exe")
GUARD = common.Guard(OUT)
OWNED = Path(tempfile.mkdtemp(prefix="ciel-foreign-owner-e2e-"))
SNAPSHOT = common.snapshot_source(SOURCE, OWNED)
CFG, CIEL_CFG, WS = (OWNED / name for name in ("claude", "ciel", "workspace"))
for path in (CFG, CIEL_CFG, WS):
    path.mkdir(parents=True, exist_ok=True)
for args in (["init", "-q"], ["config", "user.name", "e2e"], ["config", "user.email", "e2e@localhost"]):
    subprocess.run(["git", "-C", str(WS), *args], check=True, capture_output=True)
(WS / "base.txt").write_text("base\n", encoding="utf-8")
subprocess.run(["git", "-C", str(WS), "add", "-A"], check=True, capture_output=True)
subprocess.run(["git", "-C", str(WS), "commit", "-q", "-m", "base"], check=True, capture_output=True)
WS = WS.resolve()
SESSION = str(uuid.uuid4())
IDENTITY = "claude:" + SESSION
MAIN_MODEL = "fixture-main"
env = common.stripped_env()
FOREIGN = {"GIT_TEST_ASSUME_DIFFERENT_OWNER": "1"}
env.update(CIEL_RUNTIME_TEST_ISOLATED="1", CIEL_RUNTIME_CONFIG_DIR=str(CIEL_CFG), CLAUDE_CONFIG_DIR=str(CFG),
           ANTHROPIC_AUTH_TOKEN="fixture", ANTHROPIC_API_KEY="", ANTHROPIC_MODEL=MAIN_MODEL,
           CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", DISABLE_AUTOUPDATER="1", ENABLE_TOOL_SEARCH="false",
           PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1", **FOREIGN)
os.environ.update(CIEL_RUNTIME_CONFIG_DIR=str(CIEL_CFG), CIEL_RUNTIME_TEST_ISOLATED="1",
                  CIEL_RUNTIME_LAUNCH_CWD=str(WS), **FOREIGN)
# The fixture really is refused by git without trust (checked, not assumed).
REFUSED = subprocess.run(["git", "-C", str(WS), "status"], capture_output=True, text=True)
from ciel_runtime_support import runtime_paths  # noqa: E402
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
    "providers": {"anthropic": {"current_model": MAIN_MODEL, "custom_models": []}},
}
ROUTER_LOG = []


def host_run(tool, args, host=None):
    payload = dict(args)
    payload["_host"] = {"token": TOKEN, **(host or {})}
    payload.setdefault("session_id", IDENTITY)
    return json.loads(parallel.run(tool, payload, BOARD, IDENTITY, CONFIG, None))


host_run("work_parallel_configure", {"changes": {"enabled": True, "paused": False},
                                     "expected_version": parallel.ledger().read()["version"]})
doc = BOARD.read()
doc, ROOT = BOARD.transaction(lambda d: tree.set_plan(d, {
    "title": "Foreign-owner capture", "priority": "P2",
    "children": [{"title": "Capture item", "note": "Capture only; end the turn."}]}, None),
    expected_version=doc["version"])
BOARD.transaction(lambda d: tree.set_phase(d, tree.PHASE_EXECUTING, ROOT), expected_version=doc["version"])
MCP_LOG = []


def tool_response(request_id, params):
    name, args = params.get("name"), params.get("arguments") or {}
    MCP_LOG.append({"name": name, "event": args.get("event")})
    if name not in WORK_TOOL_NAMES:
        return channel_mcp_tool_response(request_id, "unknown", True)
    response = dispatch_work_tool(request_id, name, args, load_config=lambda: json.loads(json.dumps(CONFIG)),
                                  state_dir=CIEL_CFG, respond=channel_mcp_tool_response,
                                  log=lambda level, text: ROUTER_LOG.append((level, text)))
    result_part = (response or {}).get("result") or {}
    if result_part.get("isError"):
        MCP_LOG[-1]["error"] = "".join(block.get("text", "") for block in result_part.get("content") or [])[:300]
    return response


CONTROLLER = ChannelMcpHttpController(ChannelMcpHttpServices(
    version="foreign-owner-e2e", tool_schemas=lambda: work_tool_schemas(enabled=True),
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
REQUESTS = []
FIRST = {}
SEEN = threading.Event()


def last_user_text(body):
    for message in reversed(body.get("messages") or []):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        return "\n".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")
    return ""


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
        work = bool(agent) and "Execute exactly this approved Work assignment" in last_user_text(body)
        with LOCK:
            REQUESTS.append({"agent": agent[:12] if agent else None, "work": work})
            if agent and agent not in FIRST:
                FIRST[agent] = {"work": work, "order": len(REQUESTS)}
                if work:
                    SEEN.set()
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


def redact(text):
    return (str(text).replace(str(OWNED), "<owned>").replace(OWNED.as_posix(), "<owned>")
            .replace(str(Path.home()), "<home>").replace(Path.home().as_posix(), "<home>"))


result = {"kind": "foreign-owner Work fork E2E", "claude_version": version,
          "fixture": {"git_refuses_without_trust": REFUSED.returncode != 0
                      and "dubious ownership" in REFUSED.stderr,
                      "router_workspace_is_repo": Path(runtime_paths.ROUTER_WORKSPACE).resolve() == WS},
          "snapshot_manifest": common.manifest(SNAPSHOT, SOURCE, "stage1"),
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
    result["gate_load"] = common.plugin_load_verdict(DEBUG, "ciel-work-gate")
    if not result["mod_load"]["ok"]:
        raise common.ModNotLoaded("ciel-work did not load")
    proc.write(b"Start the approved Work item now.\r")
    result["steps"]["work_fork_seen"] = SEEN.wait(WAIT)
    # The bind follows the first request; give it a moment to land.
    for _ in range(40):
        leases = parallel.ledger().read()["leases"]
        if any(row.get("host_id") for row in leases.values()):
            break
        time.sleep(0.5)
    time.sleep(2)
except Exception as error:
    result["failure"] = type(error).__name__ + ": " + str(error)[:500]
finally:
    if proc is not None:
        try:
            (OUT / "console.txt").write_text(redact(common.screen_text(proc.output_tail())), encoding="utf-8")
        except Exception:
            pass
    if root_ident is not None:
        kills = GUARD.kill_owned(root_ident)
    if proc is not None:
        proc.close()
    for server in (mcp_server, api_server):
        server.shutdown(); server.server_close()
    ledger = parallel.ledger().read()
    runs = parallel.coordination(parallel.ledger()).read()["runs"]
    leases = []
    for row in ledger["leases"].values():
        path = (row.get("workspace") or {}).get("path")
        leases.append({"state": row["state"], "priority": row["priority"], "bound": row.get("host_id") is not None,
                       "bound_agent_seen": bool(row.get("host_id"))
                       and any(agent.startswith((row.get("host_id") or "-")[:12]) for agent in FIRST),
                       "workspace": None if path is None else {
                           "under_owned_worktrees": Path(path).parent == WS / ".claude" / "worktrees",
                           "exists": Path(path).is_dir()}})
    result["leases"] = leases
    result["coordination_runs"] = sorted(row["state"] for row in runs.values())
    from ciel_runtime_support import work_parallel_failures as failures_module
    if hasattr(failures_module, "public_failures"):
        result["failures"] = [row["code"] for row in failures_module.public_failures(
            parallel.ledger().path, IDENTITY)["failures"]]
    result["router_log"] = [redact(text)[:300] for level, text in ROUTER_LOG if "work_" in text][-10:]
    result["work_first_requests"] = sum(1 for row in FIRST.values() if row["work"])
    result["mcp_tail"] = [{key: redact(value) for key, value in row.items()} for row in MCP_LOG[-30:]]
    checks = {
        "fixture_refused_without_trust": result["fixture"]["git_refuses_without_trust"],
        "work_fork_first_request": bool(result["steps"].get("work_fork_seen")),
        "lease_bound_with_worktree": any(row["bound"] and row["workspace"] and row["workspace"]["exists"]
                                         and row["workspace"]["under_owned_worktrees"] for row in leases),
        "no_reserved_run_left": "reserved" not in result["coordination_runs"],
    }
    result["checks"] = checks
    result["passed"] = all(checks.values()) and "failure" not in result
    result["kills"] = len(kills)
    final = GUARD.final_report(kills)
    result["owned_survivors"] = len(final["owned_survivors"])
    result["killed_protected_overlap"] = len(final["killed_protected_overlap"])
    text = json.dumps(result, ensure_ascii=False, indent=2)
    result["evidence_secret_hits"] = {"host_token": text.count(TOKEN)}
    (OUT / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "checks": checks, "leases": leases,
                      "coordination_runs": result["coordination_runs"], "failure": result.get("failure")}))
if not result["passed"]:
    raise SystemExit(1)
