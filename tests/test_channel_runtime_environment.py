import unittest

from ciel_runtime_support.channel_runtime_environment import (
    ChannelRuntimeEnvironmentPolicy,
)


class ChannelRuntimeEnvironmentPolicyTests(unittest.TestCase):
    def policy(self, environment=None):
        return ChannelRuntimeEnvironmentPolicy(
            environment=environment or {},
            launch_recent_default=600.0,
            probe_timeout_default=15.0,
        )

    def test_defaults_match_runtime_contract(self):
        policy = self.policy()

        self.assertEqual(600.0, policy.launch_recent_seconds())
        self.assertEqual(15.0, policy.probe_timeout_seconds())
        self.assertEqual(500, policy.pending_scan_limit())
        self.assertEqual(8, policy.wake_batch_limit())
        self.assertEqual(300.0, policy.wake_claim_ttl_seconds())
        self.assertEqual(20.0, policy.unseen_retry_seconds())
        self.assertEqual(180.0, policy.inflight_stale_seconds())
        self.assertEqual(4, policy.codex_submit_retries())
        self.assertEqual(0.25, policy.codex_submit_delay_seconds())
        self.assertEqual(8.0, policy.windows_startup_grace_seconds())

    def test_numeric_settings_are_bounded(self):
        policy = self.policy(
            {
                "CIEL_RUNTIME_CHANNEL_PENDING_SCAN_LIMIT": "99999",
                "CIEL_RUNTIME_CHANNEL_WAKE_BATCH_LIMIT": "0",
                "CIEL_RUNTIME_CHANNEL_WAKE_CLAIM_TTL_SECONDS": "1",
                "CIEL_RUNTIME_CHANNEL_WAKE_UNSEEN_RETRY_SECONDS": "999",
                "CIEL_RUNTIME_CHANNEL_WAKE_INFLIGHT_STALE_SECONDS": "10",
                "CIEL_RUNTIME_CODEX_CHANNEL_WAKE_SUBMIT_RETRIES": "99",
            }
        )

        self.assertEqual(5000, policy.pending_scan_limit())
        self.assertEqual(1, policy.wake_batch_limit())
        self.assertEqual(5.0, policy.wake_claim_ttl_seconds())
        self.assertEqual(300.0, policy.unseen_retry_seconds())
        self.assertEqual(30.0, policy.inflight_stale_seconds())
        self.assertEqual(8, policy.codex_submit_retries())

    def test_millisecond_settings_are_converted_and_bounded(self):
        policy = self.policy(
            {
                "CIEL_RUNTIME_CODEX_CHANNEL_WAKE_SUBMIT_DELAY_MS": "50",
                "CIEL_RUNTIME_WINDOWS_CHANNEL_STARTUP_GRACE_MS": "1500",
            }
        )

        self.assertEqual(0.13, policy.codex_submit_delay_seconds())
        self.assertEqual(1.5, policy.windows_startup_grace_seconds())

    def test_invalid_and_nonpositive_probe_values_use_defaults(self):
        invalid = self.policy(
            {
                "CIEL_RUNTIME_CHANNEL_LAUNCH_RECENT_SECONDS": "invalid",
                "CIEL_RUNTIME_CHANNEL_PROBE_TIMEOUT_SECONDS": "invalid",
                "CIEL_RUNTIME_CHANNEL_PENDING_SCAN_LIMIT": "invalid",
            }
        )
        nonpositive = self.policy(
            {"CIEL_RUNTIME_CHANNEL_PROBE_TIMEOUT_SECONDS": "0"}
        )

        self.assertEqual(600.0, invalid.launch_recent_seconds())
        self.assertEqual(15.0, invalid.probe_timeout_seconds())
        self.assertEqual(500, invalid.pending_scan_limit())
        self.assertEqual(15.0, nonpositive.probe_timeout_seconds())

    def test_inflight_staleness_only_applies_to_unresolved_states(self):
        self.assertTrue(
            ChannelRuntimeEnvironmentPolicy.inflight_is_stale(
                "queued",
                100.0,
                161.0,
                60.0,
            )
        )
        self.assertFalse(
            ChannelRuntimeEnvironmentPolicy.inflight_is_stale(
                "completed",
                100.0,
                1000.0,
                60.0,
            )
        )


if __name__ == "__main__":
    unittest.main()
