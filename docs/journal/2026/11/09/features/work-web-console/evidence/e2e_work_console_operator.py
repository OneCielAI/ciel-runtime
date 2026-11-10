"""Owned browser proof for the console's operator controls (side, parallel, sibling, plan).

A real WorkHttpController with file-backed board, side inbox and parallel ledger, the
existing web login, and Playwright Chromium. Every action goes through REST v1 from
the browser; results are read back from the stores, not from the page.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[8]
RUNTIME = Path(os.environ.get("E2E_RUNTIME_ROOT") or ROOT)
sys.path.insert(0, str(RUNTIME))
LABEL = os.environ.get("E2E_LABEL", "")

from ciel_runtime_support import work_plan_tree as tree  # noqa: E402
from ciel_runtime_support.http_response import HttpResponseAdapter  # noqa: E402
from ciel_runtime_support.router_access import (  # noqa: E402
    RouterAccessHttpController, RouterAccessPolicy, RouterExternalTokenRepository,
)
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox  # noqa: E402
from ciel_runtime_support.side_intake_processing import claim, finish  # noqa: E402
from ciel_runtime_support.side_reply_outbox import prepare, record  # noqa: E402
from ciel_runtime_support.side_work_decision import apply  # noqa: E402
from ciel_runtime_support.web_access_http import PUBLIC_POST_PATHS, WebAccessHttpController, WebAccessPorts  # noqa: E402
from ciel_runtime_support.web_accounts import WebAccountStore  # noqa: E402
from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices  # noqa: E402
from ciel_runtime_support.work_parallel import WorkParallelStore  # noqa: E402
from ciel_runtime_support.work_parallel_mcp import project as project_parallel  # noqa: E402
from ciel_runtime_support.work_plan_store import session_work_plan_store  # noqa: E402
from ciel_runtime_support.work_web_controller import WorkWebController  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

OUT = Path(__file__).resolve().parent
CHROME = Path.home() / "AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe"
SESSION = "claude:owned-work-operator"
WALKIE_EVENT = json.dumps({"type": "com.example.mail", "data": {"text": "ticket"}})


def write_text(handler, text, status=200, content_type="text/plain; charset=utf-8"):
    body = text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def dead_pid() -> int:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


result: dict = {"runtime_root": str(RUNTIME)}
with tempfile.TemporaryDirectory(prefix="ciel-work-operator-owned-") as temporary:
    root = Path(temporary)
    state = root / "state"
    config = {"work_mode": True, "work_auto_continue": False, "router_debug_external_access": True,
              "router_debug_external_access_confirmed": True, "work_side_intake": {"enabled": True}}
    store = session_work_plan_store(state, SESSION)

    def seed(doc):
        goal = tree.add_subtree(doc, None, {"title": "Release checklist", "children": [
            {"title": "Write notes"}, {"title": "Wait for approval", "waiting_on": "Owner approval 501"}]})
        return goal
    doc, goal = store.transaction(seed)
    by_title = {node["title"]: node["id"] for node in store.read()["nodes"]}
    notes_id, approval_id = by_title["Write notes"], by_title["Wait for approval"]

    inbox = SideIntakeInbox(state, SESSION)
    route = {"source_kind": "external_event", "channel": "external:default", "sender_id": "urn:mail"}
    keys = {}
    for label, decision in (
        ("unsent", {"action": "answer", "kind": "info", "summary": "Which repo serves the app?", "reply": "ciel-qb-web"}),
        ("sent", {"action": "answer", "kind": "status", "summary": "What is running now?", "reply": "Release checklist"}),
        ("handoff", {"action": "update", "kind": "request", "item_id": approval_id, "summary": "Owner approved 501",
                     "note": "approved in 501", "reply": "Noted"}),
    ):
        key = inbox.receive(WALKIE_EVENT + label, label, route=route)["receipt_id"]
        leased = claim(state, SESSION, "side-1")
        applied = apply(store, key, decision, store.read()["version"])
        finish(state, SESSION, key, leased["generation"], applied)
        keys[label] = key
    assert prepare(state, SESSION, keys["unsent"], config=config)["state"] == "route_unavailable"
    bound = {"work_side_intake": {"reply_connectors": [{"source": "urn:mail", "receiver_id": "default",
                                                       "server": "mail", "tool": "reply", "arguments": {"message": "$reply"}}]}}
    sent = prepare(state, SESSION, keys["sent"], config=bound)
    record(state, SESSION, keys["sent"], "submitted", attempt_id=sent["attempt_id"])

    ledger = WorkParallelStore(root / "work-parallel.json")
    candidate = dict(item_id=notes_id, priority="P2", created_at=1, approved=True, focused=False,
                     dependencies_done=True, plan_version=1, usage=.05, role="worker", mode="fork")
    ledger_doc, lease_id = ledger.reserve([candidate], scope=SESSION, expected_version=0,
                                          owner={"session_pid": dead_pid(), "identity": "gone"})
    ledger.bind(lease_id, "agent-gone", expected_version=ledger_doc["version"])

    work = WorkHttpController(WorkHttpServices(
        state, lambda: config, lambda: SESSION,
        lambda identity: {"session_id": identity, "configured": {"provider": "owned", "model": "owned", "alias": "owned"},
                          "ciel_runtime": {"version": "owned", "source_fingerprint": "e2e", "instance_id": "owned"}},
        HttpResponseAdapter.write_json, "owned-instance",
        parallel_status=lambda identity, board: project_parallel(ledger, identity, board),
        save_config=lambda value: config.update(value), parallel_store=lambda: ledger,
    ))
    web = WorkWebController(write_text)
    accounts = WebAccountStore(root / "web-access")
    accounts.add("ops@example.test", "correct horse battery")
    token_repo = RouterExternalTokenRepository(root / "admin.token", root, {})
    token_repo.path.write_text("owned-operator-admin-token\n", encoding="utf-8")
    access = WebAccessHttpController(WebAccessPorts(
        write_json=HttpResponseAdapter.write_json,
        write_html=lambda handler, page: write_text(handler, page, content_type="text/html; charset=utf-8"),
        accounts=lambda: accounts, workspace_state_dir=lambda: state, admin_token=token_repo,
        external_access_enabled=lambda: True, sleep=lambda _seconds: None))
    policy = RouterAccessPolicy({}, lambda value, default=False: str(value).lower() in ("1", "true", "yes", "on"),
                                lambda value, default: default, lambda: config)
    guard = RouterAccessHttpController(
        lambda handler, cfg: policy.request_allowed(handler, cfg, token_repo.get, lambda: "bridge-only", access.session_allowed),
        policy.external_access_enabled)

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.client_address = ("192.0.2.45", self.client_address[1])

        def log_message(self, *_args):
            return None

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
            body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
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
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=str(CHROME))
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base + "/ca/work/ui", wait_until="domcontentloaded")
            page.wait_for_url("**/ca/login?next=**")
            page.locator("#email").fill("ops@example.test")
            page.locator("#password").fill("correct horse battery")
            page.get_by_role("button", name="Sign in").click()
            page.wait_for_url("**/ca/work/ui")
            page.locator(".tree-row").first.wait_for()

            # Side tab: real delivery states, retry, review and handoff.
            page.get_by_role("tab", name="Side").click()
            unsent = page.locator(f".side-review[data-receipt='{keys['unsent']}']")
            unsent.wait_for()
            result["unsent_label"] = unsent.locator(".item-head span").inner_text()
            result["sent_label"] = page.locator(f".side-review[data-receipt='{keys['sent']}'] .item-head span").inner_text()
            page.screenshot(path=str(OUT / f"04-side-before{LABEL}.png"), full_page=True)
            unsent.get_by_role("button", name="Retry send").click()
            page.locator(".toast", has_text="side-reply-retry applied").last.wait_for()
            result["retry_outbox"] = inbox._load(keys["unsent"]).get("outbox")
            page.locator(f".side-review[data-receipt='{keys['sent']}']").get_by_role("button", name="Mark OK").click()
            page.locator(".toast", has_text="side-review applied").last.wait_for()
            page.locator(f".side-review[data-receipt='{keys['unsent']}']").get_by_role("button", name="Corrected…").click()
            page.locator(f".side-review[data-receipt='{keys['unsent']}'] .inline-form textarea").fill("Repo named was right; added deploy path")
            page.locator(f".side-review[data-receipt='{keys['unsent']}'] .inline-form").get_by_role("button", name="Save").click()
            # Each side action re-renders the tab from REST; act on the refreshed view.
            page.locator(".toast", has_text="side-review applied").last.wait_for()
            page.locator(f".side-review[data-receipt='{keys['unsent']}']").wait_for(state="detached")
            handoff = page.locator(f".side-handoff[data-event='{keys['handoff']}']")
            handoff.wait_for()
            handoff.locator("textarea").fill("Approval recorded; continuing")
            handoff.locator("input[type=checkbox][value='" + approval_id + "']").check()
            page.screenshot(path=str(OUT / f"05-side-actions{LABEL}.png"), full_page=True)
            handoff.get_by_role("button", name="Acknowledge").click()
            page.locator(".side-handoff").first.wait_for(state="detached")
            board = store.read()
            result["reviews"] = {label: board["side_reviews"][keys[label]]["state"] for label in ("unsent", "sent")}
            result["handoff"] = {k: board["side_handoffs"][keys["handoff"]].get(k) for k in ("state", "answer", "clear_wait")}
            result["approval_wait_after"] = next(n for n in board["nodes"] if n["id"] == approval_id)["waiting_on"]

            # Node: sibling order and plan replacement.
            page.get_by_role("tab", name="Node").click()
            page.locator(f".tree-row[data-id='{approval_id}']").click()
            page.get_by_role("button", name="Move up").click()
            page.locator(".toast", has_text="sibling-reorder applied").last.wait_for()
            result["children_after_move"] = [n["title"] for n in store.read()["nodes"] if n["parent_id"] == goal]
            page.locator(f".tree-row[data-id='{goal}']").click()
            page.get_by_role("button", name="Replace plan…").click()
            page.locator("#plan-steps").fill("Draft notes\nPublish beta\nVerify install")
            page.get_by_role("button", name="Replace plan", exact=True).click()
            page.locator("#confirm-accept").click()
            page.locator(".toast", has_text="plan-set applied").last.wait_for()
            result["children_after_replan"] = [n["title"] for n in store.read()["nodes"] if n["parent_id"] == goal]
            page.screenshot(path=str(OUT / f"06-node-replanned{LABEL}.png"), full_page=True)

            # Parallel: orphan review and release of a lease whose owner process is gone.
            page.get_by_role("tab", name="Parallel").click()
            page.get_by_role("button", name="Inspect orphan leases").click()
            page.locator("#orphan-review").get_by_role("button", name="Release lease").wait_for()
            page.screenshot(path=str(OUT / f"07-parallel-orphan{LABEL}.png"), full_page=True)
            page.locator("#orphan-review").get_by_role("button", name="Release lease").click()
            page.locator("#confirm-accept").click()
            page.locator(".toast", has_text="parallel-orphans-apply applied").last.wait_for()
            lease = ledger.read()["leases"][lease_id]
            result["lease_after_release"] = {"state": lease["state"], "slot": lease["slot"]}
            page.set_viewport_size({"width": 390, "height": 844})
            page.get_by_role("tab", name="Side").click()
            page.locator("#panel-side h2").wait_for()
            result["toasts_kept"] = page.locator(".toast").count()
            page.locator(".toast").first.wait_for(state="detached", timeout=8000)
            result["mobile_focus"] = page.evaluate("(document.activeElement && (document.activeElement.className || document.activeElement.tagName)) + ':' + (document.activeElement && document.activeElement.textContent || '').slice(0, 30)")
            page.screenshot(path=str(OUT / f"08-side-mobile{LABEL}.png"), full_page=True)
            result["mobile_scroll_width"] = page.evaluate("document.documentElement.scrollWidth")
            result["page_errors"] = errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)

(OUT / f"e2e-operator-result{LABEL}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps(result, indent=2, ensure_ascii=False))
