from __future__ import annotations

import io
import unittest

from ciel_runtime_support.mcp_transport import (
    MCP_STREAMABLE_HTTP_PROTOCOL_VERSION,
    negotiated_protocol_version,
    read_sse_json_response,
    split_proxy_server_name,
    streamable_headers,
    upstream_url,
)


class McpTransportTests(unittest.TestCase):
    def test_sse_decoder_skips_unmatched_response(self):
        response = io.BytesIO(
            b'data: {"id":1,"result":"old"}\n\n'
            b'data: {"id":2,"result":"current"}\n\n'
        )

        self.assertEqual({"id": 2, "result": "current"}, read_sse_json_response(response, 2))

    def test_streamable_headers_add_protocol_and_session(self):
        self.assertEqual(
            {
                "Authorization": "Bearer token",
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": MCP_STREAMABLE_HTTP_PROTOCOL_VERSION,
                "Mcp-Session-Id": "session-1",
            },
            streamable_headers(
                {"Authorization": "Bearer token"},
                MCP_STREAMABLE_HTTP_PROTOCOL_VERSION,
                "session-1",
            ),
        )

    def test_split_proxy_path_decodes_only_one_safe_segment(self):
        self.assertEqual("ai net", split_proxy_server_name("/ca/codex-mcp/ai%20net"))
        self.assertIsNone(split_proxy_server_name("/ca/codex-mcp/a/b"))

    def test_upstream_url_preserves_existing_query(self):
        self.assertEqual(
            "https://example.test/mcp?token=x&cursor=2",
            upstream_url({"url": "https://example.test/mcp?token=x"}, "cursor=2"),
        )

    def test_default_streamable_revision_keeps_latest_session_sse_protocol(self):
        self.assertEqual("2025-11-25", MCP_STREAMABLE_HTTP_PROTOCOL_VERSION)

    def test_initialize_result_selects_protocol_for_followup_sse_requests(self):
        result = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"protocolVersion": "2025-06-18"},
        }

        self.assertEqual(
            "2025-06-18",
            negotiated_protocol_version(result, MCP_STREAMABLE_HTTP_PROTOCOL_VERSION),
        )

    def test_missing_initialize_revision_keeps_requested_protocol(self):
        self.assertEqual(
            MCP_STREAMABLE_HTTP_PROTOCOL_VERSION,
            negotiated_protocol_version({}, MCP_STREAMABLE_HTTP_PROTOCOL_VERSION),
        )


if __name__ == "__main__":
    unittest.main()
