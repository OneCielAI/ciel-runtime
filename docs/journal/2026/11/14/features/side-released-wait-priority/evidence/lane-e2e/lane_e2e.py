"""Owned end-to-end check of the released-wait lane on the real backend.

Real code paths, no fixtures: the session Work board store, the SSE side inbox
with a real claim and ``work_side_resolve`` (board release + sidecar row), the
real ``work_parallel_event`` reserve / bind / question / wait / reacquire, and
the real Work HTTP routes served over a socket to the real web console in
Chromium. Only the native fork and the side AI are absent: the side decision
JSON is supplied as the actor would return it.

Everything lives in a fresh temporary CIEL_RUNTIME_CONFIG_DIR; every inherited
CIEL_RUNTIME_* variable is removed first. Nothing touches the live runtime.

usage: python lane_e2e.py <repo root> <evidence dir>
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
for key in [k for k in os.environ if k.startswith("CIEL_RUNTIME_")]:
    del os.environ[key]
CONFIG = Path(tempfile.mkdtemp(prefix="ciel-lane-e2e-"))
os.environ["CIEL_RUNTIME_CONFIG_DIR"] = str(CONFIG)
os.environ["CIEL_RUNTIME_TEST_ISOLATED"] = "1"
sys.path.insert(0, str(ROOT))

from ciel_runtime_support import work_parallel_mcp as mcp  # noqa: E402
from ciel_runtime_support import work_plan_tree as tree  # noqa: E402
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox  # noqa: E402
from ciel_runtime_support.side_intake_mcp import run as side_run  # noqa: E402
from ciel_runtime_support.work_http_runtime import build_work_http  # noqa: E402
from ciel_runtime_support.work_parallel_wait_boost import boost_key  # noqa: E402
from ciel_runtime_support.work_plan_store import session_work_plan_store  # noqa: E402
from ciel_runtime_support.work_wait_release import sidecar_for  # noqa: E402

SESSION = str(uuid.uuid4())
IDENTITY = "claude:" + SESSION
TOKEN = "t" * 40
CONFIG_VALUES = {"work_mode": True, "work_side_intake": {"enabled": True},
                 "work_parallel_host": {"token": TOKEN, "capability_ready": True}}
STATE = CONFIG / "state"
INSTANCE = CONFIG / "instance"
INSTANCE.mkdir(parents=True)
(INSTANCE / "compaction-scope.json").write_text(json.dumps({"runtime": "claude", "session_id": SESSION,
                                                            "epoch": "e1"}), encoding="utf-8")
facts: list[dict] = []


def fact(name, ok, detail=None):
    facts.append({"fact": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + ("" if detail is None else " :: " + json.dumps(detail)[:300]))


board_store = session_work_plan_store(STATE, IDENTITY)


def board():
    return board_store.read()


def node(node_id):
    return next(n for n in board()["nodes"] if n["id"] == node_id)


def parallel(name, args=None, host=None):
    payload = dict(args or {})
    payload.setdefault("expected_version", mcp.ledger().read()["version"])
    if host is not None:
        payload["_host"] = {"token": TOKEN, **host}
    return json.loads(mcp.run("work_parallel_" + name, payload, board_store, IDENTITY, CONFIG_VALUES, None))


def side(name, args, host=None):
    payload = dict(args)
    if host is not None:
        payload["_host"] = {"token": TOKEN, **host}
    return side_run(name, payload, CONFIG_VALUES, STATE, IDENTITY, board_store)


def release(text, item_id, waiting_on):
    """One SSE event through the real inbox, claim and resolve."""
    inbox = SideIntakeInbox(STATE, IDENTITY)
    inbox.receive(text, "e2e-" + uuid.uuid4().hex[:8])
    claimed = side("work_side_claim", {"owner": "e2e-actor"}, host={})["claim"]
    decision = {"action": "update", "kind": "request", "item_id": item_id, "summary": "Andy approved: " + text,
                "reply": "Released.", "note": "approved via Walkie",
                "release_waits": [{"id": item_id, "waiting_on": waiting_on}]}
    resolved = side("work_side_resolve", {"decision": decision, "expected_version": board()["version"]},
                    host={"receipt_id": claimed["receipt_id"], "generation": claimed["generation"]})
    return claimed, resolved


# --- board: one executing P1 goal with three leaves (c newest) ---------------
def setup(doc):
    goal = tree.add_node(doc, None, "Ship checkout", priority="P1")
    a = tree.add_node(doc, goal, "Leaf A")
    b = tree.add_node(doc, goal, "Leaf B", waiting_on="approval from Andy via Walkie")
    c = tree.add_node(doc, goal, "Leaf C")
    return goal, a, b, c


_, (GOAL, A, B, C) = board_store.transaction(setup)
board_store.transaction(lambda d: tree.set_phase(d, tree.PHASE_EXECUTING, GOAL))


def stamp(doc):
    for n in doc["nodes"]:
        if n["id"] == B:
            n["status"] = "blocked"
        if n["id"] in (A, B, C):
            n["created_at"] = {A: 10.0, B: 20.0, C: 30.0}[n["id"]]
board_store.transaction(stamp)
fact("board root executing", tree.root_phase(board(), GOAL) == tree.PHASE_EXECUTING,
     tree.root_phase(board(), GOAL))
parallel("configure", {"changes": {"enabled": True, "launches_per_minute": 30,
                                   "priority_limits": {"P1": 2, "P2": 1, "P3": 1, "P4": 1, "P5": 1}}}, host={})

# --- HTTP: the real Work routes and console over a socket ---------------------
replies = {}


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


routes = build_work_http(STATE, INSTANCE, lambda: CONFIG_VALUES, lambda cfg: ("fixture", {}), lambda cfg: "alias",
                         write_json, "e2e", "fp", "inst", Observer(), write_text=write_text)


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
        with mcp_ledger_patch():
            if not routes.get(self, path):
                self.send_error(404)

    def log_message(self, *args):
        pass


class mcp_ledger_patch:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % server.server_address[1]


def rest_parallel():
    import urllib.request
    with urllib.request.urlopen(BASE + "/ca/work/api/v1/parallel?session_id=" + IDENTITY.replace(":", "%3A")) as r:
        return json.loads(r.read())


def shoot(name, width):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe")
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.goto(BASE + "/ca/work/ui?session_id=" + IDENTITY.replace(":", "%3A"))
        page.get_by_role("tab", name="Parallel").click()
        section = page.locator('[data-group="wait-boost"]')
        section.wait_for(timeout=15000)
        text = section.inner_text()
        section.scroll_into_view_if_needed()
        page.screenshot(path=str(OUT / (name + ".png")), full_page=True)
        section.screenshot(path=str(OUT / (name + "-section.png")))
        browser.close()
    return text


# --- 1. blocked waiting leaf B is released by an SSE event --------------------
claimed, resolved = release("Andy: approved, go ahead with B", B, "approval from Andy via Walkie")
result = resolved["result"]
fact("B released and unblocked", node(B)["status"] == "pending" and node(B)["waiting_on"] == "",
     {"status": node(B)["status"], "unblocked": result.get("unblocked_waits")})
row = sidecar_for(mcp.ledger().path).read()["rows"].get(boost_key(IDENTITY, B))
fact("sidecar row boosted for B", row is not None and row["state"] == "boosted",
     {"record": result.get("wait_boost_record")})
fact("no wait text in sidecar", "approval from Andy" not in sidecar_for(mcp.ledger().path).path.read_text())
view = rest_parallel()
fact("REST wait_boost launch_first_eligible B",
     view.get("wait_boost", {}).get("behavior") == "launch_first_eligible" and view["wait_boost"]["item_id"] == B,
     view.get("wait_boost"))
text = shoot("01-launch-first-eligible-desktop", 1280)
fact("web shows released wait for B", B in text and "First eligible launch" in text, text[:200])
shoot("02-launch-first-eligible-390", 390)

# --- 2. reserve_next takes boosted B before the newer ordinary C --------------
first = parallel("event", {"event": "reserve_next"}, host={"fork": True})
fact("reserve_next picked boosted B", first["item_id"] == B, first.get("item_id"))
fact("boost consumed by the reservation",
     sidecar_for(mcp.ledger().path).read()["rows"].get(boost_key(IDENTITY, B)) is None)
parallel("event", {"event": "bind", "lease_id": first["lease_id"]}, host={"host_id": "agent-B"})

# --- 3. parked worker on A: its slot is held, then reacquire consumes ---------
reserved = parallel("event", {"event": "reserve", "item_id": A, "priority": "P1"}, host={"fork": True})
lease_a = reserved["lease_id"]
parallel("event", {"event": "bind", "lease_id": lease_a}, host={"host_id": "agent-A"})
parallel("event", {"event": "question", "lease_id": lease_a, "plan_version": board()["version"],
                   "evidence": "need approval"}, host={"agent_id": "agent-A"})
board_store.transaction(lambda d: [n.update(waiting_on="deploy approval", status="blocked")
                                   for n in d["nodes"] if n["id"] == A])
parallel("event", {"event": "wait", "lease_id": lease_a}, host={"proof": "native host status: waiting"})
claimed, resolved = release("Andy: deploy approved for A", A, "deploy approval")
row = sidecar_for(mcp.ledger().path).read()["rows"].get(boost_key(IDENTITY, A))
fact("A boosted with its parked lease", row is not None and row["parked_lease_id"] == lease_a,
     {"parked": row and row["parked_lease_id"] == lease_a})
view = rest_parallel()
fact("REST wait_boost parked_first_eligible A",
     view.get("wait_boost", {}).get("behavior") == "parked_first_eligible" and view["wait_boost"]["item_id"] == A,
     view.get("wait_boost"))
text = shoot("03-parked-first-eligible-desktop", 1280)
fact("web shows parked worker first eligible", "Parked worker first eligible" in text and "SendMessage" in text,
     text[:240])
shoot("04-parked-first-eligible-390", 390)
try:
    blocked = parallel("event", {"event": "reserve_next"}, host={"fork": True})
    fact("ordinary C refused while A's slot is held", False, blocked)
except Exception as error:
    fact("ordinary C refused while A's slot is held", "kept for a released parked worker" in str(error),
         str(error)[:200])
reacquired = parallel("event", {"event": "reacquire", "lease_id": lease_a}, host={})
row = sidecar_for(mcp.ledger().path).read()["rows"].get(boost_key(IDENTITY, A))
fact("reacquire consumed A's boost", reacquired.get("wait_boost") == "reacquired"
     and row["state"] == "consumed" and row["outcome"] == "reacquired", reacquired)
view = rest_parallel()
fact("REST wait_boost none afterwards", view.get("wait_boost", {}).get("reason") == "none", view.get("wait_boost"))
text = shoot("05-none-desktop", 1280)
fact("web shows no released wait", "No released wait is waiting for a slot." in text, text[:200])
fact("stored root priority unchanged", next(n for n in board()["nodes"] if n["id"] == GOAL)["priority"] == "P1")

server.shutdown()
(OUT / "facts.json").write_text(json.dumps({"config_dir": "temporary", "identity": "claude:<e2e uuid>",
                                            "facts": facts}, indent=1), encoding="utf-8")
failed = [f for f in facts if not f["ok"]]
print("RESULT %d/%d facts" % (len(facts) - len(failed), len(facts)))
sys.exit(1 if failed else 0)
