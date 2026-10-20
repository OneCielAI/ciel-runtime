import json
import urllib.error
import unittest

from ciel_runtime_support.event_receiver_diagnostics import receiver_failure


class EventReceiverDiagnosticsTests(unittest.TestCase):
    def test_nested_url_error_is_typed_without_reason_text(self):
        exc = urllib.error.URLError(ConnectionRefusedError(111, "secret.endpoint.invalid token"))
        value = receiver_failure(exc, "connect")
        encoded = json.dumps(value)
        self.assertEqual("network", value["category"])
        self.assertEqual("network", value["reason"]["category"])
        self.assertEqual(111, value["reason"]["errno"])
        self.assertNotIn("secret.endpoint.invalid", encoded)
        self.assertNotIn("token", encoded)

    def test_http_status_and_phase_are_allowlisted(self):
        value = receiver_failure(
            urllib.error.HTTPError(
                "https://secret.invalid/?token=x", 503, "token", {}, None
            ),
            "headers",
        )
        self.assertEqual(
            {
                "class": "HTTPError",
                "category": "http",
                "http_status": 503,
                "phase": "headers",
            },
            value,
        )

    def test_unknown_phase_is_normalized_and_no_exception_text_is_retained(self):
        value = receiver_failure(RuntimeError("authorization=secret"), "private-url")
        self.assertEqual("worker", value["phase"])
        self.assertNotIn("authorization", json.dumps(value))


if __name__ == "__main__":
    unittest.main()
