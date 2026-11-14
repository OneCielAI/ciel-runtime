"""REST settings-update is pinned to the settings fingerprint the operator read.

The write is a compare-and-set under the shared settings lock
(work_live_settings): a change made elsewhere after the read is refused with
409 settings_conflict and nothing is saved, and a late replay of a request
whose receipt has aged out is refused the same way instead of writing again.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ciel_runtime_support.http_response import HttpResponseAdapter
from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices
from ciel_runtime_support.work_live_settings import settings_fingerprint
from ciel_runtime_support.work_plan_store import session_work_plan_store

from test_work_http_parallel_gaps import serve

SCOPE = "claude:11111111-2222-3333-4444-999999999999"
UPDATE = "/ca/work/api/v1/commands/settings-update"


class SettingsPinRestTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.state = root / "state"
        self.path = root / "workspace-config.json"
        self.path.write_text(json.dumps({"work_mode": True, "work_side_intake": {"max_active": 2}}), encoding="utf-8")
        from ciel_runtime_support import runtime_paths
        lock = patch.object(runtime_paths, "CONFIG_PATH", self.path)
        lock.start()
        self.addCleanup(lock.stop)
        self.saves = 0
        self.controller = WorkHttpController(WorkHttpServices(
            self.state, self.load, lambda: SCOPE,
            lambda identity: {"session_id": identity, "last_observed": None},
            HttpResponseAdapter.write_json, "test-instance", save_config=self.save))
        self.request = serve(self, self.controller)

    def load(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, config):
        self.saves += 1
        self.path.write_text(json.dumps(config), encoding="utf-8")

    def fingerprint(self):
        status, body = self.request("/ca/work/api/v1/settings")
        self.assertEqual(200, status)
        return body["settings_fingerprint"]

    def update(self, value, fingerprint, key="u-1"):
        body = {"key": "work_side_intake_max_active", "value": value}
        if fingerprint is not None:
            body["expected_settings_fingerprint"] = fingerprint
        return self.request(UPDATE, body, {"Idempotency-Key": key})

    def test_get_settings_exports_the_fingerprint_of_the_stored_values(self):
        self.assertEqual(settings_fingerprint(self.load()), self.fingerprint())

    def test_missing_or_malformed_fingerprint_is_refused_and_nothing_is_saved(self):
        for pin in (None, "abc", "F" * 64):
            with self.subTest(pin=pin):
                status, body = self.update("7", pin, key="bad-" + str(pin)[:3])
                self.assertEqual((422, "invalid_request"), (status, body["error"]))
                self.assertIn("expected_settings_fingerprint", body["message"])
        self.assertEqual(0, self.saves)

    def test_current_fingerprint_saves_and_a_stale_one_is_a_409(self):
        seen = self.fingerprint()
        status, body = self.update("7", seen)
        self.assertEqual(200, status, body)
        self.assertEqual(7, self.load()["work_side_intake"]["max_active"])
        before = self.path.read_bytes()
        status, body = self.update("9", seen, key="u-2")  # read before the first write
        self.assertEqual((409, "settings_conflict"), (status, body["error"]))
        self.assertEqual(settings_fingerprint(self.load()), body["settings_fingerprint"])
        self.assertEqual(before, self.path.read_bytes())

    def test_a_change_made_elsewhere_after_the_read_is_refused(self):
        seen = self.fingerprint()
        config = self.load()
        config["work_side_intake"]["aging_seconds"] = 17  # another surface, another key
        self.save(config)
        status, body = self.update("7", seen)
        self.assertEqual((409, "settings_conflict"), (status, body["error"]))
        self.assertEqual(2, self.load()["work_side_intake"]["max_active"])

    def test_same_key_replays_and_a_late_replay_after_eviction_never_writes_again(self):
        seen = self.fingerprint()
        self.assertEqual(200, self.update("7", seen)[0])
        saves = self.saves
        status, again = self.update("7", seen)
        self.assertEqual((200, True), (status, again["replayed"]))
        self.assertEqual(saves, self.saves)
        board = session_work_plan_store(self.state, SCOPE)
        board.transaction(lambda d: d["rest_operations"].pop("u-1"), audit_only=True)  # aged out
        # R3: also aged out of the operator-intent record's resolved window.
        from ciel_runtime_support.work_operator_intents import WorkOperatorIntents
        WorkOperatorIntents.beside(board.path).transaction(lambda intents: intents.pop("u-1", None))
        config = self.load()
        config["work_side_intake"]["max_active"] = 4  # changed again meanwhile
        self.save(config)
        saves = self.saves
        status, late = self.update("7", seen)
        self.assertEqual((409, "settings_conflict"), (status, late["error"]))
        self.assertEqual((4, saves), (self.load()["work_side_intake"]["max_active"], self.saves))

    def test_openapi_requires_the_fingerprint(self):
        from ciel_runtime_support.work_application import openapi_document
        document = openapi_document()
        schema = document["paths"][UPDATE]["post"]["requestBody"]["content"]["application/json"]["schema"]
        self.assertEqual({"key", "value", "expected_settings_fingerprint"}, set(schema["required"]))
        self.assertEqual("^[0-9a-f]{64}$", schema["properties"]["expected_settings_fingerprint"]["pattern"])
        self.assertIn("settings_conflict", json.dumps(document))


if __name__ == "__main__":
    unittest.main()
