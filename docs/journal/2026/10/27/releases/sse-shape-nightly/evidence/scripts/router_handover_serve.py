"""Serve one live workspace router from a verified snapshot, replacing only the old router.

Started by router_handover_launch.py with the old router's own environment.
Import happens while the old endpoint is still healthy, so workspace selection
reuses its port without taking the launcher's lease. Only the health-verified old
router PID is terminated; serving starts after its listener is gone.
"""
import json
import os
import socket
import sys
import time
import urllib.request
from pathlib import Path

new_home, port, old_pid, owner_pid = Path(sys.argv[1]).resolve(), int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
expected_instance = Path(sys.argv[5])
registry = Path(sys.argv[6])
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def health():
    with opener.open(f"http://127.0.0.1:{port}/health", timeout=5) as response:
        return json.load(response)


def check(h):
    assert h.get("ok") is True and h.get("pid") == old_pid, "old router identity changed"
    assert h.get("active_client_pids") == [owner_pid], "router clients changed"
    assert os.path.normcase(h["config_dir"]) == os.path.normcase(str(expected_instance)), "instance changed"


def listening():
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


check(health())
before = registry.read_bytes()
os.environ.pop("CIEL_RUNTIME_ROUTER_PORT", None)
sys.path.insert(0, str(new_home))
import ciel_runtime as r  # noqa: E402

assert Path(r.__file__).resolve() == new_home / "ciel_runtime.py"
assert r.ROUTER_PORT == port and os.path.normcase(str(r.ROUTER_INSTANCE_DIR)) == os.path.normcase(str(expected_instance))
assert registry.read_bytes() == before, "workspace selection changed the registry"
assert r.PID_PATH.read_text().strip() == str(old_pid)
h = health()
check(h)
assert r.pid_is_running(owner_pid)
print(f"prepared version={r.VERSION} source={r.SOURCE_FINGERPRINT}", file=sys.stderr, flush=True)
os.environ["CIEL_RUNTIME_ROUTER_PORT"] = str(port)
assert r.terminate_router_health_pid(h, quiet=True), "old router termination failed"
deadline = time.monotonic() + 10
while time.monotonic() < deadline and (r.pid_is_running(old_pid) or listening()):
    time.sleep(0.1)
if r.pid_is_running(old_pid) or listening():
    print("old listener did not close; not serving", file=sys.stderr, flush=True)
    raise SystemExit(3)
print("old router stopped; serving", file=sys.stderr, flush=True)
r.serve(None)
