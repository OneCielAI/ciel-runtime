import re
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
