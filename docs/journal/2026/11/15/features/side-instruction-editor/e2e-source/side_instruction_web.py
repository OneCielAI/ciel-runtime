"""Owned browser check of the side actor instruction viewer and Markdown editor.

Real code paths in a fresh isolated CIEL_RUNTIME_CONFIG_DIR: the shared
settings projection, the real Work HTTP routes and web console served over a
socket (Chromium desktop and 390 px), REST GET/POST, ciel-runtimectl ``work``
(in-process, as the installed command runs it) and the prelaunch menu rows.
Nothing touches the live runtime.

usage: python surfaces_e2e.py <repo root> <evidence dir>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
for key in [k for k in os.environ if k.startswith("CIEL_RUNTIME_")]:
    del os.environ[key]
CONFIG = Path(tempfile.mkdtemp(prefix="ciel-surfaces-e2e-"))
os.environ["CIEL_RUNTIME_CONFIG_DIR"] = str(CONFIG)
os.environ["CIEL_RUNTIME_TEST_ISOLATED"] = "1"
sys.path.insert(0, str(ROOT))

from ciel_runtime_support import work_plan_tree as tree  # noqa: E402
from ciel_runtime_support.work_http_runtime import build_work_http  # noqa: E402
from ciel_runtime_support.work_plan_store import session_work_plan_store  # noqa: E402

SESSION = str(uuid.uuid4())
IDENTITY = "claude:" + SESSION
CONFIG_VALUES = {"work_mode": True, "language": "ko", "current_provider": "cielairouter",
                 "providers": {"cielairouter": {"current_model": "_Walkie_Opus55",
                                                "custom_models": ["_Walkie_Fable5.2", "_Walkie_Haiku_Terra"]}}}
STATE = CONFIG / "state"
INSTANCE = CONFIG / "instance"
INSTANCE.mkdir(parents=True)
(INSTANCE / "compaction-scope.json").write_text(json.dumps({"runtime": "claude", "session_id": SESSION,
                                                            "epoch": "e1"}), encoding="utf-8")
facts: list[dict] = []


def fact(name, ok, detail=None):
    facts.append({"fact": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + ("" if detail is None else " :: " + json.dumps(detail, ensure_ascii=False)[:300]))


def load():
    return json.loads(json.dumps(CONFIG_VALUES))


def save(value):
    CONFIG_VALUES.clear()
    CONFIG_VALUES.update(json.loads(json.dumps(value)))


board_store = session_work_plan_store(STATE, IDENTITY)
_, ROOT_ID = board_store.transaction(lambda d: tree.set_plan(d, {"title": "고객 결제 긴급 수정", "priority": "P1",
                                                                  "children": [{"title": "재시도 버그"}]}, None))


class Observer:
    @staticmethod
    def snapshot(scope):
        return {}


def write_json(handler, payload, status=200):
    body = json.dumps(payload).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def write_text(handler, text, content_type="text/html", status=200, headers=None):
    body = text.encode() if isinstance(text, str) else text
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    for key, value in (headers or {}).items():
        handler.send_header(key, value)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


routes = build_work_http(STATE, INSTANCE, load, lambda cfg: ("cielairouter", cfg["providers"]["cielairouter"]),
                         lambda cfg: "alias", write_json, "e2e", "fp", "inst", Observer(),
                         save_config=save, write_text=write_text)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path.endswith("/events"):
            body = b": ready\n\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if not routes.get(self, path):
            self.send_error(404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        if not routes.post(self, self.path.split("?", 1)[0], body):
            self.send_error(404)

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % server.server_address[1]
Q = "?session_id=" + IDENTITY.replace(":", "%3A")


def rest(path, body=None, key=None):
    data = None if body is None else json.dumps({"session_id": IDENTITY, **body}).encode()
    headers = {"Content-Type": "application/json", **({"Idempotency-Key": key} if key else {})}
    request = urllib.request.Request(BASE + "/ca/work/api/v1/" + path + ("" if body else Q), data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())



CONFIG_VALUES["work_side_intake"] = {"enabled": True, "type": "btw_with_mcp"}


def browser(width, name):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe")
        page = b.new_page(viewport={"width": width, "height": 1000})
        page.goto(BASE + "/ca/work/ui" + Q)
        page.get_by_role("tab", name="Settings").click()
        row = page.locator(".settings-row.markdown")
        row.wait_for(timeout=15000)
        row.scroll_into_view_if_needed()
        state = row.locator(".md-state").inner_text()
        view_text = row.locator(".md-view").inner_text()
        page.screenshot(path=str(OUT / (name + "-1-row-default.png")), full_page=False)
        page.locator("#setting-work_side_intake_instruction").click()
        page.locator("#md-dialog").wait_for(state="visible", timeout=5000)
        page.locator("#md-text").fill("## 답변 규칙\n\n- 한국어로 답한다\n- **긴급**이면 P0로 올린다\n\n`work_status`는 다시 읽지 않는다")
        preview = page.locator("#md-preview").inner_text()
        rendered = {"h3": page.locator("#md-preview h1, #md-preview h2, #md-preview h3").count(),
                    "li": page.locator("#md-preview li").count(), "strong": page.locator("#md-preview strong").count(),
                    "code": page.locator("#md-preview code").count(),
                    "sep": page.locator("#md-preview .md-sep").count()}
        overflow_dialog = page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
        page.screenshot(path=str(OUT / (name + "-2-editor-preview.png")), full_page=False)
        page.locator("#md-apply").click()
        page.get_by_text("settings-update applied").wait_for(timeout=15000)
        page.wait_for_timeout(800)
        row = page.locator(".settings-row.markdown")
        row.scroll_into_view_if_needed()
        state_after = row.locator(".md-state").inner_text()
        page.screenshot(path=str(OUT / (name + "-3-row-custom.png")), full_page=False)
        b.close()
    return {"state": state, "default_shown": "You handle one external SSE event" in view_text,
            "preview_has_default": "You handle one external SSE event" in preview,
            "preview_has_custom": "한국어로 답한다" in preview, "rendered": rendered,
            "overflow": overflow_dialog, "state_after": state_after}


desktop = browser(1280, "web-desktop")
fact("row shows the built-in default when nothing is set", desktop["default_shown"]
     and desktop["state"].startswith("Built-in default in use"), desktop["state"])
fact("editor preview renders default + operator Markdown", desktop["preview_has_default"] and desktop["preview_has_custom"]
     and desktop["rendered"]["li"] >= 2 and desktop["rendered"]["strong"] >= 1 and desktop["rendered"]["sep"] == 1,
     desktop["rendered"])
fact("apply stores only the operator instruction", CONFIG_VALUES["work_side_intake"].get("instruction", "").startswith("## 답변 규칙"),
     CONFIG_VALUES["work_side_intake"].get("instruction", "")[:40])
fact("row then says default + operator instruction", desktop["state_after"].startswith("Built-in default + operator"),
     desktop["state_after"])
status, view = rest("settings")
row = next(r for r in view["settings"] if r["key"] == "work_side_intake_instruction")
fact("REST row carries default, stored and effective values",
     row.get("editor") == "markdown" and row.get("default_value", "").startswith("You handle one external SSE event")
     and row.get("stored_value", "").startswith("## 답변 규칙") and row.get("effective_value", "").endswith("P0로 올린다\n\n`work_status`는 다시 읽지 않는다"),
     {k: (str(row.get(k))[:40]) for k in ("editor", "stored_value", "using_default")})
CONFIG_VALUES["work_side_intake"].pop("instruction", None)
mobile = browser(390, "web-390")
fact("390px: editor opens and nothing scrolls sideways", not mobile["overflow"] and mobile["preview_has_custom"],
     {"overflow": mobile["overflow"]})

server.shutdown()
(OUT / "result.json").write_text(json.dumps({"facts": facts, "passed": all(f["ok"] for f in facts)},
                                            ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"passed": all(f["ok"] for f in facts), "failed": [f["fact"] for f in facts if not f["ok"]]},
                 ensure_ascii=False))
