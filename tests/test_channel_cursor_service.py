import unittest
from unittest import mock

from ciel_runtime_support.channel_cursor_service import (
    ChannelDeliveryCursorCommitter,
    ChannelDeliveryCursorPorts,
)


class ChannelDeliveryCursorCommitterTests(unittest.TestCase):
    def committer(self, *, status=200, enabled=True, confirmed=True):
        self.commit = mock.Mock()
        self.log = mock.Mock()
        return ChannelDeliveryCursorCommitter(
            ChannelDeliveryCursorPorts(
                response_status=mock.Mock(return_value=status),
                metadata_enabled=mock.Mock(return_value=enabled),
                delivery_confirmed=mock.Mock(return_value=confirmed),
                commit_if_newer=self.commit,
                log=self.log,
            )
        )

    def test_commits_cursor_from_body_metadata(self):
        service = self.committer()

        service.commit(
            {"metadata": {"ciel_runtime_channel_cursor_last_id": "9"}},
            object(),
        )

        self.commit.assert_called_once_with(9)

    def test_explicit_metadata_survives_sanitized_body(self):
        service = self.committer()

        service.commit(
            {},
            object(),
            {"ciel_runtime_channel_cursor_last_id": "11"},
        )

        self.commit.assert_called_once_with(11)

    def test_defers_failed_or_unconfirmed_delivery(self):
        for options in ({"status": 500}, {"confirmed": False}):
            with self.subTest(options=options):
                service = self.committer(**options)
                service.commit(
                    {"metadata": {"ciel_runtime_channel_cursor_last_id": "9"}},
                    object(),
                )
                self.commit.assert_not_called()
                self.log.assert_called_once()

    def test_invalid_cursor_is_forwarded_as_none(self):
        service = self.committer()

        service.commit(
            {"metadata": {"ciel_runtime_channel_cursor_last_id": "invalid"}},
        )

        self.commit.assert_called_once_with(None)


if __name__ == "__main__":
    unittest.main()
