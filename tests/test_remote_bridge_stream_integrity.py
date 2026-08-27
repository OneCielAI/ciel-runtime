import io
import json
import unittest
from contextlib import ExitStack
from unittest import mock

import ciel_runtime


class RemoteHandler:
    headers = {}
    connection = None
    _ciel_runtime_remote_bridge_request = True

    def __init__(self):
        self.wfile = io.BytesIO()
        self.statuses = []

    def send_response(self, status):
        self.statuses.append(status)

    def send_header(self, _name, _value):
        pass

    def end_headers(self):
        pass


class LocalHandler(RemoteHandler):
    _ciel_runtime_remote_bridge_request = False


class OpenAIResponse:
    def __init__(self, *, fail=False, terminal=False):
        self.fail = fail
        self.terminal = terminal
        self.closed = False

    def __iter__(self):
        yield (
            b'data: {"choices":[{"delta":{"content":"partial"},'
            b'"finish_reason":null}]}\n\n'
        )
        if self.terminal:
            yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
            return
        if self.fail:
            raise OSError("upstream reset")

    def close(self):
        self.closed = True


class OpenAIChunksResponse:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.closed = False

    def __iter__(self):
        yield from self.chunks

    def close(self):
        self.closed = True


class OllamaResponse:
    def __init__(self, *, fail=False, terminal=False):
        self.items = [
            (json.dumps({"message": {"content": "partial"}, "done": False}) + "\n").encode()
        ]
        if terminal:
            self.items.append(
                (json.dumps({"message": {"content": ""}, "done": True}) + "\n").encode()
            )
        elif fail:
            self.items.append(OSError("upstream reset"))
        self.closed = False

    def readline(self):
        if not self.items:
            return b""
        item = self.items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self):
        self.closed = True


class OllamaChunksResponse:
    def __init__(self, chunks):
        self.items = list(chunks)
        self.closed = False

    def readline(self):
        if not self.items:
            return b""
        item = self.items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self):
        self.closed = True


def openai_chunk(choice=None, **envelope):
    payload = dict(envelope)
    if choice is not None:
        payload["choices"] = [choice]
    return f"data: {json.dumps(payload)}\n\n".encode()


def ollama_chunk(payload):
    return (json.dumps(payload) + "\n").encode()


def event_names(handler):
    return [
        line.removeprefix("event: ")
        for line in handler.wfile.getvalue().decode("utf-8").splitlines()
        if line.startswith("event: ")
    ]


class RemoteBridgeStreamIntegrityTests(unittest.TestCase):
    def patches(self):
        stack = ExitStack()
        for name in (
            "router_log",
            "write_router_activity",
            "mark_pending_channel_delivery_failed",
            "mark_pending_channel_delivery_success",
            "dump_response_for_trace",
            "finish_outgoing_sse_trace",
            "record_outgoing_sse_event",
        ):
            stack.enter_context(mock.patch.object(ciel_runtime, name))
        stack.enter_context(
            mock.patch.object(ciel_runtime, "make_outgoing_sse_trace", return_value={})
        )
        return stack

    def test_openai_chat_truncation_is_an_error_not_a_normal_stop(self):
        for fail in (False, True):
            with self.subTest(iterator_error=fail):
                handler = RemoteHandler()
                response = OpenAIResponse(fail=fail)

                with self.patches():
                    ok = ciel_runtime.stream_openai_chat_to_anthropic_sse(
                        handler,
                        response,
                        "model",
                        "openrouter",
                    )

                self.assertFalse(ok)
                self.assertTrue(response.closed)
                self.assertIn("error", event_names(handler))
                self.assertNotIn("message_delta", event_names(handler))
                self.assertNotIn("message_stop", event_names(handler))

    def test_openai_chat_terminal_event_remains_a_normal_stop(self):
        handler = RemoteHandler()
        response = OpenAIResponse(terminal=True)

        with self.patches():
            ok = ciel_runtime.stream_openai_chat_to_anthropic_sse(
                handler,
                response,
                "model",
                "openrouter",
            )

        self.assertTrue(ok)
        self.assertIn("message_delta", event_names(handler))
        self.assertIn("message_stop", event_names(handler))
        self.assertNotIn("error", event_names(handler))

    def test_ollama_truncation_is_an_error_not_a_normal_stop(self):
        for fail in (False, True):
            with self.subTest(read_error=fail):
                handler = RemoteHandler()
                response = OllamaResponse(fail=fail)

                with self.patches():
                    ciel_runtime._ollama_stream_to_anthropic_sse(
                        handler,
                        response,
                        "model",
                    )

                self.assertEqual([200], handler.statuses)
                self.assertTrue(response.closed)
                self.assertIn("error", event_names(handler))
                self.assertNotIn("message_delta", event_names(handler))
                self.assertNotIn("message_stop", event_names(handler))

    def test_ollama_done_event_remains_a_normal_stop(self):
        handler = RemoteHandler()
        response = OllamaResponse(terminal=True)

        with self.patches():
            ciel_runtime._ollama_stream_to_anthropic_sse(
                handler,
                response,
                "model",
            )

        self.assertEqual([200], handler.statuses)
        self.assertIn("message_delta", event_names(handler))
        self.assertIn("message_stop", event_names(handler))
        self.assertNotIn("error", event_names(handler))

    def assert_openai_remote_error(self, chunks):
        handler = RemoteHandler()
        response = OpenAIChunksResponse(chunks)

        with self.patches():
            ok = ciel_runtime.stream_openai_chat_to_anthropic_sse(
                handler,
                response,
                "model",
                "openrouter",
            )

        self.assertFalse(ok)
        self.assertTrue(response.closed)
        self.assertIn("error", event_names(handler))
        self.assertNotIn("message_delta", event_names(handler))
        self.assertNotIn("message_stop", event_names(handler))

    def assert_ollama_remote_error(self, chunks):
        handler = RemoteHandler()
        response = OllamaChunksResponse(chunks)

        with self.patches():
            ciel_runtime._ollama_stream_to_anthropic_sse(
                handler,
                response,
                "model",
            )

        self.assertEqual([200], handler.statuses)
        self.assertTrue(response.closed)
        self.assertIn("error", event_names(handler))
        self.assertNotIn("message_delta", event_names(handler))
        self.assertNotIn("message_stop", event_names(handler))

    def test_openai_chat_done_without_finish_reason_is_rejected(self):
        self.assert_openai_remote_error([b"data: [DONE]\n\n"])

    def test_openai_chat_top_level_error_is_rejected(self):
        self.assert_openai_remote_error(
            [
                openai_chunk(error={"type": "server_error", "message": "bad"}),
                b"data: [DONE]\n\n",
            ]
        )

    def test_openai_chat_empty_and_content_filtered_stops_are_rejected(self):
        cases = {
            "empty_stop": {"delta": {}, "finish_reason": "stop"},
            "reasoning_only": {
                "delta": {"reasoning_content": "private reasoning"},
                "finish_reason": "stop",
            },
            "content_filter": {
                "delta": {"content": "partial"},
                "finish_reason": "content_filter",
            },
        }
        for name, choice in cases.items():
            with self.subTest(name=name):
                self.assert_openai_remote_error([openai_chunk(choice)])

    def test_openai_chat_tool_shape_and_finish_reason_are_strict(self):
        valid_call = {
            "index": 0,
            "id": "call_1",
            "type": "function",
            "function": {"name": "Read", "arguments": '{"path":"x"}'},
        }
        cases = {
            "tool_with_stop": {
                "delta": {"tool_calls": [valid_call]},
                "finish_reason": "stop",
            },
            "tool_finish_without_tool": {
                "delta": {},
                "finish_reason": "tool_calls",
            },
            "malformed_arguments": {
                "delta": {
                    "tool_calls": [
                        {
                            **valid_call,
                            "function": {
                                "name": "Read",
                                "arguments": "{not-json",
                            },
                        }
                    ]
                },
                "finish_reason": "tool_calls",
            },
            "nonobject_arguments": {
                "delta": {
                    "tool_calls": [
                        {
                            **valid_call,
                            "function": {
                                "name": "Read",
                                "arguments": "[]",
                            },
                        }
                    ]
                },
                "finish_reason": "tool_calls",
            },
            "missing_id": {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "type": "function",
                            "function": {
                                "name": "Read",
                                "arguments": "{}",
                            },
                        }
                    ]
                },
                "finish_reason": "tool_calls",
            },
            "missing_name": {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_1",
                            "type": "function",
                            "function": {"arguments": "{}"},
                        }
                    ]
                },
                "finish_reason": "tool_calls",
            },
        }
        for name, choice in cases.items():
            with self.subTest(name=name):
                self.assert_openai_remote_error([openai_chunk(choice)])

    def test_openai_chat_multiline_sse_record_is_assembled(self):
        handler = RemoteHandler()
        response = OpenAIChunksResponse(
            [
                b"event: chat.completion.chunk\n",
                b'data: {"choices":[{"delta":{"content":"multi"},\n',
                b'data: "finish_reason":"stop"}]}\n',
                b"\n",
            ]
        )

        with self.patches():
            ok = ciel_runtime.stream_openai_chat_to_anthropic_sse(
                handler,
                response,
                "model",
                "openrouter",
            )

        self.assertTrue(ok)
        self.assertIn("multi", handler.wfile.getvalue().decode("utf-8"))
        self.assertNotIn("error", event_names(handler))

    def test_openai_chat_valid_tool_call_remains_a_normal_stop(self):
        handler = RemoteHandler()
        response = OpenAIChunksResponse(
            [
                openai_chunk(
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "Read",
                                        "arguments": '{"path":"x"}',
                                    },
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ),
                openai_chunk(
                    {"delta": {}, "finish_reason": "tool_calls"}
                ),
            ]
        )

        with self.patches():
            ok = ciel_runtime.stream_openai_chat_to_anthropic_sse(
                handler,
                response,
                "model",
                "openrouter",
            )

        self.assertTrue(ok)
        self.assertIn("content_block_start", event_names(handler))
        self.assertIn("message_stop", event_names(handler))
        self.assertNotIn("error", event_names(handler))

    def test_openai_chat_invalid_utf8_is_rejected_only_for_remote_bridge(self):
        invalid = (
            b'data: {"choices":[{"delta":{"content":"bad\xff"},'
            b'"finish_reason":"stop"}]}\n\n'
        )
        self.assert_openai_remote_error([invalid])

        handler = LocalHandler()
        response = OpenAIChunksResponse([invalid])
        with self.patches():
            ok = ciel_runtime.stream_openai_chat_to_anthropic_sse(
                handler,
                response,
                "model",
                "openrouter",
            )
        self.assertTrue(ok)
        self.assertNotIn("error", event_names(handler))

    def test_ollama_error_and_missing_message_are_rejected(self):
        cases = {
            "top_level_error": {
                "error": "provider failed",
                "message": {"content": ""},
                "done": True,
            },
            "done_without_message": {"done": True},
        }
        for name, payload in cases.items():
            with self.subTest(name=name):
                self.assert_ollama_remote_error([ollama_chunk(payload)])

    def test_ollama_empty_and_reasoning_only_stops_are_rejected(self):
        cases = {
            "empty_message": {"message": {}, "done": True},
            "reasoning_only": {
                "message": {"thinking": "private reasoning"},
                "done": True,
            },
        }
        for name, payload in cases.items():
            with self.subTest(name=name):
                self.assert_ollama_remote_error([ollama_chunk(payload)])

    def test_ollama_tool_shape_is_strict(self):
        cases = {
            "malformed_arguments": "{not-json",
            "json_string_arguments": "{}",
            "nonobject_arguments": [],
            "missing_arguments": None,
        }
        for name, arguments in cases.items():
            with self.subTest(name=name):
                self.assert_ollama_remote_error(
                    [
                        ollama_chunk(
                            {
                                "message": {
                                    "tool_calls": [
                                        {
                                            "function": {
                                                "name": "Read",
                                                "arguments": arguments,
                                            }
                                        }
                                    ]
                                },
                                "done": True,
                            }
                        )
                    ]
                )

        self.assert_ollama_remote_error(
            [
                ollama_chunk(
                    {
                        "message": {
                            "tool_calls": [
                                {"function": {"arguments": {}}}
                            ]
                        },
                        "done": True,
                    }
                )
            ]
        )

    def test_ollama_valid_tool_without_upstream_id_is_accepted(self):
        handler = RemoteHandler()
        response = OllamaChunksResponse(
            [
                ollama_chunk(
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "Read",
                                        "arguments": {"path": "x"},
                                    }
                                }
                            ]
                        },
                        "done": True,
                    }
                )
            ]
        )

        with self.patches():
            ciel_runtime._ollama_stream_to_anthropic_sse(
                handler,
                response,
                "model",
            )

        output = handler.wfile.getvalue().decode("utf-8")
        self.assertIn("toolu_ollama_", output)
        self.assertIn("message_stop", event_names(handler))
        self.assertNotIn("error", event_names(handler))

    def test_ollama_invalid_utf8_is_rejected_for_remote_bridge(self):
        self.assert_ollama_remote_error(
            [b'{"message":{"content":"bad\xff"},"done":true}\n']
        )

    def test_local_streams_keep_legacy_terminal_tolerance(self):
        openai_handler = LocalHandler()
        openai_response = OpenAIChunksResponse([b"data: [DONE]\n\n"])
        with self.patches():
            ok = ciel_runtime.stream_openai_chat_to_anthropic_sse(
                openai_handler,
                openai_response,
                "model",
                "openrouter",
            )
        self.assertTrue(ok)
        self.assertNotIn("error", event_names(openai_handler))

        ollama_handler = LocalHandler()
        ollama_response = OllamaChunksResponse(
            [ollama_chunk({"done": True})]
        )
        with self.patches():
            ciel_runtime._ollama_stream_to_anthropic_sse(
                ollama_handler,
                ollama_response,
                "model",
            )
        self.assertIn("message_stop", event_names(ollama_handler))
        self.assertNotIn("error", event_names(ollama_handler))


if __name__ == "__main__":
    unittest.main()
