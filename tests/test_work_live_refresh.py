"""Live refresh of the console tabs whose facts do not move the board version.

settings.changed  a Work/SSE setting changed (settings_fingerprint), from any surface;
parallel.changed  also when the ledger version or a policy value changes;
audit.changed     an operator receipt was added or changed state (audit-only
                  writes keep the board version, so work.changed never fires).
Each is content-free apart from counters and fingerprints, and has no event id.
"""
import json
import tempfile
import unittest
import urllib.request
from pathlib import Path

from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.http_response import HttpResponseAdapter
from ciel_runtime_support.work_application import WorkApplication
from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices
from ciel_runtime_support.work_live_settings import settings_fingerprint
from ciel_runtime_support.work_parallel import WorkParallelStore
from ciel_runtime_support.work_plan_store import session_work_plan_store



def serve_base(case, controller):
    """Loopback server for the controller; returns its base URL (streams need the raw socket)."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if not controller.get(self, self.path.split("?")[0]):
                self.send_error(404)

        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    case.addCleanup(server.server_close)
    case.addCleanup(server.shutdown)
    return "http://127.0.0.1:" + str(server.server_port)

SCOPE = "claude:11111111-2222-3333-4444-aaaaaaaaaaaa"


def frames(stream, wanted, limit=40):
    """Read SSE frames until one whose event name is ``wanted``; return its lines."""
    for _ in range(limit):
        lines = []
        while True:
            line = stream.readline().decode().strip()
            if not line:
                break
            lines.append(line)
        if ("event: " + wanted) in lines:
            return lines
    raise AssertionError("no " + wanted + " frame")


class LiveRefreshTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.state = root / "state"
        self.config = {"work_mode": True, "work_side_intake": {"max_active": 2}}
        self.store = WorkParallelStore(root / "parallel" / "work-parallel.json")
        self.board = session_work_plan_store(self.state, SCOPE)
        doc = self.board.read()
        self.board.transaction(lambda d: tree.set_plan(d, {
            "title": "Goal", "note": "", "priority": "P1", "children": [
                {"title": "Item", "note": "", "ref": "", "waiting_on": "", "children": []}]}, None),
            expected_version=doc["version"])
        self.policy = {"enabled": True, "paused": False, "priority_limits": {"P1": 5},
                       "launches_per_minute": 4, "max_rework": 2}
        self.ledger_version = 3
        self.controller = WorkHttpController(WorkHttpServices(
            self.state, lambda: dict(self.config), lambda: SCOPE,
            lambda identity: {"session_id": identity, "last_observed": None},
            HttpResponseAdapter.write_json, "test-instance",
            parallel_status=lambda identity, board: self.projection(), parallel_store=lambda: self.store))
        self.base = serve_base(self, self.controller)

    def projection(self):
        empty = {name: [] for name in ("confirmed", "waiting", "reservation", "uncertain", "validating", "ended")}
        return {"version": self.ledger_version, "policy": dict(self.policy),
                "forks": {"schema": "native-fork-evidence/1", "available": True, "confirmed_count": 0, **empty},
                "device": {"active_slots": 0, "active_by_priority": {}, "paused": False}, "runtime": "claude",
                "limitations": [], "leases": {},
                "review": {"version": 1, "stranded_version": 0, "pending": [], "truncated": False},
                "legacy_reviews": []}

    # Fingerprints -----------------------------------------------------------

    def test_parallel_fingerprint_follows_ledger_version_and_every_policy_value(self):
        board = {"nodes": [], "version": 1}
        prints = [self.controller._parallel_fingerprint(SCOPE, board)[0]]
        self.ledger_version = 4
        prints.append(self.controller._parallel_fingerprint(SCOPE, board)[0])
        for key, value in (("enabled", False), ("priority_limits", {"P1": 3}), ("launches_per_minute", 2),
                           ("max_rework", 5)):
            self.policy[key] = value
            prints.append(self.controller._parallel_fingerprint(SCOPE, board)[0])
        self.assertEqual(len(prints), len(set(prints)), prints)

    def test_audit_print_follows_new_receipts_and_state_changes_only(self):
        doc = self.board.read()
        first = self.controller._audit_print(doc)
        app = WorkApplication(self.board)
        body = {"changes": {}, "expected_version": 0}
        app.operator_prepare("parallel_configure", body, "op-1")
        prepared = self.controller._audit_print(self.board.read())
        app.operator_commit("parallel_configure", body, "op-1", {"version": 1})
        committed = self.controller._audit_print(self.board.read())
        self.assertEqual(3, len({first, prepared, committed}))
        self.assertEqual(committed, self.controller._audit_print(self.board.read()))

    # The stream -------------------------------------------------------------

    def open(self):
        return urllib.request.urlopen(self.base + "/ca/work/api/v1/events", timeout=6)

    def test_stream_emits_settings_audit_and_policy_changes(self):
        stream = self.open()
        self.addCleanup(stream.close)
        frames(stream, "work.snapshot")
        self.config["work_side_intake"] = {"max_active": 7}
        settings = frames(stream, "settings.changed")
        data = json.loads(next(line for line in settings if line.startswith("data: "))[6:])
        self.assertEqual({"session_id": SCOPE, "settings_fingerprint": settings_fingerprint(self.config),
                          "refetch": "/ca/work/api/v1/settings", "coalesced": True}, data)
        self.assertFalse(any(line.startswith("id:") for line in settings))
        WorkApplication(self.board).operator_prepare("parallel_configure", {"changes": {}, "expected_version": 0},
                                                     "op-1")
        audit = frames(stream, "audit.changed")
        data = json.loads(next(line for line in audit if line.startswith("data: "))[6:])
        self.assertEqual((1, "op-1", "/ca/work/api/v1/audit"),
                         (data["count"], data["newest_operation_id"], data["refetch"]))
        self.assertFalse(any(line.startswith("id:") for line in audit))
        self.policy["max_rework"] = 5
        self.store.path.parent.mkdir(parents=True, exist_ok=True)
        self.store.path.write_text("{}", encoding="utf-8")  # a parallel file changed: the print is read
        self.assertIn("event: parallel.changed", frames(stream, "parallel.changed"))

    def test_openapi_lists_the_new_events(self):
        from ciel_runtime_support.work_application import openapi_document
        text = openapi_document()["paths"]["/ca/work/api/v1/events"]["get"]["description"]
        for name in ("settings.changed", "audit.changed", "parallel.changed"):
            self.assertIn(name, text)


if __name__ == "__main__":
    unittest.main()
