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
    status, board = http("/ca/work" + query)
    assert status == 200 and board["enabled"] is True
    status, added = http("/ca/work/nodes/add", {"session_id": identity, "expected_version": board["version"],
        "title": "Owned goal", "children": [{"title": "Phase", "children": [{"title": "Task"}]}]})
    assert status == 200 and len(added["nodes"]) == 3, (status, added)
    records.append({"check": "rest_board_add", "ok": True, "nodes": len(added["nodes"])})
    names = {t["name"] for t in rpc("tools/list")["tools"]}
    assert {"work_status", "work_plan_set", "work_execute", "work_mode_set", "work_focus"} <= names
    result = tool("work_status", {"format": "json", "session_id": identity})
    snapshot = json.loads(result["content"][0]["text"])
    assert snapshot["active_root"]["title"] == "Owned goal"
    records.append({"check": "mcp_list_and_status_over_http", "ok": True})
    status, reply = http("/ca/config/llm", {"action": "option", "key": "work_mode", "value": "off"})
    assert status == 200 and reply.get("ok"), (status, reply)
    status, disabled = http("/ca/work" + query)
    assert disabled["enabled"] is False and disabled["nodes"] == added["nodes"]
    names = {t["name"] for t in rpc("tools/list")["tools"] if t["name"].startswith("work_")}
    assert names == {"work_mode_set"}, names
    status, rejected = http("/ca/work/nodes/add", {"session_id": identity,
        "expected_version": disabled["version"], "title": "MUST NOT ADD"})
    assert status == 409 and rejected.get("error") == "work_disabled"
    records.append({"check": "rest_mode_off_persisted_gates_mutations_and_mcp_catalog", "ok": True})
    tool("work_mode_set", {"enabled": True})
    status, enabled = http("/ca/work" + query)
    assert enabled["enabled"] is True and enabled["nodes"] == added["nodes"]
    persisted = json.loads((cfg / "config.json").read_text(encoding="utf-8"))
    assert persisted["work_mode"] is True
    records.append({"check": "mcp_mode_on_persisted_preserves_plan", "ok": True})
    status, reply = http("/ca/config/llm", {"action": "option", "key": "work_mode", "value": "on"})
    assert status == 200 and reply["ok"]
    assert {"work_status", "work_execute", "work_replan"} <= {t["name"] for t in rpc("tools/list")["tools"]}
    records.append({"check": "rest_mode_on_and_full_mcp_restored", "ok": True})
    status, help_reply = http("/ca/work/help")
    assert status == 200
    records.append({"check": "rest_help", "ok": True})
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
