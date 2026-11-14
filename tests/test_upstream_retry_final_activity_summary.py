"""V1: the final ``error`` activity keeps the transient executor's retry summary.

lead-rulings-independent-review-sse-retry-20261113.md, "V1": the final
router activity merges ``transient_final_fields(exc)`` so the statusline can
say "gave up after N retries". On native non-stream ``/v1/messages``
(open_provider_request_with_key_retry) the executor's error was replaced by
``_preserved_http_error`` before that write, and the copy dropped the summary.

Each entrypoint is driven with an exhausted retryable 503 under an explicit
count budget (gateway_retries 2: 3 opens), and the last ``error`` activity must
carry reason, attempts, stop and retry_kind. The OpenAI stream and post_json
entrypoints are checked with the same shape and recorded.
"""
import io
import unittest
import urllib.error
from unittest import mock

from ciel_runtime_support import upstream_retry
from test_upstream_retry import invoke, services

UNAVAILABLE = b'{"type":"error","error":{"type":"overloaded_error","message":"service unavailable"}}'
SUMMARY = {"reason": "pre_output_retries_exhausted", "attempts": 2, "stop": "attempt_ceiling",
           "retry_kind": "http_rejection"}


class FinalActivitySummaryTests(unittest.TestCase):
    def exhaust(self, fn, **kwargs):
        opens = []

        def dispatch(req, **_):
            opens.append(req.full_url)
            raise urllib.error.HTTPError(req.full_url, 503, "unavailable",
                                         {"content-type": "application/json"}, io.BytesIO(UNAVAILABLE))

        activity = mock.Mock()
        with mock.patch.object(upstream_retry.time, "sleep"), self.assertRaises(Exception) as raised:
            invoke(fn, services(dispatch, activity=activity), {"base_url": "http://a", "gateway_retries": 2},
                   **kwargs)
        errors = [call.kwargs for call in activity.call_args_list if call.args[0] == "error"]
        return opens, errors, raised.exception

    def assert_summary(self, fn, **kwargs):
        opens, errors, error = self.exhaust(fn, **kwargs)
        self.assertEqual(3, len(opens), "1 open + 2 retries, then exhausted")
        self.assertTrue(errors, "a final error activity is written")
        final = errors[-1]
        for key, value in SUMMARY.items():
            self.assertEqual(value, final.get(key), f"final error activity field {key}: {final}")
        self.assertEqual(503, final.get("code"))
        self.assertEqual(503, getattr(error, "code", getattr(error, "upstream_status", None)))

    def test_native_messages_non_stream_final_activity_has_retry_summary(self):
        self.assert_summary(upstream_retry.open_provider_request_with_key_retry, stream=False)

    def test_native_messages_stream_final_activity_has_retry_summary(self):
        self.assert_summary(upstream_retry.open_provider_request_with_key_retry, stream=True)

    def test_openai_stream_final_activity_has_retry_summary(self):
        self.assert_summary(upstream_retry.open_openai_stream_with_rate_retry)

    def test_post_json_final_activity_has_retry_summary(self):
        self.assert_summary(upstream_retry.post_json_with_rate_retry)

    def test_preserved_error_body_and_status_are_unchanged(self):
        _opens, _errors, error = self.exhaust(upstream_retry.open_provider_request_with_key_retry, stream=False)
        self.assertIsInstance(error, urllib.error.HTTPError)
        self.assertEqual(UNAVAILABLE, error.read())
        self.assertEqual(UNAVAILABLE, error.ciel_runtime_body)
        self.assertTrue(getattr(error, "_ciel_transient_retry_handled", False))
        self.assertFalse(hasattr(error, "_ciel_transient_retry_note"), "client message unchanged")


if __name__ == "__main__":
    unittest.main()
