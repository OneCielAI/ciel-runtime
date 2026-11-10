"""Owned real-router proof for direct-certificate TLS on the router port.

Starts `ciel_runtime.py serve` from this checkout with an isolated config
directory, a free port and `web_backend.tls_mode=certificate`, then checks:
- plain HTTP from this host still works (CLI clients keep http://127.0.0.1);
- HTTPS on the same port verifies against the configured certificate;
- the Work console shell is served over that HTTPS connection.
Only the router started here is stopped. Live routers are never touched.
"""
from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[8]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tests"))
from tls_test_certificates import generate_rsa_key, make_certificate  # noqa: E402


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def get(connection: http.client.HTTPConnection, path: str) -> tuple[int, bytes]:
    connection.request("GET", path)
    response = connection.getresponse()
    return response.status, response.read()


result: dict = {}
with tempfile.TemporaryDirectory(prefix="ciel-owned-tls-router-") as temporary:
    base = Path(temporary)
    config_dir, workspace = base / "cfg", base / "ws"
    config_dir.mkdir()
    workspace.mkdir()
    key = generate_rsa_key()
    cert_file, key_file = base / "router-cert.pem", base / "router-key.pem"
    cert_file.write_bytes(make_certificate(key, ("router.example.test", "127.0.0.1")))
    key_file.write_bytes(key.private_pem())
    port = free_port()
    (config_dir / "config.json").write_text(json.dumps({"web_backend": {
        "enabled": True, "host": "127.0.0.1", "port": port, "workspace": str(workspace),
        "tls_mode": "certificate",
        "tls": {"cert_file": str(cert_file), "key_file": str(key_file),
                "server_names": ["router.example.test", "127.0.0.1"]},
    }}), encoding="utf-8")
    env = {name: value for name, value in os.environ.items() if not name.startswith("CIEL_RUNTIME_")}
    env.update(CIEL_RUNTIME_CONFIG_DIR=str(config_dir), CIEL_RUNTIME_ROUTER_PORT=str(port),
               CIEL_RUNTIME_LAUNCH_CWD=str(workspace), PYTHONIOENCODING="utf-8")
    log_path = OUT / "tls-router-serve.log"
    with log_path.open("w", encoding="utf-8") as log:
        router = subprocess.Popen([sys.executable, str(ROOT / "ciel_runtime.py"), "serve"], cwd=workspace,
                                  env=env, stdout=log, stderr=subprocess.STDOUT)
    result.update(port=port, router_pid=router.pid)
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                status, _ = get(http.client.HTTPConnection("127.0.0.1", port, timeout=2), "/health")
                if status == 200:
                    break
            except OSError:
                pass
            if router.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("owned router did not become healthy; see tls-router-serve.log")
            time.sleep(0.5)
        status, body = get(http.client.HTTPConnection("127.0.0.1", port, timeout=5), "/health")
        result["plain_local_health"] = {"status": status, "ok": json.loads(body).get("ok")}

        context = ssl.create_default_context(cafile=str(cert_file))
        secure = http.client.HTTPSConnection("127.0.0.1", port, timeout=5, context=context)
        secure.connect()
        result["https_peer_san"] = [value for _kind, value in secure.sock.getpeercert()["subjectAltName"]]
        result["https_protocol"] = secure.sock.version()
        status, body = get(secure, "/health")
        result["https_health"] = {"status": status, "ok": json.loads(body).get("ok")}
        secure = http.client.HTTPSConnection("127.0.0.1", port, timeout=5, context=context)
        status, body = get(secure, "/ca/work/ui")
        result["https_work_console"] = {"status": status, "has_title": b"Work Console" in body}

        # Bind an owned current session, as a launched CLI would, then create
        # one goal through REST v1 over the verified HTTPS connection.
        instance = next((config_dir / "router-instances").glob(f"{port}-*"))
        (instance / "compaction-scope.json").write_text(json.dumps(
            {"runtime": "claude", "session_id": "owned-tls-e2e"}), encoding="utf-8")
        secure = http.client.HTTPSConnection("127.0.0.1", port, timeout=5, context=context)
        status, body = get(secure, "/ca/work/api/v1/board")
        version = json.loads(body)["version"]
        secure = http.client.HTTPSConnection("127.0.0.1", port, timeout=5, context=context)
        secure.request("POST", "/ca/work/api/v1/commands/node-add",
                       body=json.dumps({"expected_version": version, "title": "Goal created over direct TLS",
                                        "priority": "P2", "operation_id": "owned-tls-add"}),
                       headers={"Content-Type": "application/json", "Idempotency-Key": "owned-tls-add"})
        response = secure.getresponse()
        created = json.loads(response.read())
        result["https_rest_node_add"] = {"status": response.status, "version": created.get("version")}

        from playwright.sync_api import sync_playwright
        chrome = Path.home() / "AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=str(chrome))
            # Trust was proven above with the configured certificate; the
            # browser run only shows the console served over that TLS port.
            page = browser.new_page(ignore_https_errors=True, viewport={"width": 1280, "height": 800})
            page.goto(f"https://127.0.0.1:{port}/ca/work/ui", wait_until="domcontentloaded")
            page.locator(".tree-row .node-title").first.wait_for(timeout=10000)
            result["browser_goal_title"] = page.locator(".tree-row .node-title").first.inner_text()
            result["browser_https_url"] = page.url
            result["browser_https_protocol"] = page.evaluate("location.protocol")
            page.screenshot(path=str(OUT / "03-work-console-direct-tls.png"), full_page=True)
            browser.close()

        untrusted = http.client.HTTPSConnection("127.0.0.1", port, timeout=5,
                                                context=ssl.create_default_context())
        try:
            untrusted.connect()
            result["untrusted_client"] = "connected"
        except ssl.SSLCertVerificationError as error:
            result["untrusted_client"] = "rejected: " + error.verify_message
    finally:
        router.terminate()
        try:
            router.wait(15)
        except subprocess.TimeoutExpired:
            router.kill()
            router.wait(15)
        result["router_exit"] = router.returncode

(OUT / "tls-router-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
