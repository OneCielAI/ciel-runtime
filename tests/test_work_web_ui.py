import os
import re
import tempfile
import unittest
from unittest import mock

from ciel_runtime_support.work_web_controller import WorkWebController
from ciel_runtime_support.work_web_ui import (
    WORK_API_ROOT,
    WORK_WEB_UI_PATH,
    render_work_web_ui,
)


class WorkWebUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = render_work_web_ui()

    def test_renderer_is_standalone_and_data_free(self):
        self.assertIn("<!doctype html>", self.html.lower())
        self.assertIn("Work Console", self.html)
        self.assertIn(WORK_API_ROOT, self.html)
        self.assertNotIn("Authorization: Bearer", self.html)
        self.assertNotRegex(self.html, r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*['\"][^'\"]+")
        self.assertNotIn("localStorage", self.html)
        self.assertNotIn("sessionStorage", self.html)

    def test_browser_calls_only_same_origin_work_api(self):
        self.assertIn("const API='/ca/work/api/v1'", self.html)
        self.assertIn("fetch(API+path", self.html)
        self.assertIn("new EventSource(API+'/events')", self.html)
        self.assertNotIn("fetch('http", self.html)
        self.assertNotIn('fetch("http', self.html)
        self.assertNotIn("new EventSource('http", self.html)
        self.assertIn("credentials:'same-origin'", self.html)

    def test_auxiliary_tabs_load_their_public_rest_projections(self):
        self.assertIn("const data=await request('/'+name)", self.html)
        for resource in ("side", "parallel", "settings", "audit"):
            self.assertIn(f'id="panel-{resource}"', self.html)
        self.assertNotIn("data=projectTab(name)", self.html)

    def test_mutations_use_idempotency_and_cas_refetch(self):
        self.assertIn("headers['Idempotency-Key']=options.idempotencyKey||idempotencyKey()", self.html)
        self.assertIn("const key=idempotencyKey()", self.html)
        self.assertIn("idempotencyKey:key", self.html)
        self.assertIn("operation_id:key", self.html)
        self.assertIn("command('queue-position',{root_id:node.id,queue_position:position})", self.html)
        self.assertIn("command('settings-update'", self.html)
        self.assertIn("command('parallel-configure'", self.html)
        self.assertIn("command('parallel-orphans-preview'", self.html)
        self.assertIn("Move to position", self.html)
        self.assertIn("expected_version:state.snapshot?.version??0", self.html)
        self.assertIn("response.status===409", self.html)
        self.assertIn("await loadSnapshot()", self.html)
        self.assertIn("source.onerror", self.html)

    def test_untrusted_values_use_dom_text_apis(self):
        self.assertIn("node.textContent=String(text)", self.html)
        self.assertIn("document.createTextNode(String(node.title||node.id))", self.html)
        self.assertIn("byId('confirm-message').textContent=message", self.html)
        self.assertNotIn("innerHTML", self.html)
        self.assertNotIn("insertAdjacentHTML", self.html)
        self.assertNotIn("document.write", self.html)

    def test_console_covers_required_views_and_actions(self):
        for tab in ("Node", "Side", "Parallel", "Settings", "Audit"):
            self.assertIn(">" + tab + "</button>", self.html)
        for action in (
            "Add goal", "Add child", "Remove", "Select", "Execute",
            "Replan", "Focus", "Mark urgent", "Set wait", "Queue up",
            "Queue down",
        ):
            self.assertIn(action, self.html)
        self.assertIn("confirm-dialog", self.html)
        self.assertIn("Search title, note, evidence, reference", self.html)
        self.assertIn("All statuses", self.html)

    def test_responsive_keyboard_and_accessibility_contract(self):
        self.assertIn('@media(max-width:820px)', self.html)
        self.assertIn('@media(max-width:520px)', self.html)
        self.assertIn('@media(prefers-reduced-motion:reduce)', self.html)
        self.assertIn('name="viewport"', self.html)
        self.assertIn('role="tree"', self.html)
        self.assertIn('role="tablist"', self.html)
        self.assertIn('role="status" aria-live="polite"', self.html)
        self.assertIn('aria-keyshortcuts="R"', self.html)
        self.assertIn("event.key==='j'", self.html)
        self.assertIn("event.key==='k'", self.html)
        self.assertIn(":focus-visible", self.html)
        self.assertIn("Skip to work tree", self.html)
        self.assertEqual([], re.findall(r'<(?:button|input|select|textarea)(?![^>]*(?:aria-label|id=|type="hidden"))[^>]*>', self.html))

    def test_controller_owns_only_ui_route(self):
        output = mock.Mock()
        controller = WorkWebController(output, render=lambda: "<html>work</html>")
        handler = object()

        self.assertFalse(controller.handle_get(handler, "/ca/work/api/v1/board"))
        self.assertFalse(controller.handle_get(handler, "/ca/work"))
        self.assertTrue(controller.handle_get(handler, WORK_WEB_UI_PATH))
        self.assertTrue(controller.handle_get(handler, WORK_WEB_UI_PATH + "/"))
        self.assertEqual(2, output.call_count)
        output.assert_called_with(
            handler,
            "<html>work</html>",
            content_type="text/html; charset=utf-8",
        )


    def test_every_rest_command_has_a_console_control(self):
        # A feature reachable only through REST is not finished (UI exposure rule).
        from ciel_runtime_support.work_application import openapi_document
        rest = {path.rsplit("/", 1)[1] for path in openapi_document()["paths"] if "/commands/" in path}
        ui = set(re.findall(r"command\('([a-z-]+)'", self.html))
        self.assertEqual(set(), rest - ui)
        self.assertEqual(set(), ui - rest)

    def test_side_and_parallel_tabs_render_operator_controls(self):
        for marker in ("function renderSide(", "function renderParallel(", "function renderOrphans(",
                       "'Retry send'", "'Resend (may duplicate)'", "'Release lease'", "'Acknowledge'",
                       "'Move up'", "'Replace plan…'"):
            self.assertIn(marker, self.html)
        # Unknown send outcomes and lease releases go through the confirmation dialog.
        self.assertRegex(self.html, r"Resend \(may duplicate\)',\(\)=>confirmDanger\(")
        self.assertRegex(self.html, r"'Release lease',\(\)=>confirmDanger\(")

    def test_parallel_tab_uses_native_fork_evidence_not_raw_leases(self):
        for marker in (
            "Main session is the coordinator, not a parallel fork. Side actors are on the Side tab.",
            "Native fork evidence unavailable", "Processing now · confirmed native forks",
            "No fork is processing Work right now.", "confirmed by native agent list",
            "Waiting for input", "Reservations · no native fork yet", "Uncertain or stale",
            "A slot may be held without a running fork.", "Validating", "Device capacity · all sessions",
            "Pause dispatch on this device", "function confirmedFork(", "function compactFork(",
            "source.addEventListener('parallel.changed',refreshParallel)",
            "source.addEventListener('work.changed',refreshParallel)",
        ):
            self.assertIn(marker, self.html)
        renderer = self.html.split("function renderParallel(", 1)[1].split("function renderOrphans(", 1)[0]
        self.assertNotIn("data.leases", renderer)
        self.assertNotIn("active_device", renderer)
        self.assertIn("data.forks", renderer)
        self.assertIn("deviceSection(data)", renderer)
        self.assertIn("const device=data.device||{}", self.html)

    def test_side_tab_is_receipt_first_paginated_and_draft_safe(self):
        for marker in (
            "Side receipts", "All stages", "Load older", "Newest side receipts",
            "Search receipt recap or ID", "Redacted receipt detail", "['quarantined','quarantined']", "['duplicate','duplicate']",
            "request('/side/receipt?receipt_id='+encodeURIComponent(id))",
            "const token=++state.sideDetailToken",
            "if(token!==state.sideDetailToken||state.sideExpanded!==id",
            "if(sideHasDraft())", "Your draft is preserved.",
            "source.addEventListener('side.changed',onSideChanged)",
        ):
            self.assertIn(marker, self.html)
        self.assertRegex(self.html, r"params\.set\('cursor',cursor\)")
        self.assertRegex(self.html, r"params\.set\('priority','P1'\)")
        self.assertRegex(self.html, r"params\.set\('stage',state\.sideQuery\.stage\)")
        self.assertRegex(self.html, r"params\.set\('q',state\.sideQuery\.search\)")
        self.assertIn("const receipts=Array.isArray(data.receipts)?data.receipts:[]", self.html)
        self.assertNotIn("data.receipts||{}", self.html)
        self.assertLess(self.html.index("panel.append(list);if(state.sideCursor)"),
                        self.html.index("renderSideLegacy(panel,data)"))

    @unittest.skipUnless(os.environ.get("CIEL_TEST_BROWSER"), "opt-in browser verification")
    def test_side_receipts_browser_flow_preserves_draft_and_ignores_stale_detail(self):
        from playwright.sync_api import sync_playwright
        html = self.html
        board = {"session_id": "claude:fixture", "version": 1, "nodes": [], "phase": "idle"}
        newest = {"version": 1, "counts": {"received": 1}, "next_cursor": "older-1",
                  "reviews": [], "handoffs": [], "receipts": [
                      {"receipt_id": "r-new", "priority": "P1", "stage": "received",
                       "delivery": "submitted", "recap": "Newest request <script>not executable</script>",
                       "received_at": 1}]}
        older = {"version": 1, "next_cursor": None, "reviews": [], "handoffs": [], "receipts": [
                    {"receipt_id": "r-old", "stage": "processed", "recap": "Older request"}]}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ["CIEL_TEST_BROWSER"], headless=True)
            page = browser.new_page(viewport={"width": 390, "height": 844})
            detail_routes = []
            def route(request):
                url = request.request.url
                if url.endswith("/ca/work/ui"):
                    request.fulfill(content_type="text/html", body=html)
                elif "/events" in url:
                    request.fulfill(content_type="text/event-stream", body=": ready\n\n")
                elif "/side/receipt" in url:
                    detail_routes.append(url)
                    receipt = "r-old" if "r-old" in url else "r-new"
                    request.fulfill(json={"receipt": {"receipt_id": receipt, "stage": "processed",
                                                       "recap": "safe <b>text</b>", "route": "redacted"}})
                elif "/side?" in url:
                    request.fulfill(json=older if "cursor=older-1" in url else newest)
                elif "/board" in url:
                    request.fulfill(json=board)
                else:
                    request.fulfill(json={"reviews": [], "handoffs": []})
            page.route("http://fixture.test/**", route)
            page.goto("http://fixture.test/ca/work/ui")
            page.get_by_role("tab", name="Side").click()
            page.get_by_text("Newest request").wait_for()
            self.assertIn("<script>not executable</script>", page.locator(".receipt-title").inner_text())
            self.assertIn("submitted", page.locator(".receipt-row").first.locator(".receipt-meta").inner_text())
            page.get_by_role("button", name="Load older").click()
            page.get_by_text("Older request").wait_for()
            self.assertEqual(2, page.locator(".receipt-row").count())
            page.locator(".receipt-row").first.get_by_role("button", name=re.compile("Show redacted detail")).click()
            page.get_by_text("safe <b>text</b>").wait_for()
            self.assertEqual(1, len(detail_routes))
            self.assertEqual(0, page.locator(".receipt-detail b").count())
            page.evaluate("state.sideRefreshPending=true; showSideRefreshNotice()")
            self.assertIn("draft is preserved", page.locator("#side-refresh-note").inner_text())
            with tempfile.TemporaryDirectory() as directory:
                shot = os.path.join(directory, "side-receipts-mobile.png")
                page.screenshot(path=shot, full_page=True)
                self.assertGreater(os.path.getsize(shot), 0)
            browser.close()

    @unittest.skipUnless(os.environ.get("CIEL_TEST_BROWSER"), "opt-in browser verification")
    def test_parallel_browser_separates_confirmed_forks_from_non_running_groups(self):
        from playwright.sync_api import sync_playwright
        html = self.html
        now = 1_800_000_000.0
        def row(item, group, priority="P2", native_status="running"):
            return {"lease_id": "lease-" + item, "item_id": item, "item_title": "Title " + item,
                    "item_ref": "REF-" + item, "goal_title": "Goal", "item_path": ["Goal", "Title " + item],
                    "priority": priority, "role": "implement", "state": group, "slot": True,
                    "agent_name": "fork-" + item, "agent_id": "agent-1234567890-" + item,
                    "state_changed_at": now - 8, "native": {"status": native_status,
                    "first_seen_at": now - 20, "last_seen_at": now - 2,
                    "missing_since": now - 5}, "evidence": group}
        parallel = {"schema_version": 2, "version": 7,
                    "policy": {"enabled": True, "paused": False, "priority_limits": {"P1": 1, "P2": 3}},
                    "forks": {"schema": "native-fork-evidence/1", "rule": "Only fresh native agent evidence counts.",
                              "available": True, "observed_at": now, "confirmed_count": 2,
                              "confirmed": [row("a", "running", "P1"), row("b", "running")],
                              "waiting": [row("c", "waiting_input", native_status="waiting")],
                              "reservation": [row("d", "reserved", native_status="not observed")],
                              "uncertain": [row("e", "running", native_status="stale")],
                              "validating": [], "ended": [row("f", "released", native_status="completed")]},
                    "device": {"active_slots": 3, "capacity": 6,
                               "priority_limits": {"P1": 1, "P2": 3, "P3": 1, "P4": 1, "P5": 0},
                               "this_session_by_priority": {"P1": 1, "P2": 1, "P3": 0, "P4": 0, "P5": 0},
                               "other_sessions_by_priority": {"P1": 0, "P2": 1, "P3": 0, "P4": 0, "P5": 0},
                               "paused": False, "enabled": True,
                               "note": "Device-wide lease slots; not fork evidence."}}
        board = {"session_id": "claude:fixture", "version": 1, "nodes": [], "phase": "idle"}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ["CIEL_TEST_BROWSER"], headless=True)
            page = browser.new_page(viewport={"width": 390, "height": 844})
            def route(request):
                url = request.request.url
                if url.endswith("/ca/work/ui"):
                    request.fulfill(content_type="text/html", body=html)
                elif "/events" in url:
                    request.fulfill(content_type="text/event-stream", body=": ready\n\n")
                elif "/parallel" in url:
                    request.fulfill(json=parallel)
                elif "/board" in url:
                    request.fulfill(json=board)
                else:
                    request.fulfill(json={})
            page.route("http://fixture.test/**", route)
            page.goto("http://fixture.test/ca/work/ui")
            page.get_by_role("tab", name="Parallel").click()
            page.locator('[data-group="confirmed"] .fork-title').first.wait_for()
            self.assertEqual(2, page.locator('[data-group="confirmed"] .fork-card').count())
            self.assertEqual(1, page.locator('[data-group="waiting"] .compact-fork').count())
            self.assertEqual(1, page.locator('[data-group="reservation"] .compact-fork').count())
            self.assertEqual(1, page.locator('[data-group="uncertain"] .compact-fork').count())
            self.assertEqual(1, page.locator('[data-group="ended"] .compact-fork').count())
            self.assertIn("confirmed native forks (2)", page.locator('[data-group="confirmed"] h3').inner_text())
            self.assertIn("confirmed by native agent list", page.locator('.fork-card').first.inner_text())
            self.assertIn("P2 · this session 1 · other sessions 1 · free 1 / 3",
                          page.locator('[data-group="device"]').inner_text())
            self.assertIn("slot may be held without a running fork",
                          page.locator('[data-group="uncertain"]').inner_text())
            self.assertFalse(page.locator('[data-group="ended"]').evaluate("node => node.open"))
            orphan = {"version": 7, "digest": "d" * 64, "leases": []}
            page.evaluate("preview => renderOrphans(byId('panel-parallel'), preview)", orphan)
            page.evaluate("refreshParallel()")
            page.locator("#orphan-review .orphan-stale").wait_for()
            self.assertEqual(2, page.locator('[data-group="confirmed"] .fork-card').count())
            overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            self.assertLessEqual(overflow, 0)
            browser.close()

class WorkHttpRoutesTests(unittest.TestCase):
    def test_console_shell_is_mounted_before_rest_and_post_stays_on_rest(self):
        from ciel_runtime_support.work_http_runtime import WorkHttpRoutes

        api = mock.Mock()
        api.get.return_value = True
        api.post.return_value = True
        written = []
        routes = WorkHttpRoutes(api, WorkWebController(lambda _h, text, **kw: written.append((text, kw))))
        self.assertTrue(routes.get(object(), WORK_WEB_UI_PATH))
        api.get.assert_not_called()
        self.assertEqual("text/html; charset=utf-8", written[0][1]["content_type"])
        self.assertTrue(routes.get(object(), "/ca/work/api/v1/board"))
        api.get.assert_called_once()
        self.assertTrue(routes.post(object(), "/ca/work/api/v1/commands/node-add", {}))
        api.post.assert_called_once()

    def test_routes_without_writer_do_not_serve_console(self):
        from ciel_runtime_support.work_http_runtime import WorkHttpRoutes

        api = mock.Mock()
        api.get.return_value = False
        self.assertFalse(WorkHttpRoutes(api).get(object(), WORK_WEB_UI_PATH))
        api.get.assert_called_once()


if __name__ == "__main__":
    unittest.main()
