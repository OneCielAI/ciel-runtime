"""End-to-end proof that a repetition loop ends the turn instead of running on."""

import json
import os
import unittest
from unittest import mock

import ciel_runtime
from ciel_runtime_support.response_collection_context import ResponseCollectionContext

# The block the reported session repeated until the request timed out.
LOOP = (
    "먼저 관련 레포와 지표 계산/차트 데이터 경로를 찾겠습니다. "
    "responseZEC 4h/8h 인디케이터 누락 원인을 확인하겠습니다. "
)
OFFERED_CHUNKS = 400


class ReadlineResponse:
    """Ollama NDJSON upstream; unread items prove the guard stopped reading."""

    def __init__(self, items):
        self.items = list(items)
        self.closed = False

    def readline(self):
        if not self.items:
            return b""
        return self.items.pop(0)

    def close(self):
        self.closed = True


class IterResponse:
    """SSE upstream consumed by iteration; counts what was actually read."""

    def __init__(self, items):
        self._items = list(items)
        self.consumed = 0
        self.closed = False

    def __iter__(self):
        for item in self._items:
            self.consumed += 1
            yield item

    def close(self):
        self.closed = True


class CaptureWrite:
    def __init__(self):
        self.data = bytearray()

    def write(self, data):
        self.data.extend(data)

    def flush(self):
        pass


class FakeHandler:
    headers: dict = {}
    connection = None

    def __init__(self):
        self.wfile = CaptureWrite()

    def send_response(self, _status):
        pass

    def send_header(self, _name, _value):
        pass

    def end_headers(self):
        pass


def sse_events(payload: bytes) -> list[dict]:
    events = []
    for block in payload.decode("utf-8").split("\n\n"):
        for line in block.split("\n"):
            if line.startswith("data:"):
                try:
                    events.append(json.loads(line[5:].strip()))
                except ValueError:
                    pass
    return events


def stop_reason_of(events: list[dict]) -> str | None:
    for event in reversed(events):
        if event.get("type") == "message_delta":
            return (event.get("delta") or {}).get("stop_reason")
    return None


def emitted_text(events: list[dict]) -> str:
    parts = []
    for event in events:
        delta = event.get("delta") or {}
        if event.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
            parts.append(delta.get("text") or "")
        block = event.get("content_block") or {}
        if event.get("type") == "content_block_start" and block.get("type") == "text":
            parts.append(block.get("text") or "")
    return "".join(parts)


class OllamaStreamRunawayTests(unittest.TestCase):
    def run_stream(self, chunks):
        resp = ReadlineResponse(chunks)
        handler = FakeHandler()
        with mock.patch.object(
            ciel_runtime, "router_client_connection_closed", return_value=False
        ):
            ciel_runtime._ollama_stream_to_anthropic_sse(
                handler, resp, "deepseek-v4-flash:0731", provider="ollama-cloud", idle_timeout=30.0
            )
        return resp, sse_events(bytes(handler.wfile.data))

    def test_repetition_loop_ends_the_turn_early(self):
        chunks = [
            json.dumps({"message": {"content": LOOP}, "done": False}).encode() + b"\n"
            for _ in range(OFFERED_CHUNKS)
        ]
        chunks.append(json.dumps({"message": {"content": ""}, "done": True}).encode() + b"\n")

        resp, events = self.run_stream(chunks)

        self.assertGreater(len(resp.items), 0, "guard kept reading the whole loop")
        self.assertLess(OFFERED_CHUNKS - len(resp.items), 60)
        self.assertTrue(resp.closed)
        self.assertEqual("max_tokens", stop_reason_of(events))
        self.assertIn("[ciel-runtime] Stopped a runaway repetition loop", emitted_text(events))

    def test_repeated_thinking_ends_the_turn_early(self):
        chunks = [
            json.dumps({"message": {"thinking": LOOP}, "done": False}).encode() + b"\n"
            for _ in range(OFFERED_CHUNKS)
        ]

        resp, events = self.run_stream(chunks)

        self.assertGreater(len(resp.items), 0)
        self.assertEqual("max_tokens", stop_reason_of(events))
        self.assertIn("[ciel-runtime] Stopped a runaway repetition loop", emitted_text(events))

    def test_loop_with_variation_between_repeats_ends_the_turn_early(self):
        # Not strictly periodic: every pass carries a different attempt number,
        # which is the shape a real loop usually has.
        chunks = [
            json.dumps(
                {
                    "message": {"content": LOOP + f" 시도 {index} 번째 경로를 확인합니다.\n"},
                    "done": False,
                },
                ensure_ascii=False,
            ).encode()
            + b"\n"
            for index in range(OFFERED_CHUNKS)
        ]

        resp, events = self.run_stream(chunks)

        self.assertGreater(len(resp.items), 0, "guard kept reading the whole loop")
        self.assertTrue(resp.closed)
        self.assertEqual("max_tokens", stop_reason_of(events))
        self.assertIn("[ciel-runtime] Stopped a runaway repetition loop", emitted_text(events))

    def test_healthy_stream_is_untouched(self):
        chunks = [
            json.dumps(
                {"message": {"content": f"step {index}: distinct progress\n"}, "done": False}
            ).encode()
            + b"\n"
            for index in range(OFFERED_CHUNKS)
        ]
        chunks.append(json.dumps({"message": {"content": ""}, "done": True}).encode() + b"\n")

        resp, events = self.run_stream(chunks)

        self.assertEqual([], resp.items)
        self.assertEqual("end_turn", stop_reason_of(events))
        self.assertNotIn("[ciel-runtime]", emitted_text(events))

    def test_kill_switch_restores_the_old_behaviour(self):
        chunks = [
            json.dumps({"message": {"content": LOOP}, "done": False}).encode() + b"\n"
            for _ in range(OFFERED_CHUNKS)
        ]
        chunks.append(json.dumps({"message": {"content": ""}, "done": True}).encode() + b"\n")

        with mock.patch.dict(os.environ, {"CIEL_RUNTIME_RUNAWAY_GUARD": "off"}):
            resp, events = self.run_stream(chunks)

        self.assertEqual([], resp.items)
        self.assertEqual("end_turn", stop_reason_of(events))


class AnthropicPassthroughRunawayTests(unittest.TestCase):
    """deepseek.com runs native Anthropic passthrough, not the Ollama path."""

    def upstream_lines(self, count):
        lines = [
            b'event: message_start\n',
            b'data: {"type":"message_start","message":{"id":"msg_1","content":[]}}\n',
            b"\n",
            b'event: content_block_start\n',
            b'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n',
            b"\n",
        ]
        for _ in range(count):
            payload = json.dumps(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": LOOP},
                },
                ensure_ascii=False,
            )
            lines.extend(
                [b"event: content_block_delta\n", f"data: {payload}\n".encode(), b"\n"]
            )
        lines.extend(
            [
                b"event: message_delta\n",
                b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}\n',
                b"\n",
                b"event: message_stop\n",
                b'data: {"type":"message_stop"}\n',
                b"\n",
            ]
        )
        return lines

    def run_stream(self, count):
        resp = IterResponse(self.upstream_lines(count))
        handler = FakeHandler()
        # A zero keepalive interval reads the upstream inline, so `consumed`
        # measures what the guard actually pulled off the wire.
        with mock.patch.dict(
            os.environ, {"CIEL_RUNTIME_ANTHROPIC_STREAM_KEEPALIVE_SECONDS": "0"}
        ):
            ciel_runtime._rebatch_anthropic_sse_text(
                handler,
                resp,
                model="deepseek-v4-flash",
                word_chunking=False,
                provider="deepseek",
            )
        return resp, sse_events(bytes(handler.wfile.data))

    def test_repetition_loop_ends_the_turn_early(self):
        resp, events = self.run_stream(OFFERED_CHUNKS)

        self.assertLess(resp.consumed, OFFERED_CHUNKS * 3)
        self.assertTrue(resp.closed)
        self.assertEqual("max_tokens", stop_reason_of(events))
        self.assertIn("[ciel-runtime] Stopped a runaway repetition loop", emitted_text(events))

    def test_healthy_stream_keeps_its_own_stop_reason(self):
        resp = IterResponse(
            [
                b"event: message_start\n",
                b'data: {"type":"message_start","message":{"id":"msg_1","content":[]}}\n',
                b"\n",
                b"event: content_block_start\n",
                b'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n',
                b"\n",
                b"event: content_block_delta\n",
                b'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"all good"}}\n',
                b"\n",
                b"event: content_block_stop\n",
                b'data: {"type":"content_block_stop","index":0}\n',
                b"\n",
                b"event: message_delta\n",
                b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}\n',
                b"\n",
                b"event: message_stop\n",
                b'data: {"type":"message_stop"}\n',
                b"\n",
            ]
        )
        handler = FakeHandler()
        with mock.patch.dict(
            os.environ, {"CIEL_RUNTIME_ANTHROPIC_STREAM_KEEPALIVE_SECONDS": "0"}
        ):
            ciel_runtime._rebatch_anthropic_sse_text(
                handler, resp, model="deepseek-v4-flash", word_chunking=False, provider="deepseek"
            )
        events = sse_events(bytes(handler.wfile.data))

        self.assertEqual("end_turn", stop_reason_of(events))
        self.assertNotIn("[ciel-runtime]", emitted_text(events))


class OpenAIChatStreamRunawayTests(unittest.TestCase):
    def run_stream(self, chunks):
        resp = IterResponse(chunks)
        handler = FakeHandler()
        ciel_runtime.stream_openai_chat_to_anthropic_sse(
            handler, resp, "deepseek-chat", "deepseek", source_body=None
        )
        return resp, sse_events(bytes(handler.wfile.data))

    def test_repetition_loop_ends_the_turn_early(self):
        chunks = [
            b"data: "
            + json.dumps(
                {"choices": [{"delta": {"content": LOOP}}]}, ensure_ascii=False
            ).encode()
            + b"\n"
            for _ in range(OFFERED_CHUNKS)
        ]
        chunks.append(b"data: [DONE]\n")

        resp, events = self.run_stream(chunks)

        self.assertLess(resp.consumed, OFFERED_CHUNKS)
        self.assertEqual("max_tokens", stop_reason_of(events))
        self.assertIn("[ciel-runtime] Stopped a runaway repetition loop", emitted_text(events))

    def test_healthy_stream_is_untouched(self):
        chunks = [
            b"data: "
            + json.dumps({"choices": [{"delta": {"content": f"step {i}: fine\n"}}]}).encode()
            + b"\n"
            for i in range(OFFERED_CHUNKS)
        ]
        chunks.append(b"data: [DONE]\n")

        _resp, events = self.run_stream(chunks)

        self.assertEqual("end_turn", stop_reason_of(events))
        self.assertNotIn("[ciel-runtime]", emitted_text(events))


class CollectedMessageRunawayTests(unittest.TestCase):
    """Codex is served from a collected message, so the guard trims it there."""

    def test_looping_message_is_trimmed_and_reported(self):
        message = {
            "role": "assistant",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "확인하겠습니다.\n\n" + LOOP * 60}],
        }

        guarded = ResponseCollectionContext.guard_runaway(message)

        self.assertEqual("max_tokens", guarded["stop_reason"])
        self.assertEqual("확인하겠습니다.\n\n" + LOOP, guarded["content"][0]["text"])
        self.assertIn("Stopped a runaway repetition loop", guarded["content"][-1]["text"])
        self.assertEqual("end_turn", message["stop_reason"])

    def test_healthy_message_is_returned_unchanged(self):
        message = {
            "role": "assistant",
            "stop_reason": "tool_use",
            "content": [{"type": "text", "text": "Looks fine, running the tests now."}],
        }

        self.assertIs(message, ResponseCollectionContext.guard_runaway(message))


if __name__ == "__main__":
    unittest.main()
