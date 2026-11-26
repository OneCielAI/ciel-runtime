import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
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
        self.assertIn("Search title, scope note, progress, evidence, reference", self.html)
        self.assertIn("Note (approved scope)", self.html)
        self.assertIn("node-progress", self.html)
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
        # An uncertain main wake is visible and its retry needs confirmation
        # with the exact event, attempt and revision.
        self.assertIn('Main delivery uncertain', self.html)
        self.assertRegex(self.html, r"'Retry main wake \(may duplicate\)',\(\)=>confirmDanger\(")
        self.assertRegex(self.html, r"command\('side-wake-retry',\{event_id:row\.event_id,attempt_ref:row\.wake\.attempt_ref,"
                                    r"revision:row\.wake\.revision,confirmed:true\}")

    def test_parallel_tab_uses_native_fork_evidence_not_raw_leases(self):
        for marker in (
            "Main session is the coordinator, not a parallel fork. Side actors are on the Side tab.",
            "Native fork evidence unavailable", "Processing now · confirmed native forks",
            "No fork is processing Work right now.", "confirmed by native agent list",
            "Waiting for input", "Reservations · no native fork yet", "Uncertain or stale",
            # Submitted results no longer validate in a reviewer fork: after
            # main's decision they are Finalizing.
            "A slot may be held without a running fork.", "Finalizing", "No accepted result is finalizing.",
            "Device capacity · all sessions",
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

    def test_parallel_tab_shows_the_per_priority_dispatch_diagnosis(self):
        # Stage 3, 2026-11-17: each priority's limit, use, runnable items and blockers.
        self.assertIn("const dispatch=renderPriorityDiagnosis(data);if(dispatch)panel.append(dispatch);", self.html)
        renderer = self.html.split("function renderPriorityDiagnosis(data){", 1)[1].split("function renderParallel(", 1)[0]
        self.assertIn("'Per-priority dispatch'", renderer)
        node = shutil.which("node")
        if not node:
            self.skipTest("node is required to run the renderer")
        script = self.html.split("<script>", 1)[1].split("</script>", 1)[0]
        start = script.index("const el=(")
        el_fn = script[start:script.index("\n", start)]
        put_start = script.index("const put=(")
        put_fn = script[put_start:script.index("\n", put_start)]
        harness = (
            "const made=[];const document={createElement:t=>{const n={tag:t,children:[],className:'',textContent:'',"
            "append(...c){this.children.push(...c)}};made.push(n);return n},createTextNode:t=>({textContent:String(t)})};\n"
            + el_fn + "\n" + put_fn + "\n" + "function renderPriorityDiagnosis(data){" + renderer.rstrip() + "\n"
            "const text=n=>n?((n.textContent||'')+(n.children||[]).map(text).join(' ')):'';\n"
            "const out=renderPriorityDiagnosis({priority_diagnosis:{summary:'P1 1/3 can fill',rows:["
            "{priority:'P0',limit:null,active:0,free:null,candidates:0,held_for_parked:0,state:'no_work',reason:'x'},"
            "{priority:'P1',limit:3,active:1,free:2,candidates:2,held_for_parked:0,state:'can_fill',reason:'2 runnable'},"
            "{priority:'P2',limit:2,active:0,free:2,candidates:0,held_for_parked:0,state:'no_candidates',"
            "reason:'free slot but no runnable item (goal in planning 1)'}]}});\n"
            "console.log(JSON.stringify({text:text(out),none:renderPriorityDiagnosis({})===null}))")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "render.js"
            path.write_text(harness, encoding="utf-8")
            done = subprocess.run([node, str(path)], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(0, done.returncode, done.stderr)
        result = json.loads(done.stdout)
        self.assertTrue(result["none"])
        self.assertIn("P1 1/3", result["text"])
        self.assertIn("Runnable 2 · free 2", result["text"])
        self.assertIn("no candidates", result["text"])
        self.assertIn("goal in planning 1", result["text"])
        self.assertNotIn("P0 0", result["text"])

    def test_parallel_tab_offers_main_review_controls_without_reviewer_forks(self):
        for marker in ("function renderMainReviews(", "Main review (", "Awaiting main review", "'Rework'",
                       "'Finalizing'", "No reviewer fork is started.", "'Approve'", "'Rework…'",
                       "'Changed before bind'", "changedBeforeBindText(row.changed_before_bind)",
                       "command('parallel-item-review',{lease_id:row.lease_id,decision,evidence,"
                       "expected_finalization_version:data.review_version??0,...pin})",
                       "panel.append(banner,renderMainReviews(data))"):
            self.assertIn(marker, self.html)
        # Approval finalizes the board item: it goes through the confirmation dialog.
        self.assertRegex(self.html, r"action\('Approve',\(\)=>confirmDanger\(")
        # Rework always carries the required change as evidence.
        self.assertIn("inlineNote(item,'Required rework',note=>reviewDecision(data,row,'rework',note))", self.html)
        self.assertNotIn("'reviewer'", self.html)

    def test_stranded_review_card_refuses_approve_and_offers_fresh_workspace_rework(self):
        for marker in ("function strandedText(s){", "' Approve is refused.'", "' Rework starts in a fresh workspace'",
                       "' and does not count against the rework limit.'",
                       "reviewField(details,'Stranded',strandedText(row.stranded))",
                       "row.stranded?'Unavailable'+(ws.branch?' · recorded '+ws.branch",
                       "if(status==='awaiting_main_review'&&row.stranded){",
                       "el('span','state-chip refused','Approve refused')",
                       "action('Rework in a fresh workspace…',()=>inlineNote(item,'Required rework',"
                       "note=>reviewDecision(data,row,'rework',note)),'danger')",
                       ".review-card.stranded{border-color:var(--danger)}",
                       "s.phase==='deciding'?' Recovery is being dispatched.'",
                       "s.phase==='recovered'?' Recovery was dispatched to a fresh workspace.'"):
            self.assertIn(marker, self.html)
        # A stranded card has no Approve control at all; only the normal branch builds one.
        stranded_branch = self.html.split("if(status==='awaiting_main_review'&&row.stranded){", 1)[1].split("}else if(", 1)[0]
        self.assertNotIn("action('Approve'", stranded_branch)

    def test_superseded_stranded_rows_render_only_in_an_ended_area_without_controls(self):
        # checkpoint3: an ended audit row, from parallel_reviews_ended only.
        for marker in ("function supersededRow(row){", "function renderSupersededReviews(data){",
                       "Array.isArray(data.parallel_reviews_ended)?data.parallel_reviews_ended:[]",
                       "'Stranded result superseded by Work lease '+(by?by.slice(0,8):'(unknown)')",
                       ".filter(row=>!supersededRow(row))", "details.dataset.group='superseded'"):
            self.assertIn(marker, self.html)
        ended = self.html.split("function renderSupersededReviews(data){", 1)[1].split(chr(10), 1)[0]
        for absent in ("action(", "strandedText(", "Rework", "workspace", "artifact", "receipt", "reason"):
            self.assertNotIn(absent, ended)

    @unittest.skipUnless(os.environ.get("CIEL_TEST_BROWSER"), "opt-in browser verification")
    def test_superseded_stranded_rows_browser_ended_area(self):
        from playwright.sync_api import sync_playwright
        html = self.html
        empty = {name: [] for name in ("confirmed", "waiting", "reservation", "uncertain", "validating", "ended")}
        live = {"lease_id": "L1", "item_id": "n240", "item_title": "Retry backoff", "priority": "P1", "state": "pending",
                "decision": None, "item_path": ["Checkout", "Retry backoff"], "summary": "done", "verification": "ok",
                "changed_before_bind": False}
        # A superseded row sent inside parallel_reviews is still never a review card.
        misplaced = {"lease_id": "L9", "item_id": "n249", "item_title": "Misplaced", "priority": "P2", "state": "superseded",
                     "stranded": {"kind": "no_report", "phase": "superseded", "approve": "refused",
                                  "rework": "fresh_workspace", "counts_against_rework_limit": False}}
        parallel = {"schema_version": 2, "version": 7, "review_version": 6, "policy": {"enabled": True, "paused": False},
                    "forks": {"schema": "native-fork-evidence/1", "available": True, "confirmed_count": 0, **empty},
                    "device": {"active_slots": 1, "capacity": 4, "priority_limits": {"P1": 4}},
                    "parallel_reviews": [live, misplaced],
                    "parallel_reviews_ended": [
                        {"lease_id": "S1", "item_id": "n241", "priority": "P1", "item_title": "Webhook signature",
                         "kind": "no_report", "phase": "superseded", "superseding_lease_id": "beta1234" + "0" * 24,
                         "superseding_state": "running", "observed_at": 1794529690.5},
                        {"lease_id": "S2", "item_id": "n242", "priority": "P3", "item_title": "Receipt email",
                         "kind": "workspace_unavailable", "phase": "superseded", "superseding_lease_id": "c0ffee99" + "1" * 24,
                         "superseding_state": "absent"}]}
        board = {"session_id": "claude:fixture", "version": 1, "nodes": [], "phase": "executing"}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ["CIEL_TEST_BROWSER"], headless=True)
            for label, viewport in (("mobile", {"width": 390, "height": 1400}),
                                    ("desktop", {"width": 1440, "height": 1000})):
                with self.subTest(viewport=label):
                    page = browser.new_page(viewport=viewport)
                    def route(request):
                        url = request.request.url
                        if url.endswith("/ca/work/ui"):
                            request.fulfill(content_type="text/html", body=html)
                        elif "/events" in url:
                            request.fulfill(content_type="text/event-stream", body=": ready" + chr(10) + chr(10))
                        elif "/parallel" in url:
                            request.fulfill(json=parallel)
                        elif "/board" in url:
                            request.fulfill(json=board)
                        else:
                            request.fulfill(json={})
                    page.route("http://fixture.test/**", route)
                    page.goto("http://fixture.test/ca/work/ui")
                    page.get_by_role("tab", name="Parallel").click()
                    review = page.locator('[data-group="main-review"]')
                    review.locator(".review-card").first.wait_for()
                    self.assertEqual(["L1"], review.locator(".review-card").evaluate_all(
                        "cards => cards.map(card => card.dataset.review)"))
                    self.assertIn("Main review (1)", review.inner_text())
                    ended = page.locator('[data-group="superseded"]')
                    ended.locator("summary").click()
                    self.assertIn("Superseded stranded results (2)", ended.inner_text())
                    texts = ended.locator(".item-body").all_inner_texts()
                    self.assertEqual(["Stranded result superseded by Work lease beta1234 · superseding lease running"
                                      " · observed 2026-11-13T00:28:10Z",
                                      "Stranded result superseded by Work lease c0ffee99 · superseding lease absent"], texts)
                    self.assertEqual(0, ended.get_by_role("button").count())
                    self.assertNotIn("fresh workspace", ended.inner_text())
                    evidence_dir = os.environ.get("CIEL_TEST_EVIDENCE_DIR")
                    if evidence_dir:
                        page.screenshot(path=os.path.join(evidence_dir, "superseded-ended-" + label + ".png"),
                                        full_page=label == "mobile")
                    overflow = page.evaluate(
                        "document.documentElement.scrollWidth - document.documentElement.clientWidth")
                    self.assertLessEqual(overflow, 0)
                    page.close()
            browser.close()

    def test_json_setting_is_a_formatted_textarea_validated_before_apply(self):
        # work_side_intake_reply_connectors (kind json) was shown as [object Object].
        for marker in ("}else if(setting.kind==='json'){control=input('',JSON.stringify(setting.value??[],null,2),'textarea');",
                       "function jsonSettingValue(text){", "if(!Array.isArray(value))return{error:'must be a JSON array.",
                       "command('settings-update',{key:setting.key,value:JSON.stringify(parsed.value),expected_settings_fingerprint:data.settings_fingerprint})",
                       ".settings-row.json textarea{"):
            self.assertIn(marker, self.html)
        # Every Apply is pinned to the fingerprint the tab showed; a stale pin is told once, the tab
        # is refreshed with the person's edit kept, and nothing is resent (backend contract 2026-11-13).
        for marker in ("command('settings-update',{key:setting.key,value:String(control.value),expected_settings_fingerprint:data.settings_fingerprint})",
                       "if(response.status===409&&data.error==='settings_conflict'){if(state.tab==='settings')onSettingsChanged();",
                       "nothing was applied. Apply again to send it."):
            self.assertIn(marker, self.html)
        # Text/select, JSON and (2026-11-15) the side instruction Markdown editor.
        self.assertEqual(3, self.html.count("expected_settings_fingerprint:data.settings_fingerprint"))
        # An invalid value posts nothing: the error branch returns before command().
        branch = self.html.split("const parsed=jsonSettingValue(control.value);", 1)[1].split("command(", 1)[0]
        self.assertIn("if(parsed.error){toast(", branch)
        self.assertIn(";return}", branch)

    @unittest.skipUnless(os.environ.get("CIEL_TEST_BROWSER"), "opt-in browser verification")
    def test_json_setting_browser_round_trip(self):
        from playwright.sync_api import sync_playwright
        html = self.html
        connectors = [{"source": "*", "receiver_id": "walkie-1", "preset": "walkie", "server": "walkie"}]
        settings = {"applies": "Next side event.", "settings_fingerprint": "fp-1", "settings": [
            {"key": "work_side_intake_reply_connectors", "label": "External reply route / Walkie preset (JSON)",
             "value": connectors, "kind": "json", "status": "next_side_event"},
            {"key": "work_side_intake_max_active", "label": "Max active", "value": 2, "kind": "number"}]}
        board = {"session_id": "claude:fixture", "version": 1, "nodes": [], "phase": "executing"}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ["CIEL_TEST_BROWSER"], headless=True)
            for label, viewport in (("mobile", {"width": 390, "height": 1000}),
                                    ("desktop", {"width": 1440, "height": 900})):
                with self.subTest(viewport=label):
                    posted = []
                    server = json.loads(json.dumps(settings))
                    page = browser.new_page(viewport=viewport)
                    def route(request):
                        url = request.request.url
                        if url.endswith("/ca/work/ui"):
                            request.fulfill(content_type="text/html", body=html)
                        elif "/events" in url:
                            request.fulfill(content_type="text/event-stream", body=": ready\n\n")
                        elif "/commands/settings-update" in url:
                            body = json.loads(request.request.post_data)
                            posted.append(body)
                            if body.get("expected_settings_fingerprint") != server["settings_fingerprint"]:
                                request.fulfill(status=409, json={"ok": False, "error": "settings_conflict",
                                                                  "message": "settings changed; reread",
                                                                  "settings_fingerprint": server["settings_fingerprint"]})
                                return
                            for row in server["settings"]:
                                if row["key"] == body["key"]:
                                    row["value"] = json.loads(body["value"]) if row["kind"] == "json" else body["value"]
                            server["settings_fingerprint"] = "fp-applied-" + str(len(posted))
                            request.fulfill(json={"ok": True, "result": {}})
                        elif "/settings" in url:
                            request.fulfill(json=server)
                        elif "/board" in url:
                            request.fulfill(json=board)
                        else:
                            request.fulfill(json={})
                    page.route("http://fixture.test/**", route)
                    page.goto("http://fixture.test/ca/work/ui")
                    page.get_by_role("tab", name="Settings").click()
                    box = page.locator("#setting-work_side_intake_reply_connectors")
                    box.wait_for()
                    self.assertEqual("textarea", box.evaluate("node => node.tagName.toLowerCase()"))
                    self.assertEqual(connectors, json.loads(box.input_value()))
                    self.assertNotIn("[object Object]", page.locator("#panel-settings").inner_text())
                    self.assertIn('\n  {\n    "source": "*"', box.input_value())
                    evidence_dir = os.environ.get("CIEL_TEST_EVIDENCE_DIR")
                    if evidence_dir:
                        page.screenshot(path=os.path.join(evidence_dir, "settings-json-" + label + ".png"),
                                        full_page=label == "mobile")
                    row = page.locator(".settings-row.json")
                    # Invalid JSON and a non-array are refused before any request.
                    for bad in ('[{"source": "*",', '{"source": "*"}'):
                        box.fill(bad)
                        row.get_by_role("button", name="Apply").click()
                        page.wait_for_timeout(150)
                        self.assertEqual([], posted)
                        self.assertEqual("true", box.get_attribute("aria-invalid"))
                    if evidence_dir:
                        page.screenshot(path=os.path.join(evidence_dir, "settings-json-invalid-" + label + ".png"),
                                        full_page=label == "mobile")
                    edited = connectors + [{"source": "slack:C1", "receiver_id": "walkie-2", "preset": "walkie",
                                            "server": "walkie"}]
                    box.fill(json.dumps(edited, indent=2))
                    row.get_by_role("button", name="Apply").click()
                    page.wait_for_timeout(300)
                    self.assertEqual(1, len(posted), posted)
                    self.assertEqual("work_side_intake_reply_connectors", posted[0]["key"])
                    self.assertEqual(edited, json.loads(posted[0]["value"]))
                    self.assertEqual("fp-1", posted[0]["expected_settings_fingerprint"])
                    self.assertIsNone(box.get_attribute("aria-invalid"))
                    # After the applied change the tab refetches: the next Apply carries the new fingerprint.
                    page.wait_for_timeout(300)
                    self.assertEqual(edited, json.loads(page.locator("#setting-work_side_intake_reply_connectors").input_value()))
                    # Someone else changes a setting; this tab has not refetched yet.
                    server["settings"][1]["value"] = 5
                    server["settings_fingerprint"] = "fp-external"
                    again = edited + [{"source": "slack:C2", "receiver_id": "walkie-3", "preset": "walkie", "server": "walkie"}]
                    box = page.locator("#setting-work_side_intake_reply_connectors")
                    box.fill(json.dumps(again, indent=2))
                    page.locator(".settings-row.json").get_by_role("button", name="Apply").click()
                    page.locator("#toasts .toast", has_text="Settings changed elsewhere").wait_for()
                    self.assertEqual("fp-applied-1", posted[1]["expected_settings_fingerprint"])
                    # One toast for it, no generic error toast, nothing resent.
                    self.assertEqual(0, page.locator("#toasts .toast", has_text="settings changed; reread").count())
                    page.wait_for_timeout(300)
                    self.assertEqual(2, len(posted), posted)
                    self.assertEqual("5", page.locator("#setting-work_side_intake_max_active").input_value())
                    box = page.locator("#setting-work_side_intake_reply_connectors")
                    self.assertEqual(again, json.loads(box.input_value()))
                    self.assertIn("Unsent edit kept after the settings refreshed.", page.locator(".settings-row.json").inner_text())
                    if evidence_dir:
                        page.screenshot(path=os.path.join(evidence_dir, "settings-conflict-" + label + ".png"),
                                        full_page=label == "mobile")
                    page.locator(".settings-row.json").get_by_role("button", name="Apply").click()
                    page.wait_for_timeout(400)
                    self.assertEqual(3, len(posted), posted)
                    self.assertEqual(("fp-external", again), (posted[2]["expected_settings_fingerprint"], json.loads(posted[2]["value"])))
                    self.assertNotIn("Unsent edit kept", page.locator(".settings-row.json").inner_text())
                    overflow = page.evaluate(
                        "document.documentElement.scrollWidth - document.documentElement.clientWidth")
                    self.assertLessEqual(overflow, 0)
                    page.close()
            browser.close()

    def test_legacy_reviewed_results_offer_one_confirmed_accept_or_a_refusal(self):
        for marker in ("function renderLegacyReviews(data){", "Array.isArray(data.parallel_legacy_reviews)",
                       "if(row.reviewers==='multiple')item.append(el('div','warn','multiple legacy reviewer results; "
                       "resolve in the web dashboard or menu'))",
                       "command('parallel-legacy-accept',{lease_id:row.lease_id,confirmed:true,expected_version:data.version??0})",
                       "const legacy=renderLegacyReviews(data);if(legacy)panel.append(legacy);"):
            self.assertIn(marker, self.html)
        # The accept command is posted only from the confirm dialog's callback.
        self.assertIn("action('Accept legacy reviewed result…',()=>confirmDanger(", self.html)
        # The draining legacy reviewer fork group, labelled by the backend, with state and slot only.
        for marker in ("function legacyReviewerSection(forks){", "String((forks.group_labels||{}).legacy_reviewer||'Legacy reviewer')",
                       "'slot '+(rows[index]&&rows[index].slot?'held':'released')",
                       "const legacyReviewers=legacyReviewerSection(forks);if(legacyReviewers)panel.append(legacyReviewers);"):
            self.assertIn(marker, self.html)

    @unittest.skipUnless(os.environ.get("CIEL_TEST_BROWSER"), "opt-in browser verification")
    def test_legacy_reviewed_results_browser_accept(self):
        from playwright.sync_api import sync_playwright
        html = self.html
        empty = {name: [] for name in ("confirmed", "waiting", "reservation", "uncertain", "validating", "ended")}
        parallel = {"schema_version": 2, "version": 7, "review_version": 6, "policy": {"enabled": True, "paused": False},
                    "forks": {"schema": "native-fork-evidence/1", "available": True, "confirmed_count": 0, **empty,
                              "legacy_reviewer": [{"item_id": "n302", "item_title": "Old review", "priority": "P2",
                                                   "state": "reviewing", "slot": True, "agent_id": None, "agent_name": None}],
                              "group_labels": {"legacy_reviewer": "Legacy reviewer (57dbfb3, read-only, draining)"}},
                    "device": {"active_slots": 0, "capacity": 4, "priority_limits": {"P1": 4}},
                    "parallel_reviews": [],
                    "parallel_legacy_reviews": [
                        {"lease_id": "LG1", "item_id": "n300", "priority": "P2", "item_title": "Old export", "reviewers": "one"},
                        {"lease_id": "LG2", "item_id": "n301", "priority": "P1", "item_title": "Old import",
                         "reviewers": "multiple"}]}
        board = {"session_id": "claude:fixture", "version": 1, "nodes": [], "phase": "executing"}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ["CIEL_TEST_BROWSER"], headless=True)
            for label, viewport in (("mobile", {"width": 390, "height": 1100}),
                                    ("desktop", {"width": 1440, "height": 900})):
                with self.subTest(viewport=label):
                    posted = []
                    page = browser.new_page(viewport=viewport)
                    def route(request):
                        url = request.request.url
                        if url.endswith("/ca/work/ui"):
                            request.fulfill(content_type="text/html", body=html)
                        elif "/events" in url:
                            request.fulfill(content_type="text/event-stream", body=": ready" + chr(10) + chr(10))
                        elif "/commands/parallel-legacy-accept" in url:
                            posted.append(json.loads(request.request.post_data))
                            request.fulfill(json={"ok": True, "result": {"lease_id": "LG1", "state": "done"}})
                        elif "/parallel" in url:
                            request.fulfill(json=parallel)
                        elif "/board" in url:
                            request.fulfill(json=board)
                        else:
                            request.fulfill(json={})
                    page.route("http://fixture.test/**", route)
                    page.goto("http://fixture.test/ca/work/ui")
                    page.get_by_role("tab", name="Parallel").click()
                    section = page.locator('[data-group="legacy-review"]')
                    section.wait_for()
                    reviewers = page.locator('section[data-group="legacy_reviewer"]')
                    self.assertIn("Legacy reviewer (57dbfb3, read-only, draining) (1)", reviewers.inner_text())
                    self.assertIn("slot held", reviewers.inner_text())
                    self.assertEqual(0, reviewers.get_by_role("button").count())
                    self.assertIn("Legacy reviewed results (2)", section.inner_text())
                    one = section.locator('[data-legacy="LG1"]')
                    multiple = section.locator('[data-legacy="LG2"]')
                    self.assertEqual(1, one.get_by_role("button", name="Accept legacy reviewed result…").count())
                    self.assertEqual(0, multiple.get_by_role("button").count())
                    self.assertIn("multiple legacy reviewer results; resolve in the web dashboard or menu",
                                  multiple.inner_text())
                    evidence_dir = os.environ.get("CIEL_TEST_EVIDENCE_DIR")
                    if evidence_dir:
                        page.screenshot(path=os.path.join(evidence_dir, "legacy-accept-" + label + ".png"),
                                        full_page=label == "mobile")
                    one.get_by_role("button", name="Accept legacy reviewed result…").click()
                    self.assertIn("Old export", page.locator("#confirm-dialog").inner_text())
                    self.assertEqual([], posted)
                    page.get_by_role("button", name="Confirm").click()
                    page.wait_for_timeout(300)
                    self.assertEqual([{"lease_id": "LG1", "confirmed": True, "expected_version": 7}],
                                     [{k: p[k] for k in ("lease_id", "confirmed", "expected_version")} for p in posted])
                    overflow = page.evaluate(
                        "document.documentElement.scrollWidth - document.documentElement.clientWidth")
                    self.assertLessEqual(overflow, 0)
                    page.close()
            browser.close()

    def test_parallel_failure_log_renders_fixed_codes_short_ids_and_times(self):
        for marker in ("function renderParallelFailures(data){", "if(!Array.isArray(data.parallel_failures))return null;",
                       "revival_refused:'Message to a Work fork refused", "decision_refused_stop:'Main decision refused",
                       "fork_attribution_stop:'Late rework fork stopped", "if(data.failures_truncated)",
                       "const failures=renderParallelFailures(data);if(failures)panel.append(failures);"):
            self.assertIn(marker, self.html)
        section = self.html.split("function renderParallelFailures(data){", 1)[1].split(chr(10), 1)[0]
        self.assertNotIn("action(", section)

    @unittest.skipUnless(os.environ.get("CIEL_TEST_BROWSER"), "opt-in browser verification")
    def test_parallel_failures_and_stranded_launch_resolve_browser(self):
        from playwright.sync_api import sync_playwright
        html = self.html
        empty = {name: [] for name in ("confirmed", "waiting", "reservation", "uncertain", "validating", "ended")}
        stranded = {"lease_id": "S1", "item_id": "n250", "item_title": "Broken worktree", "priority": "P1",
                    "state": "rework_ready", "decision": "rework", "review_journal": "stranded",
                    "stranded": {"kind": "workspace_unavailable", "phase": "recovered", "approve": "refused",
                                 "rework": "fresh_workspace", "counts_against_rework_limit": False},
                    "launch": {"state": "launch_intent", "attempt": 1, "launch_ref": "d" * 32,
                               "unresolved_seconds": 95, "stuck": True}}
        normal = {"lease_id": "F1", "item_id": "n251", "item_title": "Retry", "priority": "P2", "state": "rework_ready",
                  "decision": "rework", "review_journal": "finalization",
                  "launch": {"state": "launch_intent", "attempt": 1, "launch_ref": "e" * 32,
                             "unresolved_seconds": 95, "stuck": True}}
        parallel = {"schema_version": 2, "version": 7, "review_version": 6, "stranded_version": 11,
                    "policy": {"enabled": True, "paused": False},
                    "forks": {"schema": "native-fork-evidence/1", "available": True, "confirmed_count": 0, **empty},
                    "device": {"active_slots": 0, "capacity": 4, "priority_limits": {"P1": 4}},
                    "parallel_reviews": [stranded, normal],
                    "parallel_failures": [
                        {"id": "a" * 16, "code": "revival_refused", "lease_id": "810050df231c4a98a45cb07bcf890852",
                         "first_at": 1794529000.0, "at": 1794529690.0, "count": 3},
                        {"id": "b" * 16, "code": "fork_attribution_stop", "lease_id": None,
                         "first_at": 1794529500.0, "at": 1794529500.0, "count": 1}],
                    "failures_version": 4, "failures_truncated": True}
        board = {"session_id": "claude:fixture", "version": 1, "nodes": [], "phase": "executing"}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ["CIEL_TEST_BROWSER"], headless=True)
            for label, viewport in (("mobile", {"width": 390, "height": 1600}),
                                    ("desktop", {"width": 1440, "height": 1100})):
                with self.subTest(viewport=label):
                    posted = []
                    page = browser.new_page(viewport=viewport)
                    def route(request):
                        url = request.request.url
                        if url.endswith("/ca/work/ui"):
                            request.fulfill(content_type="text/html", body=html)
                        elif "/events" in url:
                            request.fulfill(content_type="text/event-stream", body=": ready" + chr(10) + chr(10))
                        elif "/commands/parallel-launch-resolve" in url:
                            posted.append(json.loads(request.request.post_data))
                            request.fulfill(json={"ok": True, "result": {"state": "manual_requested"}})
                        elif "/parallel" in url:
                            request.fulfill(json=parallel)
                        elif "/board" in url:
                            request.fulfill(json=board)
                        else:
                            request.fulfill(json={})
                    page.route("http://fixture.test/**", route)
                    page.goto("http://fixture.test/ca/work/ui")
                    page.get_by_role("tab", name="Parallel").click()
                    failures = page.locator('[data-group="failures"]')
                    failures.wait_for()
                    self.assertIn("Recent parallel failures (2)", failures.inner_text())
                    first = failures.locator('[data-failure="' + "a" * 16 + '"]')
                    self.assertIn("Message to a Work fork refused", first.inner_text())
                    self.assertIn("×3", first.inner_text())
                    self.assertIn("lease 810050df231c · last 2026-11-13T00:28:10Z · first 2026-11-13T00:16:40Z",
                                  first.inner_text())
                    second = failures.locator('[data-failure="' + "b" * 16 + '"]')
                    self.assertIn("no lease · last 2026-11-13T00:25:00Z", second.inner_text())
                    self.assertIn("Older failures are not shown.", failures.inner_text())
                    self.assertEqual(0, failures.get_by_role("button").count())
                    evidence_dir = os.environ.get("CIEL_TEST_EVIDENCE_DIR")
                    if evidence_dir:
                        page.screenshot(path=os.path.join(evidence_dir, "parallel-failures-" + label + ".png"),
                                        full_page=label == "mobile")
                    review = page.locator('[data-group="main-review"]')
                    for lease in ("S1", "F1"):
                        review.locator('[data-review="' + lease + '"]').get_by_role("button", name="Resolve stuck launch…").click()
                        page.get_by_role("button", name="Confirm").click()
                        page.wait_for_timeout(300)
                    self.assertEqual([("S1", "d" * 32, 11, None), ("F1", "e" * 32, None, 6)],
                                     [(p["lease_id"], p["launch_ref"], p.get("expected_stranded_version"),
                                       p.get("expected_finalization_version")) for p in posted])
                    overflow = page.evaluate(
                        "document.documentElement.scrollWidth - document.documentElement.clientWidth")
                    self.assertLessEqual(overflow, 0)
                    page.close()
            browser.close()

    def test_legacy_orphan_release_is_a_separately_confirmed_single_lease_request(self):
        for marker in ("if(row.owner_state==='legacy_no_owner')item.append(put(el('div','actions'),action('Release legacy lease…',",
                       "command('parallel-orphans-apply',{preview_digest:preview.digest,lease_id:row.lease_id,"
                       "expected_version:preview.version,legacy_confirmed:true})"):
            self.assertIn(marker, self.html)
        # The audit source is derived from auth on the server; the page sends no source header.
        self.assertNotIn("X-Ciel-Operator-Source", self.html)
        # The legacy release is posted only from the confirm dialog's callback.
        legacy = self.html.split("action('Release legacy lease…',", 1)[1].split("list.append(item)", 1)[0]
        self.assertTrue(legacy.startswith("()=>confirmDanger("), legacy[:80])

    def test_never_launched_records_are_retired_only_from_a_confirmed_preview(self):
        self.assertIn("Never-launched reservation records", self.html)
        retire = self.html.split("action('Retire records…',", 1)[1].split("'danger')", 1)[0]
        self.assertTrue(retire.startswith("()=>confirmDanger("), retire[:80])
        self.assertIn("command('parallel-orphans-reconcile',{preview_digest:rec.digest,confirmed:true})", retire)

    def test_recorded_failures_show_the_server_text(self):
        # Workspace failure rows carry their message; the page prefers it over its own labels.
        self.assertIn("String(row.message||FAILURE_LABELS[row.code]||row.code||'unknown')", self.html)

    @unittest.skipUnless(os.environ.get("CIEL_TEST_BROWSER"), "opt-in browser verification")
    def test_legacy_orphan_release_browser(self):
        from playwright.sync_api import sync_playwright
        html = self.html
        empty = {name: [] for name in ("confirmed", "waiting", "reservation", "uncertain", "validating", "ended")}
        parallel = {"schema_version": 2, "version": 7, "review_version": 6, "policy": {"enabled": True, "paused": False},
                    "forks": {"schema": "native-fork-evidence/1", "available": True, "confirmed_count": 0, **empty},
                    "device": {"active_slots": 3, "capacity": 4, "priority_limits": {"P1": 4}}, "parallel_reviews": []}
        preview = {"version": 7, "digest": "f" * 64, "leases": [
            {"lease_id": "dead00000001", "scope": "claude:a", "item_id": "n400", "priority": "P1", "state": "running",
             "owner_state": "dead", "observed_identity": {"pid": 1}},
            {"lease_id": "legacy000002", "scope": "claude:b", "item_id": "n401", "priority": "P2", "state": "running",
             "owner_state": "legacy_no_owner", "observed_identity": None},
            {"lease_id": "live00000003", "scope": "claude:c", "item_id": "n402", "priority": "P1", "state": "running",
             "owner_state": "live", "observed_identity": {"pid": 2}}]}
        board = {"session_id": "claude:fixture", "version": 1, "nodes": [], "phase": "executing"}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ["CIEL_TEST_BROWSER"], headless=True)
            for label, viewport in (("mobile", {"width": 390, "height": 1400}),
                                    ("desktop", {"width": 1440, "height": 1000})):
                with self.subTest(viewport=label):
                    posted = []
                    page = browser.new_page(viewport=viewport)
                    def route(request):
                        url = request.request.url
                        if url.endswith("/ca/work/ui"):
                            request.fulfill(content_type="text/html", body=html)
                        elif "/events" in url:
                            request.fulfill(content_type="text/event-stream", body=": ready" + chr(10) + chr(10))
                        elif "/commands/parallel-orphans-preview" in url:
                            request.fulfill(json={"ok": True, "result": preview})
                        elif "/commands/parallel-orphans-apply" in url:
                            posted.append({"body": json.loads(request.request.post_data)})
                            request.fulfill(json={"ok": True, "result": {"lease_id": "legacy000002", "state": "orphaned"}})
                        elif "/parallel" in url:
                            request.fulfill(json=parallel)
                        elif "/board" in url:
                            request.fulfill(json=board)
                        else:
                            request.fulfill(json={})
                    page.route("http://fixture.test/**", route)
                    page.goto("http://fixture.test/ca/work/ui")
                    page.get_by_role("tab", name="Parallel").click()
                    page.get_by_role("button", name="Inspect orphan leases").click()
                    review = page.locator("#orphan-review")
                    review.wait_for()
                    items = review.locator("article.item")
                    self.assertEqual(3, items.count())
                    self.assertEqual(["Release lease"], items.nth(0).get_by_role("button").all_inner_texts())
                    self.assertEqual(["Release legacy lease…"], items.nth(1).get_by_role("button").all_inner_texts())
                    self.assertEqual(0, items.nth(2).get_by_role("button").count())
                    items.nth(1).get_by_role("button", name="Release legacy lease…").click()
                    self.assertIn("no owner record", page.locator("#confirm-dialog").inner_text())
                    self.assertEqual([], posted)
                    evidence_dir = os.environ.get("CIEL_TEST_EVIDENCE_DIR")
                    if evidence_dir:
                        page.screenshot(path=os.path.join(evidence_dir, "legacy-orphan-confirm-" + label + ".png"),
                                        full_page=label == "mobile")
                    page.get_by_role("button", name="Confirm").click()
                    page.wait_for_timeout(300)
                    self.assertEqual(1, len(posted), posted)
                    body = posted[0]["body"]
                    self.assertEqual(("legacy000002", "f" * 64, 7, True),
                                     (body["lease_id"], body["preview_digest"], body["expected_version"],
                                      body.get("legacy_confirmed")))
                    overflow = page.evaluate(
                        "document.documentElement.scrollWidth - document.documentElement.clientWidth")
                    self.assertLessEqual(overflow, 0)
                    page.close()
            browser.close()

    def test_settings_and_audit_tabs_refetch_on_their_change_events(self):
        for marker in ("source.addEventListener('settings.changed',onSettingsChanged);",
                       "source.addEventListener('audit.changed',onAuditChanged);",
                       "function onAuditChanged(){if(state.tab==='audit')loadTab('audit')}",
                       "async function onSettingsChanged(){if(state.tab!=='settings')return;",
                       "control.dataset.original=String(control.value);",
                       # The Audit tab names each command (the generic renderer showed "record" for every row).
                       "if(name==='audit'){renderAudit(panel,data);return}", "function renderAudit(panel,data){",
                       "el('strong','',String(row.command||'command'))"):
            self.assertIn(marker, self.html)
        # Stored audit text is rendered as text only, and only _audit_projection's fields are read.
        self.assertNotIn("innerHTML", self.html)
        # renderAudit is a single line of the page script.
        audit = self.html.split("function renderAudit(panel,data){", 1)[1].split(chr(10), 1)[0]
        self.assertEqual({"operation_id", "origin", "created_at", "version", "changed", "command", "source", "outcome",
                          "last_reconcile", "receipt_version", "resolution", "request"}, set(re.findall(r"row\.([a-z_]+)", audit)))
        # An outcome-unknown operation stays visible and is resolved only through confirmed controls.
        for marker in ("if(row.outcome==='unknown'){item.classList.add('outcome-unknown');head.append(el('span','state-chip uncertain','Outcome unknown'))}",
                       "action('Reconcile…',()=>confirmDanger(",
                       "Nothing is re-run automatically. If the store cannot show it, the outcome stays unknown.",
                       "command('operation-reconcile',{operation_id:row.operation_id,confirmed:true})",
                       "if(row.last_reconcile&&row.last_reconcile.result==='not_observable'&&Number.isInteger(row.receipt_version))controls.append(",
                       "action('Release unverified…',()=>confirmDanger(",
                       "the effect may already have happened; retrying may duplicate it.",
                       "command('operation-release',{operation_id:row.operation_id,confirmed:true,expected_receipt_version:row.receipt_version})",
                       "released_unverified:'released unverified'"):
            self.assertIn(marker, audit + self.html)
        # The release button reads only /audit, never tab-local state.
        self.assertNotIn("auditNotObservable", self.html)
        # Person-facing 409s are told once and never resent.
        for marker in ("if(response.status===409&&data.error==='receipt_changed'){if(state.tab==='audit')loadTab('audit');",
                       "Nothing was resent.",
                       "if(response.status===409&&data.error==='operator_receipts_full'){",
                       "toastAction(data.message,'Open Audit tab',()=>showTab('audit'))",
                       ".catch(error=>{if(!error.handled)toast(error.message,true)})}"):
            self.assertIn(marker, self.html)
        self.assertIn("['auth','os_user','pid'].forEach(key=>{if(origin[key]!=null)", audit)

    @unittest.skipUnless(os.environ.get("CIEL_TEST_BROWSER"), "opt-in browser verification")
    def test_external_changes_refresh_open_settings_and_audit_tabs_browser(self):
        from playwright.sync_api import sync_playwright
        html = self.html
        board = {"session_id": "claude:fixture", "version": 1, "nodes": [], "phase": "executing"}
        connectors = [{"source": "*", "receiver_id": "walkie-1", "preset": "walkie", "server": "walkie"}]
        served = {"max": 2, "audit": [{"operation_id": "op-1", "command": "settings_update", "created_at": 1794529000.0,
                                        "source": "web"}]}
        def settings():
            return {"applies": "Next side event.", "settings": [
                {"key": "work_side_intake_max_active", "label": "Max active", "value": served["max"], "kind": "number"},
                {"key": "work_side_intake_reply_connectors", "label": "Reply routes", "value": connectors, "kind": "json"}]}
        # A controllable EventSource: the test fires a server event by name.
        fake_events = """
          window.__es = [];
          window.EventSource = class { constructor(url) { this.url = url; this.listeners = {}; window.__es.push(this);
            setTimeout(() => this.onopen && this.onopen(), 0) }
            addEventListener(kind, fn) { (this.listeners[kind] ||= []).push(fn) } close() {} };
          window.__fire = kind => window.__es.at(-1).listeners[kind]?.forEach(fn => fn({ data: '{}' }));
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ["CIEL_TEST_BROWSER"], headless=True)
            for label, viewport in (("mobile", {"width": 390, "height": 1100}),
                                    ("desktop", {"width": 1440, "height": 900})):
                with self.subTest(viewport=label):
                    served["max"] = 2
                    served["audit"] = served["audit"][:1]
                    reconciles, releases = [], []
                    page = browser.new_page(viewport=viewport)
                    page.add_init_script(fake_events)
                    def route(request):
                        url = request.request.url
                        if url.endswith("/ca/work/ui"):
                            request.fulfill(content_type="text/html", body=html)
                        elif "/commands/settings-update" in url:
                            request.fulfill(status=409, json={"ok": False, "error": "operator_receipts_full",
                                                              "message": "all 128 operation receipts are operations whose outcome is unknown; resolve outcome-unknown operations first"})
                        elif "/settings" in url:
                            request.fulfill(json=settings())
                        elif "/commands/operation-reconcile" in url:
                            reconciles.append(json.loads(request.request.post_data))
                            served["audit"][2]["last_reconcile"] = {"result": "not_observable", "at": 1794529900.0}
                            served["audit"][2]["receipt_version"] = 2
                            request.fulfill(json={"ok": True, "result": {"operation_id": "op-4", "command": "parallel_item_review",
                                                                         "outcome": "unknown", "observed": "no_stored_proof",
                                                                         "receipt_version": 2}})
                        elif "/commands/operation-release" in url:
                            releases.append(json.loads(request.request.post_data))
                            if len(releases) == 1:
                                # Someone else touched the receipt first: version pin refused.
                                served["audit"][2]["receipt_version"] = 3
                                request.fulfill(status=409, json={"ok": False, "error": "receipt_changed",
                                                                  "message": "receipt changed; reread"})
                            else:
                                served["audit"][2].update(outcome="released_unverified", last_reconcile=None, receipt_version=4,
                                                          resolution={"command": "operation-release", "source": "web",
                                                                      "at": 1794530000.0},
                                                          request={"lease_id": "L4", "decision": "rework", "evidence_count": 1})
                                request.fulfill(json={"ok": True, "result": {"operation_id": "op-4", "command": "parallel_item_review",
                                                                             "outcome": "released_unverified", "receipt_version": 4}})
                        elif "/audit" in url:
                            request.fulfill(json={"ok": True, "schema_version": 1, "version": 3, "events": list(reversed(served["audit"]))})
                        elif "/board" in url:
                            request.fulfill(json=board)
                        else:
                            request.fulfill(json={})
                    page.route("http://fixture.test/**", route)
                    page.goto("http://fixture.test/ca/work/ui")
                    page.get_by_role("tab", name="Settings").click()
                    box = page.locator("#setting-work_side_intake_reply_connectors")
                    box.wait_for()
                    self.assertEqual("2", page.locator("#setting-work_side_intake_max_active").input_value())
                    draft = json.dumps(connectors + [{"source": "slack:C9", "receiver_id": "walkie-9", "preset": "walkie",
                                                      "server": "walkie"}], indent=2)
                    box.fill(draft)
                    # Another operator (REST or ctl) changes a setting; the server emits settings.changed.
                    served["max"] = 5
                    page.evaluate("window.__fire('settings.changed')")
                    # The page CSP has no 'unsafe-eval', so poll from Python instead of wait_for_function.
                    for _ in range(50):
                        if page.locator("#setting-work_side_intake_max_active").input_value() == "5":
                            break
                        page.wait_for_timeout(100)
                    self.assertEqual("5", page.locator("#setting-work_side_intake_max_active").input_value())
                    self.assertEqual(draft, page.locator("#setting-work_side_intake_reply_connectors").input_value())
                    self.assertIn("Unsent edit kept after the settings refreshed.", page.locator("#panel-settings").inner_text())
                    evidence_dir = os.environ.get("CIEL_TEST_EVIDENCE_DIR")
                    if evidence_dir:
                        page.screenshot(path=os.path.join(evidence_dir, "settings-live-refresh-" + label + ".png"),
                                        full_page=label == "mobile")
                    page.locator(".settings-row").first.get_by_role("button", name="Apply").click()
                    full = page.locator("#toasts .toast", has_text="resolve outcome-unknown operations first")
                    full.wait_for()
                    # R3 ruling bffac131: web displays the server text verbatim,
                    # while retaining the Open Audit action.
                    self.assertIn("all 128 operation receipts are operations whose outcome is unknown; resolve outcome-unknown operations first",
                                  full.inner_text())
                    # One toast for it, not a second generic one.
                    self.assertEqual(0, page.locator("#toasts .toast", has_text="receipt store at its cap").count())
                    if evidence_dir:
                        page.screenshot(path=os.path.join(evidence_dir, "receipts-full-toast-" + label + ".png"),
                                        full_page=label == "mobile")
                    full.get_by_role("button", name="Open Audit tab").click()
                    self.assertEqual("true", page.locator("#tab-audit").get_attribute("aria-selected"))
                    page.locator("#panel-audit").get_by_text("settings_update").first.wait_for()
                    self.assertNotIn("parallel_configure", page.locator("#panel-audit").inner_text())
                    served["audit"].append({"operation_id": "op-2", "command": "parallel_configure",
                                            "created_at": 1794529100.0, "source": "rest"})
                    served["audit"].append({"operation_id": "op-4", "command": "parallel_item_review", "outcome": "unknown",
                                            "created_at": 1794529300.0, "source": "web", "last_reconcile": None,
                                            "receipt_version": 1})
                    served["audit"].append({"operation_id": "op-3", "command": '<img src=x onerror="window.__xss=1">',
                                            "created_at": 1794529200.0, "source": "ctl",
                                            "origin": {"auth": "token", "pid": 4242}})
                    page.evaluate("window.__fire('audit.changed')")
                    page.locator("#panel-audit").get_by_text("parallel_configure").first.wait_for()
                    rows = page.locator("#panel-audit .audit-row")
                    self.assertEqual(["op-3", "op-4", "op-2", "op-1"], rows.evaluate_all("rows => rows.map(row => row.dataset.operation)"))
                    unknown = page.locator('#panel-audit [data-operation="op-4"]')
                    self.assertIn("Outcome unknown", unknown.inner_text())
                    # Before any reconcile, only Reconcile is offered.
                    self.assertEqual(["Reconcile…"], unknown.get_by_role("button").all_inner_texts())
                    self.assertEqual(0, page.locator('#panel-audit [data-operation="op-2"]').get_by_role("button").count())
                    unknown.get_by_role("button", name="Reconcile…").click()
                    dialog = page.locator("#confirm-dialog").inner_text()
                    self.assertIn("checks the actual store", dialog)
                    self.assertIn("Nothing is re-run automatically", dialog)
                    self.assertEqual([], reconciles)
                    if evidence_dir:
                        page.screenshot(path=os.path.join(evidence_dir, "audit-reconcile-confirm-" + label + ".png"),
                                        full_page=label == "mobile")
                    page.get_by_role("button", name="Confirm").click()
                    page.wait_for_timeout(300)
                    self.assertEqual([("op-4", True)], [(r["operation_id"], r["confirmed"]) for r in reconciles])
                    # The store could not show it: /audit now records last_reconcile.result not_observable.
                    unknown = page.locator('#panel-audit [data-operation="op-4"]')
                    self.assertEqual(["Reconcile…", "Release unverified…"], unknown.get_by_role("button").all_inner_texts())
                    # The button comes from /audit, not from this tab: it survives a full reload.
                    page.reload()
                    page.get_by_role("tab", name="Audit").click()
                    unknown = page.locator('#panel-audit [data-operation="op-4"]')
                    unknown.wait_for()
                    self.assertEqual(["Reconcile…", "Release unverified…"], unknown.get_by_role("button").all_inner_texts())
                    self.assertIn("last reconcile not observable at 2026-11-13T00:31:40Z", unknown.inner_text())
                    unknown.get_by_role("button", name="Release unverified…").click()
                    self.assertIn("the effect may already have happened; retrying may duplicate it",
                                  page.locator("#confirm-dialog").inner_text())
                    self.assertEqual([], releases)
                    if evidence_dir:
                        page.screenshot(path=os.path.join(evidence_dir, "audit-release-confirm-" + label + ".png"),
                                        full_page=label == "mobile")
                    page.get_by_role("button", name="Confirm").click()
                    page.wait_for_timeout(300)
                    # 409 receipt_changed: told once, the tab re-read, nothing resent.
                    self.assertIn("This operation changed since the Audit tab showed it", page.locator("#toasts").inner_text())
                    self.assertEqual(1, len(releases))
                    unknown = page.locator('#panel-audit [data-operation="op-4"]')
                    self.assertEqual(["Reconcile…", "Release unverified…"], unknown.get_by_role("button").all_inner_texts())
                    unknown.get_by_role("button", name="Release unverified…").click()
                    page.get_by_role("button", name="Confirm").click()
                    page.wait_for_timeout(300)
                    # Each attempt is pinned to the receipt version the row showed at that time.
                    self.assertEqual([("op-4", True, 2), ("op-4", True, 3)],
                                     [(r["operation_id"], r["confirmed"], r["expected_receipt_version"]) for r in releases])
                    released = page.locator('#panel-audit [data-operation="op-4"]')
                    self.assertIn("released unverified", released.inner_text())
                    self.assertNotIn("Outcome unknown", released.inner_text())
                    self.assertIn("resolved by web via operation-release at 2026-11-13T00:33:20Z", released.inner_text())
                    self.assertIn("request lease_id=L4, decision=rework, evidence_count=1", released.inner_text())
                    self.assertEqual(0, released.get_by_role("button").count())
                    # A stored command holding markup renders as text and runs nothing.
                    self.assertEqual(0, page.locator("#panel-audit img").count())
                    self.assertIsNone(page.evaluate("window.__xss ?? null"))
                    self.assertIn('<img src=x onerror="window.__xss=1">', rows.nth(0).inner_text())
                    self.assertIn("auth token · pid 4242", rows.nth(0).inner_text())
                    # op-4 (outcome unknown) sits between op-3 and op-2 by time, so address op-2 by id.
                    rest_row = page.locator('#panel-audit [data-operation="op-2"]')
                    self.assertIn("rest", rest_row.inner_text())
                    self.assertIn("2026-11-13T00:18:20Z", rest_row.inner_text())
                    if evidence_dir:
                        page.screenshot(path=os.path.join(evidence_dir, "audit-live-refresh-" + label + ".png"),
                                        full_page=label == "mobile")
                    overflow = page.evaluate(
                        "document.documentElement.scrollWidth - document.documentElement.clientWidth")
                    self.assertLessEqual(overflow, 0)
                    page.close()
            browser.close()

    def test_unknown_operation_outcome_rereads_before_any_retry(self):
        # Backend 409 operation_outcome_unknown: re-read, tell the person, never resend the same key.
        self.assertIn("if(response.status===409&&data.error==='operation_outcome_unknown'){await loadSnapshot();"
                      "if(state.tab&&state.tab!=='node')loadTab(state.tab);", self.html)
        self.assertNotIn("replayed", self.html)
        # Every command call mints its own key; nothing reuses one.
        self.assertIn("function command(name,body={}){const key=idempotencyKey();", self.html)

    def test_review_decision_sends_the_pin_of_the_rendered_row(self):
        # R1 (backend 170c6e63): the decision carries the review_pin of the row
        # the person saw, in the router's exact format only; none when absent.
        self.assertIn("const pin=typeof row.review_pin==='string'&&/^rp1:[0-9a-f]{64}$/.test(row.review_pin)"
                      "?{expected_review_pin:row.review_pin}:{};", self.html)

    def test_parallel_banner_shows_the_reported_fork_gate_state(self):
        # G5 (backend 9a852b08): display only. The server's message is printed as
        # is; a not_loaded state is a warning, loaded a quiet line.
        for marker in ("function forkGateLine(gate){",
                       "if(state==='loaded')return el('div','meta','Work fork gate '+String(gate.gate_version||'')+' loaded'",
                       "el('span',state==='not_loaded'?'warn fork-gate-off':'meta',String(gate.message||'Work fork gate presence unknown'))",
                       "const gate=forkGateLine(data.fork_gate);if(gate)banner.append(gate);"):
            self.assertIn(marker, self.html)

    def test_owner_offline_rework_is_shown_as_waiting_not_as_an_error(self):
        # Backend owner_offline: the rework is queued until the conversation resumes.
        for marker in ("function ownerOffline(row){return!!(row.blocked&&row.blocked.code==='owner_offline')}",
                       "return'Rework (waiting for the conversation to resume)'",
                       "if(ownerOffline(row))reviewField(details,'Waiting','Rework queued: waiting for the conversation"
                       " to resume. It starts by itself when that conversation runs again; nothing failed.');"
                       "else reviewField(details,'Blocked',"):
            self.assertIn(marker, self.html)

    def test_review_states_include_waiting_for_slot_and_blocked_code(self):
        for marker in ("awaiting_capacity:'Rework (waiting for slot)'", "if(value==='awaiting_capacity')return'awaiting_capacity'",
                       "reviewField(details,'Blocked',", "not_applicable:'no source reply (event referenced no message)'"):
            self.assertIn(marker, self.html)

    def test_stuck_rework_launch_is_labelled_and_resolution_is_a_confirmed_request(self):
        for marker in ("return'Rework launch stuck'", "return'Waiting for host check'",
                       "return'Rework (agent present)'", "return'Rework launching'",
                       "command('parallel-launch-resolve',row.review_journal==='stranded'?{lease_id:row.lease_id,"
                       "launch_ref:row.launch.launch_ref,confirmed:true,expected_stranded_version:data.stranded_version??0}"
                       ":{lease_id:row.lease_id,launch_ref:row.launch.launch_ref,confirmed:true,"
                       "expected_finalization_version:data.review_version??0})",
                       "row.launch.stuck&&row.launch.launch_ref&&",
                       "action('Resolve stuck launch…',()=>resolveLaunch(data,row),'danger')"):
            self.assertIn(marker, self.html)
        self.assertNotIn("dispatch_name", self.html)
        self.assertRegex(self.html, r"function resolveLaunch\(data,row\)\{return confirmDanger\(")

    @unittest.skipUnless(os.environ.get("CIEL_TEST_BROWSER"), "opt-in browser verification")
    def test_stuck_rework_launch_browser_resolution_request(self):
        from playwright.sync_api import sync_playwright
        html = self.html
        empty = {name: [] for name in ("confirmed", "waiting", "reservation", "uncertain", "validating", "ended")}
        def row(lease, item, title, launch):
            return {"lease_id": lease, "item_id": item, "item_title": title, "priority": "P1", "state": "rework_ready",
                    "decision": "rework", "evidence": "add the jitter test", "item_path": ["Checkout", title],
                    "summary": "first attempt", "verification": "pytest: 1 failed", "launch": launch}
        parallel = {"schema_version": 2, "version": 7, "review_version": 6, "policy": {"enabled": True, "paused": False},
                    "forks": {"schema": "native-fork-evidence/1", "available": True, "confirmed_count": 0, **empty},
                    "device": {"active_slots": 2, "capacity": 4, "priority_limits": {"P1": 4}},
                    "parallel_reviews": [
                        row("L1", "n240", "Retry backoff", {"state": "launch_intent", "attempt": 1, "launch_ref": "a" * 32,
                                                             "unresolved_seconds": 95, "stuck": True}),
                        row("L2", "n241", "Webhook", {"state": "launch_intent", "attempt": 1, "launch_ref": "b" * 32,
                                                       "unresolved_seconds": 4, "stuck": False}),
                        row("L3", "n242", "Receipt email", {"state": "manual_requested", "attempt": 1,
                                                             "launch_ref": "c" * 32}),
                        row("L4", "n243", "Refund", {"state": "bound", "attempt": 1, "agent_present": True})]}
        board = {"session_id": "claude:fixture", "version": 1, "nodes": [], "phase": "executing"}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ["CIEL_TEST_BROWSER"], headless=True)
            for label, viewport in (("mobile", {"width": 390, "height": 1400}),
                                    ("desktop", {"width": 1440, "height": 1000})):
                with self.subTest(viewport=label):
                    posted = []
                    page = browser.new_page(viewport=viewport)
                    # One argument: Playwright passes (route, request) to a two-parameter handler.
                    # Pages run one at a time, so the loop's current `posted` is the right list.
                    def route(request):
                        url = request.request.url
                        if url.endswith("/ca/work/ui"):
                            request.fulfill(content_type="text/html", body=html)
                        elif "/events" in url:
                            request.fulfill(content_type="text/event-stream", body=": ready\n\n")
                        elif "/commands/parallel-launch-resolve" in url:
                            posted.append(json.loads(request.request.post_data))
                            request.fulfill(json={"ok": True, "result": {"state": "manual_requested"}})
                        elif "/parallel" in url:
                            request.fulfill(json=parallel)
                        elif "/board" in url:
                            request.fulfill(json=board)
                        else:
                            request.fulfill(json={})
                    page.route("http://fixture.test/**", route)
                    page.goto("http://fixture.test/ca/work/ui")
                    page.get_by_role("tab", name="Parallel").click()
                    section = page.locator('[data-group="main-review"]')
                    section.locator(".review-card").first.wait_for()
                    self.assertEqual(["Rework launch stuck", "Rework launching", "Waiting for host check",
                                      "Rework (agent present)"],
                                     section.locator(".review-card .state-chip").all_inner_texts())
                    self.assertIn("agent present", section.locator(".review-card").nth(3).inner_text())
                    # Only the stuck launch offers the request; nothing is posted before confirmation.
                    self.assertEqual(1, section.get_by_role("button", name="Resolve stuck launch…").count())
                    section.get_by_role("button", name="Resolve stuck launch…").click()
                    self.assertIn("first checks its native agent list", page.locator("#confirm-dialog").inner_text())
                    self.assertEqual([], posted)
                    evidence_dir = os.environ.get("CIEL_TEST_EVIDENCE_DIR")
                    if evidence_dir:
                        page.screenshot(path=os.path.join(evidence_dir, "rework-launch-stuck-" + label + ".png"),
                                        full_page=label == "mobile")
                    page.get_by_role("button", name="Confirm").click()
                    page.wait_for_timeout(300)
                    self.assertEqual([("L1", "a" * 32, True, 6)],
                                     [(p["lease_id"], p["launch_ref"], p["confirmed"],
                                       p["expected_finalization_version"]) for p in posted])
                    self.assertTrue(all("dispatch_name" not in p for p in posted))
                    overflow = page.evaluate(
                        "document.documentElement.scrollWidth - document.documentElement.clientWidth")
                    self.assertLessEqual(overflow, 0)
                    page.close()
            browser.close()

    def test_side_handoff_list_labels_bounded_time_limit(self):
        for marker in ("row.kind==='time_limit'&&row.time_limit", "'Time limit: '", "' · due '", "'Evidence: '"):
            self.assertIn(marker, self.html)

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
        self.assertIn("view:'receipts',limit:'15'", self.html)
        self.assertLess(self.html.index('renderSide(panel,state.side)'),
                        self.html.index('loadSideSummary(summary)'))

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
            summaries = []
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
                elif "view=summary" in url:
                    summaries.append(request)
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
            self.assertEqual(1, len(summaries))
            self.assertEqual(0, page.locator('.side-handoff').count())
            if os.environ.get('CIEL_SIDE_SCREENSHOT'):
                page.screenshot(path=os.environ['CIEL_SIDE_SCREENSHOT'], full_page=True)
            summaries[0].fulfill(json={'version': 1, 'counts': {'received': 1}, 'reviews': [],
                                      'handoffs': [{'event_id': 'h1', 'state': 'requested', 'summary': 'Draft target'}]})
            draft = page.locator('.side-handoff textarea')
            draft.fill('Unsubmitted answer')
            page.get_by_role('button', name='P1', exact=True).click()
            page.get_by_text('Newest request').wait_for()
            self.assertEqual('Unsubmitted answer', draft.input_value())
            self.assertEqual(1, len(summaries))
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

    @unittest.skipUnless(os.environ.get("CIEL_TEST_BROWSER"), "opt-in browser verification")
    def test_board_and_parallel_visualize_active_forks_and_all_p0_to_p5_lanes(self):
        from playwright.sync_api import sync_playwright
        html = self.html
        now = 1_800_000_000.0
        nodes = [
            {"id": "g0", "parent_id": None, "title": "P0 Hotfix outage", "status": "in_progress", "priority": "P1", "effective_priority": "P0", "done": 0, "total": 1},
            {"id": "n0", "parent_id": "g0", "title": "Patch webhook verifier", "status": "in_progress", "priority": "", "done": 0, "total": 0},
            {"id": "g1", "parent_id": None, "title": "P1 Checkout reliability", "status": "in_progress", "priority": "P1", "effective_priority": "P1", "done": 0, "total": 1},
            {"id": "n1", "parent_id": "g1", "title": "Retry payment ledger", "status": "in_progress", "priority": "", "done": 0, "total": 0},
            {"id": "g2", "parent_id": None, "title": "P2 Analytics pipeline", "status": "in_progress", "priority": "P2", "effective_priority": "P2", "done": 0, "total": 1},
            {"id": "n2", "parent_id": "g2", "title": "Backfill cohort metrics", "status": "in_progress", "priority": "", "done": 0, "total": 0},
            {"id": "g3", "parent_id": None, "title": "P3 Docs automation", "status": "in_progress", "priority": "P3", "effective_priority": "P3", "done": 0, "total": 1},
            {"id": "n3", "parent_id": "g3", "title": "Regenerate API spec", "status": "pending", "priority": "", "done": 0, "total": 0},
            {"id": "g4", "parent_id": None, "title": "P4 Log hygiene", "status": "in_progress", "priority": "P4", "effective_priority": "P4", "done": 0, "total": 1},
            {"id": "n4", "parent_id": "g4", "title": "Prune stale debug files", "status": "in_progress", "priority": "", "done": 0, "total": 0},
            {"id": "g5", "parent_id": None, "title": "P5 Archive sweep", "status": "pending", "priority": "P5", "effective_priority": "P5", "done": 0, "total": 1},
            {"id": "n5", "parent_id": "g5", "title": "Compress 2025 logs", "status": "pending", "priority": "", "done": 0, "total": 0},
        ]
        board = {
            "session_id": "claude:p0p5", "version": 42, "phase": "executing",
            "done": 0, "total": 6, "active_root_id": "g0",
            "queue_ids": ["g1", "g2", "g3", "g4", "g5"], "nodes": nodes,
        }
        def fork_row(lease, item, goal_id, goal_title, item_title, prio, eff, group, state="running", native_status="running"):
            return {
                "lease_id": lease, "item_id": item, "goal_id": goal_id, "goal_title": goal_title,
                "item_title": item_title, "item_ref": "REF-" + item, "item_path": [goal_title, item_title],
                "priority": prio, "effective_priority": eff, "role": "worker", "state": state, "slot": True,
                "agent_name": "work-" + item, "agent_id": "agent-123456-" + item,
                "state_changed_at": now - 10,
                "native": {"status": native_status, "first_seen_at": now - 30, "last_seen_at": now - 2, "missing_since": None},
                "evidence": group,
            }
        parallel = {
            "schema_version": 2, "version": 15,
            "policy": {"enabled": True, "paused": False, "priority_limits": {"P1": 3, "P2": 2, "P3": 1, "P4": 1, "P5": 1}},
            "forks": {
                "schema": "native-fork-evidence/1", "rule": "Only fresh native agent evidence counts.",
                "available": True, "observed_at": now - 1, "confirmed_count": 4,
                "confirmed": [
                    fork_row("L0", "n0", "g0", "P0 Hotfix outage", "Patch webhook verifier", "P1", "P0", "confirmed"),
                    fork_row("L1", "n1", "g1", "P1 Checkout reliability", "Retry payment ledger", "P1", "P1", "confirmed"),
                    fork_row("L2", "n2", "g2", "P2 Analytics pipeline", "Backfill cohort metrics", "P2", "P2", "confirmed"),
                    fork_row("L4", "n4", "g4", "P4 Log hygiene", "Prune stale debug files", "P4", "P4", "confirmed"),
                ],
                "waiting": [],
                "reservation": [],
                "uncertain": [
                    fork_row("L3", "n3", "g3", "P3 Docs automation", "Regenerate API spec", "P3", "P3", "uncertain", state="running", native_status="stale"),
                ],
                "validating": [],
                "ended": [],
            },
            "device": {
                "active_slots": 5, "capacity": 8,
                "priority_limits": {"P1": 3, "P2": 2, "P3": 1, "P4": 1, "P5": 1},
                "this_session_by_priority": {"P1": 2, "P2": 1, "P3": 1, "P4": 1, "P5": 0},
                "other_sessions_by_priority": {"P1": 0, "P2": 0, "P3": 0, "P4": 0, "P5": 0},
                "paused": False, "enabled": True,
                "note": "Device-wide lease slots; not fork evidence.",
            },
            "priority_diagnosis": {
                "summary": "P0 1 goals running · P1 1/3 can fill · P2 1/2 can fill · P3 1/1 full · P4 1/1 full · P5 0/1 no candidates",
                "attention": ["P5"],
                "rows": [
                    {"priority": "P0", "limit": None, "active": 1, "free": None, "candidates": 0, "held_for_parked": 0, "state": "goals_running", "reason": "1 P0 goal(s) running"},
                    {"priority": "P1", "limit": 3, "active": 1, "free": 2, "candidates": 1, "held_for_parked": 0, "state": "can_fill", "reason": "1 runnable"},
                    {"priority": "P2", "limit": 2, "active": 1, "free": 1, "candidates": 1, "held_for_parked": 0, "state": "can_fill", "reason": "1 runnable"},
                    {"priority": "P3", "limit": 1, "active": 1, "free": 0, "candidates": 0, "held_for_parked": 0, "state": "full", "reason": "all slots in use"},
                    {"priority": "P4", "limit": 1, "active": 1, "free": 0, "candidates": 0, "held_for_parked": 0, "state": "full", "reason": "all slots in use"},
                    {"priority": "P5", "limit": 1, "active": 0, "free": 1, "candidates": 0, "held_for_parked": 0, "state": "no_candidates", "reason": "free slot but no runnable item (goal in planning 1)"},
                ],
            },
        }
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ["CIEL_TEST_BROWSER"], headless=True)
            for label, viewport in (("desktop", {"width": 1440, "height": 960}),
                                    ("mobile", {"width": 390, "height": 1100})):
                with self.subTest(viewport=label):
                    page = browser.new_page(viewport=viewport)
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
                    # Without switching to the Parallel tab, the P0-P5 live strip and tree fork badges load.
                    strip = page.locator("#priority-lanes")
                    strip.wait_for()
                    for prio in ("P0", "P1", "P2", "P3", "P4", "P5"):
                        cell = strip.locator(f'.lane-card[data-priority="{prio}"]')
                        self.assertEqual(1, cell.count(), f"missing lane {prio}")
                    # P0 lane shows n0 as confirmed fork (effective_priority=P0, not mislabeled P1).
                    self.assertIn("n0", strip.locator('.lane-card[data-priority="P0"]').inner_text())
                    self.assertNotIn("n0", strip.locator('.lane-card[data-priority="P1"]').inner_text())
                    # P3 has an uncertain lease, not a confirmed fork — must not be counted as confirmed.
                    self.assertIn("uncertain", strip.locator('.lane-card[data-priority="P3"]').inner_text().lower())
                    # Tree rows mark active fork leaves AND their parent goals.
                    self.assertIn("FORK", page.locator('.tree-row[data-id="n0"]').inner_text())
                    self.assertIn("FORK", page.locator('.tree-row[data-id="g0"]').inner_text())
                    self.assertIn("FORK", page.locator('.tree-row[data-id="n4"]').inner_text())
                    self.assertIn("uncertain", page.locator('.tree-row[data-id="n3"]').inner_text().lower())
                    # Filter by fork_running keeps confirmed running items + ancestors only.
                    page.locator("#status-filter").select_option("fork_running")
                    visible_ids = page.locator("#work-tree .tree-row").evaluate_all("rows => rows.map(r => r.dataset.id)")
                    self.assertIn("n0", visible_ids)
                    self.assertIn("n4", visible_ids)
                    self.assertNotIn("n5", visible_ids)
                    self.assertNotIn("n3", visible_ids)
                    page.locator("#status-filter").select_option("")
                    # Selecting a running item shows its live fork card inside the Node inspector.
                    page.locator('.tree-row[data-id="n0"]').click()
                    self.assertIn("work-n0", page.locator("#panel-node").inner_text())
                    self.assertIn("P0", page.locator("#panel-node").inner_text())
                    evidence_dir = os.environ.get("CIEL_TEST_EVIDENCE_DIR")
                    if evidence_dir:
                        page.screenshot(path=os.path.join(evidence_dir, f"p0p5-board-{label}.png"),
                                        full_page=label == "mobile")
                    # Switch to Parallel tab: P0 card carries P0 chip (not P1), P0-P5 matrix is visible at top.
                    page.get_by_role("tab", name="Parallel").click()
                    page.locator('[data-group="confirmed"] .fork-card').first.wait_for()
                    p0_card = page.locator('[data-group="confirmed"] .fork-card[data-item="n0"]')
                    self.assertEqual("P0", p0_card.locator(".priority-chip").inner_text().strip())
                    if evidence_dir:
                        page.screenshot(path=os.path.join(evidence_dir, f"p0p5-parallel-{label}.png"),
                                        full_page=label == "mobile")
                    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
                    self.assertLessEqual(overflow, 0)
                    page.close()
            browser.close()

    @unittest.skipUnless(os.environ.get("CIEL_TEST_BROWSER"), "opt-in browser verification")
    def test_main_review_browser_approve_and_rework_controls(self):
        from playwright.sync_api import sync_playwright
        html = self.html
        empty = {name: [] for name in ("confirmed", "waiting", "reservation", "uncertain", "validating", "ended")}
        def row(lease, item, title, state, decision=None, **extra):
            return {"lease_id": lease, "item_id": item, "item_title": title, "priority": "P1", "state": state,
                    "decision": decision, "item_path": ["Checkout", title], "artifact": "a" * 64,
                    "completion_receipt": "d" * 64, "summary": "Retry backoff implemented <b>safely</b>",
                    "coverage": [{"item_id": item, "verified": True}], "verification": "pytest: 12 passed",
                    "workspace": {"branch": "work/" + item, "head": "abc1234def", "dirty": False,
                                  "changed_files": ["src/retry.py", "tests/test_retry.py"], "changed_count": 2,
                                  "diff_stat": "2 files changed, 40 insertions(+)"}, **extra}
        parallel = {"schema_version": 2, "version": 7, "review_version": 4, "policy": {"enabled": True, "paused": False},
                    "forks": {"schema": "native-fork-evidence/1", "available": True, "confirmed_count": 0, **empty},
                    "device": {"active_slots": 0, "capacity": 2, "priority_limits": {"P1": 2}},
                    "parallel_reviews": [row("L1", "n229", "Add retry backoff", "pending", changed_before_bind=False,
                                             review_pin="rp1:" + "ab" * 32),
                                         row("L2", "n230", "Payment webhook", "rework_ready", "rework",
                                             evidence="missing idempotency test", changed_before_bind="unknown"),
                                         row("L3", "n231", "Receipt email", "deciding", "approve",
                                             evidence="verified", changed_before_bind=True)]}
        board = {"session_id": "claude:fixture", "version": 1, "nodes": [], "phase": "executing"}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ["CIEL_TEST_BROWSER"], headless=True)
            for label, viewport in (("mobile", {"width": 390, "height": 1100}),
                                    ("desktop", {"width": 1440, "height": 1000})):
                with self.subTest(viewport=label):
                    posted = []
                    page = browser.new_page(viewport=viewport)
                    # One argument: Playwright passes (route, request) to a two-parameter handler.
                    # Pages run one at a time, so the loop's current `posted` is the right list.
                    def route(request):
                        url = request.request.url
                        if url.endswith("/ca/work/ui"):
                            request.fulfill(content_type="text/html", body=html)
                        elif "/events" in url:
                            request.fulfill(content_type="text/event-stream", body=": ready\n\n")
                        elif "/commands/parallel-item-review" in url:
                            posted.append(json.loads(request.request.post_data))
                            request.fulfill(json={"ok": True, "result": {"state": "finalized"}})
                        elif "/parallel" in url:
                            request.fulfill(json=parallel)
                        elif "/board" in url:
                            request.fulfill(json=board)
                        else:
                            request.fulfill(json={})
                    page.route("http://fixture.test/**", route)
                    page.goto("http://fixture.test/ca/work/ui")
                    page.get_by_role("tab", name="Parallel").click()
                    section = page.locator('[data-group="main-review"]')
                    section.locator(".review-card").first.wait_for()
                    self.assertIn("Main review (3)", section.locator("h3").inner_text())
                    labels = section.locator(".review-card .state-chip").all_inner_texts()
                    self.assertEqual(["Awaiting main review", "Rework", "Finalizing"], labels)
                    self.assertEqual(0, section.locator(".review-card b").count())
                    self.assertIn("<b>safely</b>", section.locator(".review-card").first.inner_text())
                    cards = section.locator(".review-card")
                    self.assertIn("matched the recorded handoff baseline", cards.nth(0).inner_text())
                    self.assertIn("interrupted-attempt changes cannot be excluded", cards.nth(1).inner_text())
                    self.assertIn("retained content changed", cards.nth(2).inner_text())
                    # Only the undecided result offers decisions.
                    self.assertEqual(1, section.get_by_role("button", name="Approve").count())
                    self.assertEqual(1, section.get_by_role("button", name="Rework…").count())
                    evidence_dir = os.environ.get("CIEL_TEST_EVIDENCE_DIR")
                    if evidence_dir:
                        page.screenshot(path=os.path.join(evidence_dir, "main-review-" + label + ".png"),
                                        full_page=label == "mobile")
                    section.get_by_role("button", name="Rework…").click()
                    section.get_by_role("textbox", name="Required rework").fill("Add the jitter unit test")
                    section.get_by_role("button", name="Save").click()
                    page.wait_for_function("() => document.querySelectorAll('.toast').length > 0")
                    section.get_by_role("button", name="Approve").click()
                    page.get_by_role("button", name="Confirm").click()
                    page.wait_for_timeout(300)
                    pin = "rp1:" + "ab" * 32
                    self.assertEqual([("rework", "Add the jitter unit test", 4, pin), ("approve", "", 4, pin)],
                                     [(p["decision"], p["evidence"], p["expected_finalization_version"],
                                       p.get("expected_review_pin")) for p in posted])
                    self.assertTrue(all(p["lease_id"] == "L1" and p["operation_id"] for p in posted))
                    overflow = page.evaluate(
                        "document.documentElement.scrollWidth - document.documentElement.clientWidth")
                    self.assertLessEqual(overflow, 0)
                    page.close()
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
