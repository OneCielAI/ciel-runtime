import unittest

from ciel_runtime_support.channel_message_dedupe import (
    ChannelMessageDedupePorts,
    ChannelMessageDedupeService,
)


class ChannelMessageDedupeServiceTests(unittest.TestCase):
    def service(self, rows, guard=None, now=100.0):
        return ChannelMessageDedupeService(
            ports=ChannelMessageDedupePorts(
                stable_key=lambda message: message.get("stable"),
                fallback_key=lambda message: message.get("fallback"),
                recent_rows=lambda: rows,
                launch_guard=lambda: guard,
                timestamp_seconds=lambda value: float(value or 0),
                now=lambda: now,
            ),
            fallback_ttl_seconds=10.0,
        )

    def test_stable_identity_matches_regardless_of_age(self):
        row = {"id": 1, "stable": "same", "time": 1}
        self.assertIs(
            row,
            self.service([row]).duplicate({"stable": "same"}),
        )

    def test_recent_fallback_identity_matches_within_ttl(self):
        row = {"id": 2, "fallback": "same", "time": 95}
        self.assertIs(
            row,
            self.service([row]).duplicate({"fallback": "same"}),
        )

    def test_launch_guard_matches_preexisting_fallback_row(self):
        row = {"id": 3, "fallback": "same", "time": 1}
        self.assertIs(
            row,
            self.service(
                [row],
                guard={"max_existing_id": 3},
            ).duplicate({"fallback": "same"}),
        )

    def test_unrelated_or_unidentified_message_is_not_duplicate(self):
        service = self.service(
            [{"id": 1, "fallback": "other", "time": 99}]
        )
        self.assertIsNone(service.duplicate({"fallback": "new"}))
        self.assertIsNone(service.duplicate({}))


if __name__ == "__main__":
    unittest.main()
