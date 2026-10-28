"""Exercise the published snapshot with isolated config and real production HTTP."""
import json, os, socket, sys, tempfile, threading, urllib.request, urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path
snapshot = Path(sys.argv[1])
out = Path(sys.argv[2])
sys.path.insert(0, str(snapshot))
for key in list(os.environ):
    if key.upper().startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI")):
        os.environ.pop(key)
with tempfile.TemporaryDirectory(prefix="work-installed-http-") as directory:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    os.environ.update(CIEL_RUNTIME_CONFIG_DIR=directory, CIEL_RUNTIME_ROUTER_PORT=str(port), CIEL_RUNTIME_TEST_ISOLATED="1")
    import ciel_runtime as runtime
    handler = runtime.RouterHandler
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:" + str(port)
    def request(path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(base + path, data=data, headers={"content-type": "application/json"})
        try:
            result = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as exc:
            result = exc
        with result:
            return result.status, json.load(result)
    try:
        path = "/ca/work?session_id=claude:installed-proof"
        status, initial = request(path)
        assert status == 200 and initial["nodes"] == []
        status, added = request("/ca/work/nodes/add", {"session_id": "claude:installed-proof", "expected_version": 0,
            "title": "Installed API proof", "children": [{"title": "phase", "children": [{"title": "A"}, {"title": "B"}]}]})
        assert status == 200 and len(added["nodes"]) == 4
        target = next(n["id"] for n in added["nodes"] if n["title"] == "B")
        status, changed = request("/ca/work/nodes/reorder", {"session_id": "claude:installed-proof", "expected_version": added["version"], "id": target, "position": 1})
        assert status == 200 and changed["changed"]
        status, conflict = request("/ca/work/nodes/remove", {"session_id": "claude:installed-proof", "expected_version": 0, "id": target})
        assert status == 409
        status, other = request("/ca/work?session_id=codex:other")
        assert other["nodes"] == []
        record = {"ok": True, "snapshot": str(snapshot), "version": runtime.VERSION,
            "initial": initial, "changed": changed, "conflict": conflict, "isolation": other["session_id"],
            "help_status": request("/ca/work/help")[0]}
        out.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"ok": True, "version": runtime.VERSION, "nodes": len(changed["nodes"]), "conflict_status": status}))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
