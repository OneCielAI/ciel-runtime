import io
import json
import unittest

from ciel_runtime_support.protocols.openai_responses import (
    openai_response_to_anthropic_message,
)
from ciel_runtime_support.remote_bridge import REMOTE_BRIDGE_CONTEXT_ATTRIBUTE
from ciel_runtime_support.responses_anthropic_stream import (
    ResponsesAnthropicStreamWriter,
)


class _Handler:
    def __init__(self, *, remote: bool = False) -> None:
        self.status = None
        self.headers = []
        self.wfile = io.BytesIO()
        setattr(self, REMOTE_BRIDGE_CONTEXT_ATTRIBUTE, remote)

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, name: str, value: str) -> None:
        self.headers.append((name, value))

    def end_headers(self) -> None:
        return None


def _response(
    *,
    status: str = "completed",
    output: list[dict] | None = None,
    incomplete_reason: str | None = None,
) -> dict:
    value = {
        "id": "resp_test",
        "object": "response",
        "status": status,
        "model": "gpt-5.6-sol",
        "output": output or [],
        "usage": {
            "input_tokens": 11,
            "output_tokens": 7,
            "input_tokens_details": {
                "cached_tokens": 3,
                "cache_write_tokens": 0,
            },
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 18,
        },
    }
    if incomplete_reason is not None:
        value["incomplete_details"] = {"reason": incomplete_reason}
    return value


def _sse_lines(events: list[dict]) -> list[bytes]:
    lines = []
    for event in events:
        lines.extend(
            (
                f"event: {event['type']}\n".encode(),
                (
                    "data: "
                    + json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                    + "\n"
                ).encode(),
                b"\n",
            )
        )
    return lines


def _parse_events(raw: bytes) -> list[tuple[str, dict]]:
    events = []
    for frame in raw.decode("utf-8").split("\n\n"):
        if not frame:
            continue
        name = ""
        data_lines = []
        for line in frame.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        events.append((name, json.loads("\n".join(data_lines))))
    return events


class ResponsesAnthropicStreamWriterTests(unittest.TestCase):
    def run_stream(
        self, events: list[dict], *, remote: bool = False
    ) -> tuple[_Handler, list[tuple[str, dict]]]:
        return self.run_lines(_sse_lines(events), remote=remote)

    def run_lines(
        self, lines: list[bytes], *, remote: bool = False
    ) -> tuple[_Handler, list[tuple[str, dict]]]:
        handler = _Handler(remote=remote)
        ResponsesAnthropicStreamWriter(
            openai_response_to_anthropic_message
        ).forward(handler, lines, "gpt-5.6-sol")
        return handler, _parse_events(handler.wfile.getvalue())

    def test_reasoning_reuses_its_reserved_index_without_a_gap(self):
        reasoning = {
            "type": "reasoning",
            "id": "rs_1",
            "status": "completed",
            "summary": [],
            "encrypted_content": "sealed",
        }
        message = {
            "type": "message",
            "id": "msg_1",
            "role": "assistant",
            "status": "completed",
            "content": [
                {"type": "output_text", "text": "hello", "annotations": []}
            ],
        }
        _, events = self.run_stream(
            [
                {"type": "response.created", "response": _response()},
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {**reasoning, "status": "in_progress", "encrypted_content": None},
                },
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": reasoning,
                },
                {
                    "type": "response.output_item.added",
                    "output_index": 1,
                    "item": {**message, "status": "in_progress", "content": []},
                },
                {
                    "type": "response.output_text.delta",
                    "item_id": "msg_1",
                    "output_index": 1,
                    "content_index": 0,
                    "delta": "hello",
                },
                {
                    "type": "response.output_item.done",
                    "output_index": 1,
                    "item": message,
                },
                {
                    "type": "response.completed",
                    "response": _response(output=[reasoning, message]),
                },
            ]
        )

        starts = [payload for name, payload in events if name == "content_block_start"]
        self.assertEqual([0, 1], [event["index"] for event in starts])
        self.assertEqual(
            ["redacted_thinking", "text"],
            [event["content_block"]["type"] for event in starts],
        )

    def test_content_index_creates_distinct_text_blocks(self):
        message = {
            "type": "message",
            "id": "msg_multi",
            "role": "assistant",
            "status": "completed",
            "content": [
                {"type": "output_text", "text": "first", "annotations": []},
                {"type": "output_text", "text": "second", "annotations": []},
            ],
        }
        _, events = self.run_stream(
            [
                {"type": "response.created", "response": _response()},
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {**message, "status": "in_progress", "content": []},
                },
                {
                    "type": "response.output_text.delta",
                    "item_id": "msg_multi",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "first",
                },
                {
                    "type": "response.output_text.delta",
                    "item_id": "msg_multi",
                    "output_index": 0,
                    "content_index": 1,
                    "delta": "second",
                },
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": message,
                },
                {
                    "type": "response.completed",
                    "response": _response(output=[message]),
                },
            ]
        )

        starts = [payload for name, payload in events if name == "content_block_start"]
        deltas = [payload for name, payload in events if name == "content_block_delta"]
        self.assertEqual([0, 1], [event["index"] for event in starts])
        self.assertEqual(
            [(0, "first"), (1, "second")],
            [(event["index"], event["delta"]["text"]) for event in deltas],
        )

    def test_done_only_items_catch_up_reasoning_text_and_tool_arguments(self):
        reasoning = {
            "type": "reasoning",
            "id": "rs_done",
            "status": "completed",
            "summary": [],
            "encrypted_content": "sealed-done",
        }
        message = {
            "type": "message",
            "id": "msg_done",
            "role": "assistant",
            "status": "completed",
            "content": [
                {"type": "output_text", "text": "done text", "annotations": []}
            ],
        }
        tool = {
            "type": "function_call",
            "id": "fc_done",
            "call_id": "call_done",
            "name": "lookup",
            "arguments": '{"query":"done"}',
            "status": "completed",
        }
        output = [reasoning, message, tool]
        stream = [{"type": "response.created", "response": _response()}]
        for index, item in enumerate(output):
            stream.extend(
                (
                    {
                        "type": "response.output_item.added",
                        "output_index": index,
                        "item": {
                            **item,
                            "status": "in_progress",
                            **(
                                {"content": []}
                                if item["type"] == "message"
                                else {}
                            ),
                        },
                    },
                    {
                        "type": "response.output_item.done",
                        "output_index": index,
                        "item": item,
                    },
                )
            )
        stream.append(
            {"type": "response.completed", "response": _response(output=output)}
        )

        _, events = self.run_stream(stream)

        starts = [payload for name, payload in events if name == "content_block_start"]
        self.assertEqual([0, 1, 2], [event["index"] for event in starts])
        self.assertEqual(
            ["redacted_thinking", "text", "tool_use"],
            [event["content_block"]["type"] for event in starts],
        )
        text = "".join(
            payload["delta"].get("text", "")
            for name, payload in events
            if name == "content_block_delta"
        )
        arguments = "".join(
            payload["delta"].get("partial_json", "")
            for name, payload in events
            if name == "content_block_delta"
        )
        self.assertEqual("done text", text)
        self.assertEqual({"query": "done"}, json.loads(arguments))

    def test_completed_event_alone_catches_up_full_output(self):
        message = {
            "type": "message",
            "id": "msg_terminal",
            "role": "assistant",
            "status": "completed",
            "content": [
                {"type": "output_text", "text": "terminal", "annotations": []}
            ],
        }
        _, events = self.run_stream(
            [
                {
                    "type": "response.completed",
                    "response": _response(output=[message]),
                }
            ]
        )

        self.assertIn(
            "terminal",
            [
                payload["delta"]["text"]
                for name, payload in events
                if name == "content_block_delta"
            ],
        )

    def test_refusal_is_streamed_and_uses_refusal_stop_reason(self):
        message = {
            "type": "message",
            "id": "msg_refusal",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "refusal", "refusal": "cannot comply"}],
        }
        _, events = self.run_stream(
            [
                {"type": "response.created", "response": _response()},
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {**message, "status": "in_progress", "content": []},
                },
                {
                    "type": "response.refusal.delta",
                    "item_id": "msg_refusal",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "cannot ",
                },
                {
                    "type": "response.refusal.done",
                    "item_id": "msg_refusal",
                    "output_index": 0,
                    "content_index": 0,
                    "refusal": "cannot comply",
                },
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": message,
                },
                {
                    "type": "response.completed",
                    "response": _response(output=[message]),
                },
            ]
        )

        refusal_text = "".join(
            payload["delta"].get("text", "")
            for name, payload in events
            if name == "content_block_delta"
        )
        message_delta = next(
            payload for name, payload in events if name == "message_delta"
        )
        self.assertEqual("cannot comply", refusal_text)
        self.assertEqual("refusal", message_delta["delta"]["stop_reason"])

    def test_refusal_done_without_full_text_does_not_emit_none(self):
        message = {
            "type": "message",
            "id": "msg_refusal_sparse",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "refusal", "refusal": "cannot"}],
        }
        _, events = self.run_stream(
            [
                {"type": "response.created", "response": _response()},
                {
                    "type": "response.refusal.delta",
                    "item_id": "msg_refusal_sparse",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "cannot",
                },
                {
                    "type": "response.refusal.done",
                    "item_id": "msg_refusal_sparse",
                    "output_index": 0,
                    "content_index": 0,
                },
                {
                    "type": "response.completed",
                    "response": _response(output=[message]),
                },
            ]
        )

        refusal_text = "".join(
            payload["delta"].get("text", "")
            for name, payload in events
            if name == "content_block_delta"
        )
        self.assertEqual("cannot", refusal_text)

    def test_custom_tool_stream_is_wrapped_as_anthropic_json_input(self):
        tool = {
            "type": "custom_tool_call",
            "id": "ct_1",
            "call_id": "call_custom",
            "name": "shell",
            "input": "echo hi",
            "status": "completed",
        }
        _, events = self.run_stream(
            [
                {"type": "response.created", "response": _response()},
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {**tool, "status": "in_progress", "input": ""},
                },
                {
                    "type": "response.custom_tool_call_input.delta",
                    "item_id": "ct_1",
                    "output_index": 0,
                    "delta": "echo ",
                },
                {
                    "type": "response.custom_tool_call_input.delta",
                    "item_id": "ct_1",
                    "output_index": 0,
                    "delta": "hi",
                },
                {
                    "type": "response.custom_tool_call_input.done",
                    "item_id": "ct_1",
                    "output_index": 0,
                    "input": "echo hi",
                },
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": tool,
                },
                {
                    "type": "response.completed",
                    "response": _response(output=[tool]),
                },
            ]
        )

        partial_json = "".join(
            payload["delta"].get("partial_json", "")
            for name, payload in events
            if name == "content_block_delta"
        )
        self.assertEqual({"input": "echo hi"}, json.loads(partial_json))

    def test_top_level_error_message_is_preserved(self):
        _, events = self.run_stream(
            [
                {
                    "type": "error",
                    "code": "usage_limit_reached",
                    "message": "quota exhausted",
                    "param": None,
                }
            ]
        )

        self.assertEqual(["error"], [name for name, _payload in events])
        self.assertEqual("quota exhausted", events[0][1]["error"]["message"])

    def test_clean_eof_after_partial_output_emits_error_not_success(self):
        _, events = self.run_stream(
            [
                {"type": "response.created", "response": _response()},
                {
                    "type": "response.output_text.delta",
                    "item_id": "msg_partial",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "partial",
                },
            ]
        )

        names = [name for name, _payload in events]
        self.assertEqual("error", names[-1])
        self.assertNotIn("message_delta", names)
        self.assertNotIn("message_stop", names)
        self.assertIn("before a terminal event", events[-1][1]["error"]["message"])

    def test_malformed_sse_payload_is_converted_to_anthropic_error(self):
        _, events = self.run_lines(
            [
                b"event: response.output_text.delta\n",
                b"data: {not-json}\n",
                b"\n",
            ]
        )

        self.assertEqual(["error"], [name for name, _payload in events])
        self.assertIn("invalid upstream Responses stream", events[0][1]["error"]["message"])

    def test_invalid_utf8_is_an_error_not_replacement_text(self):
        _, events = self.run_lines(
            [
                b'event: response.output_text.delta\n',
                b'data: {"type":"response.output_text.delta","delta":"a\xffb"}\n',
                b"\n",
            ]
        )

        names = [name for name, _payload in events]
        self.assertEqual(["error"], names)
        self.assertIn("invalid upstream Responses stream", events[0][1]["error"]["message"])

    def test_incomplete_max_output_tokens_maps_to_max_tokens(self):
        message = {
            "type": "message",
            "id": "msg_limited",
            "role": "assistant",
            "status": "incomplete",
            "content": [
                {"type": "output_text", "text": "partial", "annotations": []}
            ],
        }
        _, events = self.run_stream(
            [
                {
                    "type": "response.incomplete",
                    "response": _response(
                        status="incomplete",
                        output=[message],
                        incomplete_reason="max_output_tokens",
                    ),
                }
            ]
        )

        message_delta = next(
            payload for name, payload in events if name == "message_delta"
        )
        self.assertEqual("max_tokens", message_delta["delta"]["stop_reason"])

    def test_incomplete_content_filter_maps_to_refusal(self):
        refusal = {
            "type": "message",
            "id": "msg_filtered",
            "role": "assistant",
            "status": "incomplete",
            "content": [{"type": "refusal", "refusal": "filtered"}],
        }
        _, events = self.run_stream(
            [
                {
                    "type": "response.incomplete",
                    "response": _response(
                        status="incomplete",
                        output=[refusal],
                        incomplete_reason="content_filter",
                    ),
                }
            ]
        )

        message_delta = next(
            payload for name, payload in events if name == "message_delta"
        )
        self.assertEqual("refusal", message_delta["delta"]["stop_reason"])

    def test_incomplete_without_supported_reason_is_an_error(self):
        _, events = self.run_stream(
            [
                {
                    "type": "response.incomplete",
                    "response": _response(status="incomplete"),
                }
            ]
        )

        names = [name for name, _payload in events]
        self.assertEqual("error", names[-1])
        self.assertNotIn("message_delta", names)
        self.assertNotIn("message_stop", names)
        self.assertIn("supported reason", events[-1][1]["error"]["message"])

    def test_remote_commentary_stays_visible_and_preserves_phase_envelope(self):
        commentary = {
            "type": "message",
            "id": "msg_commentary",
            "role": "assistant",
            "status": "completed",
            "phase": "commentary",
            "content": [
                {"type": "output_text", "text": "Checking now", "annotations": []}
            ],
        }
        created = {**_response(), "status": "in_progress", "output": []}
        _, events = self.run_stream(
            [
                {"type": "response.created", "response": created},
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {**commentary, "status": "in_progress", "content": []},
                },
                {
                    "type": "response.output_text.delta",
                    "item_id": "msg_commentary",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "Checking now",
                },
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": commentary,
                },
                {
                    "type": "response.completed",
                    "response": _response(output=[commentary]),
                },
            ],
            remote=True,
        )

        text = "".join(
            payload["delta"]["text"]
            for name, payload in events
            if name == "content_block_delta"
            and payload["delta"]["type"] == "text_delta"
        )
        starts = [
            payload["content_block"]
            for name, payload in events
            if name == "content_block_start"
        ]
        self.assertEqual("Checking now", text)
        self.assertEqual(["text", "redacted_thinking"], [b["type"] for b in starts])
        self.assertTrue(
            starts[1]["data"].startswith("ciel-responses-commentary-v1:")
        )

    def test_remote_tool_waits_for_done_and_preserves_late_metadata(self):
        completed_call = {
            "id": "fc_1",
            "type": "function_call",
            "status": "completed",
            "call_id": "call_1",
            "name": "lookup",
            "arguments": "{}",
            "caller": {"type": "direct"},
            "namespace": "crm",
        }
        created = {**_response(), "status": "in_progress", "output": []}
        _, events = self.run_stream(
            [
                {"type": "response.created", "response": created},
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {
                        "id": "fc_1",
                        "type": "function_call",
                        "status": "in_progress",
                        "call_id": "call_1",
                        "name": "lookup",
                        "arguments": "",
                    },
                },
                {
                    "type": "response.function_call_arguments.delta",
                    "item_id": "fc_1",
                    "output_index": 0,
                    "delta": "{}",
                },
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": completed_call,
                },
                {
                    "type": "response.completed",
                    "response": _response(output=[completed_call]),
                },
            ],
            remote=True,
        )

        tool = next(
            payload["content_block"]
            for name, payload in events
            if name == "content_block_start"
            and payload["content_block"]["type"] == "tool_use"
        )
        self.assertEqual({"type": "direct"}, tool["caller"])
        self.assertEqual("crm", tool["toolset_name"])
        self.assertEqual("message_stop", events[-1][0])

    def test_remote_tool_missing_call_id_never_emits_executable_block(self):
        created = {**_response(), "status": "in_progress", "output": []}
        _, events = self.run_stream(
            [
                {"type": "response.created", "response": created},
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {
                        "id": "fc_1",
                        "type": "function_call",
                        "status": "in_progress",
                        "name": "dangerous",
                        "arguments": "",
                    },
                },
            ],
            remote=True,
        )

        self.assertNotIn(
            "content_block_start", [name for name, _payload in events]
        )
        self.assertEqual("error", events[-1][0])
        self.assertIn("call_id", events[-1][1]["error"]["message"])

    def test_remote_terminal_validation_precedes_tool_emission(self):
        call = {
            "id": "fc_1",
            "type": "function_call",
            "status": "completed",
            "call_id": "call_1",
            "name": "dangerous",
            "arguments": '{"path":"important.txt"}',
        }
        created = {**_response(), "status": "in_progress", "output": []}
        invalid_terminal = _response(output=[call])
        invalid_terminal.pop("id")
        _, events = self.run_stream(
            [
                {"type": "response.created", "response": created},
                {"type": "response.completed", "response": invalid_terminal},
            ],
            remote=True,
        )

        self.assertNotIn(
            "content_block_start", [name for name, _payload in events]
        )
        self.assertEqual("error", events[-1][0])

    def test_remote_stream_rejects_response_identity_change(self):
        created = {**_response(), "status": "in_progress", "output": []}
        terminal = {**_response(), "id": "resp_other"}
        _, events = self.run_stream(
            [
                {"type": "response.created", "response": created},
                {"type": "response.completed", "response": terminal},
            ],
            remote=True,
        )

        self.assertEqual("error", events[-1][0])
        self.assertNotIn("message_stop", [name for name, _payload in events])
        self.assertIn("id/model changed", events[-1][1]["error"]["message"])

    def test_remote_rejects_tool_item_absent_from_terminal_response(self):
        injected = {
            "id": "fc_injected",
            "type": "function_call",
            "status": "completed",
            "call_id": "call_injected",
            "name": "delete_all",
            "arguments": '{"confirm":true}',
        }
        benign = {
            "id": "msg_benign",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "safe"}],
        }
        created = {**_response(), "status": "in_progress", "output": []}
        _, events = self.run_stream(
            [
                {"type": "response.created", "response": created},
                {
                    "type": "response.output_item.added",
                    "output_index": 99,
                    "item": {**injected, "status": "in_progress", "arguments": ""},
                },
                {
                    "type": "response.output_item.done",
                    "output_index": 99,
                    "item": injected,
                },
                {
                    "type": "response.completed",
                    "response": _response(output=[benign]),
                },
            ],
            remote=True,
        )

        self.assertNotIn(
            "content_block_start", [name for name, _payload in events]
        )
        self.assertEqual("error", events[-1][0])
        self.assertNotIn("message_stop", [name for name, _payload in events])

    def test_remote_rejects_content_index_absent_from_completed_item(self):
        message = {
            "id": "msg_safe",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "terminal"}],
        }
        created = {**_response(), "status": "in_progress", "output": []}
        _, events = self.run_stream(
            [
                {"type": "response.created", "response": created},
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {**message, "status": "in_progress", "content": []},
                },
                {
                    "type": "response.output_text.delta",
                    "item_id": "msg_safe",
                    "output_index": 0,
                    "content_index": 99,
                    "delta": "injected",
                },
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": message,
                },
                {
                    "type": "response.completed",
                    "response": _response(output=[message]),
                },
            ],
            remote=True,
        )

        self.assertNotIn(
            "content_block_start", [name for name, _payload in events]
        )
        self.assertEqual("error", events[-1][0])


if __name__ == "__main__":
    unittest.main()
