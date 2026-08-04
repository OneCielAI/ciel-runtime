import unittest

from ciel_runtime_support.channel_replay_policy import ChannelReplaySafetyPolicy


class ChannelReplaySafetyPolicyTests(unittest.TestCase):
    def policy(self, *, now: float = 10_000.0, ttl: float = 600.0):
        return ChannelReplaySafetyPolicy(
            now=lambda: now,
            replay_ttl_seconds=lambda: ttl,
            timestamp_seconds=lambda message: message.get("created_at_epoch"),
            is_web_chat=lambda message: message.get("kind") == "web_chat",
        )

    def test_old_web_chat_is_expired(self):
        reason = self.policy().skip_reason(
            {"kind": "web_chat", "created_at_epoch": 1_000.0}
        )

        self.assertEqual("stale_web_chat_replay", reason)

    def test_recent_web_chat_remains_deliverable(self):
        reason = self.policy().skip_reason(
            {"kind": "web_chat", "created_at_epoch": 9_900.0}
        )

        self.assertEqual("", reason)

    def test_non_web_channel_history_is_not_changed(self):
        reason = self.policy().skip_reason(
            {"kind": "channel", "created_at_epoch": 1_000.0}
        )

        self.assertEqual("", reason)

    def test_missing_legacy_timestamp_does_not_drop_message(self):
        self.assertEqual("", self.policy().skip_reason({"kind": "web_chat"}))


if __name__ == "__main__":
    unittest.main()
