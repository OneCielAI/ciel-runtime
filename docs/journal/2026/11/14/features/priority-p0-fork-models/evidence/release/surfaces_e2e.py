"""Owned four-surface check of the P0-P5/side fork models and P0 priority.

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


MODEL_KEYS = [f"work_parallel_model_p{n}" for n in range(6)] + ["work_side_intake_model"]

# --- 1. REST: settings rows, then set P0 and side models -----------------------
status, view = rest("settings")
rows = {row["key"]: row for row in view["settings"]}
fact("REST lists all seven model rows", all(key in rows for key in MODEL_KEYS), [k for k in MODEL_KEYS if k not in rows])
fact("REST choices are inherit + current provider models",
     rows["work_parallel_model_p0"]["choices"] == ["inherit", "_Walkie_Opus55", "_Walkie_Fable5.2", "_Walkie_Haiku_Terra"],
     rows["work_parallel_model_p0"]["choices"])
for key, value in (("work_parallel_model_p0", "_Walkie_Fable5.2"), ("work_side_intake_model", "_Walkie_Haiku_Terra")):
    status, done = rest("commands/settings-update", {"key": key, "value": value,
                                                     "expected_settings_fingerprint": rest("settings")[1]["settings_fingerprint"]},
                        key="set-" + key)
    fact("REST sets " + key, status == 200, done.get("error"))
status, refused = rest("commands/settings-update", {"key": "work_parallel_model_p3", "value": "not-a-model",
                                                    "expected_settings_fingerprint": rest("settings")[1]["settings_fingerprint"]},
                       key="bad-model")
fact("REST refuses a model outside the provider list", status == 422, refused.get("message"))
fact("config stores per-provider table",
     CONFIG_VALUES.get("work_parallel_models") == {"cielairouter": {"P0": "_Walkie_Fable5.2", "side": "_Walkie_Haiku_Terra"}},
     CONFIG_VALUES.get("work_parallel_models"))

# --- 2. REST: existing-root P0 priority ----------------------------------------
version = rest("board")[1]["version"]
status, p0 = rest("commands/node-update", {"expected_version": version, "id": ROOT_ID, "priority": "P0"}, key="p0-root")
fact("REST activates existing-root P0", status == 200 and p0.get("result", {}).get("effective_priority") == "P0", p0)
status, parallel = rest("parallel")
fact("REST parallel shows effective P0 and fork models",
     any(r["effective_priority"] == "P0" for r in parallel.get("root_priorities", []))
     and parallel["fork_models"]["ids"]["P0"] == "_Walkie_Fable5.2",
     {"roots": parallel.get("root_priorities"), "ids": parallel.get("fork_models", {}).get("ids")})
(OUT / "rest-parallel.json").write_text(json.dumps({k: parallel.get(k) for k in ("root_priorities", "fork_models")},
                                                   ensure_ascii=False, indent=2), encoding="utf-8")

# --- 3. ciel-runtimectl work (in-process command, the installed entry) ---------
from ciel_runtime_support.work_settings_cli import command  # noqa: E402
import ciel_runtime_support.work_ctl as work_ctl  # noqa: E402
work_ctl.state_dir = lambda: STATE


def ctl(action, key=None, value=None, **extra):
    base = dict(action=action, key=key, value=value, json=False, session=None, confirm=False, expected_version=None,
                expected_finalization_version=None, preview_digest=None, attempt_ref=None,
                expected_settings_fingerprint=None, revision=None, clear_wait=[], activate=False, operation_id=None)
    base.update(extra)
    lines = []
    try:
        command(load, save, lines.append)(argparse.Namespace(**base))
        code = 0
    except SystemExit as stop:
        code = stop.code
    return code, lines


code, lines = ctl("set", "work_parallel_model_p2", "_Walkie_Opus55")
fact("ctl sets P2 model", code == 0 and any(line.startswith("work_parallel_model_p2 = _Walkie_Opus55") for line in lines),
     lines[:2])
code, lines = ctl("list")
model_lines = [line for line in lines if line.split(" = ")[0] in MODEL_KEYS]
fact("ctl lists the seven model rows", code == 0 and len(model_lines) == 7, model_lines)
(OUT / "ctl-list.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

# --- 4. prelaunch menu rows (the same projection the terminal menu draws) ------
from ciel_runtime_support.work_settings_menu import choices, description  # noqa: E402
labels, values = choices(load(), "work_parallel_model_p0")
fact("menu offers inherit + provider models for P0", values[:4] == ["inherit", "_Walkie_Opus55", "_Walkie_Fable5.2",
                                                                    "_Walkie_Haiku_Terra"], values)
fact("menu describes the side model in Korean", "SSE 사이드 포크" in description(load(), "work_side_intake_model"))

# --- 5. web console in Chromium (desktop and 390 px) ----------------------------
def shoot(name, width):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe")
        page = browser.new_page(viewport={"width": width, "height": 1000})
        page.goto(BASE + "/ca/work/ui" + Q)
        page.get_by_role("tab", name="Settings").click()
        page.locator("#setting-work_parallel_model_p0").wait_for(timeout=15000)
        selected = page.locator("#setting-work_parallel_model_p0").input_value()
        options = page.locator("#setting-work_parallel_model_p0 option").all_inner_texts()
        page.locator("#setting-work_parallel_model_p0").scroll_into_view_if_needed()
        page.screenshot(path=str(OUT / (name + "-settings.png")), full_page=False)
        overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
        page.get_by_role("tab", name="Parallel").click()
        page.get_by_text("Goal priorities").wait_for(timeout=15000)
        priorities = page.get_by_text("Goal priorities").locator("xpath=..").inner_text()
        page.get_by_text("Goal priorities").scroll_into_view_if_needed()
        page.screenshot(path=str(OUT / (name + "-parallel.png")), full_page=False)
        browser.close()
    return selected, options, overflow, priorities


selected, options, overflow, priorities = shoot("web-desktop", 1280)
fact("web Settings shows the saved P0 model", selected == "_Walkie_Fable5.2", selected)
fact("web P0 choices are inherit + provider models", options == ["inherit", "_Walkie_Opus55", "_Walkie_Fable5.2",
                                                                  "_Walkie_Haiku_Terra"], options)
fact("web Parallel shows the P0 goal", "P0" in priorities and "고객 결제 긴급 수정" in priorities, priorities[:200])
selected, options, overflow, priorities = shoot("web-390", 390)
fact("web 390px shows the P0 model row without horizontal scroll", selected == "_Walkie_Fable5.2" and not overflow,
     {"selected": selected, "overflow": overflow})

server.shutdown()
(OUT / "surfaces-result.json").write_text(json.dumps({"facts": facts, "passed": all(f["ok"] for f in facts)},
                                                     ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"passed": all(f["ok"] for f in facts), "failed": [f["fact"] for f in facts if not f["ok"]]},
                 ensure_ascii=False))
