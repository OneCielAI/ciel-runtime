"""Render the real TUI factory against owned synthetic partial history."""
import os
for key in tuple(os.environ):
    if key.startswith(("CIEL_RUNTIME_", "ANTHROPIC", "CLAUDE", "CODEX_", "OPENAI_")):
        os.environ.pop(key, None)
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
from unittest.mock import patch
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).resolve().parents[8]
parser = argparse.ArgumentParser()
parser.add_argument("--runtime", default=str(ROOT))
parser.add_argument("--label", default="source")
args = parser.parse_args()
OUT = Path(__file__).resolve().parent
with tempfile.TemporaryDirectory(prefix="ciel-tui-browser-") as state:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    # The browser keeps the host profile environment (Edge refuses to start
    # with a redirected USERPROFILE); only the runtime import gets temp HOME.
    browser_env = dict(os.environ)
    home = Path(state) / "home"
    (home / "AppData/Roaming").mkdir(parents=True)
    (home / "AppData/Local").mkdir(parents=True)
    os.environ.update(HOME=str(home), USERPROFILE=str(home), APPDATA=str(home / "AppData/Roaming"), LOCALAPPDATA=str(home / "AppData/Local"), CIEL_RUNTIME_CONFIG_DIR=state, CIEL_RUNTIME_ROUTER_PORT=str(port), CIEL_RUNTIME_TEST_ISOLATED="1")
    sys.path.insert(0, str(Path(args.runtime).resolve()))
    import ciel_runtime as runtime
    from ciel_runtime_support.runtime_input_status import RuntimeInputStatusRepository
    from ciel_runtime_support.tui_observation import TuiObservationBus
    root = Path(state)
    status = root / "runtime-input-status.jsonl"
    inputs = root / "runtime-inputs.jsonl"
    # Line 2 is unrecoverable damage; line 3 is a complete row after a NUL prefix.
    status.write_bytes(b'{"request_id":1,"status":"queued","updated_at_epoch":100}'+bytes([10])
                       +bytes([0])+b'owned corrupt fixture'+bytes([10])
                       +bytes([0, 0])+b'{"request_id":2,"status":"queued","updated_at_epoch":110}'+bytes([10]))
    inputs.write_text(json.dumps({"id":1,"created_at_epoch":90,"channel":"external:default","message":"PRIVATE_PAYLOAD"})+chr(10), encoding="utf-8")
    before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (status, inputs)]
    repository = RuntimeInputStatusRepository(status, lambda **_kw: None, lambda *_a: None, threading.RLock())
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if not runtime.handle_tui_observation_get(self, self.path.split("?")[0], {}):
                self.send_error(404)
        def log_message(self, *_args):
            pass
    with patch.object(runtime, "_RUNTIME_INPUT_STATUS_REPOSITORY", repository), patch.object(runtime, "TUI_OBSERVATION_BUS", TuiObservationBus(enabled=True, capacity=20)):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(channel="msedge", headless=True, env=browser_env)
                page = browser.new_page(viewport={"width":1280,"height":720})
                page.goto(f"http://127.0.0.1:{server.server_port}/ca/tui", wait_until="domcontentloaded")
                page.wait_for_function("document.getElementById('state').textContent.includes('not_blocked')")
                text = page.locator("#state").inner_text()
                assert "not_blocked" in text and "known queued 2" in text
                assert "history_observation_partial line 2" in text
                assert "admission_blocked" not in text
                assert "PRIVATE_PAYLOAD" not in page.locator("body").inner_text()
                page.screenshot(path=str(OUT / (args.label+"-tui-not-blocked.png")), full_page=True)
                api_page = browser.new_page(viewport={"width":1280,"height":1000})
                api_page.goto(f"http://127.0.0.1:{server.server_port}/ca/tui/status")
                api_text = api_page.locator("body").inner_text()
                body = json.loads(api_text)
                delivery = body["delivery"]
                assert delivery["state"] == "not_blocked"
                assert delivery["admission_blocked"] is False
                assert delivery["queued_coverage"] == "partial"
                assert delivery["reason"] == "history_observation_partial"
                assert delivery["history"]["coverage"] == "partial"
                assert delivery["line_number"] == 2
                assert delivery["history"]["recovered_count"] == 1
                assert delivery["known_queued_count"] == 2
                assert "PRIVATE_PAYLOAD" not in api_text
                api_page.screenshot(path=str(OUT / (args.label+"-tui-status-partial.png")), full_page=True)
                browser.close()
            after = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (status, inputs)]
            assert before == after
            result = {"passed":True,"status_text":text,"source_unchanged":True,"runtime":str(Path(runtime.__file__).resolve()),"screenshot":args.label+"-tui-not-blocked.png"}
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)
            assert not thread.is_alive()
    result["owned_server_closed"] = True
    result["delivery"] = delivery
    from ciel_runtime_support.runtime_input_delivery_health import (
        delivery_health_text, runtime_input_delivery_health)
    # Same text the receiver menu "Session delivery" row renders.
    result["menu_row_text"] = delivery_health_text(delivery)
    real_open = Path.open
    def denied(path, *a, **kw):
        if Path(path) == status:
            raise PermissionError("owned fixture read denied")
        return real_open(path, *a, **kw)
    with patch.object(Path, "open", denied):
        unreadable = runtime_input_delivery_health(RuntimeInputStatusRepository(
            status, lambda **_kw: None, lambda *_a: None, threading.RLock()), receiver_id="unreadable-case")
    assert unreadable["state"] == "unknown" and unreadable["admission_blocked"] is None
    result["unreadable_case"] = {"delivery": unreadable, "menu_row_text": delivery_health_text(unreadable)}
    result["api_screenshot"] = args.label+"-tui-status-partial.png"
    (OUT / (args.label+"-tui-browser.json")).write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result,indent=2))
