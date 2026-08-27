import io
import unittest
from unittest import mock

from ciel_runtime_support.openai_chat_compatibility_bridge import (
    OpenAIChatCompatibilityBridge,
    OpenAIChatCompatibilityOutput,
    OpenAIChatCompatibilityPorts,
    OpenAIChatCompatibilityProjection,
    OpenAIChatCompatibilityRouting,
    OpenAIChatCompatibilityTransport,
)
from ciel_runtime_support.protocols.openai_chat_compat import (
    anthropic_message_to_openai_chat_completion,
    openai_chat_to_anthropic_messages,
)
from ciel_runtime_support.protocols.openai_responses import (
    anthropic_messages_to_openai_responses,
    openai_response_to_anthropic_message,
)


def completed_message():
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "upstream-model",
        "content": [
            {"type": "thinking", "thinking": "inspect", "signature": "sig-secret"},
            {"type": "redacted_thinking", "data": "ciphertext"},
            {"type": "text", "text": "done"},
            {
                "type": "tool_use",
                "id": "call_1",
                "name": "lookup",
                "input": {"q": "value"},
            },
        ],
        "stop_reason": "tool_use",
        "usage": {
            "input_tokens": 7,
            "cache_read_input_tokens": 3,
            "output_tokens": 5,
        },
    }


class Handler:
    def __init__(self):
        self.headers = {"authorization": "Bearer bridge-token"}
        self.wfile = io.BytesIO()
        self.statuses = []
        self.response_headers = []

    def send_response(self, status):
        self.statuses.append(status)

    def send_header(self, name, value):
        self.response_headers.append((name, value))

    def end_headers(self):
        pass


class FailingWriter:
    def __init__(self, fail_on):
        self.fail_on = fail_on
        self.writes = 0

    def write(self, data):
        self.writes += 1
        if self.writes == self.fail_on:
            raise OSError("client disconnected")
        return len(data)

    def flush(self):
        pass


class OpenAIChatCompatibilityProjectionTests(unittest.TestCase):
    def test_n_must_be_the_integer_one_when_present(self):
        for value in (0, -1, "1", "bad", True):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "n=1"):
                    openai_chat_to_anthropic_messages(
                        {"messages": [], "n": value}
                    )

    def test_chat_request_projects_history_tools_images_and_effort(self):
        request = openai_chat_to_anthropic_messages(
            {
                "model": "model",
                "messages": [
                    {"role": "system", "content": "system"},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "look"},
                            {
                                "type": "image_url",
                                "image_url": {"url": "data:image/png;base64,YQ=="},
                            },
                        ],
                    },
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "lookup",
                                    "arguments": '{"q":"value"}',
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call_1",
                        "content": "result",
                    },
                ],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "lookup",
                            "description": "Lookup",
                            "parameters": {
                                "type": "object",
                                "properties": {"q": {"type": "string"}},
                            },
                        },
                    }
                ],
                "tool_choice": "required",
                "parallel_tool_calls": False,
                "max_completion_tokens": 123,
                "reasoning_effort": "high",
                "stream": True,
            }
        )

        self.assertEqual("system", request["system"])
        self.assertEqual(123, request["max_tokens"])
        self.assertEqual({"effort": "high"}, request["output_config"])
        self.assertEqual("image", request["messages"][0]["content"][1]["type"])
        self.assertEqual(
            {"q": "value"}, request["messages"][1]["content"][0]["input"]
        )
        self.assertEqual(
            "call_1", request["messages"][2]["content"][0]["tool_use_id"]
        )
        self.assertEqual("any", request["tool_choice"]["type"])
        self.assertTrue(request["tool_choice"]["disable_parallel_tool_use"])

    def test_tool_choice_none_removes_tools_instead_of_falling_back_to_auto(self):
        request = openai_chat_to_anthropic_messages(
            {
                "model": "model",
                "messages": [{"role": "user", "content": "hello"}],
                "tools": [
                    {
                        "type": "function",
                        "function": {"name": "lookup", "parameters": {}},
                    }
                ],
                "tool_choice": "none",
            }
        )

        self.assertNotIn("tools", request)
        self.assertNotIn("tool_choice", request)

    def test_custom_tools_are_rejected_instead_of_silently_removed(self):
        with self.assertRaisesRegex(ValueError, "custom"):
            openai_chat_to_anthropic_messages(
                {
                    "model": "model",
                    "messages": [{"role": "user", "content": "run"}],
                    "tools": [
                        {
                            "type": "custom",
                            "custom": {"name": "shell", "format": {"type": "text"}},
                        }
                    ],
                    "tool_choice": "required",
                }
            )

    def test_allowed_tools_filters_functions_and_preserves_required_mode(self):
        request = openai_chat_to_anthropic_messages(
            {
                "model": "model",
                "messages": [{"role": "user", "content": "run"}],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": name,
                            "parameters": {"type": "object"},
                        },
                    }
                    for name in ("allowed", "blocked")
                ],
                "tool_choice": {
                    "type": "allowed_tools",
                    "allowed_tools": {
                        "mode": "required",
                        "tools": [
                            {"type": "function", "function": {"name": "allowed"}}
                        ],
                    },
                },
            }
        )

        self.assertEqual(["allowed"], [tool["name"] for tool in request["tools"]])
        self.assertEqual({"type": "any"}, request["tool_choice"])

    def test_strict_function_tool_survives_the_responses_projection(self):
        anthropic = openai_chat_to_anthropic_messages(
            {
                "model": "model",
                "messages": [{"role": "user", "content": "run"}],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "parameters": {
                                "type": "object",
                                "additionalProperties": False,
                            },
                            "strict": True,
                        },
                    }
                ],
            }
        )

        responses = anthropic_messages_to_openai_responses(anthropic, "model")

        self.assertTrue(responses["tools"][0]["strict"])

    def test_omitted_function_strict_projects_as_explicit_false(self):
        anthropic = openai_chat_to_anthropic_messages(
            {
                "model": "model",
                "messages": [{"role": "user", "content": "run"}],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "parameters": {"type": "object"},
                        },
                    }
                ],
            }
        )

        responses = anthropic_messages_to_openai_responses(anthropic, "model")

        self.assertIs(responses["tools"][0]["strict"], False)

    def test_non_object_function_parameters_are_rejected(self):
        for parameters in ([], "schema", 1, False):
            with self.subTest(parameters=parameters):
                with self.assertRaisesRegex(
                    ValueError, "function.parameters must be an object"
                ):
                    openai_chat_to_anthropic_messages(
                        {
                            "model": "model",
                            "messages": [{"role": "user", "content": "run"}],
                            "tools": [
                                {
                                    "type": "function",
                                    "function": {
                                        "name": "lookup",
                                        "parameters": parameters,
                                    },
                                }
                            ],
                        }
                    )

    def test_missing_or_null_function_parameters_use_empty_object_schema(self):
        for function in ({"name": "lookup"}, {"name": "lookup", "parameters": None}):
            with self.subTest(function=function):
                request = openai_chat_to_anthropic_messages(
                    {
                        "model": "model",
                        "messages": [{"role": "user", "content": "run"}],
                        "tools": [{"type": "function", "function": function}],
                    }
                )

                self.assertEqual(
                    {"type": "object", "properties": {}},
                    request["tools"][0]["input_schema"],
                )

    def test_named_messages_are_rejected_instead_of_losing_the_name(self):
        for role in ("system", "user", "assistant"):
            with self.subTest(role=role):
                with self.assertRaisesRegex(
                    ValueError, r"messages\[0\]\.name is not supported"
                ):
                    openai_chat_to_anthropic_messages(
                        {
                            "model": "model",
                            "messages": [
                                {
                                    "role": role,
                                    "name": "participant",
                                    "content": "hello",
                                }
                            ],
                        }
                    )

    def test_lossy_or_malformed_chat_fields_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "logprobs"):
            openai_chat_to_anthropic_messages(
                {"messages": [], "logprobs": True}
            )
        with self.assertRaisesRegex(ValueError, "JSON object"):
            openai_chat_to_anthropic_messages(
                {
                    "messages": [
                        {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "function": {
                                        "name": "lookup",
                                        "arguments": "not-json",
                                    },
                                }
                            ],
                        }
                    ]
                }
            )
        with self.assertRaisesRegex(ValueError, "input_audio"):
            openai_chat_to_anthropic_messages(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": [{"type": "input_audio", "data": "..."}],
                        }
                    ]
                }
            )

    def test_anthropic_message_projects_chat_tools_finish_and_usage(self):
        response = anthropic_message_to_openai_chat_completion(
            completed_message(), "requested-model"
        )

        choice = response["choices"][0]
        self.assertEqual("tool_calls", choice["finish_reason"])
        self.assertEqual("inspect", choice["message"]["reasoning_content"])
        self.assertEqual("done", choice["message"]["content"])
        self.assertEqual("lookup", choice["message"]["tool_calls"][0]["function"]["name"])
        self.assertEqual(10, response["usage"]["prompt_tokens"])
        self.assertEqual(15, response["usage"]["total_tokens"])

    def test_signed_and_encrypted_reasoning_round_trips_through_chat_history(self):
        chat = anthropic_message_to_openai_chat_completion(
            completed_message(), "requested-model"
        )
        assistant = chat["choices"][0]["message"]

        replay = openai_chat_to_anthropic_messages(
            {"model": "requested-model", "messages": [assistant]}
        )

        blocks = replay["messages"][0]["content"]
        self.assertEqual("thinking", blocks[0]["type"])
        self.assertEqual("sig-secret", blocks[0]["signature"])
        self.assertEqual(
            {"type": "redacted_thinking", "data": "ciphertext"}, blocks[1]
        )
        self.assertEqual("tool_use", blocks[-1]["type"])


class OpenAIChatCompatibilityBridgeTests(unittest.TestCase):
    def bridge(
        self,
        *,
        payload=None,
        collected=None,
        responses_to_anthropic=openai_response_to_anthropic_message,
    ):
        writes = []
        post_json = mock.Mock(return_value=payload)
        collect = mock.Mock(return_value=collected)
        bridge = OpenAIChatCompatibilityBridge(
            OpenAIChatCompatibilityPorts(
                projection=OpenAIChatCompatibilityProjection(
                    openai_chat_to_anthropic_messages,
                    anthropic_message_to_openai_chat_completion,
                    anthropic_messages_to_openai_responses,
                    responses_to_anthropic,
                ),
                routing=OpenAIChatCompatibilityRouting(
                    collect,
                    collect,
                    lambda _provider, _config, model: f"wire-{model}",
                    lambda _provider, _config, body, _operation: body,
                ),
                transport=OpenAIChatCompatibilityTransport(
                    lambda _provider, _config, _operation: "https://upstream.test/responses",
                    lambda *_args: {"authorization": "Bearer host-oauth"},
                    post_json,
                    lambda _config: 30.0,
                ),
                output=OpenAIChatCompatibilityOutput(
                    lambda _handler, body, status=200: writes.append((status, body))
                ),
            )
        )
        return bridge, post_json, collect, writes

    def test_responses_only_route_uses_host_headers_and_returns_chat_json(self):
        payload = {
            "id": "resp_1",
            "object": "response",
            "status": "completed",
            "model": "wire-model",
            "output": [
                {
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "ok"}],
                }
            ],
            "usage": {"input_tokens": 2, "output_tokens": 1},
        }
        bridge, post_json, collect, writes = self.bridge(payload=payload)
        handler = Handler()

        bridge.forward(
            handler,
            "github-copilot-oauth",
            {"max_output_tokens": 100},
            {
                "model": "gpt-5.6-luna",
                "messages": [{"role": "user", "content": "hello"}],
                "reasoning_effort": "low",
                "stream": False,
            },
            "openai_responses",
        )

        collect.assert_not_called()
        request_args = post_json.call_args.args
        self.assertEqual("https://upstream.test/responses", request_args[0])
        self.assertEqual("wire-gpt-5.6-luna", request_args[1]["model"])
        self.assertFalse(request_args[1]["stream"])
        self.assertEqual("low", request_args[1]["reasoning"]["effort"])
        self.assertEqual("Bearer host-oauth", request_args[2]["authorization"])
        self.assertNotIn("bridge-token", str(request_args[2]))
        self.assertEqual(200, writes[0][0])
        self.assertEqual("ok", writes[0][1]["choices"][0]["message"]["content"])

    def test_responses_route_preserves_current_controls_and_strict_tools(self):
        payload = {
            "id": "resp_1",
            "object": "response",
            "status": "completed",
            "model": "wire-model",
            "output": [
                {
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "ok"}],
                }
            ],
            "usage": {
                "input_tokens": 1,
                "input_tokens_details": {
                    "cached_tokens": 0,
                    "cache_write_tokens": 0,
                },
                "output_tokens": 1,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 2,
            },
        }
        bridge, post_json, _collect, writes = self.bridge(payload=payload)

        bridge.forward(
            Handler(),
            "github-copilot-oauth",
            {},
            {
                "model": "gpt-5.6-luna",
                "messages": [{"role": "user", "content": "hello"}],
                "store": True,
                "metadata": {"trace": "e2e"},
                "prompt_cache_key": "cache-key",
                "prompt_cache_retention": "24h",
                "safety_identifier": "safe-user",
                "service_tier": "priority",
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "lookup",
                            "parameters": {"type": "object"},
                            "strict": True,
                        },
                    }
                ],
            },
            "openai_responses",
        )

        request = post_json.call_args.args[1]
        self.assertTrue(request["store"])
        self.assertEqual({"trace": "e2e"}, request["metadata"])
        self.assertEqual("cache-key", request["prompt_cache_key"])
        self.assertEqual("24h", request["prompt_cache_retention"])
        self.assertEqual("safe-user", request["safety_identifier"])
        self.assertEqual("priority", request["service_tier"])
        self.assertTrue(request["tools"][0]["strict"])
        self.assertEqual(200, writes[0][0])

    def test_anthropic_route_collects_then_emits_valid_chat_sse(self):
        bridge, post_json, collect, writes = self.bridge(collected=completed_message())
        handler = Handler()

        bridge.forward(
            handler,
            "anthropic",
            {"max_output_tokens": 100},
            {
                "model": "claude-sonnet-5",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
                "stream_options": {"include_usage": True},
            },
            "anthropic_messages",
        )

        post_json.assert_not_called()
        collect.assert_called_once()
        self.assertEqual([], writes)
        self.assertEqual([200], handler.statuses)
        output = handler.wfile.getvalue().decode("utf-8")
        self.assertIn('"object": "chat.completion.chunk"', output)
        self.assertIn('"finish_reason": "tool_calls"', output)
        self.assertIn('"usage": {', output)
        self.assertIn('"reasoning_opaque":', output)
        self.assertIn('"reasoning_signature": "sig-secret"', output)
        self.assertTrue(output.endswith("data: [DONE]\n\n"))

    def test_strict_tools_are_rejected_for_non_responses_upstreams(self):
        bridge, post_json, collect, writes = self.bridge()

        bridge.forward(
            Handler(),
            "anthropic",
            {},
            {
                "model": "claude-sonnet-5",
                "messages": [{"role": "user", "content": "hello"}],
                "tools": [
                    {
                        "type": "function",
                        "function": {"name": "lookup", "strict": True},
                    }
                ],
            },
            "anthropic_messages",
        )

        post_json.assert_not_called()
        collect.assert_not_called()
        self.assertEqual(400, writes[0][0])
        self.assertEqual("tools", writes[0][1]["error"]["param"])

    def test_projection_error_returns_json_before_stream_starts(self):
        bridge, post_json, collect, writes = self.bridge()

        bridge.forward(
            Handler(),
            "anthropic",
            {},
            {
                "model": "model",
                "messages": [],
                "stream": True,
                "logprobs": True,
            },
            "anthropic_messages",
        )

        post_json.assert_not_called()
        collect.assert_not_called()
        self.assertEqual(400, writes[0][0])
        self.assertEqual("invalid_request", writes[0][1]["error"]["code"])

    def test_stop_is_rejected_for_responses_only_models(self):
        bridge, post_json, collect, writes = self.bridge()

        bridge.forward(
            Handler(),
            "github-copilot-oauth",
            {},
            {
                "model": "gpt-5.6-luna",
                "messages": [{"role": "user", "content": "hello"}],
                "stop": ["END"],
            },
            "openai_responses",
        )

        post_json.assert_not_called()
        collect.assert_not_called()
        self.assertEqual(400, writes[0][0])
        self.assertEqual("stop", writes[0][1]["error"]["param"])

    def test_malformed_upstream_projection_is_a_502_not_a_client_error(self):
        convert = mock.Mock(side_effect=ValueError("malformed upstream response"))
        bridge, _post_json, _collect, writes = self.bridge(
            payload={"object": "response"},
            responses_to_anthropic=convert,
        )

        bridge.forward(
            Handler(),
            "github-copilot-oauth",
            {},
            {
                "model": "gpt-5.6-luna",
                "messages": [{"role": "user", "content": "hello"}],
            },
            "openai_responses",
        )

        self.assertEqual(502, writes[0][0])
        self.assertEqual("upstream_error", writes[0][1]["error"]["code"])

    def test_incomplete_or_missing_responses_status_never_becomes_chat_success(self):
        invalid = (
            {},
            {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "status": "in_progress",
                        "content": [{"type": "output_text", "text": "truncated"}],
                    }
                ],
            },
        )
        for payload in invalid:
            with self.subTest(payload=payload):
                bridge, _post_json, _collect, writes = self.bridge(payload=payload)

                bridge.forward(
                    Handler(),
                    "github-copilot-oauth",
                    {},
                    {
                        "model": "gpt-5.6-luna",
                        "messages": [{"role": "user", "content": "hello"}],
                    },
                    "openai_responses",
                )

                self.assertEqual(502, writes[0][0])
                self.assertEqual("upstream_error", writes[0][1]["error"]["code"])

    def test_stream_write_failure_never_writes_a_second_http_response(self):
        for fail_on in (1, 3):
            with self.subTest(fail_on=fail_on):
                bridge, _post_json, _collect, writes = self.bridge(
                    collected=completed_message()
                )
                handler = Handler()
                handler.wfile = FailingWriter(fail_on)

                bridge.forward(
                    handler,
                    "anthropic",
                    {},
                    {
                        "model": "claude-sonnet-5",
                        "messages": [{"role": "user", "content": "hello"}],
                        "stream": True,
                    },
                    "anthropic_messages",
                )

                self.assertEqual([200], handler.statuses)
                self.assertEqual([], writes)
                self.assertTrue(handler.close_connection)


if __name__ == "__main__":
    unittest.main()
