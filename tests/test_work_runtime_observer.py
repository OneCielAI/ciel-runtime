import io
import json
import unittest
from types import SimpleNamespace
from ciel_runtime_support.work_runtime_observer import WorkRuntimeObserver


class WorkRuntimeObserverTests(unittest.TestCase):
    def setUp(self):
        self.scope = SimpleNamespace(runtime="claude", session_id="one", epoch="launch-a")
        self.binding = SimpleNamespace(request_scope=lambda *_a: self.scope)
        self.observer = WorkRuntimeObserver()
        self.handler = SimpleNamespace(headers={}, wfile=io.BytesIO(), _ciel_runtime_response_status=200)

    def write(self, payload):
        self.handler.wfile.write(b"HTTP/1.1 200 OK" + bytes([13,10]) + b"Content-Type: application/json" + bytes([13,10,13,10]))
        self.handler.wfile.write(json.dumps(payload).encode())

    def test_configured_alias_and_response_reported_model_are_distinct(self):
        original = self.handler.wfile
        with self.observer.observe(self.handler, "/v1/messages", "gateway", "request-alias", {}, self.binding):
            self.assertEqual("request-alias", self.observer.snapshot(self.scope)["in_flight"][0]["model"])
            self.write({"model": "actual-upstream", "content": [{"type": "text", "text": "do-not-retain"}]})
        snapshot = self.observer.snapshot(self.scope)
        self.assertEqual([], snapshot["in_flight"])
        self.assertEqual("actual-upstream", snapshot["last_observed"]["model"])
        self.assertEqual("request-alias", snapshot["last_observed"]["request_model"])
        self.assertNotIn("do-not-retain", str(snapshot))
        self.assertIs(original, self.handler.wfile)

    def test_504_and_credential404_record_only_typed_metadata(self):
        for status in (504,404):
            self.handler._ciel_runtime_response_status = status
            with self.observer.observe(self.handler, "/v1/messages", "gateway", "m", {}, self.binding):
                self.write({"error": {"message": "No active credentials secret-body"}})
            error = self.observer.snapshot(self.scope)["last_error"]
            self.assertEqual(status,error["status"])
            self.assertNotIn("secret-body",str(error))
            self.assertEqual("timeout" if status==504 else "http_error",error["category"])

    def test_other_scope_and_unbound_request_are_unavailable(self):
        unknown = SimpleNamespace(request_scope=lambda *_a: None)
        with self.observer.observe(self.handler,"/v1/messages","g","m",{},unknown):
            self.write({"model":"not-bound"})
        self.assertIsNone(self.observer.snapshot(self.scope)["last_observed"])

    def test_exception_finishes_inflight_and_preserves_transparency(self):
        with self.assertRaises(TimeoutError):
            with self.observer.observe(self.handler,"/v1/messages","g","m",{},self.binding):
                raise TimeoutError()
        self.assertEqual([], self.observer.snapshot(self.scope)["in_flight"])
        self.assertEqual("timeout",self.observer.snapshot(self.scope)["last_error"]["category"])
