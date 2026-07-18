import io
import unittest

from ciel_runtime_support.mcp_transport import (
    split_proxy_server_name,
    read_sse_json_response,
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
                "MCP-Protocol-Version": "2025-03-26",
                "Mcp-Session-Id": "session-1",
            },
            streamable_headers({"Authorization": "Bearer token"}, "2025-03-26", "session-1"),
        )

    def test_split_proxy_path_decodes_only_one_safe_segment(self):
        self.assertEqual("ai net", split_proxy_server_name("/ca/codex-mcp/ai%20net"))
        self.assertIsNone(split_proxy_server_name("/ca/codex-mcp/a/b"))

    def test_upstream_url_preserves_existing_query(self):
        self.assertEqual(
            "https://example.test/mcp?token=x&cursor=2",
            upstream_url({"url": "https://example.test/mcp?token=x"}, "cursor=2"),
        )


if __name__ == "__main__":
    unittest.main()
