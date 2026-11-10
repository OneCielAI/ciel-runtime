"""Owned browser proof for the Work web console Side receipt stream.

A real WorkHttpController, the existing web login and a file-backed
SideIntakeInbox, driven by Playwright Chromium. Every receipt state is produced
by the production inbox functions (receive, claim, finish, fail, prepare,
record); nothing in the product is patched or mocked. Live changes are made
from Python while the page is open and must reach the page through real
``side.changed`` SSE frames with no click. Results are read back from the
inbox files, not only from the page.

Contract targeted (from the worktree source on 2026-11-10; adjust here if it moves):
  GET  /ca/work/api/v1/side?limit=25[&cursor=&stage=&priority=&q=]
  GET  /ca/work/api/v1/side/receipt?receipt_id=<64 hex>
  SSE  /ca/work/api/v1/events  event: side.changed
  DOM  #side-receipts .receipt-row[data-receipt] .receipt-meta, #side-stage,
       #side-search + "Search", .side-scope "P1"/"All", #side-load-older,
       "Show redacted detail for ..." -> .receipt-detail, #side-refresh-note
       + "Refresh now", .side-handoff textarea (draft).

Run only after the receipt API and UI have landed:
  python docs/journal/2026/11/10/features/work-web-side-receipts/evidence/e2e_work_side_receipts.py
Optional: E2E_RUNTIME_ROOT=<installed snapshot> and E2E_LABEL=-installed.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[8]
RUNTIME = Path(os.environ.get("E2E_RUNTIME_ROOT") or ROOT)
sys.path.insert(0, str(RUNTIME))
LABEL = os.environ.get("E2E_LABEL", "")

from ciel_runtime_support import work_plan_tree as tree  # noqa: E402
from ciel_runtime_support.http_response import HttpResponseAdapter  # noqa: E402
from ciel_runtime_support.router_access import (  # noqa: E402
    RouterAccessHttpController,
    RouterAccessPolicy,
    RouterExternalTokenRepository,
)
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox, source_priority  # noqa: E402
from ciel_runtime_support.side_intake_processing import claim, fail, finish  # noqa: E402
from ciel_runtime_support.side_reply_outbox import prepare, record  # noqa: E402
from ciel_runtime_support.side_work_decision import apply  # noqa: E402
from ciel_runtime_support.web_access_http import (  # noqa: E402
    PUBLIC_POST_PATHS,
    WebAccessHttpController,
    WebAccessPorts,
)
from ciel_runtime_support.web_accounts import WebAccountStore  # noqa: E402
from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices  # noqa: E402
from ciel_runtime_support.work_plan_store import session_work_plan_store  # noqa: E402
from ciel_runtime_support.work_web_controller import WorkWebController  # noqa: E402
from playwright.sync_api import Page, sync_playwright  # noqa: E402

OUT = Path(__file__).resolve().parent
CHROME = Path.home() / "AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe"
SESSION = "claude:owned-side-receipts"
API = "/ca/work/api/v1"
PAGE_SIZE = 25  # The console requests limit=25.
FILLERS = PAGE_SIZE * 2 + 7  # Oldest rows: forces at least three pages.
LIVE_TIMEOUT_MS = 15000

# Secrets live on disk in the route and inside the original event text. None
# may reach list JSON, detail JSON or the DOM.
ROUTE_SECRETS = ("owned-route-reply-token-7d1", "Bearer owned-route-auth-2c9", "owned-route-token-5e4")
TEXT_SECRETS = ("owned-text-auth-91b", "owned-text-reply-token-3fa", "owned-text-password-c07")
SECRETS = ROUTE_SECRETS + TEXT_SECRETS
SEARCH_TOKEN = "SEARCH-NONCE-4417"
OLDEST_TEXT = "older filler 00"

ROUTE = {"source_kind": "external_event", "channel": "external:default", "sender_id": "urn:owned-mail"}
CONFIG = {
    "work_mode": True,
    "work_auto_continue": False,
    "router_debug_external_access": True,
    "router_debug_external_access_confirmed": True,
    "work_side_intake": {"enabled": True},
}
BOUND = {"work_side_intake": {"enabled": True, "reply_connectors": [{
    "source": "urn:owned-mail", "receiver_id": "default", "server": "owned-mail",
    "tool": "reply", "arguments": {"message": "$reply"}}]}}

SSE_HOOK = """() => {
    window.__ownedSideEvents = [];
    window.__ownedSideSource = new EventSource('/ca/work/api/v1/events');
    window.__ownedSideSource.addEventListener('side.changed', event => {
        let data = null; try { data = JSON.parse(event.data); } catch (_) { data = String(event.data); }
        window.__ownedSideEvents.push({at: Date.now(), id: event.lastEventId, data});
    });
}"""
ROW_IDS = "() => [...document.querySelectorAll('#side-receipts .receipt-row')].map(row => row.dataset.receipt)"
ROW_META = ("id => { const row = document.querySelector(`#side-receipts .receipt-row[data-receipt='${id}']`);"
            " const meta = row && row.querySelector('.receipt-meta');"
            " return meta ? meta.innerText.split('·').map(part => part.trim()) : null; }")


def write_text(handler: BaseHTTPRequestHandler, text: str, status: int = 200,
               content_type: str = "text/plain; charset=utf-8") -> None:
    body = text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def leaked(value: Any) -> list[str]:
    rendered = value if isinstance(value, str) else dump(value)
    return [secret for secret in SECRETS if secret in rendered]


def answer(label: str) -> dict[str, Any]:
    return {"action": "answer", "kind": "info", "summary": label, "reply": "Owned answer: " + label}


result: dict[str, Any] = {"runtime_root": str(RUNTIME), "page_size": PAGE_SIZE, "fillers": FILLERS,
                          "checks": {}}
checks: dict[str, Any] = result["checks"]


def check(name: str, condition: bool, detail: Any = None) -> None:
    checks[name] = {"ok": bool(condition), **({"detail": detail} if detail is not None else {})}
    assert condition, (name, detail)


with tempfile.TemporaryDirectory(prefix="ciel-side-receipts-owned-") as temporary:
    root = Path(temporary)
    state = root / "state"
    config = json.loads(json.dumps(CONFIG))
    store = session_work_plan_store(state, SESSION)
    store.transaction(lambda doc: tree.add_subtree(doc, None, {
        "title": "Receipt stream owned proof", "children": [{"title": "Write release notes"}]}))
    inbox = SideIntakeInbox(state, SESSION)

    def receive(event_key: str, label: str, *, route: dict[str, Any] | None = None,
                extra: dict[str, Any] | None = None) -> str:
        data = {"text": label, **(extra or {})}
        receipt = inbox.receive(json.dumps({"type": "owned.side", "data": data}), event_key,
                                route=ROUTE if route is None else route)
        assert receipt["duplicate"] is False, event_key
        return receipt["receipt_id"]

    def claim_exact(receipt_id: str, lease_seconds: int = 1800) -> str:
        lease = claim(state, SESSION, "owned-receipt-probe", lease_seconds=lease_seconds)
        assert lease is not None and lease["receipt_id"] == receipt_id, ("seed claim order", receipt_id, lease)
        return lease["generation"]

    def processed(event_key: str, label: str, decision: dict[str, Any] | None = None, **kwargs: Any) -> str:
        key = receive(event_key, label, **kwargs)
        generation = claim_exact(key)
        applied = apply(store, key, decision or answer(label), store.read()["version"])
        finish(state, SESSION, key, generation, applied)
        return key

    seed_started = time.monotonic()
    # 1. Oldest: processed fillers through the full production lifecycle. Their
    #    side reviews are closed through the real review path so the Side tab
    #    is not flooded with review cards.
    fillers = [processed(f"owned-filler-{index:02d}", f"older filler {index:02d}") for index in range(FILLERS)]
    from ciel_runtime_support.side_review import review  # noqa: E402
    for start in range(0, len(fillers), 50):
        review(store, fillers[start:start + 50], verdict="ok", note="", expected_version=store.read()["version"])

    # 2. P1 request: creates a P1 goal and a real main handoff (its textarea is the draft).
    p1_processed = processed("owned-p1-processed", "P1 deployment outage", {
        "action": "create", "kind": "request", "summary": "P1 deployment outage needs investigation",
        "reply": "Queued the urgent investigation.", "title": "Investigate P1 deployment outage",
        "priority": "P1"}, extra={"priority": "P1"})

    # 3. Delivery states from the real outbox.
    sent = processed("owned-sent", "processed and sent receipt")
    attempt = prepare(state, SESSION, sent, config=BOUND)
    record(state, SESSION, sent, "submitted", attempt_id=attempt["attempt_id"])
    route_unavailable = processed("owned-no-route", "route unavailable receipt")
    check("seed_route_unavailable", prepare(state, SESSION, route_unavailable, config=config)["state"] == "route_unavailable")
    delivery_failed = processed("owned-delivery-failed", "delivery failed receipt")
    attempt = prepare(state, SESSION, delivery_failed, config=BOUND)
    record(state, SESSION, delivery_failed, "failed", attempt_id=attempt["attempt_id"])

    # 4. Same source message, second transport event: settled as duplicate_source.
    source_first = processed("owned-source-first", "source message created",
                             extra={"conversation": "conv-owned", "payload": {"message_id": 701}})
    duplicate_source = receive("owned-source-second", "source message auto-assigned",
                               extra={"conversation": "conv-owned", "payload": {"message_id": 701}})
    check("seed_duplicate_source_settled", claim(state, SESSION, "owned-receipt-probe") is None
          and (inbox._load(duplicate_source) or {}).get("result", {}).get("delivery") == "duplicate_source")

    # 5. Secrets on disk (route + original text), searchable by a nonce.
    secret = processed("owned-secret", SEARCH_TOKEN + " redaction target", route={
        **ROUTE, "reply_token": ROUTE_SECRETS[0], "authorization": ROUTE_SECRETS[1], "token": ROUTE_SECRETS[2]},
        extra={"authorization": TEXT_SECRETS[0], "reply_token": TEXT_SECRETS[1],
               "credentials": {"password": TEXT_SECRETS[2]}})
    check("seed_secrets_on_disk", not set(SECRETS) - set(leaked(inbox._load(secret))))

    # 6. Permanent quarantine (completed turn with an invalid decision).
    quarantined = receive("owned-quarantined", "invalid answer quarantined receipt")
    fail(state, SESSION, quarantined, claim_exact(quarantined), {
        "category": "invalid_answer", "reason": "owned invalid decision", "model": "owned",
        "model_fingerprint": "owned-e2e"})
    check("seed_quarantined", (inbox._load(quarantined) or {}).get("side_failure", {}).get("quarantined") is True)

    # 7. A held lease (processing) finished live later, and a retryable failure.
    processing = receive("owned-processing", "processing receipt finishes live")
    processing_generation = claim_exact(processing, lease_seconds=3600)
    retryable = receive("owned-retryable", "retryable model failure receipt")
    fail(state, SESSION, retryable, claim_exact(retryable), {
        "category": "model_error", "reason": "owned transient 503", "model": "owned",
        "model_fingerprint": "owned-e2e", "outcome": {"status": 503, "code": "overloaded"}})

    # 8. Newest seeded: received rows (no more claims after this point).
    received = receive("owned-received", "ordinary received receipt")
    p1_received = receive("owned-p1-received", "P1 received urgent request", extra={"priority": "P1"})
    again = inbox.receive(json.dumps({"type": "owned.side", "data": {
        "text": "P1 received urgent request", "priority": "P1"}}), "owned-p1-received", route=ROUTE)
    check("seed_exact_duplicate_same_receipt", again["duplicate"] is True and again["receipt_id"] == p1_received)
    check("seed_p1_priority", source_priority((inbox._load(p1_received) or {}).get("text")) == "P1")
    result["seed_seconds"] = round(time.monotonic() - seed_started, 2)

    ids = {"oldest": fillers[0], "p1_processed": p1_processed, "sent": sent,
           "route_unavailable": route_unavailable, "delivery_failed": delivery_failed,
           "source_first": source_first, "duplicate_source": duplicate_source, "secret": secret,
           "quarantined": quarantined, "processing": processing, "retryable": retryable,
           "received": received, "p1_received": p1_received}
    result["seeded"] = ids

    work = WorkHttpController(WorkHttpServices(
        state, lambda: config, lambda: SESSION,
        lambda identity: {"session_id": identity, "configured": {"provider": "owned", "model": "owned", "alias": "owned"},
                          "ciel_runtime": {"version": "owned", "source_fingerprint": "e2e", "instance_id": "owned"}},
        HttpResponseAdapter.write_json, "owned-receipt-instance"))
    web = WorkWebController(write_text)
    accounts = WebAccountStore(root / "web-access")
    accounts.add("ops@example.test", "correct horse battery")
    token_repo = RouterExternalTokenRepository(root / "admin.token", root, {})
    token_repo.path.write_text("owned-receipt-admin-token\n", encoding="utf-8")
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
        def setup(self) -> None:
            super().setup()
            self.client_address = ("192.0.2.45", self.client_address[1])

        def log_message(self, *_args: Any) -> None:
            return None

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if access.handle_public_get(self, path):
                return
            if guard.reject_external_request(self, config):
                return
            if access.handle_get(self, path) or web.handle_get(self, path) or work.get(self, path):
                return
            self.send_error(404)

        def do_POST(self) -> None:
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
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    page: Page | None = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=str(CHROME))
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors: list[str] = []
            side_requests: list[dict[str, Any]] = []
            clicks = {"count": 0}
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: side_requests.append({"at": time.time(), "url": request.url})
                    if "/ca/work/api/v1/side" in request.url else None)

            def click(locator) -> None:
                clicks["count"] += 1
                locator.click()

            def row(receipt_id: str):
                return page.locator(f"#side-receipts .receipt-row[data-receipt='{receipt_id}']")

            def meta(receipt_id: str) -> list[str] | None:
                return page.evaluate(ROW_META, receipt_id)

            def rows_settled(predicate_js: str, arg: Any = None) -> list[str]:
                page.wait_for_function(predicate_js, arg=arg, timeout=LIVE_TIMEOUT_MS)
                return page.evaluate(ROW_IDS)

            def open_detail(receipt_id: str) -> str:
                target = row(receipt_id)
                click(target.get_by_role("button", name="Show redacted detail", exact=False))
                detail = target.locator(".receipt-detail")
                detail.locator("dl").wait_for(timeout=LIVE_TIMEOUT_MS)
                return detail.inner_text()

            def api(path: str) -> dict[str, Any]:
                return page.evaluate("""async path => {
                    const response = await fetch(path, {cache: 'no-store', credentials: 'same-origin'});
                    return {status: response.status, body: await response.json()};
                }""", path)

            # Login, then the Side tab: newest page only.
            page.goto(base + "/ca/work/ui", wait_until="domcontentloaded")
            page.wait_for_url("**/ca/login?next=**")
            page.locator("#email").fill("ops@example.test")
            page.locator("#password").fill("correct horse battery")
            click(page.get_by_role("button", name="Sign in"))
            page.wait_for_url("**/ca/work/ui")
            page.locator(".tree-row").first.wait_for()
            click(page.get_by_role("tab", name="Side"))
            first_page = rows_settled("n => document.querySelectorAll('#side-receipts .receipt-row').length === n",
                                      PAGE_SIZE)
            check("first_page_is_one_page", len(first_page) == PAGE_SIZE and len(set(first_page)) == PAGE_SIZE,
                  len(first_page))
            check("first_page_excludes_oldest", ids["oldest"] not in first_page)
            check("load_older_offered", page.locator("#side-load-older").count() == 1)
            expected_stage = {"p1_processed": "processed", "sent": "processed", "route_unavailable": "processed",
                              "delivery_failed": "processed", "duplicate_source": "duplicate",
                              "secret": "processed", "quarantined": "quarantined", "processing": "processing",
                              "retryable": "failed", "received": "received", "p1_received": "received"}
            shown = {label: meta(ids[label]) for label in expected_stage}
            result["first_page_meta"] = shown
            check("every_state_rendered", all(shown[label] and stage in shown[label]
                                              for label, stage in expected_stage.items()), shown)
            check("p1_rendered", "P1" in (shown["p1_received"] or []) and "P1" in (shown["p1_processed"] or []))
            check("exact_duplicate_one_row", first_page.count(ids["p1_received"]) == 1)
            page.screenshot(path=str(OUT / f"00-side-receipts-desktop{LABEL}.png"), full_page=True)

            # Server responses are redacted regardless of rendering choices.
            listing = api(API + "/side?limit=25")
            detail_json = api(API + "/side/receipt?receipt_id=" + ids["secret"])
            check("list_json_redacted", listing["status"] == 200 and not leaked(listing["body"]), leaked(listing["body"]))
            check("detail_json_redacted", detail_json["status"] == 200 and not leaked(detail_json["body"]),
                  leaked(detail_json["body"]))
            body = detail_json["body"].get("receipt") or {}
            result["detail_fields"] = sorted(body)
            check("detail_keeps_original_text", SEARCH_TOKEN in str(body.get("original", "")))
            # Opaque keys are omitted rather than replaced by a marker. This is
            # the public projector contract: the operator can see the bounded
            # surrounding event while credential-bearing keys never serialize.
            lowered = dump(body).casefold()
            check("detail_opaque_keys_omitted", all(key not in lowered for key in (
                "activity_context", "reply_token", "authorization", "credentials", "password")),
                "opaque keys absent from detail JSON")

            # Detail panels in the page.
            secret_detail = open_detail(ids["secret"])
            check("detail_dom_redacted", SEARCH_TOKEN in secret_detail and not leaked(secret_detail)
                  and not leaked(page.content()), leaked(page.content()))
            page.screenshot(path=str(OUT / f"01-side-receipt-redacted-detail{LABEL}.png"), full_page=True)
            no_route_detail = open_detail(ids["route_unavailable"])
            check("route_unavailable_detail", "route_unavailable" in no_route_detail)
            quarantine_detail = open_detail(ids["quarantined"])
            check("quarantine_detail", "invalid_answer" in quarantine_detail)

            # Live: inbox writes from Python reach the page with no click.
            page.evaluate(SSE_HOOK)
            page.wait_for_function("() => window.__ownedSideSource && window.__ownedSideSource.readyState === 1",
                                   timeout=LIVE_TIMEOUT_MS)
            clicks_before = clicks["count"]
            events_before = page.evaluate("window.__ownedSideEvents.length")
            started = time.monotonic()
            live = receive("owned-live-arrival", "LIVE-ARRIVAL appeared without click")
            rows_settled("([id, count]) => window.__ownedSideEvents.length > count"
                         " && !!document.querySelector(`#side-receipts .receipt-row[data-receipt='${id}']`)",
                         [live, events_before])
            arrival_seconds = round(time.monotonic() - started, 3)
            check("live_arrival_without_click", clicks["count"] == clicks_before and meta(live) is not None
                  and "received" in meta(live), {"seconds": arrival_seconds, "meta": meta(live)})
            check("live_arrival_on_top", page.evaluate(ROW_IDS)[0] == live)

            events_before = page.evaluate("window.__ownedSideEvents.length")
            started = time.monotonic()
            applied = apply(store, processing, answer("processing receipt finished live"), store.read()["version"])
            finish(state, SESSION, processing, processing_generation, applied)
            page.wait_for_function(
                "([id, count]) => { const row = document.querySelector(`#side-receipts .receipt-row[data-receipt='${id}']`);"
                " const meta = row && row.querySelector('.receipt-meta');"
                " return window.__ownedSideEvents.length > count && !!meta"
                " && meta.innerText.split('·').map(part => part.trim()).includes('processed'); }",
                arg=[processing, events_before], timeout=LIVE_TIMEOUT_MS)
            check("live_processing_to_processed_without_click", clicks["count"] == clicks_before,
                  {"seconds": round(time.monotonic() - started, 3), "meta": meta(processing)})
            result["side_requests_during_live"] = len([r for r in side_requests if r["at"] >= time.time() - 60])
            page.screenshot(path=str(OUT / f"02-side-receipt-live{LABEL}.png"), full_page=True)

            # Draft preservation: a typed handoff answer blocks the re-render.
            handoff = page.locator(f".side-handoff[data-event='{ids['p1_processed']}']")
            handoff.wait_for()
            draft = handoff.locator("textarea")
            draft.fill("DRAFT-KEEP-7f39")
            page.locator("#side-search").fill("UNSUBMITTED-8ac2")
            events_before = page.evaluate("window.__ownedSideEvents.length")
            clicks_before = clicks["count"]
            during_draft = receive("owned-during-draft", "receipt arriving while the operator types")
            page.wait_for_function("count => window.__ownedSideEvents.length > count"
                                   " && !!document.querySelector('#side-refresh-note')",
                                   arg=events_before, timeout=LIVE_TIMEOUT_MS)
            check("draft_preserved", draft.input_value() == "DRAFT-KEEP-7f39"
                  and page.locator("#side-search").input_value() == "UNSUBMITTED-8ac2"
                  and clicks["count"] == clicks_before)
            check("draft_defers_rerender", row(during_draft).count() == 0)
            page.screenshot(path=str(OUT / f"03-side-draft-preserved{LABEL}.png"), full_page=True)
            draft.fill("")
            click(page.locator("#side-refresh-note").get_by_role("button", name="Refresh now"))
            row(during_draft).wait_for(timeout=LIVE_TIMEOUT_MS)
            check("refresh_shows_deferred_receipt", row(during_draft).count() == 1)

            # Filters and search (server-side query, so they reach older pages too).
            page.locator("#side-stage").select_option("quarantined")
            got = rows_settled("id => { const rows = [...document.querySelectorAll('#side-receipts .receipt-row')];"
                               " return rows.length === 1 && rows[0].dataset.receipt === id; }", ids["quarantined"])
            check("stage_filter_quarantined", got == [ids["quarantined"]], got)
            page.locator("#side-stage").select_option("duplicate")
            got = rows_settled("id => { const rows = [...document.querySelectorAll('#side-receipts .receipt-row')];"
                               " return rows.length === 1 && rows[0].dataset.receipt === id; }", ids["duplicate_source"])
            check("stage_filter_duplicate", got == [ids["duplicate_source"]], got)
            page.locator("#side-stage").select_option("")
            click(page.locator(".side-scope").get_by_role("button", name="P1", exact=True))
            want = sorted([ids["p1_processed"], ids["p1_received"]])
            got = rows_settled("ids => { const rows = [...document.querySelectorAll('#side-receipts .receipt-row')]"
                               ".map(row => row.dataset.receipt).sort(); return JSON.stringify(rows) === JSON.stringify(ids); }",
                               want)
            check("priority_scope_p1", sorted(got) == want, got)
            click(page.locator(".side-scope").get_by_role("button", name="All", exact=True))
            page.locator("#side-search").fill(SEARCH_TOKEN)
            click(page.get_by_role("button", name="Search", exact=True))
            got = rows_settled("id => { const rows = [...document.querySelectorAll('#side-receipts .receipt-row')];"
                               " return rows.length === 1 && rows[0].dataset.receipt === id; }", ids["secret"])
            check("search_by_text", got == [ids["secret"]], got)
            page.locator("#side-search").fill(OLDEST_TEXT)
            click(page.get_by_role("button", name="Search", exact=True))
            got = rows_settled("id => [...document.querySelectorAll('#side-receipts .receipt-row')]"
                               ".some(row => row.dataset.receipt === id)", ids["oldest"])
            check("search_reaches_older_pages", ids["oldest"] in got, len(got))
            page.locator("#side-search").fill(ids["secret"][:16])
            click(page.get_by_role("button", name="Search", exact=True))
            got = rows_settled("id => { const rows = [...document.querySelectorAll('#side-receipts .receipt-row')];"
                               " return rows.length === 1 && rows[0].dataset.receipt === id; }", ids["secret"])
            check("search_by_receipt_id", got == [ids["secret"]], got)
            page.screenshot(path=str(OUT / f"04-side-filter-search{LABEL}.png"), full_page=True)
            page.locator("#side-search").fill("")
            click(page.get_by_role("button", name="Search", exact=True))
            rows_settled("n => document.querySelectorAll('#side-receipts .receipt-row').length === n", PAGE_SIZE)

            # Load older until the oldest seeded filler is present.
            pages = 1
            while row(ids["oldest"]).count() == 0:
                more = page.locator("#side-load-older")
                check(f"load_older_available_page_{pages + 1}", more.count() == 1)
                before = len(page.evaluate(ROW_IDS))
                click(more)
                rows_settled("n => document.querySelectorAll('#side-receipts .receipt-row').length > n", before)
                pages += 1
                assert pages < 20, "pagination did not reach the oldest receipt"
            loaded = page.evaluate(ROW_IDS)
            check("load_older_reaches_oldest", pages >= 3 and len(loaded) == len(set(loaded)),
                  {"pages": pages, "rows": len(loaded)})
            check("load_older_complete", set(ids.values()) | set(fillers) <= set(loaded))
            page.screenshot(path=str(OUT / f"05-side-load-older{LABEL}.png"), full_page=True)

            # Mobile 390: nothing overflows horizontally, including an open detail.
            page.set_viewport_size({"width": 390, "height": 844})
            click(page.get_by_role("tab", name="Side"))
            rows_settled("n => document.querySelectorAll('#side-receipts .receipt-row').length === n", PAGE_SIZE)
            open_detail(ids["secret"])
            overflow = page.evaluate("""() => {
                const width = document.documentElement.clientWidth;
                const offenders = [...document.querySelectorAll('#panel-side *')].filter(node => {
                    const box = node.getBoundingClientRect();
                    return box.width > 0 && (box.right > width + 1 || box.left < -1);
                }).map(node => node.tagName + '.' + String(node.className || '')).slice(0, 20);
                return {width, scrollWidth: document.documentElement.scrollWidth,
                        bodyScrollWidth: document.body.scrollWidth, offenders};
            }""")
            check("mobile_390_no_overflow", overflow["scrollWidth"] <= 390 and overflow["bodyScrollWidth"] <= 390
                  and not overflow["offenders"], overflow)
            page.screenshot(path=str(OUT / f"06-side-receipts-mobile-390{LABEL}.png"), full_page=True)

            result["side_changed_frames"] = page.evaluate("window.__ownedSideEvents")
            check("side_changed_frames_seen", len(result["side_changed_frames"]) >= 3, len(result["side_changed_frames"]))
            result["side_requests"] = len(side_requests)
            result["page_errors"] = errors
            check("no_page_errors", not errors, errors)
            page.evaluate("window.__ownedSideSource.close()")
            browser.close()
    except Exception:
        result["error"] = traceback.format_exc()
        if page is not None:
            try:
                page.screenshot(path=str(OUT / f"99-failure{LABEL}.png"), full_page=True)
            except Exception:
                pass
        raise
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        # Readback from the durable inbox files, independent of the page.
        readback = {}
        for label, key in {**ids, "live": locals().get("live"), "during_draft": locals().get("during_draft")}.items():
            row_on_disk = inbox._load(key) if key else None
            if row_on_disk is None:
                continue
            failure = row_on_disk.get("side_failure") or {}
            readback[label] = {"receipt_id": key, "stage": row_on_disk.get("stage"),
                               "processing": bool(row_on_disk.get("processing")),
                               "priority": source_priority(row_on_disk.get("text")),
                               "delivery": (row_on_disk.get("outbox") or {}).get("state")
                               or (row_on_disk.get("result") or {}).get("delivery"),
                               "failure": failure.get("category"), "quarantined": bool(failure.get("quarantined"))}
        result["readback"] = readback
        result["secrets_still_on_disk"] = not set(SECRETS) - set(leaked(inbox._load(secret)))
        (OUT / f"e2e-side-receipts-result{LABEL}.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

print(json.dumps(result, indent=2, ensure_ascii=False))
