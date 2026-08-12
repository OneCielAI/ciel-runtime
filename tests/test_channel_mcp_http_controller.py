from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from ciel_runtime_support.channel_mcp_http_controller import (
    ChannelMcpHttpController,
    ChannelMcpHttpServices,
)


class ChannelMcpHttpControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.responses: list[tuple[dict[str, Any] | None, int]] = []

        def write_json(_handler: Any, payload: dict[str, Any], status: int = 200) -> None:
            self.responses.append((payload, status))

        def accepted(_handler: Any) -> None:
            self.responses.append((None, 202))

        def call_tool(request_id: Any, _params: dict[str, Any]) -> dict[str, Any]:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"content": [{"type": "text", "text": "ok"}]},
            }

        self.controller = ChannelMcpHttpController(
            ChannelMcpHttpServices(
                version="test",
                tool_schemas=lambda: [{"name": "send_message", "inputSchema": {"type": "object"}}],
                tool_call_response=call_tool,
                write_json=write_json,
                write_accepted=accepted,
                log=lambda _level, _message: None,
            )
        )

    @staticmethod
    def handler(headers: dict[str, str] | None = None) -> Any:
        return SimpleNamespace(headers=headers or {}, path="/ca/mcp")

    @staticmethod
    def modern_request(method: str, request_id: Any = 1, **params: Any) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": {
                **params,
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientCapabilities": {},
                },
            },
        }

    def test_deprecated_sse_endpoint_is_absent(self) -> None:
        self.assertFalse(self.controller.get(self.handler(), "/ca/mcp/sse"))

    def test_2026_discovery_is_stateless_and_cacheable(self) -> None:
        body = self.modern_request("server/discover")
        headers = {
            "MCP-Protocol-Version": "2026-07-28",
            "Mcp-Method": "server/discover",
        }
        self.assertTrue(self.controller.post(self.handler(headers), "/ca/mcp", body))
        payload, status = self.responses[-1]
        self.assertEqual(200, status)
        result = payload["result"]
        self.assertEqual("complete", result["resultType"])
        self.assertEqual(["2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26"], result["supportedVersions"])
        self.assertEqual({"tools": {"listChanged": False}}, result["capabilities"])

    def test_2026_tool_call_requires_matching_name_header(self) -> None:
        body = self.modern_request("tools/call", name="send_message", arguments={})
        headers = {
            "MCP-Protocol-Version": "2026-07-28",
            "Mcp-Method": "tools/call",
            "Mcp-Name": "wrong",
        }
        self.controller.post(self.handler(headers), "/ca/mcp", body)
        payload, status = self.responses[-1]
        self.assertEqual(400, status)
        self.assertEqual(-32020, payload["error"]["code"])

    def test_unknown_protocol_version_returns_standard_error(self) -> None:
        body = self.modern_request("tools/list")
        body["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] = "2099-01-01"
        headers = {
            "MCP-Protocol-Version": "2099-01-01",
            "Mcp-Method": "tools/list",
        }
        self.controller.post(self.handler(headers), "/ca/mcp", body)
        payload, status = self.responses[-1]
        self.assertEqual(400, status)
        self.assertEqual(-32022, payload["error"]["code"])

    def test_2026_tool_result_has_result_type(self) -> None:
        body = self.modern_request("tools/call", name="send_message", arguments={})
        headers = {
            "MCP-Protocol-Version": "2026-07-28",
            "Mcp-Method": "tools/call",
            "Mcp-Name": "send_message",
        }
        self.controller.post(self.handler(headers), "/ca/mcp", body)
        payload, status = self.responses[-1]
        self.assertEqual(200, status)
        self.assertEqual("complete", payload["result"]["resultType"])

    def test_2026_removed_ping_method_is_not_advertised(self) -> None:
        body = self.modern_request("ping")
        headers = {
            "MCP-Protocol-Version": "2026-07-28",
            "Mcp-Method": "ping",
        }
        self.controller.post(self.handler(headers), "/ca/mcp", body)
        payload, status = self.responses[-1]
        self.assertEqual(404, status)
        self.assertEqual(-32601, payload["error"]["code"])

    def test_legacy_streamable_http_initialize_stays_stateless(self) -> None:
        body = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25"},
        }
        self.controller.post(self.handler(), "/ca/mcp", body)
        payload, status = self.responses[-1]
        self.assertEqual(200, status)
        self.assertEqual("2025-11-25", payload["result"]["protocolVersion"])
        self.assertNotIn("sessionId", payload["result"])


if __name__ == "__main__":
    unittest.main()
