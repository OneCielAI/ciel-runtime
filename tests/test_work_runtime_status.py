import unittest

from ciel_runtime_support.work_runtime_status import RuntimeStatusScope, RuntimeStatusTracker


class WorkRuntimeStatusTests(unittest.TestCase):
    def setUp(self):
        self.scope = RuntimeStatusScope("claude", "session-a", "epoch-a")
        self.tracker = RuntimeStatusTracker(now=lambda: 1793058000.0)
        self.tracker.bind(self.scope)

    def test_projection_separates_configured_from_observed(self):
        self.tracker.observe_configuration("cielairouter", "claude-opus-5-5")
        self.tracker.observe_request(self.scope, "request-a", "anthropic", "claude-opus-5-5")
        snapshot = self.tracker.snapshot()
        self.assertEqual("cielairouter", snapshot["configured_provider"])
        self.assertEqual("claude-opus-5-5", snapshot["configured_model"])
        self.assertIsNone(snapshot["observed_provider"])
        self.assertIsNone(snapshot["observed_model"])
        self.assertIsNone(snapshot["last_error"])

    def test_response_requires_exact_scope_and_request_correlation(self):
        other = RuntimeStatusScope("claude", "session-b", "epoch-a")
        self.assertFalse(self.tracker.observe_request(other, "request-b", "openai", "gpt"))
        self.assertFalse(self.tracker.observe_response(self.scope, "request-b", status=200))
        self.assertTrue(self.tracker.observe_request(self.scope, "request-a", "anthropic", "claude-opus-5-5"))
        self.assertTrue(self.tracker.observe_response(self.scope, "request-a", provider="anthropic", model="claude-opus-5-5", status=200, observed_at=1793058001.0))
        snapshot = self.tracker.snapshot()
        self.assertEqual("anthropic", snapshot["observed_provider"])
        self.assertEqual("claude-opus-5-5", snapshot["observed_model"])
        self.assertEqual("2026-10-26T23:40:01Z", snapshot["observed_at"])

    def test_failures_are_allowlisted_and_correlated(self):
        self.assertTrue(self.tracker.observe_request(self.scope, "request-a", "claude", "claude-opus-5-5"))
        self.assertFalse(self.tracker.observe_failure(self.scope, "request-a", category="provider timed out; inspect credentials"))
        self.assertTrue(self.tracker.observe_failure(self.scope, "request-a", category="timeout", status=504))
        error = self.tracker.snapshot()["last_error"]
        self.assertEqual({"category": "timeout", "provider": "claude", "model": "claude-opus-5-5", "status": 504,
                          "observed_at": "2026-10-26T23:40:00Z", "request_id": "request-a", "source": "correlated_request_failure"}, error)

    def test_rebinding_epoch_discards_previous_observations(self):
        self.tracker.observe_request(self.scope, "request-a", "anthropic", "model")
        self.tracker.bind(RuntimeStatusScope("claude", "session-a", "epoch-b"))
        self.assertFalse(self.tracker.observe_response(self.scope, "request-a"))
        snapshot = self.tracker.snapshot()
        self.assertEqual("epoch-b", snapshot["scope"]["epoch"])
        self.assertIsNone(snapshot["last_response"])


if __name__ == "__main__":
    unittest.main()
