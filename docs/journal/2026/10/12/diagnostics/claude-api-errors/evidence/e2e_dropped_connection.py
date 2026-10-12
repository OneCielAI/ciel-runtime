"""An isolated router whose Anthropic upstream drops the connection mid-request: what reaches the client and router.log.

usage: python e2e_dropped_connection.py REPO OUT_DIR
A local fake upstream reads the request headers and part of the body, then resets the connection (RST),
the shape of 'WinError 10054 ... forcibly closed by the remote host'. The router runs from REPO with its
own config dir (test API key, base URL = the fake upstream) and router port; nothing of the user's config is used.
"""
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

repo, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


upstream = socket.socket()
upstream.bind(("127.0.0.1", 0))
upstream.listen(8)
upstream_port = upstream.getsockname()[1]
seen = []


def serve_upstream() -> None:
    while True:
        try:
            conn, _ = upstream.accept()
        except OSError:
            return
        data = b""
        conn.settimeout(5)
        try:
            while b"\r\n\r\n" not in data:
                data += conn.recv(65536)
            data += conn.recv(65536)  # part of the body
        except OSError:
            pass
        seen.append(data.split(b"\r\n", 1)[0].decode("latin-1"))
        conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))  # close with RST
        conn.close()


threading.Thread(target=serve_upstream, daemon=True).start()
config = Path(tempfile.mkdtemp(prefix="ciel-e2e-drop-"))
(config / "config.json").write_text(json.dumps({
    "current_provider": "anthropic", "language": "en",
    "providers": {"anthropic": {"api_key": "sk-ant-e2e-test-not-a-key", "base_url": f"http://127.0.0.1:{upstream_port}",
                                "current_model": "claude-opus-5-5", "gateway_retries": 0}},
}), encoding="utf-8")
(config / "log-level").write_text("INFO")
router_port = free_port()
env = {key: value for key, value in os.environ.items() if not key.startswith(("CIEL_RUNTIME_", "ANTHROPIC_", "CLAUDE_"))}
env.update(CIEL_RUNTIME_CONFIG_DIR=str(config), CIEL_RUNTIME_ROUTER_PORT=str(router_port), CIEL_RUNTIME_TEST_ISOLATED="1")
router = subprocess.Popen([sys.executable, str(repo / "ciel_runtime.py"), "serve"], cwd=str(config), env=env,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{router_port}/health", timeout=1)
            break
        except OSError:
            time.sleep(0.5)
    body = {"model": "claude-opus-5-5", "max_tokens": 16, "stream": True,
            "messages": [{"role": "user", "content": "x" * 2_000_000}]}
    request = urllib.request.Request(f"http://127.0.0.1:{router_port}/v1/messages", data=json.dumps(body).encode(),
                                     headers={"content-type": "application/json", "anthropic-version": "2023-06-01",
                                              "x-api-key": "local"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            status, reply = response.status, response.read(600)
    except urllib.error.HTTPError as error:
        status, reply = error.code, error.read(600)
    time.sleep(1)
    logs = [path for path in config.rglob("router.log")]
    lines = []
    for path in logs:
        lines += [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
                  if any(word in line for word in ("upstream_direct", "anthropic_route_error", "access \"POST /v1/messages"))]
    result = {"repo": str(repo), "upstream_saw": seen, "client_status": status, "client_body": reply.decode("utf-8", "replace"),
              "router_log_lines": [line[:400] for line in lines]}
    (out / f"result-{repo.name}.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=1, ensure_ascii=False))
finally:
    router.kill()
    router.wait()
    upstream.close()
    shutil.rmtree(config, ignore_errors=True)
