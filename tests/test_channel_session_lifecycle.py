import io
import unittest
import urllib.error
from unittest import mock

from ciel_runtime_support.channel_session_lifecycle import (
    ChannelSessionLifecycleServices,
    cleanup_stale_channel_sessions,
    delete_channel_session,
)


class ChannelSessionLifecycleTests(unittest.TestCase):
    def services(self, *, urlopen, records=None):
        return ChannelSessionLifecycleServices(
            streamable_headers=lambda headers, version, session, **_kwargs: {
                **headers,
                "Mcp-Session-Id": session,
                "MCP-Protocol-Version": version,
            },
            http_error_body=lambda _exc: "",
            session_not_found=lambda _exc, _body: False,
            records=lambda: list(records or []),
            forget=mock.Mock(),
            log=mock.Mock(),
            urlopen=urlopen,
        )

    def test_successful_delete_forgets_repository_record(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        services = self.services(urlopen=mock.Mock(return_value=response))

        deleted = delete_channel_session(
            "channel",
            "https://mcp.test",
            {},
            "v1",
            "session-id",
            "stop",
            services,
            default_protocol_version="v1",
        )

        self.assertTrue(deleted)
        services.forget.assert_called_once_with("channel", "https://mcp.test", "session-id")

    def test_not_found_delete_is_idempotent(self):
        error = urllib.error.HTTPError("https://mcp.test", 404, "missing", {}, io.BytesIO())
        services = self.services(urlopen=mock.Mock(side_effect=error))

        deleted = delete_channel_session(
            "channel",
            "https://mcp.test",
            {},
            "v1",
            "missing",
            "cleanup",
            services,
            default_protocol_version="v1",
        )

        self.assertTrue(deleted)
        services.forget.assert_called_once()

    def test_cleanup_keeps_current_session(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        urlopen = mock.Mock(return_value=response)
        services = self.services(
            urlopen=urlopen,
            records=[
                {"url": "https://mcp.test", "session_id": "old", "protocol_version": "v1"},
                {"url": "https://mcp.test", "session_id": "current", "protocol_version": "v1"},
            ],
        )

        cleanup_stale_channel_sessions(
            "channel",
            "https://mcp.test",
            {},
            "v1",
            services,
            default_protocol_version="v1",
            keep_session_id="current",
        )

        urlopen.assert_called_once()
        services.forget.assert_called_once_with("channel", "https://mcp.test", "old")


if __name__ == "__main__":
    unittest.main()
