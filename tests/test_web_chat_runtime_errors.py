import json
import os
from pathlib import Path
import unittest

from ciel_runtime_support.web_ui import render_web_chat_page


class WebChatRuntimeErrorsTests(unittest.TestCase):
    def test_error_subscription_is_separate_from_input_delivery(self):
        html = render_web_chat_page(model="fixture", provider="fixture", mode="native", api_status="ok", timeout_ms=1000, workspace="fixture", router_port=9000, instance_id="fixture")
        handler = html.split("function startRuntimeErrorStream()", 1)[1].split("async function sendMessage", 1)[0]
        self.assertIn("category=runtime.error", handler)
        self.assertIn("addBubble('system'", handler)
        self.assertIn("runtimeErrorIds.has(id)", handler)
        self.assertNotIn("fetch(", handler)

    @unittest.skipUnless(os.environ.get("CIEL_TEST_BROWSER"), "opt-in browser verification")
    def test_browser_renders_error_once_without_posting_it(self):
        from playwright.sync_api import sync_playwright
        html = render_web_chat_page(model="fixture", provider="fixture", mode="native", api_status="ok", timeout_ms=1000, workspace="fixture", router_port=9000, instance_id="fixture")
        event = {"id": 1, "category": "runtime.error", "provider": "fixture", "message": "Usage limit reached. <script>not executable</script>", "data": {"retrying": False}}
        posts = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ["CIEL_TEST_BROWSER"], headless=True)
            page = browser.new_page(viewport={"width": 1100, "height": 800})
            def route(request):
                url = request.request.url
                if request.request.method == "POST":
                    posts.append(url)
                if url.endswith("/ca/web/chat"):
                    request.fulfill(content_type="text/html", body=html)
                elif "/health" in url:
                    request.fulfill(json={"ok": True, "instance_id": "fixture", "workspace": "fixture", "router_port": 9000})
                elif "/ca/events/stream" in url:
                    frame = "event: event\ndata: " + json.dumps(event) + "\n\n"
                    request.fulfill(content_type="text/event-stream", body=frame + frame)
                elif "/ca/channel/stream" in url:
                    request.fulfill(content_type="text/event-stream", body=": keepalive\n\n")
                else:
                    request.fulfill(json={"ok": True, "messages": [], "asr": {"enabled": False}, "tts": {"enabled": False}})
            page.route("http://fixture.test/**", route)
            page.goto("http://fixture.test/ca/web/chat")
            bubble = page.locator(".row.system .bubble").filter(has_text="Usage limit reached")
            bubble.first.wait_for()
            self.assertEqual(1, bubble.count())
            self.assertIn("<script>not executable</script>", bubble.inner_text())
            self.assertFalse(posts, posts)
            evidence = os.environ.get("CIEL_TEST_SCREENSHOT")
            if evidence:
                page.screenshot(path=str(Path(evidence).resolve()), full_page=True)
            print("Browser verified: error bubble=1, duplicate ignored, outgoing POST=0")
            browser.close()


if __name__ == "__main__":
    unittest.main()
