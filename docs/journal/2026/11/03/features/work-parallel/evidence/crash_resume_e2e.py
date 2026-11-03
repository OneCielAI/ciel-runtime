"""Owned crash/resume E2E: force-kill the parent Claude process while a Work fork
child is in flight, resume the same parent session, and verify the SAME worker
agent resumes through SendMessage without a second lease. Loopback model and MCP
fixtures only; every process killed here was started by this script."""
import json, os, subprocess, sys, threading, uuid, tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PACKAGE = ROOT.parents[7]
RUN = ROOT / ("crash-resume-" + uuid.uuid4().hex[:8]); RUN.mkdir()
removed = [k for k in os.environ if k.upper().startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI"))]
for k in removed:
    os.environ.pop(k, None)
HOME = RUN / "home"; HOME.mkdir()
CFG = RUN / "ccfg"; CFG.mkdir()
WS = Path(tempfile.mkdtemp(prefix="wp-crash-"))
for argv in [("init",), ("config", "user.name", "fixture"), ("config", "user.email", "fixture@example.invalid")]:
    subprocess.run(["git", "-C", str(WS), *argv], check=True, capture_output=True)
(WS / "fixture.txt").write_text("owned fixture")
subprocess.run(["git", "-C", str(WS), "add", "fixture.txt"], check=True, capture_output=True)
subprocess.run(["git", "-C", str(WS), "commit", "-m", "owned fixture"], check=True, capture_output=True)
os.environ.update(HOME=str(HOME), USERPROFILE=str(HOME), APPDATA=str(HOME / "AppData/Roaming"),
                  LOCALAPPDATA=str(HOME / "AppData/Local"), CIEL_RUNTIME_CONFIG_DIR=str(RUN / "runtime-cfg"))
sys.path.insert(0, str(PACKAGE))
sys.path.append("C:/Users/djlov/AppData/Roaming/Python/Python311/site-packages")
import psutil  # noqa: E402
from ciel_runtime_support import runtime_paths, work_parallel_mcp  # noqa: E402
from ciel_runtime_support.work_mcp_tools import dispatch_work_tool, work_tool_schemas  # noqa: E402
from ciel_runtime_support.work_plan_store import session_work_plan_store  # noqa: E402
assert runtime_paths.CONFIG_DIR.resolve() == (RUN / "runtime-cfg").resolve()

SESSION = str(uuid.uuid4()); IDENTITY = "claude:" + SESSION
TOKEN = uuid.uuid4().hex + uuid.uuid4().hex
CONFIG = {"work_mode": True, "work_auto_continue": False, "work_parallel_host": {"token": TOKEN, "capability_ready": True}}
MCP_ROWS, ROWS = [], []
ALL = {"messages": []}
PHASE = {"n": 1}
CHILD_IN_FLIGHT = threading.Event(); RELEASE = threading.Event()


def respond(_, text, error=False):
    return {"content": [{"type": "text", "text": text}], "isError": error}


def dispatch(name, args):
    if args.get("session_id") != IDENTITY:
        out = respond(None, "exact session mismatch", True)
    else:
        out = dispatch_work_tool(None, name, args, load_config=lambda: CONFIG, state_dir=RUN / "state",
                                 respond=respond, save_config=lambda d: CONFIG.update(d))
    safe = json.loads(json.dumps(args))
    if "_host" in safe:
        safe["_host"]["token"] = "[redacted]"
    MCP_ROWS.append({"phase": PHASE["n"], "name": name, "args": safe, "result": out})
    (RUN / "mcp.json").write_text(json.dumps(MCP_ROWS, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


dispatch("work_plan_set", {"session_id": IDENTITY, "goal": "Owned fixture",
                           "children": [{"title": "Phase", "children": [{"title": "Proof A"}]}]})
dispatch("work_execute", {"session_id": IDENTITY, "user_confirmed": True})
ITEM = next(n["id"] for n in session_work_plan_store(RUN / "state", IDENTITY).read()["nodes"] if n["title"] == "Proof A")
TOOLS = work_tool_schemas(); known = {t["name"] for t in TOOLS}
TOOLS += [t for t in work_parallel_mcp.schemas() if t["name"] not in known]


class MCP(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_error(405)

    def do_POST(self):
        b = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
        i, m = b.get("id"), b.get("method")
        if i is None:
            self.send_response(202); self.end_headers(); return
        if m == "initialize":
            r = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "owned", "version": "1"}}
        elif m == "tools/list":
            r = {"tools": TOOLS}
        elif m == "tools/call":
            r = dispatch(b["params"]["name"], b["params"].get("arguments", {}))
        else:
            r = {}
        raw = json.dumps({"jsonrpc": "2.0", "id": i, "result": r}).encode()
        self.send_response(200); self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw))); self.end_headers(); self.wfile.write(raw)


def texts(messages, role="user"):
    out = []
    for m in messages:
        if m.get("role") != role:
            continue
        content = m.get("content")
        blocks = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
        for x in blocks:
            if x.get("type") == "text":
                out.append(x.get("text", ""))
            elif x.get("type") == "tool_result":
                c = x.get("content")
                out.append(c if isinstance(c, str) else json.dumps(c, ensure_ascii=False))
    return out


def tool(name, args):
    return {"type": "tool_use", "id": "toolu_" + uuid.uuid4().hex[:12], "name": name, "input": args}


def mcp_tool(name, args):
    return tool("mcp__ciel-runtime-router__" + name, args)


def choose(tag, n, user, state):
    v = state["version"]
    lease = next(iter(state["leases"].values()), None)
    last = user[-1] if user else ""
    if tag == "main1":
        if n == 0:
            return mcp_tool("work_parallel_configure", {"changes": {"enabled": True, "max_active": 1, "p1_reserved": 1}, "expected_version": v})
        if n == 1:
            return mcp_tool("work_parallel_run", {"item_id": ITEM, "priority": "P1", "role": "worker", "expected_version": v})
        if n == 2:
            ticket = None
            for t in reversed(user):
                try:
                    value = json.loads(t)
                except (ValueError, TypeError):
                    continue
                if isinstance(value, dict) and value.get("dispatch") == "native_agent_required":
                    ticket = value; break
            return tool("Agent", {k: ticket[k] for k in ("subagent_type", "name", "description", "prompt")})
        return {"type": "text", "text": "MAIN-WAITING-FOR-WORKER"}
    if tag == "child1":
        if n == 0:
            return mcp_tool("work_parallel_status", {})
        return None  # hold the worker mid-task; the parent is force-killed now
    if tag == "main2":
        # Hook context arrives as a system-role block in the request, not a user turn.
        recovery = any("Work recovery: lease" in t for t in user + texts(ALL["messages"], "system"))
        if recovery and lease and not any(r.get("sent") for r in ROWS):
            ROWS[-1]["sent"] = True
            return tool("SendMessage", {"to": lease["host_id"], "summary": "Resume Work worker",
                                        "message": "Work recovery: continue approved Work item " + lease["item_id"]
                                        + " under lease " + lease["id"] + ", then report through work_parallel_event."})
        return {"type": "text", "text": "OWNED-MAIN2-" + ("RECOVERED" if any(r.get("sent") for r in ROWS) else "NO-RECOVERY-CONTEXT")}
    if "Work recovery: continue" in last and lease and lease["state"] == "running":
        return mcp_tool("work_parallel_event", {"event": "result", "lease_id": lease["id"], "plan_version": lease["plan_version"],
                                                "expected_version": v, "artifact": "c" * 64, "evidence": "resumed worker result"})
    return {"type": "text", "text": "OWNED-CHILD-RESUMED-RESULT"}


class Gateway(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        b = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
        if "count_tokens" in self.path:
            raw = json.dumps({"input_tokens": 100}).encode()
            self.send_response(200); self.send_header("content-length", str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        messages = b.get("messages", [])
        ALL["messages"] = messages
        user = texts(messages)
        # Only a plain user text block is the fork directive; the parent's own
        # tool_result also carries that sentence inside the dispatch ticket.
        child = any(isinstance(m.get("content"), list) and any(
            x.get("type") == "text" and "Execute only approved Work item" in x.get("text", "") for x in m["content"])
            for m in messages if m.get("role") == "user")
        tag = ("child" if child else "main") + str(PHASE["n"])
        n = sum(1 for r in ROWS if r["tag"] == tag)
        ROWS.append({"tag": tag, "n": n})
        (RUN / ("request-" + str(len(ROWS)) + ".json")).write_text(json.dumps(b, indent=1, ensure_ascii=False), encoding="utf-8")
        c = choose(tag, n, user, work_parallel_mcp.ledger().read())
        if c is None:
            CHILD_IN_FLIGHT.set()
            RELEASE.wait(120)
            return
        reason = "tool_use" if c["type"] == "tool_use" else "end_turn"
        msg = {"id": "msg_" + uuid.uuid4().hex[:10], "type": "message", "role": "assistant", "model": b.get("model"),
               "content": [c], "stop_reason": reason, "usage": {"input_tokens": 100, "output_tokens": 10}}
        if not b.get("stream"):
            raw = json.dumps(msg).encode()
            self.send_response(200); self.send_header("content-length", str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        empty = {"type": "tool_use", "id": c["id"], "name": c["name"], "input": {}} if reason == "tool_use" else {"type": "text", "text": ""}
        delta = ({"type": "input_json_delta", "partial_json": json.dumps(c["input"], ensure_ascii=False)} if reason == "tool_use"
                 else {"type": "text_delta", "text": c["text"]})
        ev = [("message_start", {"type": "message_start", "message": {**msg, "content": [], "stop_reason": None}}),
              ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": empty}),
              ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": delta}),
              ("content_block_stop", {"type": "content_block_stop", "index": 0}),
              ("message_delta", {"type": "message_delta", "delta": {"stop_reason": reason, "stop_sequence": None}, "usage": {"output_tokens": 10}}),
              ("message_stop", {"type": "message_stop"})]
        self.send_response(200); self.send_header("content-type", "text/event-stream"); self.send_header("connection", "close"); self.end_headers()
        for name, data in ev:
            self.wfile.write(("event: " + name + chr(10) + "data: " + json.dumps(data, ensure_ascii=False) + chr(10) * 2).encode()); self.wfile.flush()
        self.close_connection = True


mcp = ThreadingHTTPServer(("127.0.0.1", 0), MCP); mcp.daemon_threads = True
threading.Thread(target=mcp.serve_forever, daemon=True).start()
gw = ThreadingHTTPServer(("127.0.0.1", 0), Gateway); gw.daemon_threads = True
threading.Thread(target=gw.serve_forever, daemon=True).start()
env = dict(os.environ)
env.update({"CLAUDE_CONFIG_DIR": str(CFG), "ANTHROPIC_BASE_URL": "http://127.0.0.1:" + str(gw.server_port),
            "ANTHROPIC_AUTH_TOKEN": "fixture", "ANTHROPIC_API_KEY": "", "ANTHROPIC_MODEL": "fixture",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1", "ENABLE_TOOL_SEARCH": "false",
            "CIEL_RUNTIME_WORK_PARALLEL_HOST_TOKEN": TOKEN})
CLI = "C:/Users/djlov/AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe"
PLUGIN = PACKAGE / "ciel_runtime_support/claude_mods/ciel-work"
MCP_CONFIG = json.dumps({"mcpServers": {"ciel-runtime-router": {"type": "http", "url": "http://127.0.0.1:" + str(mcp.server_port) + "/mcp"}}})
COMMON = ["--dangerously-skip-permissions", "--plugin-dir", str(PLUGIN), "--strict-mcp-config", "--mcp-config", MCP_CONFIG,
          "--output-format", "stream-json", "--verbose"]
result = {"session": IDENTITY, "removed_env_names": removed}
try:
    out1 = (RUN / "phase1-stdout.jsonl").open("w", encoding="utf-8")
    p1 = subprocess.Popen([CLI, "--session-id", SESSION, "--debug-file", str(RUN / "phase1-debug.log"), "-p",
                           "User requests enabling Work parallel cap 1 and running the approved fixture. Do not edit files.", *COMMON],
                          env=env, cwd=WS, stdout=out1, stderr=subprocess.STDOUT)
    result["child_in_flight"] = CHILD_IN_FLIGHT.wait(120)
    # Kill only once the native agent id is durably bound (bind follows spawn resolution).
    for _ in range(300):
        before = work_parallel_mcp.ledger().read()
        if any(r.get("host_id") for r in before["leases"].values()):
            break
        threading.Event().wait(0.1)
    (RUN / "ledger-before-kill.json").write_text(json.dumps(before, indent=2), encoding="utf-8")
    tree = [p1.pid] + [c.pid for c in psutil.Process(p1.pid).children(recursive=True)]
    for pid in reversed(tree):
        try:
            psutil.Process(pid).kill()
        except psutil.NoSuchProcess:
            pass
    p1.wait(30); out1.close()
    result["forced_kill"] = {"pids": tree, "exit": p1.returncode}
    RELEASE.set()
    PHASE["n"] = 2
    p2 = subprocess.run([CLI, "--resume", SESSION, "--debug-file", str(RUN / "phase2-debug.log"), "-p",
                         "계속-세션재시작됨", *COMMON], env=env, cwd=WS, capture_output=True, text=True,
                        encoding="utf-8", timeout=180)
    (RUN / "phase2-stdout.jsonl").write_text(p2.stdout, encoding="utf-8")
    (RUN / "phase2-stderr.txt").write_text(p2.stderr, encoding="utf-8")
    after = work_parallel_mcp.ledger().read()
    (RUN / "ledger-after.json").write_text(json.dumps(after, indent=2), encoding="utf-8")
    leases = list(after["leases"].values())
    lease_before = next(iter(before["leases"].values()), {})
    resumed = [r for r in MCP_ROWS if r["phase"] == 2 and r["args"].get("event") == "result" and not r["result"]["isError"]]
    checks = {
        "child_in_flight_before_kill": bool(result["child_in_flight"]),
        "bound_before_kill": lease_before.get("state") == "running" and bool(lease_before.get("host_id")),
        "recovery_instruction_to_resumed_parent": any(r.get("sent") for r in ROWS),
        "same_worker_reported": bool(resumed) and resumed[0]["args"]["_host"].get("agent_id") == lease_before.get("host_id"),
        "single_lease_no_duplicate": len(leases) == 1,
        "submitted_after_resume": bool(leases) and leases[0]["state"] == "submitted" and leases[0]["host_id"] == lease_before.get("host_id"),
        "phase2_exit_zero": p2.returncode == 0,
    }
    result.update(checks=checks, success=all(checks.values()), requests=ROWS, phase2_exit=p2.returncode)
finally:
    RELEASE.set(); gw.shutdown(); mcp.shutdown()
    (RUN / "verification.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: result.get(k) for k in ("checks", "success", "forced_kill", "phase2_exit")}, indent=2, ensure_ascii=False))
    print(RUN)
