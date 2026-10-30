"""Shape logs retain fixed classifications, never upstream-controlled strings."""
import json
import unittest
from types import SimpleNamespace

from ciel_runtime_support.sse_diagnostics import payload_shape, prefix_shape, response_shape


class SSEDiagnosticsTests(unittest.TestCase):
    def test_only_allowlisted_names_types_and_tokens(self):
        raw = json.dumps({"type": "error", "PRIVATE-KEY": "PRIVATE-VALUE",
                          "error": {"type": "api_error", "message": "PRIVATE-MESSAGE",
                                    "code": 504, "param": "PRIVATE-PARAM", "PRIVATE-ERROR": "secret"}})
        shape = payload_shape(raw, "error")
        self.assertEqual(shape["error"]["type_token"], "api_error")
        self.assertEqual(shape["error"]["fields"]["code"], "number")
        self.assertEqual(shape["other_fields"], 1)
        self.assertEqual(shape["error"]["other_fields"], 1)
        self.assertNotIn("PRIVATE", json.dumps(shape))
        self.assertNotIn("504", json.dumps(shape))

    def test_gateway_metadata_names_without_values_are_observable(self):
        shape = payload_shape(json.dumps({"error": {"type": "server_error", "message": "PRIVATE"},
            "diagnostics": {"terminalReason": "PRIVATE"},
            "recovery_hint": {"next_step": "PRIVATE"}}), "error")
        self.assertEqual(shape["fields"]["diagnostics"], "object")
        self.assertEqual(shape["fields"]["recovery_hint"], "object")
        self.assertEqual(shape["other_fields"], 0)
        self.assertNotIn("PRIVATE", json.dumps(shape))
        self.assertNotIn("next_step", json.dumps(shape))

    def test_unknown_tokens_and_invalid_json_are_never_logged(self):
        for raw in ("PRIVATE invalid json", '["PRIVATE"]', '"PRIVATE"',
                    '{"error":{"type":"PRIVATE-TOKEN","message":"PRIVATE"}}',
                    "[" * 3000, "PRIVATE" * 10000):
            with self.subTest(raw_length=len(raw)):
                shape = payload_shape(raw, "PRIVATE-EVENT")
                self.assertNotIn("PRIVATE", json.dumps(shape))
                self.assertEqual(shape["event"], "other")

    def test_frame_bound_and_partial_tail(self):
        raw = b'event: ping' + bytes([10]) + b'data: {"type":"ping"}' + bytes([10, 10])
        raw = raw * 10 + b'data: PRIVATE'
        shape = prefix_shape(raw)
        self.assertEqual(shape["complete_frames"], 10)
        self.assertEqual(len(shape["frames"]), 4)
        self.assertEqual(shape["tail_bytes"], len(b'data: PRIVATE'))
        self.assertNotIn("PRIVATE", json.dumps(shape))

    def test_response_diagnostic_values_are_allowlisted(self):
        for value in ("PRIVATE-STATE", ["PRIVATE"], {"PRIVATE": True}):
            response = SimpleNamespace(headers={}, _ciel_preoutput_diagnostic={
                "state": value, "reason": value, "operation_id": "PRIVATE-ID", "PRIVATE": "SECRET"})
            self.assertNotIn("PRIVATE", json.dumps(response_shape(response)))
            self.assertNotIn("SECRET", json.dumps(response_shape(response)))

    def test_error_after_long_ping_preamble_remains_in_shape(self):
        ping = b'event: ping' + bytes([10]) + b'data: {"type":"ping"}' + bytes([10, 10])
        error = b'event: error' + bytes([10]) + b'data: {"error":{"type":"api_error","code":504}}' + bytes([10, 10])
        shape = prefix_shape(ping * 100 + error)
        self.assertEqual(shape["complete_frames"], 101)
        self.assertEqual(len(shape["frames"]), 4)
        self.assertEqual(shape["frames"][-1]["error"]["fields"]["code"], "number")

    def test_response_media_never_logs_raw_header(self):
        for media, expected in [("PRIVATE-HEADER", "other"), ("", "absent"),
                                ("Text/Event-Stream; PRIVATE", "text/event-stream")]:
            shape = response_shape(SimpleNamespace(headers={"content-type": media}))
            self.assertEqual(shape["media"], expected)
            self.assertNotIn("PRIVATE", json.dumps(shape))
