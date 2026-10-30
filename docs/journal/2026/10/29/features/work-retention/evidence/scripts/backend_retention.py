"""Actual HTTP Work/REST/MCP parity probe on an owned isolated backend."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

repo = Path(sys.argv[1])
out = Path(sys.argv[2])
root = Path(tempfile.mkdtemp(prefix="work-api-proof-"))
env = {k: v for k, v in os.environ.items() if not k.startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI"))}
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
cfg = root / "cfg"
cfg.mkdir()
initial = {"current_provider": "cielairouter", "work_mode": True,
           "providers": {"cielairouter": {"base_url": "http://127.0.0.1:1/v1", "current_model": "owned-work-model"}}}
(cfg / "config.json").write_text(json.dumps(initial), encoding="utf-8")
env.update(HOME=str(root / "home"), USERPROFILE=str(root / "home"),
           CIEL_RUNTIME_CONFIG_DIR=str(cfg), CIEL_RUNTIME_WORKSPACE_STATE_DIR=str(cfg),
           CIEL_RUNTIME_TEST_ISOLATED="1", CIEL_RUNTIME_ROUTER_PORT=str(port),
           HTTP_PROXY="http://127.0.0.1:1", HTTPS_PROXY="http://127.0.0.1:1",
           NO_PROXY="127.0.0.1,localhost,::1")
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
def http(path, body=None):
    req = urllib.request.Request("http://127.0.0.1:" + str(port) + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    try:
        with opener.open(req, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())
def rpc(method, params=None):
    status, response = http("/ca/mcp", {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
    assert status == 200, (status, response)
    assert "error" not in response, response
    return response["result"]
def tool(name, arguments):
    result = rpc("tools/call", {"name": name, "arguments": arguments})
    assert not result.get("isError"), result
    return result
identity = "claude:owned-api-probe"
query = "?session_id=" + identity
records = []
log = (out / "owned-backend.log").open("wb")
proc = subprocess.Popen([sys.executable, "-I", "-c",
    "import sys,runpy;sys.path.insert(0,sys.argv[1]);sys.argv=[sys.argv[1]+'/ciel_runtime.py','serve'];runpy.run_path(sys.argv[0],run_name='__main__')", str(repo)],
    env=env, cwd=root, stdout=log, stderr=subprocess.STDOUT)
try:
    for _ in range(100):
        try:
            if http("/health")[1].get("ok"):
                break
        except OSError:
            time.sleep(.2)
    else:
        raise RuntimeError("owned backend did not start")
    status, settings = http("/ca/config/llm")
    assert status == 200
    assert any(row["key"] == "work_completed_auto_delete" for row in settings["options"])
    status, reply = http("/ca/config/llm", {"action": "option", "key": "work_completed_auto_delete", "value": "off"})
    assert status == 200 and reply["ok"]
    # Owned state only. Expired/recent/open/focused fixtures in one real backend.
    sys.path.insert(0, str(repo))
    from ciel_runtime_support import work_plan_tree as tree
    from ciel_runtime_support.work_plan_store import session_work_plan_store
    stamp = time.time()
    store = session_work_plan_store(cfg, identity)
    def seed(doc):
        tree.set_plan(doc, {"title": "Retention fixture", "children": [{"title": "Phase", "children": [
            {"title": "EXPIRED-DONE"}, {"title": "RECENT-DONE"}, {"title": "OPEN-TASK"}]}]})
        tree.set_phase(doc, "executing")
        tree.update_node(doc, "n3", status="done", evidence="owned proof", now=stamp - 86401)
        tree.update_node(doc, "n4", status="done", evidence="owned proof", now=stamp - 86300)
    saved, _ = store.transaction(seed)
    status, board = http("/ca/work" + query)
    assert status == 200 and board["retention"]["enabled"] is False
    assert len(board["nodes"]) == 5 and (board["done"], board["total"]) == (2, 3)
    records.append({"check": "disabled_retention_keeps_expired_and_menu_option_visible", "ok": True})
    mcp = json.loads(tool("work_status", {"format": "json", "session_id": identity})["content"][0]["text"])
    assert mcp["retention"]["enabled"] is False and mcp["retention"]["completed_after_seconds"] == 86400
    records.append({"check": "mcp_policy_visible_over_real_http", "ok": True})
    # Owned router's actual periodic service runs every60s; no direct sweep call.
    status, reply = http("/ca/config/llm", {"action": "option", "key": "work_completed_auto_delete", "value": "on"})
    assert status == 200 and reply["ok"]
    deadline = time.monotonic() + 75
    while time.monotonic() < deadline:
        status, board = http("/ca/work" + query)
        if not any(n["title"] == "EXPIRED-DONE" for n in board["nodes"]):
            break
        time.sleep(.25)
    else:
        raise AssertionError("periodic retention did not remove expired item")
    titles = {n["title"] for n in board["nodes"]}
    assert {"RECENT-DONE", "OPEN-TASK"} <= titles
    assert (board["done"], board["total"]) == (2, 3)
    assert board["phase"] == "executing" and board["version"] > saved["version"]
    assert json.loads((cfg / "config.json").read_text())["work_completed_auto_delete"] is True
    records.append({"check": "actual_periodic_expiry_keeps_recent_open_and_progress", "ok": True})
    records.append({"check": "atomic_version_persistence_and_phase_preserved", "ok": True})
    status, conflict = http("/ca/work/nodes/update", {"session_id": identity,
        "expected_version": saved["version"], "id": "n5", "note": "stale"})
    assert status == 409 and conflict["error"] == "version_conflict"
    records.append({"check": "cleanup_invalidates_stale_mutation_version", "ok": True})
    # Restart only our owned router to prove deletion persists, not client filtering.
    proc.terminate()
    proc.wait(timeout=15)
    proc = subprocess.Popen([sys.executable, "-I", "-c",
        "import sys,runpy;sys.path.insert(0,sys.argv[1]);sys.argv=[sys.argv[1]+'/ciel_runtime.py','serve'];runpy.run_path(sys.argv[0],run_name='__main__')", str(repo)],
        env=env, cwd=root, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(100):
        try:
            status, board = http("/ca/work" + query)
            if status == 200:
                break
        except OSError:
            time.sleep(.2)
    assert "EXPIRED-DONE" not in {n["title"] for n in board["nodes"]}
    assert (board["done"], board["total"]) == (2, 3)
    records.append({"check": "owned_restart_preserves_pruned_state_and_counts", "ok": True})

finally:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
    log.close()
(out / "backend-probe.json").write_text(json.dumps({"checks": records, "owned_pid": proc.pid,
    "transport": "real HTTP against owned production router", "scratch": str(root)}, indent=2), encoding="utf-8")
print(json.dumps(records, indent=2))
