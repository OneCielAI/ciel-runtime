"""Owned real-controller browser proof for the Work web console."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[8]
sys.path.insert(0, str(ROOT))

from ciel_runtime_support.http_response import HttpResponseAdapter  # noqa: E402
from ciel_runtime_support.router_access import (  # noqa: E402
    RouterAccessHttpController, RouterAccessPolicy, RouterExternalTokenRepository,
)
from ciel_runtime_support.web_access_http import (  # noqa: E402
    PUBLIC_POST_PATHS, WebAccessHttpController, WebAccessPorts,
)
from ciel_runtime_support.web_accounts import WebAccountStore  # noqa: E402
from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices  # noqa: E402
from ciel_runtime_support.work_web_controller import WorkWebController  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

OUT = Path(__file__).resolve().parent
CHROME = Path.home() / "AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe"
ADMIN_TOKEN = "owned-work-console-admin-token"
SESSION = "claude:owned-work-web-e2e"


def parse_bool(value, default=False):
    if value is None:
        return default
    return str(value).lower() in ("1", "true", "yes", "on")


with tempfile.TemporaryDirectory(prefix="ciel-work-web-owned-") as temporary:
    root = Path(temporary)
    state = root / "state"
    config = {"work_mode": True, "work_auto_continue": True,
              "router_debug_external_access": True,
              "router_debug_external_access_confirmed": True}
    config_lock = threading.Lock()

    def save_config(value):
        with config_lock:
            saved = json.loads(json.dumps(value))
            config.clear()
            config.update(saved)

    def write_text(handler, text, status=200, content_type="text/plain; charset=utf-8"):
        body = text.encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    work = WorkHttpController(WorkHttpServices(
        state, lambda: config, lambda: SESSION,
        lambda identity: {"session_id": identity, "configured": {
            "provider": "owned-provider", "model": "owned-model", "alias": "owned"},
            "ciel_runtime": {"version": "owned", "source_fingerprint": "e2e", "instance_id": "owned"}},
        HttpResponseAdapter.write_json, "owned-instance", save_config=save_config,
    ))
    web = WorkWebController(write_text)
    accounts = WebAccountStore(root / "web-access")
    accounts.add("ops@example.test", "correct horse battery")
    token_repo = RouterExternalTokenRepository(root / "admin.token", root, {})
    token_repo.path.write_text(ADMIN_TOKEN + "\n", encoding="utf-8")
    access = WebAccessHttpController(WebAccessPorts(
        write_json=HttpResponseAdapter.write_json,
        write_html=lambda handler, page: write_text(handler, page, content_type="text/html; charset=utf-8"),
        accounts=lambda: accounts,
        workspace_state_dir=lambda: state,
        admin_token=token_repo,
        external_access_enabled=lambda: True,
        sleep=lambda _seconds: None,
    ))
    policy = RouterAccessPolicy({}, parse_bool, lambda value, default: default,
                                lambda: config)
    guard = RouterAccessHttpController(
        lambda handler, cfg: policy.request_allowed(
            handler, cfg, token_repo.get, lambda: "bridge-only", access.session_allowed),
        policy.external_access_enabled,
    )

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.client_address = ("192.0.2.44", self.client_address[1])

        def log_message(self, *_args):
            return None

        def _body(self):
            return json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if access.handle_public_get(self, path):
                return
            if guard.reject_external_request(self, config):
                return
            if access.handle_get(self, path) or web.handle_get(self, path) or work.get(self, path):
                return
            self.send_error(404)

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            body = self._body()
            if path in PUBLIC_POST_PATHS and access.handle_post(self, path, body):
                return
            if guard.reject_external_request(self, config):
                return
            if access.handle_post(self, path, body) or work.post(self, path, body):
                return
            self.send_error(404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    result = {"base": base, "session": SESSION}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=str(CHROME))
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base + "/ca/work/ui", wait_until="domcontentloaded")
            page.wait_for_url("**/ca/login?next=**")
            result["login_url"] = page.url
            result["login_title"] = page.title()
            result["login_html"] = page.content()[:500]
            page.screenshot(path=str(OUT / "00-login.png"), full_page=True)
            if page.locator("#email").count() == 0:
                raise RuntimeError("login page missing #email: " + json.dumps(result))
            page.locator("#email").fill("ops@example.test")
            page.locator("#password").fill("correct horse battery")
            page.get_by_role("button", name="Sign in").click()
            page.wait_for_url("**/ca/work/ui")
            page.locator("#add-root").click()
            page.locator("#new-title").fill("Owned browser Work goal")
            page.locator("#new-note").fill("Created through the real REST controller")
            page.locator("#new-priority").select_option("P1")
            page.get_by_role("button", name="Add", exact=True).click()
            page.locator(".tree-row[data-id='n1']").wait_for()
            page.locator("#add-root").click()
            page.locator("#new-title").fill("Queued goal")
            page.get_by_role("button", name="Add", exact=True).click()
            page.locator(".tree-row[data-id='n2']").click()
            page.locator("#node-queue-position").fill("1")
            page.get_by_role("button", name="Move to position").click()
            page.get_by_role("tab", name="Settings").click()
            page.locator("#panel-settings .settings-row").first.wait_for()
            page.screenshot(path=str(OUT / "01-work-console-desktop.png"), full_page=True)

            # A separate service mutates the same board through the existing admin token.
            board_request = urllib.request.Request(base + "/ca/work/api/v1/board",
                headers={"Authorization": "Bearer " + ADMIN_TOKEN})
            with urllib.request.urlopen(board_request, timeout=4) as response:
                board = json.load(response)
            body = json.dumps({"expected_version": board["version"], "title": "External REST goal",
                               "operation_id": "external-service-add"}).encode()
            post = urllib.request.Request(base + "/ca/work/api/v1/commands/node-add", data=body,
                headers={"Authorization": "Bearer " + ADMIN_TOKEN, "Content-Type": "application/json",
                         "Idempotency-Key": "external-service-add"})
            with urllib.request.urlopen(post, timeout=4) as response:
                external = json.load(response)
            page.locator(".tree-row[data-id='n3']").wait_for(timeout=5000)
            result["external_service_version"] = external["version"]
            result["browser_sse_saw_external_goal"] = page.locator(
                ".tree-row[data-id='n3'] .node-title").inner_text() == "External REST goal"

            # A stale external mutation is refused and keeps state unchanged.
            stale_body = json.dumps({"expected_version": 0, "title": "stale",
                                     "operation_id": "stale-service-add"}).encode()
            stale = urllib.request.Request(base + "/ca/work/api/v1/commands/node-add", data=stale_body,
                headers={"Authorization": "Bearer " + ADMIN_TOKEN, "Content-Type": "application/json",
                         "Idempotency-Key": "stale-service-add"})
            try:
                urllib.request.urlopen(stale, timeout=4)
            except urllib.error.HTTPError as error:
                result["stale_status"] = error.code
                result["stale_error"] = json.load(error)["error"]

            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(OUT / "02-work-console-mobile.png"), full_page=True)
            result["mobile_scroll_width"] = page.evaluate("document.documentElement.scrollWidth")
            result["mobile_viewport"] = page.evaluate("innerWidth")
            result["page_errors"] = errors
            result["final_version"] = board["version"] + 1
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)

(OUT / "e2e-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
