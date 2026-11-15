"""Owned four-surface check of the idle gate re-check interval (work_gate_check_minutes).

Real code paths in a fresh isolated CIEL_RUNTIME_CONFIG_DIR: the shared
settings projection, the real Work HTTP routes and web console served over a
socket (Chromium desktop and 390 px), REST GET/POST, ciel-runtimectl ``work``
(in-process, as the installed command runs it) and the prelaunch menu rows.
Nothing touches the live runtime.

It also queues one real gate re-check through the MCP dispatch the ciel-work
timer calls.

usage: python gate_check_surfaces.py <repo root> <evidence dir>
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
CHILD_ID = next(n["id"] for n in board_store.read()["nodes"] if n.get("parent_id") == ROOT_ID)
board_store.transaction(lambda d: tree.update_node(d, CHILD_ID, waiting_on="CI run 4242 green"))


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
HOST_TOKEN = uuid.uuid4().hex + uuid.uuid4().hex
CONFIG_VALUES["work_parallel_host"] = {"token": HOST_TOKEN}
KEY = "work_gate_check_minutes"


def row_of(view):
    return next((row for row in view.get("settings", []) if row["key"] == KEY), {})


# --- 1. REST ------------------------------------------------------------------------
status, view = rest("settings")
row = row_of(view)
fact("REST lists the gate re-check row (default 5, number)", (row.get("value"), row.get("kind")) == (5, "number"),
     {k: row.get(k) for k in ("label", "value", "kind", "status")})
fact("REST label is Korean", row.get("label") == "게이트 재확인 주기 (분, 0 = 끔)", row.get("label"))
status, done = rest("commands/settings-update", {"key": KEY, "value": "10",
                                                 "expected_settings_fingerprint": view["settings_fingerprint"]},
                    key="set-10")
fact("REST sets 10 minutes", status == 200 and CONFIG_VALUES.get("work_gate_check") == {"minutes": 10},
     {"status": status, "stored": CONFIG_VALUES.get("work_gate_check")})
status, bad = rest("commands/settings-update", {"key": KEY, "value": "2000",
                                                "expected_settings_fingerprint": rest("settings")[1]["settings_fingerprint"]},
                   key="set-2000")
fact("REST refuses 2000 and keeps 10", status >= 400 and CONFIG_VALUES.get("work_gate_check") == {"minutes": 10},
     {"status": status, "error": bad.get("error"), "stored": CONFIG_VALUES.get("work_gate_check")})

# --- 2. ciel-runtimectl work --------------------------------------------------------
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


code, lines = ctl("list")
gate_lines = [line for line in lines if line.split(" = ")[0] == KEY]
fact("ctl lists the gate re-check row", code == 0 and bool(gate_lines) and gate_lines[0].startswith(KEY + " = 10"),
     gate_lines)
(OUT / "ctl-list.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
code, lines = ctl("set", KEY, "15")
fact("ctl sets 15 minutes", code == 0 and CONFIG_VALUES.get("work_gate_check") == {"minutes": 15},
     {"code": code, "lines": lines[:3], "stored": CONFIG_VALUES.get("work_gate_check")})

# --- 3. prelaunch menu --------------------------------------------------------------
from ciel_runtime_support.work_settings_menu import NUMBER_RANGES, choices, description  # noqa: E402
labels, values = choices(load(), KEY)
fact("menu offers 0(off)/1/2/5/10/15/30/60 plus Edit",
     values == ["0", "1", "2", "5", "10", "15", "30", "60", "__edit__", "back"], values)
fact("menu range is 0-1440", NUMBER_RANGES.get(KEY) == (0, 1440), NUMBER_RANGES.get(KEY))
text = description(load(), KEY)
fact("menu describes the check in Korean", "사이드 액터" in text and "0이면 끕니다" in text, text[:160])


# --- 4. web console in Chromium (desktop and 390 px) ---------------------------------
def shoot(name, width, new_value=None):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True,
                                    executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe")
        page = browser.new_page(viewport={"width": width, "height": 1000})
        page.goto(BASE + "/ca/work/ui" + Q)
        page.get_by_role("tab", name="Settings").click()
        control = page.locator("#setting-" + KEY)
        control.wait_for(timeout=15000)
        shown = control.input_value()
        control.scroll_into_view_if_needed()
        page.screenshot(path=str(OUT / (name + "-1-row.png")), full_page=False)
        after = None
        if new_value is not None:
            control.fill(new_value)
            control.locator("xpath=..").get_by_role("button", name="Apply").click()
            page.wait_for_function("v => document.querySelector('#setting-" + KEY + "')?.dataset.original === v",
                                   arg=new_value, timeout=15000)
            page.locator("#setting-" + KEY).scroll_into_view_if_needed()
            page.screenshot(path=str(OUT / (name + "-2-applied.png")), full_page=False)
            after = page.locator("#setting-" + KEY).input_value()
        overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
        browser.close()
    return shown, after, overflow


shown, after, overflow = shoot("web-desktop", 1280, "5")
fact("web Settings shows the stored 15 minutes", shown == "15", shown)
fact("web Apply 5 stores the default (no override)", after == "5" and "work_gate_check" not in CONFIG_VALUES,
     {"after": after, "stored": CONFIG_VALUES.get("work_gate_check")})
shown, _, overflow = shoot("web-390", 390)
fact("web 390px shows the row without horizontal scroll", shown == "5" and not overflow,
     {"shown": shown, "overflow": overflow})

# --- 5. the router call the ciel-work timer makes ------------------------------------
from ciel_runtime_support.work_mcp_tools import dispatch_work_tool  # noqa: E402
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox  # noqa: E402


def gate_call(**args):
    result = dispatch_work_tool(1, "work_side_gate_check", {"idle": True, "forks_running": 0, **args,
                                                            "session_id": IDENTITY, "_host": {"token": HOST_TOKEN}},
                                load_config=load, state_dir=STATE, save_config=save,
                                respond=lambda _id, text, error=False: {"text": text, "error": error})
    return json.loads(result["text"]) if not result["error"] else {"error": result["text"]}


busy = gate_call(forks_running=1)
fact("a running Work fork holds the check", busy.get("reason") == "not_idle", busy)
queued = gate_call()
fact("idle main + no fork queues one side event for the waiting item",
     queued.get("queued") is True and queued.get("gates") == [CHILD_ID], queued)
row = SideIntakeInbox(STATE, IDENTITY)._load(queued.get("receipt_id") or "0" * 64)
fact("the event is a durable gate-check receipt with no source to reply to",
     bool(row) and row["event_key"].startswith("gate-check:") and row["route"] == {"source_kind": "gate_check"},
     {"event_key": (row or {}).get("event_key"), "route": (row or {}).get("route")})
(OUT / "gate-check-event.txt").write_text((row or {}).get("text", ""), encoding="utf-8")
again = gate_call()
fact("a second tick inside the interval queues nothing", again.get("reason") == "not_due", again)

server.shutdown()
(OUT / "surfaces-result.json").write_text(json.dumps({"facts": facts, "passed": all(f["ok"] for f in facts)},
                                                     ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"passed": all(f["ok"] for f in facts), "failed": [f["fact"] for f in facts if not f["ok"]]},
                 ensure_ascii=False))
