import json
import unittest

from ciel_runtime_support.mcp_probe_codec import (
    channel_capability_present,
    decode_sse_events,
    find_initialize_response,
    initialize_payload_bytes,
    probe_strategy,
)


class McpProbeCodecTests(unittest.TestCase):
    def test_probe_strategy_requires_explicit_legacy_framing(self):
        self.assertEqual("jsonl", probe_strategy({}))
        self.assertEqual("framed", probe_strategy({"stdio_mode": "content-length"}))

    def test_finds_initialize_response_in_both_stdio_formats(self):
        message = {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}
        body = json.dumps(message).encode()
        framed = b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body

        self.assertEqual(message, find_initialize_response(body + b"\n", framed=False))
        self.assertEqual(message, find_initialize_response(framed, framed=True))

    def test_detects_declared_channel_capability(self):
        response = {"result": {"capabilities": {"experimental": {"claude/channel": {}}}}}
        self.assertTrue(channel_capability_present(response))
        self.assertFalse(channel_capability_present({"result": {"capabilities": {}}}))

    def test_initialize_payload_uses_supplied_version(self):
        payload = json.loads(initialize_payload_bytes("1.2.3"))
        self.assertEqual("1.2.3", payload["params"]["clientInfo"]["version"])

    def test_sse_decoder_preserves_partial_event(self):
        events, remainder = decode_sse_events(bytearray(b"event: endpoint\ndata: /rpc\n\ndata: partial"))
        self.assertEqual([("endpoint", "/rpc")], events)
        self.assertEqual(bytearray(b"data: partial"), remainder)


if __name__ == "__main__":
    unittest.main()
