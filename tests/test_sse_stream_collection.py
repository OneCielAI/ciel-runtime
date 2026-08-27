"""Assembling SSE collection responses, and cutting a loop off mid-stream."""

import json
import unittest

from ciel_runtime_support.sse_stream_collection import (
    UpstreamSseError,
    collect_anthropic_message_stream,
    collect_openai_chat_stream,
    iter_sse_payloads,
)

LOOP = (
    "먼저 관련 레포와 지표 계산/차트 데이터 경로를 찾겠습니다. "
    "responseZEC 4h/8h 인디케이터 누락 원인을 확인하겠습니다. "
)
OFFERED = 4000


def sse(*payloads):
    return [b"data: " + json.dumps(p, ensure_ascii=False).encode() + b"\n" for p in payloads]


class CountingStream:
    def __init__(self, lines):
        self._lines = list(lines)
        self.consumed = 0

    def __iter__(self):
        for line in self._lines:
            self.consumed += 1
            yield line


class SsePayloadTests(unittest.TestCase):
    def test_framing_keepalives_and_done_are_ignored(self):
        lines = [
            b"event: content_block_delta\n",
            b": keepalive\n",
            b"\n",
            b'data: {"type":"ok"}\n',
            b"data: [DONE]\n",
            b"data: not json\n",
        ]

        self.assertEqual([{"type": "ok"}], list(iter_sse_payloads(lines)))


class OpenAIChatStreamCollectionTests(unittest.TestCase):
    def test_strict_mode_rejects_clean_eof_before_terminal_event(self):
        lines = sse({"choices": [{"delta": {"content": "partial"}}]})

        with self.assertRaises(UpstreamSseError) as caught:
            collect_openai_chat_stream(lines, strict=True)

        self.assertEqual("incomplete_stream", caught.exception.code)
        self.assertTrue(caught.exception.output_started)

    def test_strict_mode_rejects_malformed_json(self):
        with self.assertRaises(UpstreamSseError) as caught:
            collect_openai_chat_stream([b"data: {broken\n"], strict=True)

        self.assertEqual("invalid_stream", caught.exception.code)

    def test_strict_mode_accepts_done_sentinel(self):
        lines = sse({"choices": [{"delta": {"content": "ok"}}]})
        lines.append(b"data: [DONE]\n")

        collected = collect_openai_chat_stream(lines, strict=True)

        self.assertEqual(
            "ok", collected.response["choices"][0]["message"]["content"]
        )

    def test_strict_mode_accepts_multiline_data_event(self):
        lines = [
            b'data: {"choices":[{"delta":{"content":\n',
            b'data: "ok"},"finish_reason":"stop"}]}\n',
            b"\n",
        ]

        collected = collect_openai_chat_stream(lines, strict=True)

        self.assertEqual(
            "ok", collected.response["choices"][0]["message"]["content"]
        )

    def test_strict_mode_rejects_done_without_a_choice(self):
        with self.assertRaisesRegex(UpstreamSseError, "no choice"):
            collect_openai_chat_stream([b"data: [DONE]\n"], strict=True)

    def test_strict_mode_rejects_malformed_tool_arguments(self):
        lines = sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "function": {
                                        "name": "delete_file",
                                        "arguments": '{"path":',
                                    },
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        )
        lines.append(b"data: [DONE]\n")

        with self.assertRaisesRegex(UpstreamSseError, "malformed arguments"):
            collect_openai_chat_stream(lines, strict=True)

    def test_strict_mode_rejects_tool_calls_with_non_tool_finish_reason(self):
        lines = sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": "{}",
                                    },
                                }
                            ]
                        },
                        "finish_reason": "stop",
                    }
                ]
            }
        )

        with self.assertRaisesRegex(UpstreamSseError, "inconsistent"):
            collect_openai_chat_stream(lines, strict=True)

    def test_strict_mode_rejects_tool_finish_reason_without_tool_calls(self):
        lines = sse(
            {"choices": [{"delta": {"content": "done"}, "finish_reason": "tool_calls"}]}
        )

        with self.assertRaisesRegex(UpstreamSseError, "inconsistent"):
            collect_openai_chat_stream(lines, strict=True)

    def test_strict_mode_rejects_invalid_utf8(self):
        with self.assertRaisesRegex(UpstreamSseError, "invalid UTF-8"):
            collect_openai_chat_stream(
                [b'data: {"choices":[]}' + bytes([0xFF]) + b"\n"], strict=True
            )

    def test_surfaces_an_error_event_instead_of_returning_an_empty_answer(self):
        lines = sse({"error": {"type": "internal_server_error", "message": "We're currently experiencing high demand, which may cause temporary errors."}})

        with self.assertRaises(UpstreamSseError) as caught:
            collect_openai_chat_stream(lines)

        self.assertEqual("internal_server_error", caught.exception.code)
        self.assertFalse(caught.exception.output_started)

    def test_assembles_text_reasoning_and_tool_calls(self):
        lines = sse(
            {"id": "chatcmpl-1", "model": "deepseek-chat", "choices": [{"delta": {"reasoning_content": "thinking "}}]},
            {"choices": [{"delta": {"reasoning_content": "hard"}}]},
            {"choices": [{"delta": {"content": "Hello "}}]},
            {"choices": [{"delta": {"content": "world"}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_a", "function": {"name": "Read", "arguments": '{"pa'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'th":"x"}'}}]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 11, "completion_tokens": 22}},
        )

        collection = collect_openai_chat_stream(lines)

        self.assertIsNone(collection.verdict)
        choice = collection.response["choices"][0]
        self.assertEqual("Hello world", choice["message"]["content"])
        self.assertEqual("thinking hard", choice["message"]["reasoning_content"])
        self.assertEqual("tool_calls", choice["finish_reason"])
        call = choice["message"]["tool_calls"][0]
        self.assertEqual("call_a", call["id"])
        self.assertEqual("Read", call["function"]["name"])
        self.assertEqual({"path": "x"}, json.loads(call["function"]["arguments"]))
        self.assertEqual(11, collection.response["usage"]["prompt_tokens"])
        self.assertEqual("chatcmpl-1", collection.response["id"])

    def test_orders_parallel_tool_calls_by_index(self):
        lines = sse(
            {"choices": [{"delta": {"tool_calls": [{"index": 1, "id": "b", "function": {"name": "Two", "arguments": "{}"}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "a", "function": {"name": "One", "arguments": "{}"}}]}}]},
        )

        calls = collect_openai_chat_stream(lines).response["choices"][0]["message"]["tool_calls"]

        self.assertEqual(["One", "Two"], [call["function"]["name"] for call in calls])

    def test_a_loop_is_cut_off_mid_stream(self):
        stream = CountingStream(sse(*({"choices": [{"delta": {"content": LOOP}}]} for _ in range(OFFERED))))

        collection = collect_openai_chat_stream(stream)

        self.assertIsNotNone(collection.verdict)
        self.assertLess(stream.consumed, 60)
        self.assertLess(len(collection.response["choices"][0]["message"]["content"]), 4000)

    def test_a_reasoning_loop_is_cut_off_too(self):
        stream = CountingStream(
            sse(*({"choices": [{"delta": {"reasoning_content": LOOP}}]} for _ in range(OFFERED)))
        )

        collection = collect_openai_chat_stream(stream)

        self.assertIsNotNone(collection.verdict)
        self.assertLess(stream.consumed, 60)


class AnthropicMessageStreamCollectionTests(unittest.TestCase):
    def message_lines(self, *events):
        return sse(
            {
                "type": "message_start",
                "message": {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "model": "deepseek-v4-flash",
                    "content": [],
                    "usage": {"input_tokens": 7},
                },
            },
            *events,
        )

    def test_assembles_thinking_text_and_tool_use_in_order(self):
        lines = self.message_lines(
            {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "reasoning"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Hello "}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "world"}},
            {"type": "content_block_stop", "index": 1},
            {"type": "content_block_start", "index": 2, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {}}},
            {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"path"'}},
            {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": ':"x"}'}},
            {"type": "content_block_stop", "index": 2},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 42}},
            {"type": "message_stop"},
        )

        collection = collect_anthropic_message_stream(lines)

        message = collection.response
        self.assertIsNone(collection.verdict)
        self.assertEqual("msg_1", message["id"])
        self.assertEqual("deepseek-v4-flash", message["model"])
        self.assertEqual("tool_use", message["stop_reason"])
        self.assertEqual(["thinking", "text", "tool_use"], [b["type"] for b in message["content"]])
        self.assertEqual("reasoning", message["content"][0]["thinking"])
        self.assertEqual("sig", message["content"][0]["signature"])
        self.assertEqual("Hello world", message["content"][1]["text"])
        self.assertEqual({"path": "x"}, message["content"][2]["input"])
        self.assertEqual(7, message["usage"]["input_tokens"])
        self.assertEqual(42, message["usage"]["output_tokens"])

    def test_strict_mode_rejects_clean_eof_before_message_stop(self):
        lines = self.message_lines(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": "partial"},
            }
        )

        with self.assertRaises(UpstreamSseError) as caught:
            collect_anthropic_message_stream(lines, strict=True)

        self.assertEqual("incomplete_stream", caught.exception.code)
        self.assertTrue(caught.exception.output_started)

    def test_strict_mode_rejects_malformed_json(self):
        with self.assertRaises(UpstreamSseError) as caught:
            collect_anthropic_message_stream([b"data: {broken\n"], strict=True)

        self.assertEqual("invalid_stream", caught.exception.code)

    def test_strict_mode_accepts_message_stop(self):
        collected = collect_anthropic_message_stream(
            self.message_lines(
                {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
                {"type": "message_stop"},
            ),
            strict=True,
        )

        self.assertEqual("msg_1", collected.response["id"])

    def test_strict_mode_accepts_multiline_data_event(self):
        lines = [
            b'data: {"type":"message_start",\n',
            b'data: "message":{"id":"msg_1","type":"message","role":"assistant",'
            b'"model":"x","content":[]}}\n',
            b"\n",
            b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}\n',
            b"\n",
            b'data: {"type":"message_stop"}\n',
            b"\n",
        ]

        collected = collect_anthropic_message_stream(lines, strict=True)

        self.assertEqual("msg_1", collected.response["id"])

    def test_strict_mode_rejects_terminal_only_stream(self):
        with self.assertRaisesRegex(UpstreamSseError, "message_start"):
            collect_anthropic_message_stream(
                sse({"type": "message_stop"}), strict=True
            )

    def test_strict_mode_rejects_malformed_tool_input(self):
        lines = self.message_lines(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "call_1",
                    "name": "delete_file",
                    "input": {},
                },
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"path":'},
            },
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
            {"type": "message_stop"},
        )

        with self.assertRaisesRegex(UpstreamSseError, "malformed JSON"):
            collect_anthropic_message_stream(lines, strict=True)

    def test_strict_mode_rejects_non_object_tool_input(self):
        lines = self.message_lines(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "call_1",
                    "name": "delete_file",
                    "input": "bad",
                },
            },
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
            {"type": "message_stop"},
        )

        with self.assertRaisesRegex(UpstreamSseError, "object input"):
            collect_anthropic_message_stream(lines, strict=True)

    def test_strict_mode_rejects_tool_use_with_end_turn(self):
        lines = self.message_lines(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "call_1",
                    "name": "read_file",
                    "input": {},
                },
            },
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
            {"type": "message_stop"},
        )

        with self.assertRaisesRegex(UpstreamSseError, "inconsistent"):
            collect_anthropic_message_stream(lines, strict=True)

    def test_strict_mode_rejects_tool_stop_reason_without_tool_use(self):
        lines = self.message_lines(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": "done"},
            },
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
            {"type": "message_stop"},
        )

        with self.assertRaisesRegex(UpstreamSseError, "inconsistent"):
            collect_anthropic_message_stream(lines, strict=True)

    def test_strict_mode_rejects_invalid_utf8(self):
        with self.assertRaisesRegex(UpstreamSseError, "invalid UTF-8"):
            collect_anthropic_message_stream(
                [b'data: {"type":"message_stop"}' + bytes([0xFF]) + b"\n"],
                strict=True,
            )

    def test_unparsable_tool_input_keeps_the_started_block(self):
        lines = self.message_lines(
            {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "t", "name": "Read", "input": {}}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": "{broken"}},
            {"type": "content_block_stop", "index": 0},
        )

        message = collect_anthropic_message_stream(lines).response

        self.assertEqual({}, message["content"][0]["input"])

    def test_a_loop_is_cut_off_mid_stream(self):
        stream = CountingStream(
            self.message_lines(
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                *(
                    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": LOOP}}
                    for _ in range(OFFERED)
                ),
            )
        )

        collection = collect_anthropic_message_stream(stream)

        self.assertIsNotNone(collection.verdict)
        self.assertLess(stream.consumed, 60)
        self.assertEqual("max_tokens", collection.response["stop_reason"])
        self.assertLess(len(collection.response["content"][0]["text"]), 4000)

    def test_a_thinking_loop_is_cut_off_too(self):
        stream = CountingStream(
            self.message_lines(
                {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
                *(
                    {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": LOOP}}
                    for _ in range(OFFERED)
                ),
            )
        )

        collection = collect_anthropic_message_stream(stream)

        self.assertIsNotNone(collection.verdict)
        self.assertLess(stream.consumed, 60)

    def test_an_upstream_stop_reason_is_not_overwritten(self):
        lines = self.message_lines(
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": "hi"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
        )

        self.assertEqual("end_turn", collect_anthropic_message_stream(lines).response["stop_reason"])


if __name__ == "__main__":
    unittest.main()
