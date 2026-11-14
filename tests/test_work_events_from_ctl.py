"""Measurement: /events after real settings and limit-only parallel writes from ctl and REST.

Open item from 2026-11-13 run-3 (journal ctl-work-sse-parity): "no /events
emission for settings_update or limit-only parallel_configure". Here every
store is real: a temp config file read and written by the same functions the
writers use, the real WorkParallelStore, and the real work_parallel_mcp
projection. Every write goes through the real ctl entry
(work_settings_cli.command) or the real REST operator path. The stream is the
controller's own /ca/work/api/v1/events on a loopback thread; no process is
started.
"""
import argparse
import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from ciel_runtime_support import runtime_paths
from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.http_response import HttpResponseAdapter
from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices
from ciel_runtime_support.work_live_settings import settings_fingerprint
from ciel_runtime_support.work_parallel import WorkParallelStore
from ciel_runtime_support.work_plan_store import session_work_plan_store

SCOPE = "claude:11111111-2222-3333-4444-cccccccccccc"


def frames_until(stream, wanted, limit=60):
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


class EventsFromCtlAndRest(unittest.TestCase):
    def setUp(self):
        temp_root = Path(tempfile.gettempdir()).resolve()
        self.assertTrue(str(Path(runtime_paths.CONFIG_DIR).resolve()).lower().startswith(str(temp_root).lower()),
                        "not an isolated profile")
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.config_path = root / "config.json"
        self.config_path.write_text(json.dumps({"work_mode": True}), encoding="utf-8")
        self.store = WorkParallelStore(root / "parallel" / "work-parallel.json")
        state = root / "state"
        board = session_work_plan_store(state, SCOPE)
        board.transaction(lambda d: tree.set_plan(d, {"title": "Goal", "children": [{"title": "Item"}]}, None),
                          expected_version=board.read()["version"])
        for target in (patch("ciel_runtime_support.work_parallel_mcp.ledger", return_value=self.store),
                       patch.object(runtime_paths, "CONFIG_PATH", self.config_path)):
            target.start()
            self.addCleanup(target.stop)
        from ciel_runtime_support.work_parallel_mcp import ledger, project
        self.controller = WorkHttpController(WorkHttpServices(
            state, self.load, lambda: SCOPE, lambda identity: {"session_id": identity}, HttpResponseAdapter.write_json,
            "test", lambda _identity: [], lambda identity, doc: project(ledger(), identity, doc), self.save, ledger))
        controller = self.controller

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if not controller.get(self, self.path.split("?")[0]):
                    self.send_error(404)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                if not controller.post(self, self.path, body):
                    self.send_error(404)

            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.base = "http://127.0.0.1:" + str(server.server_port)
        self.stream = urllib.request.urlopen(self.base + "/ca/work/api/v1/events", timeout=10)
        self.addCleanup(self.stream.close)
        frames_until(self.stream, "work.snapshot")

    def load(self):
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def save(self, config):
        self.config_path.write_text(json.dumps(config), encoding="utf-8")

    def ctl_set(self, key, value):
        from ciel_runtime_support.work_settings_cli import command
        args = argparse.Namespace(action="set", key=key, value=value, json=True, session=None, confirm=False,
                                  expected_version=None, expected_finalization_version=None, preview_digest=None,
                                  attempt_ref=None, revision=None, clear_wait=[], activate=False, operation_id=None,
                                  expected_settings_fingerprint=None, expected_review_pin=None)
        output = []
        with patch("ciel_runtime_support.work_ctl.state_dir", return_value=self.controller.services.state_dir):
            command(self.load, self.save, output.append)(args)
        return json.loads(output[-1])

    def rest(self, command, body):
        request = urllib.request.Request(self.base + "/ca/work/api/v1/commands/" + command,
                                         data=json.dumps({"session_id": SCOPE, **body}).encode(),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())

    def test_ctl_settings_write_emits_settings_changed(self):
        self.ctl_set("work_side_intake_max_active", "7")
        lines = frames_until(self.stream, "settings.changed")
        data = json.loads(next(line for line in lines if line.startswith("data: "))[6:])
        self.assertEqual(settings_fingerprint(self.load()), data["settings_fingerprint"])

    def test_rest_settings_update_emits_settings_changed(self):
        pin = settings_fingerprint(self.load())
        self.assertTrue(self.rest("settings-update", {"key": "work_auto_continue", "value": "off",
                                                      "expected_settings_fingerprint": pin})["ok"])
        lines = frames_until(self.stream, "settings.changed")
        data = json.loads(next(line for line in lines if line.startswith("data: "))[6:])
        self.assertEqual(settings_fingerprint(self.load()), data["settings_fingerprint"])

    def test_ctl_limit_only_change_emits_parallel_changed(self):
        before = self.store.read()["policy"]["priority_limits"]["P1"]
        self.ctl_set("work_parallel_p1_limit", str(1 if before != 1 else 2))
        self.assertNotEqual(before, self.store.read()["policy"]["priority_limits"]["P1"])
        self.assertIn("event: parallel.changed", frames_until(self.stream, "parallel.changed"))

    def test_rest_limit_only_parallel_configure_emits_parallel_changed(self):
        state = self.store.read()
        limits = dict(state["policy"]["priority_limits"])
        limits["P2"] = 1 if limits.get("P2") != 1 else 2
        self.assertTrue(self.rest("parallel-configure", {"changes": {"priority_limits": limits},
                                                         "expected_version": state["version"]})["ok"])
        self.assertIn("event: parallel.changed", frames_until(self.stream, "parallel.changed"))


if __name__ == "__main__":
    unittest.main()
