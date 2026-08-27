"""Non-streaming Anthropic Messages <-> OpenAI Responses bridge contracts."""

from __future__ import annotations

import io
import json
import urllib.error
import unittest
from types import SimpleNamespace
from unittest import mock

from ciel_runtime_support.anthropic_responses_bridge import (
    AnthropicResponsesBridge,
    AnthropicResponsesBridgePorts,
    AnthropicResponsesOutputPorts,
    AnthropicResponsesProjectionPorts,
    AnthropicResponsesTransportPorts,
)
from ciel_runtime_support.protocols.openai_responses import (
    anthropic_messages_to_openai_responses,
    anthropic_message_to_openai_response,
    openai_response_to_anthropic_message,
    openai_responses_to_anthropic_messages,
)
from ciel_runtime_support.remote_bridge import REMOTE_BRIDGE_CONFIG_MARKER


class AnthropicResponsesProjectionTests(unittest.TestCase):
    def test_strict_anthropic_request_requires_positive_max_tokens(self):
        for body in (
            {"messages": [{"role": "user", "content": "hello"}]},
            {
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": None,
            },
            {
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": False,
            },
            {
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": -1,
            },
            {
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 0,
            },
            {
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 1.0,
            },
        ):
            with self.subTest(body=body), self.assertRaisesRegex(
                ValueError, "max_tokens"
            ):
                anthropic_messages_to_openai_responses(body, strict=True)

        request = anthropic_messages_to_openai_responses(
            {
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 32,
            },
            strict=True,
        )
        self.assertEqual(32, request["max_output_tokens"])

    def test_non_strict_synthetic_request_keeps_optional_max_tokens(self):
        request = anthropic_messages_to_openai_responses(
            {"messages": [{"role": "user", "content": "hello"}]}
        )

        self.assertNotIn("max_output_tokens", request)

    def test_stop_sequences_are_rejected_instead_of_silently_removed(self):
        with self.assertRaisesRegex(ValueError, "stop_sequences"):
            anthropic_messages_to_openai_responses(
                {
                    "model": "model",
                    "max_tokens": 32,
                    "stop_sequences": ["SECRET_END"],
                    "messages": [{"role": "user", "content": "hello"}],
                }
            )

    def test_anthropic_request_preserves_order_tools_effort_and_limits(self):
        request = anthropic_messages_to_openai_responses(
            {
                "model": "client-alias",
                "system": [
                    {"type": "text", "text": "First instruction"},
                    {"type": "text", "text": "Second instruction"},
                ],
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": "inspect"}],
                    },
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "calling"},
                            {
                                "type": "tool_use",
                                "id": "toolu_1",
                                "name": "read_file",
                                "input": {"path": "a.py"},
                            },
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_1",
                                "content": "contents",
                            },
                            {"type": "text", "text": "continue"},
                        ],
                    },
                ],
                "tools": [
                    {
                        "name": "read_file",
                        "description": "Read one file",
                        "input_schema": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    }
                ],
                "tool_choice": {
                    "type": "tool",
                    "name": "read_file",
                    "disable_parallel_tool_use": True,
                },
                "max_tokens": 8192,
                "output_config": {"effort": "xhigh"},
                "temperature": 0.25,
                "top_p": 0.75,
                "stream": False,
            },
            "fallback-model",
        )

        self.assertEqual("client-alias", request["model"])
        self.assertEqual(
            "First instruction\n\nSecond instruction", request["instructions"]
        )
        self.assertEqual(
            [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "inspect"}],
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "input_text", "text": "calling"}],
                },
                {
                    "type": "function_call",
                    "call_id": "toolu_1",
                    "name": "read_file",
                    "arguments": '{"path": "a.py"}',
                },
                {
                    "type": "function_call_output",
                    "call_id": "toolu_1",
                    "output": "contents",
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "continue"}],
                },
            ],
            request["input"],
        )
        self.assertEqual(
            [
                {
                    "type": "function",
                    "name": "read_file",
                    "description": "Read one file",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                    "strict": False,
                }
            ],
            request["tools"],
        )
        self.assertEqual(
            {"type": "function", "name": "read_file"}, request["tool_choice"]
        )
        self.assertFalse(request["parallel_tool_calls"])
        self.assertEqual(8192, request["max_output_tokens"])
        self.assertEqual({"effort": "xhigh"}, request["reasoning"])
        self.assertEqual(0.25, request["temperature"])
        self.assertEqual(0.75, request["top_p"])
        self.assertEqual(["reasoning.encrypted_content"], request["include"])
        self.assertFalse(request["store"])
        self.assertFalse(request["stream"])

    def test_strict_anthropic_identity_rejects_lossy_normalization(self):
        base = {
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "inspect"}],
        }
        schema = {"type": "object", "properties": {}}
        for malformed_tool in (
            {"name": " read", "input_schema": schema},
            {"name": "read ", "input_schema": schema},
            {"type": "CUSTOM", "name": "read", "input_schema": schema},
            {"type": " custom ", "name": "read", "input_schema": schema},
            {"type": "", "name": "read", "input_schema": schema},
        ):
            with self.subTest(tool=malformed_tool), self.assertRaises(ValueError):
                anthropic_messages_to_openai_responses(
                    {**base, "tools": [malformed_tool]},
                    strict=True,
                )
        with self.assertRaises(ValueError):
            anthropic_messages_to_openai_responses(
                {
                    **base,
                    "tools": [
                        {"name": "first", "input_schema": schema},
                        {
                            "type": " CUSTOM ",
                            "name": " second ",
                            "input_schema": schema,
                        },
                    ],
                },
                strict=True,
            )

        for malformed_choice in (
            " AUTO ",
            {"type": " TOOL ", "name": "read"},
            {"type": "tool", "name": " read "},
        ):
            with self.subTest(choice=malformed_choice), self.assertRaises(ValueError):
                anthropic_messages_to_openai_responses(
                    {**base, "tool_choice": malformed_choice},
                    strict=True,
                )

        def history(tool_use: dict[str, object], tool_result: dict[str, object]):
            return {
                "max_tokens": 32,
                "messages": [
                    {"role": "assistant", "content": [tool_use]},
                    {"role": "user", "content": [tool_result]},
                ],
            }

        valid_use = {
            "type": "tool_use",
            "id": "call_1",
            "name": "read",
            "input": {},
        }
        valid_result = {
            "type": "tool_result",
            "tool_use_id": "call_1",
            "content": "done",
        }
        malformed_histories = (
            history(
                {**valid_use, "id": " call_1"},
                {**valid_result, "tool_use_id": " call_1"},
            ),
            history({**valid_use, "name": " read "}, valid_result),
            history(valid_use, {**valid_result, "tool_use_id": "call_1 "}),
            history(
                {**valid_use, "toolset_name": " crm "},
                {**valid_result, "toolset_name": " crm "},
            ),
        )
        for malformed_history in malformed_histories:
            with self.subTest(history=malformed_history), self.assertRaises(ValueError):
                anthropic_messages_to_openai_responses(
                    malformed_history,
                    strict=True,
                )

    def test_anthropic_controls_project_without_silent_loss(self):
        schema = {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        }
        request = anthropic_messages_to_openai_responses(
            {
                "model": "model",
                "messages": [{"role": "user", "content": "hello"}],
                "output_config": {
                    "effort": "high",
                    "format": {"type": "json_schema", "schema": schema},
                },
                "thinking": {"type": "adaptive", "display": "omitted"},
                "service_tier": "standard_only",
                "metadata": {"user_id": "opaque-user-1"},
            }
        )

        self.assertEqual({"effort": "high"}, request["reasoning"])
        self.assertEqual("default", request["service_tier"])
        self.assertEqual("opaque-user-1", request["safety_identifier"])
        self.assertEqual(
            {
                "type": "json_schema",
                "name": "anthropic_output",
                "schema": schema,
                "strict": True,
            },
            request["text"]["format"],
        )

    def test_anthropic_disabled_thinking_projects_to_none(self):
        request = anthropic_messages_to_openai_responses(
            {
                "messages": [{"role": "user", "content": "hello"}],
                "thinking": {"type": "disabled"},
            }
        )

        self.assertEqual({"effort": "none"}, request["reasoning"])

    def test_anthropic_tool_result_preserves_text_image_and_document(self):
        request = anthropic_messages_to_openai_responses(
            {
                "messages": [
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "call_1",
                                "name": "inspect_result",
                                "input": {},
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "call_1",
                                "content": [
                                    {"type": "text", "text": "caption"},
                                    {
                                        "type": "image",
                                        "source": {
                                            "type": "url",
                                            "url": "https://example.test/image.png",
                                        },
                                    },
                                    {
                                        "type": "document",
                                        "source": {
                                            "type": "url",
                                            "url": "https://example.test/file.pdf",
                                        },
                                    },
                                ],
                            }
                        ],
                    }
                ]
            }
        )

        self.assertEqual(
            [
                {"type": "input_text", "text": "caption"},
                {
                    "type": "input_image",
                    "image_url": "https://example.test/image.png",
                    "detail": "auto",
                },
                {
                    "type": "input_file",
                    "file_url": "https://example.test/file.pdf",
                },
            ],
            request["input"][1]["output"],
        )

    def test_anthropic_document_sources_preserve_filename_and_file_id(self):
        request = anthropic_messages_to_openai_responses(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "document",
                                "source": {
                                    "type": "url",
                                    "url": "https://example.test/report.pdf",
                                },
                                "title": "report.pdf",
                            },
                            {
                                "type": "document",
                                "source": {"type": "file", "file_id": "file_1"},
                                "title": "stored.pdf",
                            },
                        ],
                    }
                ]
            }
        )

        self.assertEqual(
            {
                "type": "input_file",
                "file_url": "https://example.test/report.pdf",
                "filename": "report.pdf",
            },
            request["input"][0]["content"][0],
        )
        self.assertEqual(
            {
                "type": "input_file",
                "file_id": "file_1",
                "filename": "stored.pdf",
            },
            request["input"][0]["content"][1],
        )

    def test_anthropic_unprojectable_controls_and_shapes_fail_closed(self):
        invalid_bodies = (
            {"messages": "bad"},
            {"messages": [{}]},
            {"messages": [{"role": "bogus", "content": "x"}]},
            {"messages": [{"role": "user", "content": {"type": "text", "text": "x"}}]},
            {"messages": [{"role": "user", "content": [{"text": "x", "unknown": 1}]}]},
            {"messages": [{"role": "user", "content": []}], "stream": "false"},
            {"messages": [{"role": "user", "content": []}], "max_tokens": 1.9},
            {"messages": [{"role": "user", "content": []}], "top_k": 5},
            {"messages": [{"role": "user", "content": []}], "cache_control": {}},
            {"messages": [{"role": "user", "content": []}], "container": "c_1"},
            {"messages": [{"role": "user", "content": []}], "inference_geo": "us"},
            {
                "messages": [{"role": "user", "content": []}],
                "thinking": {"type": "enabled", "budget_tokens": 1024},
            },
            {
                "messages": [{"role": "user", "content": []}],
                "tools": [{"type": "web_search_20250305", "name": "web_search"}],
            },
            {
                "messages": [{"role": "user", "content": []}],
                "tools": [{"name": "missing_schema"}],
            },
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "tool_use", "id": "c", "name": "t", "input": {}}
                        ],
                    }
                ]
            },
            {
                "messages": [
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "tool_result", "tool_use_id": "c", "content": "x"}
                        ],
                    }
                ]
            },
            {
                "messages": [
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "thinking", "thinking": "private", "signature": "sig"}
                        ],
                    }
                ]
            },
            {
                "messages": [
                    {
                        "role": "assistant",
                        "content": [{"type": "redacted_thinking", "data": "foreign"}],
                    }
                ]
            },
        )

        for body in invalid_bodies:
            with self.subTest(body=body):
                with self.assertRaises(ValueError):
                    anthropic_messages_to_openai_responses(body)

    def test_responses_request_preserves_client_sampling_for_anthropic_wire(self):
        request = openai_responses_to_anthropic_messages(
            {
                "model": "upstream-model",
                "input": "inspect",
                "temperature": 0.2,
                "top_p": 0.8,
                "stream": False,
            },
            "fallback-model",
        )

        self.assertEqual(0.2, request["temperature"])
        self.assertEqual(0.8, request["top_p"])
        self.assertFalse(request["stream"])

    def test_codex_transport_hints_are_validated_and_not_leaked_to_anthropic(self):
        request = openai_responses_to_anthropic_messages(
            {
                "_ciel_remote_bridge_request": True,
                "input": "inspect",
                "client_metadata": {
                    "installation_id": "install-1",
                    "thread_id": "thread-1",
                },
                "prompt_cache_key": "thread-1",
                "text": {"verbosity": "low"},
            },
            "fallback-model",
        )

        self.assertNotIn("client_metadata", request)
        self.assertNotIn("prompt_cache_key", request)
        self.assertNotIn("text", request)

        invalid_values = (
            {"client_metadata": {"thread_id": 1}},
            {"client_metadata": ["thread-1"]},
            {"prompt_cache_key": ""},
            {"prompt_cache_key": 1},
            {"text": {"verbosity": "verbose"}},
        )
        for value in invalid_values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                openai_responses_to_anthropic_messages(
                    {
                        "_ciel_remote_bridge_request": True,
                        "input": "inspect",
                        **value,
                    },
                    "fallback-model",
                )

    def test_codex_responses_lite_controls_and_additional_tools_round_trip(self):
        namespace = {
            "type": "namespace",
            "name": "functions",
            "description": "Codex client tools.",
            "tools": [
                {
                    "type": "function",
                    "name": "exec",
                    "description": "Run one command.",
                    "parameters": {
                        "type": "object",
                        "properties": {"cmd": {"type": "string"}},
                        "required": ["cmd"],
                    },
                    "strict": False,
                }
            ],
        }
        source_body = {
            "_ciel_remote_bridge_request": True,
            "model": "gpt-5.6-luna",
            "input": [
                {
                    "type": "additional_tools",
                    "role": "developer",
                    "tools": [namespace],
                },
                {
                    "type": "message",
                    "role": "developer",
                    "content": [
                        {"type": "input_text", "text": "Use tools carefully."}
                    ],
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Inspect."}],
                },
            ],
            "include": ["reasoning.encrypted_content"],
            "parallel_tool_calls": False,
            "reasoning": {
                "effort": "low",
                "summary": "auto",
                "context": "all_turns",
            },
            "stream": True,
            "stream_options": {
                "reasoning_summary_delivery": "sequential_cutoff"
            },
        }

        request = openai_responses_to_anthropic_messages(source_body, "fallback")

        self.assertEqual("gpt-5.6-luna", request["model"])
        self.assertEqual("functions__exec", request["tools"][0]["name"])
        self.assertEqual(
            {"type": "auto", "disable_parallel_tool_use": True},
            request["tool_choice"],
        )
        self.assertEqual({"effort": "low"}, request["output_config"])
        self.assertEqual(
            {"type": "adaptive", "display": "summarized"}, request["thinking"]
        )
        self.assertEqual("Inspect.", request["messages"][0]["content"][0]["text"])
        self.assertEqual(
            "Use tools carefully.", request["system"][0]["text"]
        )
        self.assertNotIn("reasoning", request)
        self.assertNotIn("stream_options", request)

        restored = anthropic_message_to_openai_response(
            {
                "id": "msg_lite",
                "type": "message",
                "role": "assistant",
                "model": "claude",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call_exec",
                        "name": "functions__exec",
                        "input": {"cmd": "pwd"},
                    }
                ],
                "stop_reason": "tool_use",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
            source_body,
        )
        self.assertEqual("exec", restored["output"][0]["name"])
        self.assertEqual("functions", restored["output"][0]["namespace"])

    def test_codex_openai_provider_metadata_is_validated_and_consumed(self):
        metadata_field = "internal_chat_message_metadata_passthrough"
        user_item = {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Inspect."}],
            metadata_field: {
                "turn_id": "turn-1",
                "create_time": 1_777_777_777.25,
                "content_item_kinds": ["generic.user_message"],
            },
        }
        request = openai_responses_to_anthropic_messages(
            {
                "_ciel_remote_bridge_request": True,
                "input": [user_item],
                "reasoning": {"summary": "auto", "context": "all_turns"},
                "stream": True,
                "stream_options": {
                    "reasoning_summary_delivery": "sequential_cutoff"
                },
            },
            "fallback",
        )

        self.assertEqual("Inspect.", request["messages"][0]["content"][0]["text"])
        self.assertNotIn(metadata_field, request["messages"][0])

        invalid_metadata = (
            None,
            {"unknown": True},
            {"turn_id": 1},
            {"create_time": True},
            {"create_time": float("inf")},
            {"content_item_kinds": "generic.user_message"},
            {"content_item_kinds": []},
            {"executed_tool_calls": []},
        )
        for metadata in invalid_metadata:
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                openai_responses_to_anthropic_messages(
                    {
                        "_ciel_remote_bridge_request": True,
                        "input": [{**user_item, metadata_field: metadata}],
                    },
                    "fallback",
                )

    def test_codex_responses_lite_all_turns_replays_ciel_reasoning_envelope(self):
        declaration = {
            "type": "additional_tools",
            "role": "developer",
            "tools": [],
        }
        source_body = {
            "_ciel_remote_bridge_request": True,
            "model": "gpt-5.6-luna",
            "input": [
                declaration,
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "First turn"}],
                },
            ],
            "include": ["reasoning.encrypted_content"],
            "reasoning": {"effort": "low", "context": "all_turns"},
        }
        first_response = anthropic_message_to_openai_response(
            {
                "id": "msg_lite_reasoning",
                "type": "message",
                "role": "assistant",
                "model": "claude",
                "content": [
                    {"type": "thinking", "thinking": "opaque", "signature": "sig"},
                    {"type": "text", "text": "First answer"},
                ],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 2},
            },
            source_body,
        )
        continuation = openai_responses_to_anthropic_messages(
            {
                **source_body,
                "input": [
                    declaration,
                    *first_response["output"],
                    {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "Second turn"}
                        ],
                    },
                ],
            },
            "fallback",
        )

        self.assertEqual(
            {"type": "thinking", "thinking": "opaque", "signature": "sig"},
            continuation["messages"][0]["content"][0],
        )
        self.assertEqual(
            "Second turn", continuation["messages"][1]["content"][0]["text"]
        )

    def test_codex_responses_lite_invalid_controls_and_declarations_fail_closed(self):
        marker = {
            "_ciel_remote_bridge_request": True,
            "input": "inspect",
        }
        invalid_controls = (
            {"reasoning": {"context": "auto"}},
            {"reasoning": {"context": "current_turn"}},
            {"reasoning": {"context": 1}},
            {
                "reasoning": {"summary": "auto"},
                "stream_options": {"reasoning_summary_delivery": "parallel"},
            },
            {
                "reasoning": {"summary": "auto"},
                "stream_options": {
                    "reasoning_summary_delivery": "sequential_cutoff",
                    "unknown": True,
                },
            },
            {
                "reasoning": {"summary": "auto"},
                "stream": False,
                "stream_options": {
                    "reasoning_summary_delivery": "sequential_cutoff"
                },
            },
            {
                "reasoning": {},
                "stream_options": {
                    "reasoning_summary_delivery": "sequential_cutoff"
                },
            },
            {
                "stream_options": {
                    "reasoning_summary_delivery": "sequential_cutoff"
                }
            },
        )
        for value in invalid_controls:
            with self.subTest(value=value), self.assertRaises(ValueError):
                openai_responses_to_anthropic_messages(
                    {**marker, **value}, "fallback"
                )

        valid_tool = {
            "type": "function",
            "name": "read",
            "parameters": {"type": "object", "properties": {}},
        }
        declaration = {
            "type": "additional_tools",
            "role": "developer",
            "tools": [valid_tool],
        }
        user_item = {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "inspect"}],
        }
        invalid_declarations = (
            [{**declaration, "role": "user"}, user_item],
            [user_item, declaration],
            [declaration, declaration, user_item],
            [{**declaration, "tools": {}}, user_item],
            [{**declaration, "unknown": True}, user_item],
            [{**declaration, "id": ""}, user_item],
        )
        for input_items in invalid_declarations:
            with self.subTest(input_items=input_items), self.assertRaises(ValueError):
                openai_responses_to_anthropic_messages(
                    {
                        "_ciel_remote_bridge_request": True,
                        "input": input_items,
                    },
                    "fallback",
                )

        with self.assertRaisesRegex(ValueError, "conflicts with top-level tools"):
            openai_responses_to_anthropic_messages(
                {
                    "_ciel_remote_bridge_request": True,
                    "input": [declaration, user_item],
                    "tools": [valid_tool],
                },
                "fallback",
            )

    def test_responses_tool_constraints_remain_enforced_on_anthropic_wire(self):
        request = openai_responses_to_anthropic_messages(
            {
                "_ciel_remote_bridge_request": True,
                "model": "upstream-model",
                "input": "inspect",
                "tools": [
                    {
                        "type": "function",
                        "name": "danger",
                        "parameters": {"type": "object", "properties": {}},
                        "strict": True,
                    }
                ],
                "tool_choice": "none",
                "parallel_tool_calls": False,
            },
            "fallback-model",
        )

        self.assertTrue(request["tools"][0]["strict"])
        self.assertEqual({"type": "none"}, request["tool_choice"])

        required = openai_responses_to_anthropic_messages(
            {
                "_ciel_remote_bridge_request": True,
                "input": "inspect",
                "tools": [
                    {
                        "type": "function",
                        "name": "read",
                        "parameters": {"type": "object"},
                    }
                ],
                "tool_choice": "required",
                "parallel_tool_calls": False,
            },
            "fallback-model",
        )
        self.assertEqual(
            {"type": "any", "disable_parallel_tool_use": True},
            required["tool_choice"],
        )

    def test_codex_namespace_tools_flatten_and_restore_without_toolset_spoofing(self):
        namespace = {
            "type": "namespace",
            "name": "mcp__calendar",
            "description": "Calendar tools.",
            "tools": [
                {
                    "type": "function",
                    "name": "list_events",
                    "description": "List calendar events.",
                    "parameters": {"type": "object", "properties": {}},
                    "strict": False,
                }
            ],
        }
        source_body = {
            "_ciel_remote_bridge_request": True,
            "input": "inspect",
            "tools": [namespace],
        }

        request = openai_responses_to_anthropic_messages(source_body, "fallback")

        self.assertEqual(1, len(request["tools"]))
        self.assertEqual("mcp__calendar__list_events", request["tools"][0]["name"])
        self.assertIn("Calendar tools.", request["tools"][0]["description"])
        self.assertNotIn("toolset_name", request["tools"][0])

        namespace_message = {
            "id": "msg_namespace",
            "type": "message",
            "role": "assistant",
            "model": "claude",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call_1",
                    "name": "mcp__calendar__list_events",
                    "input": {},
                }
            ],
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        restored = anthropic_message_to_openai_response(
            namespace_message,
            source_body,
        )

        self.assertEqual("list_events", restored["output"][0]["name"])
        self.assertEqual("mcp__calendar", restored["output"][0]["namespace"])

        matching_toolset = {
            **namespace_message,
            "content": [
                {
                    **namespace_message["content"][0],
                    "toolset_name": "mcp__calendar",
                }
            ],
        }
        matching_restored = anthropic_message_to_openai_response(
            matching_toolset,
            source_body,
        )
        self.assertEqual(
            "mcp__calendar", matching_restored["output"][0]["namespace"]
        )

        mismatched_toolset = {
            **namespace_message,
            "content": [
                {
                    **namespace_message["content"][0],
                    "toolset_name": "admin",
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "does not match"):
            anthropic_message_to_openai_response(
                mismatched_toolset,
                source_body,
            )

        plain_source = {
            "_ciel_remote_bridge_request": True,
            "input": "inspect",
            "tools": [
                {
                    "type": "function",
                    "name": "read",
                    "parameters": {"type": "object", "properties": {}},
                }
            ],
        }
        spoofed_plain_tool = {
            **namespace_message,
            "content": [
                {
                    "type": "tool_use",
                    "id": "call_plain",
                    "name": "read",
                    "input": {},
                    "toolset_name": "admin",
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "does not match"):
            anthropic_message_to_openai_response(
                spoofed_plain_tool,
                plain_source,
            )

        for malformed in (
            {**namespace, "name": ""},
            {**namespace, "tools": []},
            {**namespace, "unknown": True},
        ):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                openai_responses_to_anthropic_messages(
                    {
                        "_ciel_remote_bridge_request": True,
                        "input": "inspect",
                        "tools": [malformed],
                    },
                    "fallback",
                )

    def test_strict_responses_tool_identity_rejects_normalized_names_and_types(self):
        function = {
            "type": "function",
            "name": "read",
            "parameters": {"type": "object", "properties": {}},
        }
        custom = {
            "type": "custom",
            "name": "exec",
            "description": "Execute code.",
        }
        namespace = {
            "type": "namespace",
            "name": "functions",
            "tools": [function],
        }
        malformed_tools = (
            {**function, "type": "FUNCTION"},
            {**function, "type": " function "},
            {**function, "name": " read"},
            {**function, "name": "read "},
            {**custom, "type": "CUSTOM"},
            {**custom, "name": " exec "},
            {**namespace, "type": " NAMESPACE "},
            {**namespace, "name": " functions"},
            {**namespace, "name": "functions "},
            {**namespace, "tools": [{**function, "type": "FUNCTION"}]},
            {**namespace, "tools": [{**function, "name": " read "}]},
            {**namespace, "tools": [{**custom, "type": " CUSTOM "}]},
            {**namespace, "tools": [{**custom, "name": " exec "}]},
        )
        for malformed in malformed_tools:
            with self.subTest(tool=malformed), self.assertRaises(ValueError):
                openai_responses_to_anthropic_messages(
                    {
                        "_ciel_remote_bridge_request": True,
                        "input": "inspect",
                        "tools": [malformed],
                    },
                    "fallback",
                )
        for malformed_choice in (
            " AUTO ",
            {"type": "function", "name": " read"},
            {"type": "function", "name": "read "},
            {"type": "function", "name": 1},
        ):
            with self.subTest(choice=malformed_choice), self.assertRaises(ValueError):
                openai_responses_to_anthropic_messages(
                    {
                        "_ciel_remote_bridge_request": True,
                        "input": "inspect",
                        "tools": [function],
                        "tool_choice": malformed_choice,
                    },
                    "fallback",
                )

    def test_codex_0150_freeform_apply_patch_lark_projects_and_roundtrips_exactly(self):
        grammar = """start: begin_patch hunk+ end_patch
begin_patch: "*** Begin Patch" LF
end_patch: "*** End Patch" LF?

hunk: add_hunk | delete_hunk | update_hunk
add_hunk: "*** Add File: " filename LF add_line+
delete_hunk: "*** Delete File: " filename LF
update_hunk: "*** Update File: " filename LF change_move? change?

filename: /(.+)/
add_line: "+" /(.*)/ LF -> line

change_move: "*** Move to: " filename LF
change: (change_context | change_line)+ eof_line?
change_context: ("@@" | "@@ " /(.+)/) LF
change_line: ("+" | "-" | " ") /(.*)/ LF
eof_line: "*** End of File" LF

%import common.LF
"""
        format_value = {
            "type": "grammar",
            "syntax": "lark",
            "definition": grammar,
        }
        apply_patch_tool = {
            "type": "custom",
            "name": "apply_patch",
            "description": (
                "The `apply_patch` tool can be used to edit files. This is a "
                "FREEFORM tool, so do not wrap the patch in JSON."
            ),
            "format": format_value,
        }
        source_body = {
            "_ciel_remote_bridge_request": True,
            "model": "gpt-5.5",
            "input": "Create one file.",
            "tools": [apply_patch_tool],
        }

        request = openai_responses_to_anthropic_messages(source_body, "fallback")

        windows_source_body = {
            **source_body,
            "tools": [
                {
                    **apply_patch_tool,
                    "format": {
                        **format_value,
                        "definition": grammar.replace("\n", "\r\n"),
                    },
                }
            ],
        }
        windows_request = openai_responses_to_anthropic_messages(
            windows_source_body,
            "fallback",
        )
        environment_grammar = grammar.replace(
            "start: begin_patch hunk+ end_patch",
            "start: begin_patch environment_id? hunk+ end_patch\n"
            'environment_id: "*** Environment ID: " filename LF',
        )
        environment_source_body = {
            **source_body,
            "tools": [
                {
                    **apply_patch_tool,
                    "format": {
                        **format_value,
                        "definition": environment_grammar,
                    },
                }
            ],
        }
        environment_request = openai_responses_to_anthropic_messages(
            environment_source_body,
            "fallback",
        )

        projected_tool = request["tools"][0]
        self.assertEqual(
            windows_request["tools"][0]["input_schema"],
            projected_tool["input_schema"],
        )
        self.assertEqual(
            environment_request["tools"][0]["input_schema"],
            projected_tool["input_schema"],
        )
        self.assertEqual("apply_patch", projected_tool["name"])
        self.assertEqual(
            {
                "type": "object",
                "properties": {
                    "input": {
                        "type": "string",
                        "description": (
                            "Complete raw input for the custom tool; it must obey "
                            "the format contract in the tool description."
                        ),
                    }
                },
                "required": ["input"],
                "additionalProperties": False,
            },
            projected_tool["input_schema"],
        )
        self.assertIn(
            json.dumps(
                format_value,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            projected_tool["description"],
        )
        self.assertIn("exactly one JSON field named `input`", projected_tool["description"])
        local_request = openai_responses_to_anthropic_messages(
            {key: value for key, value in source_body.items() if not key.startswith("_ciel_")},
            "fallback",
        )
        self.assertIn(
            json.dumps(format_value, ensure_ascii=False, separators=(",", ":")),
            local_request["tools"][0]["description"],
        )

        patch = "*** Begin Patch\n*** Add File: proof.txt\n+ok\n*** End Patch\n"
        restored = anthropic_message_to_openai_response(
            {
                "id": "msg_apply_patch",
                "type": "message",
                "role": "assistant",
                "model": "claude",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call_apply_patch",
                        "name": "apply_patch",
                        "input": {"input": patch},
                    }
                ],
                "stop_reason": "tool_use",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
            source_body,
        )

        restored_call = restored["output"][0]
        self.assertEqual("custom_tool_call", restored_call["type"])
        self.assertEqual("apply_patch", restored_call["name"])
        self.assertEqual(patch, restored_call["input"])
        continuation = openai_responses_to_anthropic_messages(
            {
                **source_body,
                "input": [
                    restored_call,
                    {
                        "type": "custom_tool_call_output",
                        "call_id": "call_apply_patch",
                        "output": "Done!",
                    },
                ],
            },
            "fallback",
        )
        self.assertEqual(
            {"input": patch}, continuation["messages"][0]["content"][0]["input"]
        )
        self.assertEqual(
            "call_apply_patch",
            continuation["messages"][1]["content"][0]["tool_use_id"],
        )

        valid_patches = (
            "*** Begin Patch\n*** Delete File: old.txt\n*** End Patch\n",
            (
                "*** Begin Patch\n*** Update File: old.txt\n@@\n-old\n+new\n"
                "*** End of File\n*** End Patch\n"
            ),
            (
                "*** Begin Patch\n*** Update File: old.txt\n"
                "*** Move to: new.txt\n*** End Patch\n"
            ),
        )
        for valid_patch in valid_patches:
            with self.subTest(valid_patch=valid_patch):
                projected = anthropic_message_to_openai_response(
                    {
                        "id": "msg_apply_patch_valid",
                        "type": "message",
                        "role": "assistant",
                        "model": "claude",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "call_apply_patch_valid",
                                "name": "apply_patch",
                                "input": {"input": valid_patch},
                            }
                        ],
                        "stop_reason": "tool_use",
                        "stop_sequence": None,
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    },
                    source_body,
                )
                self.assertEqual(valid_patch, projected["output"][0]["input"])

        environment_patch = (
            "*** Begin Patch\n"
            "*** Environment ID: env-1\n"
            "*** Add File: proof.txt\n"
            "+proof\n"
            "*** End Patch\n"
        )
        environment_message = {
            "id": "msg_apply_patch_environment",
            "type": "message",
            "role": "assistant",
            "model": "claude",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call_apply_patch_environment",
                    "name": "apply_patch",
                    "input": {"input": environment_patch},
                }
            ],
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        environment_restored = anthropic_message_to_openai_response(
            environment_message,
            environment_source_body,
        )
        self.assertEqual(
            environment_patch,
            environment_restored["output"][0]["input"],
        )
        with self.assertRaisesRegex(ValueError, "not declared"):
            anthropic_message_to_openai_response(
                environment_message,
                source_body,
            )
        for invalid_environment_patch in (
            environment_patch.replace("env-1", ""),
            environment_patch.replace(
                "*** Add File: proof.txt\n",
                "*** Environment ID: env-2\n*** Add File: proof.txt\n",
            ),
        ):
            malformed_environment_message = {
                **environment_message,
                "content": [
                    {
                        **environment_message["content"][0],
                        "input": {"input": invalid_environment_patch},
                    }
                ],
            }
            with self.assertRaisesRegex(ValueError, "grammar"):
                anthropic_message_to_openai_response(
                    malformed_environment_message,
                    environment_source_body,
                )

        invalid_patches = (
            "NOT_A_PATCH",
            (
                "*** Begin Patch\n*** Add File: proof.txt\n"
                "missing-plus\n*** End Patch\n"
            ),
            (
                "*** Begin Patch\n*** Update File: proof.txt\n"
                "*** End of File\n*** End Patch\n"
            ),
        )
        for invalid_patch in invalid_patches:
            with self.subTest(invalid_patch=invalid_patch), self.assertRaisesRegex(
                ValueError, "does not satisfy"
            ):
                anthropic_message_to_openai_response(
                    {
                        "id": "msg_apply_patch_invalid",
                        "type": "message",
                        "role": "assistant",
                        "model": "claude",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "call_apply_patch_invalid",
                                "name": "apply_patch",
                                "input": {"input": invalid_patch},
                            }
                        ],
                        "stop_reason": "tool_use",
                        "stop_sequence": None,
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    },
                    source_body,
                )

    def test_custom_tool_format_and_return_envelope_fail_closed(self):
        base_tool = {
            "type": "custom",
            "name": "apply_patch",
            "description": "Apply a patch.",
        }
        invalid_formats = (
            [],
            {},
            {"type": "regex", "syntax": "lark", "definition": "start: /x/"},
            {"type": "grammar", "syntax": "regex", "definition": "start: /x/"},
            {"type": "grammar", "syntax": "lark", "definition": ""},
            {"type": "grammar", "syntax": "lark", "definition": 1},
            {
                "type": "grammar",
                "syntax": "lark",
                "definition": "start: /x/",
            },
            {
                "type": "grammar",
                "syntax": "lark",
                "definition": "this is definitely not valid lark ???",
            },
            {
                "type": "grammar",
                "syntax": "lark",
                "definition": "start: /x/",
                "unknown": True,
            },
        )
        for format_value in invalid_formats:
            with self.subTest(format=format_value), self.assertRaises(ValueError):
                openai_responses_to_anthropic_messages(
                    {
                        "_ciel_remote_bridge_request": True,
                        "input": "inspect",
                        "tools": [{**base_tool, "format": format_value}],
                    },
                    "fallback",
                )

        source_body = {
            "_ciel_remote_bridge_request": True,
            "input": "inspect",
            "tools": [base_tool],
        }
        for malformed_input in (
            {"input": "raw", "unexpected": "discarded"},
            {"input": {"not": "raw text"}},
            {"payload": "raw"},
        ):
            with self.subTest(input=malformed_input), self.assertRaisesRegex(
                ValueError, "exactly one string field"
            ):
                anthropic_message_to_openai_response(
                    {
                        "id": "msg_custom",
                        "type": "message",
                        "role": "assistant",
                        "model": "claude",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "call_custom",
                                "name": "apply_patch",
                                "input": malformed_input,
                            }
                        ],
                        "stop_reason": "tool_use",
                        "stop_sequence": None,
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    },
                    source_body,
                )

    def test_codex_0150_code_mode_exec_lark_projects_and_roundtrips(self):
        grammar = r"""
start: pragma_source | plain_source
pragma_source: PRAGMA_LINE NEWLINE SOURCE
plain_source: SOURCE

PRAGMA_LINE: /[ \t]*\/\/ @exec:[^\r\n]*/
NEWLINE: /\r?\n/
SOURCE: /[\s\S]+/
"""
        source_body = {
            "_ciel_remote_bridge_request": True,
            "model": "gpt-5.6-luna",
            "input": "Inspect the repository.",
            "tools": [
                {
                    "type": "namespace",
                    "name": "functions",
                    "description": "Code-mode tools.",
                    "tools": [
                        {
                            "type": "custom",
                            "name": "exec",
                            "description": "Execute code mode.",
                            "format": {
                                "type": "grammar",
                                "syntax": "lark",
                                "definition": grammar,
                            },
                        }
                    ],
                }
            ],
        }

        request = openai_responses_to_anthropic_messages(source_body, "fallback")
        emitted_name = request["tools"][0]["name"]
        self.assertIn("functions", emitted_name)
        self.assertIn("exec", emitted_name)

        message = {
            "id": "msg_code_mode",
            "type": "message",
            "role": "assistant",
            "model": "claude",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call_exec",
                    "name": emitted_name,
                    "toolset_name": "functions",
                    "input": {"input": "text(true);"},
                }
            ],
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        restored = anthropic_message_to_openai_response(message, source_body)
        self.assertEqual("custom_tool_call", restored["output"][0]["type"])
        self.assertEqual("functions", restored["output"][0]["namespace"])
        self.assertEqual("exec", restored["output"][0]["name"])
        self.assertEqual("text(true);", restored["output"][0]["input"])

        empty_message = {
            **message,
            "content": [{**message["content"][0], "input": {"input": ""}}],
        }
        with self.assertRaisesRegex(ValueError, "SOURCE must be non-empty"):
            anthropic_message_to_openai_response(empty_message, source_body)

    def test_remote_responses_reasoning_does_not_invent_an_effort(self):
        marker = {"_ciel_remote_bridge_request": True, "input": "inspect"}

        defaulted = openai_responses_to_anthropic_messages(
            {**marker, "reasoning": {}},
            "fallback",
        )
        summarized = openai_responses_to_anthropic_messages(
            {**marker, "reasoning": {"summary": "auto"}},
            "fallback",
        )

        self.assertNotIn("thinking", defaulted)
        self.assertNotIn("output_config", defaulted)
        self.assertEqual(
            {"type": "adaptive", "display": "summarized"},
            summarized["thinking"],
        )
        self.assertNotIn("output_config", summarized)

        for reasoning in (
            {"effort": "minimal"},
            {"effort": "none", "summary": "auto"},
            {"effort": "high", "summary": "detailed"},
        ):
            with self.subTest(reasoning=reasoning), self.assertRaises(ValueError):
                openai_responses_to_anthropic_messages(
                    {**marker, "reasoning": reasoning},
                    "fallback",
                )

    def test_remote_responses_tool_metadata_is_validated_before_projection(self):
        base_tool = {
            "type": "function",
            "name": "read",
            "parameters": {"type": "object", "properties": {}},
        }
        request = openai_responses_to_anthropic_messages(
            {
                "_ciel_remote_bridge_request": True,
                "input": "inspect",
                "tools": [
                    {
                        **base_tool,
                        "allowed_callers": ["direct"],
                        "defer_loading": False,
                        "input_examples": [{"path": "a.py"}],
                    }
                ],
            },
            "fallback",
        )

        self.assertEqual(["direct"], request["tools"][0]["allowed_callers"])
        self.assertFalse(request["tools"][0]["defer_loading"])
        self.assertEqual(
            [{"path": "a.py"}], request["tools"][0]["input_examples"]
        )

        invalid_tools = (
            {**base_tool, "allowed_callers": ["programmatic"]},
            {**base_tool, "allowed_callers": "direct"},
            {**base_tool, "defer_loading": True},
            {**base_tool, "defer_loading": "false"},
            {**base_tool, "input_examples": ["not-an-object"]},
        )
        for tool in invalid_tools:
            with self.subTest(tool=tool), self.assertRaises(ValueError):
                openai_responses_to_anthropic_messages(
                    {
                        "_ciel_remote_bridge_request": True,
                        "input": "inspect",
                        "tools": [tool],
                    },
                    "fallback",
                )

    def test_remote_responses_tool_history_requires_matching_call_ids(self):
        marker = {"_ciel_remote_bridge_request": True}
        paired = openai_responses_to_anthropic_messages(
            {
                **marker,
                "input": [
                    {
                        "type": "function_call",
                        "id": "fc_item",
                        "call_id": "call_1",
                        "name": "read",
                        "arguments": "{}",
                    },
                    {
                        "type": "function_call_output",
                        "id": "out_item",
                        "call_id": "call_1",
                        "output": "done",
                    },
                ],
            },
            "fallback",
        )
        self.assertEqual("call_1", paired["messages"][0]["content"][0]["id"])
        self.assertEqual(
            "call_1", paired["messages"][1]["content"][0]["tool_use_id"]
        )

        namespaced = openai_responses_to_anthropic_messages(
            {
                **marker,
                "input": [
                    {
                        "type": "function_call",
                        "call_id": "call_ns",
                        "caller": {"type": "direct"},
                        "namespace": "crm",
                        "name": "lookup",
                        "arguments": "{}",
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call_ns",
                        "output": "done",
                    },
                ],
            },
            "fallback",
        )
        self.assertEqual(
            {"type": "direct"},
            namespaced["messages"][0]["content"][0]["caller"],
        )
        self.assertEqual(
            "crm__lookup", namespaced["messages"][0]["content"][0]["name"]
        )
        self.assertNotIn("toolset_name", namespaced["messages"][0]["content"][0])

        invalid_inputs = (
            [
                {
                    "type": "function_call",
                    "id": "fc_item",
                    "name": "read",
                    "arguments": "{}",
                },
                {
                    "type": "function_call_output",
                    "id": "out_item",
                    "output": "done",
                },
            ],
            [
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "read",
                    "arguments": "{}",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_2",
                    "output": "done",
                },
            ],
            [
                {
                    "type": "custom_tool_call",
                    "call_id": "call_1",
                    "name": "custom",
                    "input": "raw",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": "done",
                },
            ],
        )
        for input_items in invalid_inputs:
            with self.subTest(input=input_items), self.assertRaises(ValueError):
                openai_responses_to_anthropic_messages(
                    {**marker, "input": input_items},
                    "fallback",
                )

    def test_remote_responses_controls_and_attachments_project_to_anthropic(self):
        schema = {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        }
        request = openai_responses_to_anthropic_messages(
            {
                "_ciel_remote_bridge_request": True,
                "model": "claude",
                "instructions": "Be precise.",
                "input": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "inspect"},
                            {
                                "type": "input_image",
                                "image_url": "data:image/png;base64,AAAA",
                                "detail": "auto",
                            },
                            {
                                "type": "input_file",
                                "file_url": "https://example.test/file.pdf",
                                "filename": "file.pdf",
                            },
                        ],
                    }
                ],
                "reasoning": {"effort": "high", "summary": "auto"},
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "answer",
                        "schema": schema,
                        "strict": True,
                    }
                },
                "service_tier": "default",
                "safety_identifier": "opaque-user",
                "store": False,
                "include": ["reasoning.encrypted_content"],
                "stream": False,
            },
            "fallback",
        )

        self.assertEqual(
            {"type": "adaptive", "display": "summarized"}, request["thinking"]
        )
        self.assertEqual("high", request["output_config"]["effort"])
        self.assertEqual(
            {"type": "json_schema", "schema": schema},
            request["output_config"]["format"],
        )
        self.assertEqual("standard_only", request["service_tier"])
        self.assertEqual({"user_id": "opaque-user"}, request["metadata"])
        self.assertEqual(
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
            request["messages"][0]["content"][1],
        )
        self.assertEqual(
            {
                "type": "document",
                "source": {"type": "url", "url": "https://example.test/file.pdf"},
                "title": "file.pdf",
            },
            request["messages"][0]["content"][2],
        )

    def test_remote_responses_unprojectable_semantics_fail_closed(self):
        marker = {"_ciel_remote_bridge_request": True}
        invalid = (
            {"input": ["not-an-item"]},
            {"input": "x", "stream": "false"},
            {"input": "x", "max_output_tokens": "12"},
            {"input": "x", "previous_response_id": "resp_1"},
            {
                "input": "x",
                "tools": [{"type": "web_search", "search_context_size": "low"}],
            },
            {
                "input": [
                    {"role": "user", "content": "first"},
                    {"role": "developer", "content": "late priority"},
                ]
            },
            {
                "input": [
                    {
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "tool",
                        "arguments": "[]",
                    }
                ]
            },
            {
                "input": [
                    {"type": "function_call_output", "output": "missing id"}
                ]
            },
            {
                "input": [
                    {
                        "type": "reasoning",
                        "encrypted_content": "foreign-envelope",
                        "summary": [],
                    }
                ]
            },
            {
                "input": [
                    {
                        "type": "reasoning",
                        "status": "in_progress",
                        "encrypted_content": "foreign-envelope",
                        "summary": [],
                    }
                ]
            },
        )

        for body in invalid:
            with self.subTest(body=body):
                with self.assertRaises(ValueError):
                    openai_responses_to_anthropic_messages(
                        {**marker, **body},
                        "fallback",
                    )

    def test_response_projects_text_reasoning_tool_and_cached_usage(self):
        message = openai_response_to_anthropic_message(
            {
                "id": "resp_bridge_1",
                "object": "response",
                "status": "completed",
                "model": "gpt-5.6-sol",
                "output": [
                    {
                        "type": "reasoning",
                        "id": "rs_1",
                        "encrypted_content": "sealed-value",
                        "summary": [
                            {"type": "summary_text", "text": "private summary"}
                        ],
                    },
                    {
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "Result"}],
                    },
                    {
                        "type": "function_call",
                        "status": "completed",
                        "call_id": "call_1",
                        "name": "read_file",
                        "arguments": '{"path":"a.py"}',
                    },
                ],
                "usage": {
                    "input_tokens": 120,
                    "input_tokens_details": {"cached_tokens": 80},
                    "output_tokens": 9,
                },
            },
            "fallback-model",
        )

        self.assertEqual("msg_bridge_1", message["id"])
        self.assertEqual("gpt-5.6-sol", message["model"])
        self.assertEqual("tool_use", message["stop_reason"])
        self.assertEqual("redacted_thinking", message["content"][0]["type"])
        self.assertTrue(
            message["content"][0]["data"].startswith(
                "ciel-responses-reasoning-v1:"
            )
        )
        self.assertEqual({"type": "text", "text": "Result"}, message["content"][1])
        self.assertEqual(
            {
                "type": "tool_use",
                "id": "call_1",
                "name": "read_file",
                "input": {"path": "a.py"},
            },
            message["content"][2],
        )
        self.assertEqual(
            {
                "input_tokens": 40,
                "cache_read_input_tokens": 80,
                "output_tokens": 9,
                "output_tokens_details": {"thinking_tokens": 0},
            },
            message["usage"],
        )

    def test_encrypted_reasoning_round_trips_into_the_next_responses_input(self):
        reasoning = {
            "type": "reasoning",
            "id": "rs_1",
            "encrypted_content": "sealed-value",
            "summary": [{"type": "summary_text", "text": "summary"}],
        }
        message = openai_response_to_anthropic_message(
            {
                "id": "resp_1",
                "status": "completed",
                "model": "gpt-5.6-sol",
                "output": [reasoning],
            }
        )
        next_request = anthropic_messages_to_openai_responses(
            {
                "model": "gpt-5.6-sol",
                "messages": [
                    {"role": "assistant", "content": message["content"]},
                    {"role": "user", "content": "continue"},
                ],
            }
        )

        self.assertEqual(reasoning, next_request["input"][0])
        self.assertEqual(
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "continue"}],
            },
            next_request["input"][1],
        )

    def test_malformed_tool_history_is_rejected_before_transport(self):
        invalid_blocks = (
            {"type": "tool_use", "id": "", "name": "tool", "input": {}},
            {"type": "tool_use", "id": "call_1", "name": "", "input": {}},
            {
                "type": "tool_use",
                "id": "call_1",
                "name": "tool",
                "input": "not-an-object",
            },
            {"type": "tool_result", "tool_use_id": "", "content": "done"},
        )

        for block in invalid_blocks:
            with self.subTest(block=block):
                with self.assertRaises(ValueError):
                    anthropic_messages_to_openai_responses(
                        {"messages": [{"role": "user", "content": [block]}]}
                    )

    def test_malformed_or_nonterminal_tool_response_is_rejected(self):
        invalid_responses = (
            {
                "status": "in_progress",
                "output": [],
            },
            {
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "status": "in_progress",
                        "call_id": "call_1",
                        "name": "tool",
                        "arguments": "{}",
                    }
                ],
            },
            {
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "status": "completed",
                        "call_id": "call_1",
                        "name": "tool",
                        "arguments": "not-json",
                    }
                ],
            },
            {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "user",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "wrong role"}],
                    }
                ],
            },
            {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "cited",
                                "annotations": [{"type": "url_citation"}],
                            }
                        ],
                    }
                ],
            },
            {
                "status": "completed",
                "output": [
                    {
                        "type": "reasoning",
                        "status": "in_progress",
                        "encrypted_content": "sealed",
                        "summary": [],
                    }
                ],
            },
            {
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "id": "fc_not_a_call_id",
                        "status": "completed",
                        "name": "tool",
                        "arguments": "{}",
                    }
                ],
            },
        )

        for response in invalid_responses:
            with self.subTest(response=response):
                with self.assertRaises(ValueError):
                    openai_response_to_anthropic_message(response)

    def test_nonstream_refusal_and_content_filter_map_to_refusal(self):
        for response in (
            {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "refusal", "refusal": "Cannot comply"}],
                    }
                ],
            },
            {
                "status": "incomplete",
                "incomplete_details": {"reason": "content_filter"},
                "output": [],
            },
        ):
            with self.subTest(response=response):
                message = openai_response_to_anthropic_message(response)
                self.assertEqual("refusal", message["stop_reason"])

    def test_nonstream_incomplete_without_supported_reason_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "supported reason"):
            openai_response_to_anthropic_message(
                {
                    "status": "incomplete",
                    "incomplete_details": {"reason": "unknown"},
                    "output": [],
                }
            )

    def test_strict_responses_response_requires_official_envelope_and_usage(self):
        valid = {
            "id": "resp_strict",
            "object": "response",
            "status": "completed",
            "model": "gpt-5.6-sol",
            "output": [
                {
                    "id": "msg_output",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "ok"}],
                }
            ],
            "usage": {
                "input_tokens": 10,
                "input_tokens_details": {
                    "cached_tokens": 3,
                    "cache_write_tokens": 2,
                },
                "output_tokens": 6,
                "output_tokens_details": {"reasoning_tokens": 4},
                "total_tokens": 16,
            },
        }

        message = openai_response_to_anthropic_message(valid, strict=True)

        self.assertEqual("msg_strict", message["id"])
        self.assertEqual(
            {
                "input_tokens": 5,
                "cache_read_input_tokens": 3,
                "cache_creation_input_tokens": 2,
                "output_tokens": 6,
                "output_tokens_details": {"thinking_tokens": 4},
            },
            message["usage"],
        )
        for field in ("id", "object", "model", "usage"):
            malformed = dict(valid)
            malformed.pop(field)
            with self.subTest(field=field), self.assertRaises(ValueError):
                openai_response_to_anthropic_message(malformed, strict=True)

    def test_incomplete_responses_tool_call_keeps_max_token_termination(self):
        message = openai_response_to_anthropic_message(
            {
                "id": "resp_limited_tool",
                "object": "response",
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "model": "gpt-5.6-sol",
                "output": [
                    {
                        "id": "fc_limited",
                        "type": "function_call",
                        "status": "completed",
                        "call_id": "call_1",
                        "name": "lookup",
                        "arguments": "{}",
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
            },
            strict=True,
        )

        self.assertEqual("max_tokens", message["stop_reason"])

    def test_strict_responses_tool_caller_and_namespace_are_preserved(self):
        response = {
            "id": "resp_tool_metadata",
            "object": "response",
            "status": "completed",
            "model": "gpt-5.6-sol",
            "output": [
                {
                    "id": "fc_metadata",
                    "type": "function_call",
                    "status": "completed",
                    "call_id": "call_1",
                    "caller": {"type": "direct"},
                    "namespace": "crm",
                    "name": "lookup",
                    "arguments": "{}",
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

        message = openai_response_to_anthropic_message(response, strict=True)

        self.assertEqual(
            {"type": "direct"}, message["content"][0]["caller"]
        )
        self.assertEqual("crm", message["content"][0]["toolset_name"])

        response["output"][0]["caller"] = {
            "type": "program",
            "caller_id": "prog_1",
        }
        with self.assertRaisesRegex(ValueError, "caller"):
            openai_response_to_anthropic_message(response, strict=True)

    def test_completed_responses_reject_incomplete_details(self):
        with self.assertRaisesRegex(ValueError, "cannot include incomplete_details"):
            openai_response_to_anthropic_message(
                {
                    "status": "completed",
                    "incomplete_details": {"reason": "content_filter"},
                    "output": [
                        {
                            "type": "message",
                            "status": "completed",
                            "content": [{"type": "output_text", "text": "ok"}],
                        }
                    ],
                }
            )

    def test_remote_anthropic_max_tokens_and_refusal_preserve_terminal_meaning(self):
        strict_source = {"_ciel_remote_bridge_request": True}
        incomplete = anthropic_message_to_openai_response(
            {
                "id": "msg_limited",
                "type": "message",
                "role": "assistant",
                "model": "claude",
                "content": [{"type": "text", "text": "partial"}],
                "stop_reason": "max_tokens",
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 2,
                    "output_tokens": 1,
                    "output_tokens_details": {"thinking_tokens": 0},
                },
            },
            strict_source,
        )
        refusal = anthropic_message_to_openai_response(
            {
                "id": "msg_refusal",
                "type": "message",
                "role": "assistant",
                "model": "claude",
                "content": [{"type": "text", "text": "blocked"}],
                "stop_reason": "refusal",
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 2,
                    "output_tokens": 1,
                    "output_tokens_details": {"thinking_tokens": 0},
                },
            },
            strict_source,
        )

        self.assertEqual("incomplete", incomplete["status"])
        self.assertEqual(
            {"reason": "max_output_tokens"}, incomplete["incomplete_details"]
        )
        self.assertEqual("incomplete", incomplete["output"][0]["status"])
        self.assertEqual("completed", refusal["status"])
        self.assertEqual(
            {"type": "refusal", "refusal": "blocked"},
            refusal["output"][0]["content"][0],
        )

    def test_remote_anthropic_response_integrity_fails_closed(self):
        strict_source = {"_ciel_remote_bridge_request": True}
        valid_envelope = {
            "id": "msg_integrity",
            "type": "message",
            "role": "assistant",
            "model": "claude",
            "usage": {
                "input_tokens": 1,
                "output_tokens": 1,
                "output_tokens_details": {"thinking_tokens": 0},
            },
            "stop_sequence": None,
        }
        invalid_messages = (
            {"content": [], "stop_reason": "end_turn"},
            {
                "content": [
                    {"type": "tool_use", "id": "", "name": "tool", "input": {}}
                ],
                "stop_reason": "tool_use",
            },
            {
                "content": [
                    {"type": "tool_use", "id": "call_1", "name": "", "input": {}}
                ],
                "stop_reason": "tool_use",
            },
            {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "tool",
                        "input": "BAD",
                    }
                ],
                "stop_reason": "tool_use",
            },
            {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "tool",
                        "input": {},
                    }
                ],
                "stop_reason": "end_turn",
            },
            {"content": [{"type": "text", "text": "x"}], "stop_reason": "tool_use"},
            {
                "content": [
                    {"type": "server_tool_use", "id": "srv_1", "name": "web_search", "input": {}}
                ],
                "stop_reason": "end_turn",
            },
            {
                "content": [{"type": "redacted_thinking", "data": ""}],
                "stop_reason": "end_turn",
            },
            {
                "content": [{"type": "text", "text": "later"}],
                "stop_reason": "pause_turn",
            },
        )

        for message in invalid_messages:
            with self.subTest(message=message):
                with self.assertRaises(ValueError):
                    anthropic_message_to_openai_response(
                        {**valid_envelope, **message}, strict_source
                    )

    def test_remote_anthropic_response_requires_official_message_envelope(self):
        strict_source = {"_ciel_remote_bridge_request": True, "model": "claude"}
        required_fields = ("id", "type", "role", "model", "usage")
        valid = {
            "id": "msg_valid",
            "type": "message",
            "role": "assistant",
            "model": "claude",
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {
                "input_tokens": 1,
                "output_tokens": 1,
                "output_tokens_details": {"thinking_tokens": 0},
            },
        }

        for field in required_fields:
            malformed = dict(valid)
            malformed.pop(field)
            with self.subTest(field=field), self.assertRaises(ValueError):
                anthropic_message_to_openai_response(malformed, strict_source)

    def test_remote_anthropic_usage_preserves_reasoning_token_details(self):
        strict_source = {"_ciel_remote_bridge_request": True}
        valid = {
            "id": "msg_usage",
            "type": "message",
            "role": "assistant",
            "model": "claude",
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {
                "input_tokens": 3,
                "cache_creation_input_tokens": 2,
                "cache_read_input_tokens": 5,
                "output_tokens": 11,
                "output_tokens_details": {"thinking_tokens": 7},
                "service_tier": "priority",
            },
        }

        response = anthropic_message_to_openai_response(valid, strict_source)

        self.assertEqual(10, response["usage"]["input_tokens"])
        self.assertEqual(
            {"cache_write_tokens": 2, "cached_tokens": 5},
            response["usage"]["input_tokens_details"],
        )
        self.assertEqual(
            {"reasoning_tokens": 7},
            response["usage"]["output_tokens_details"],
        )
        self.assertEqual("priority", response["service_tier"])

        nullable = anthropic_message_to_openai_response(
            {
                **valid,
                "container": None,
                "stop_details": None,
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call_direct",
                        "name": "lookup",
                        "input": {},
                        "caller": {"type": "direct"},
                        "toolset_name": None,
                    }
                ],
                "stop_reason": "tool_use",
                "usage": {
                    "input_tokens": 1,
                    "cache_creation_input_tokens": None,
                    "cache_read_input_tokens": None,
                    "output_tokens": 1,
                    "output_tokens_details": None,
                },
            },
            strict_source,
        )
        self.assertEqual(
            {"type": "direct"}, nullable["output"][0]["caller"]
        )
        self.assertEqual(
            {"cache_write_tokens": 0, "cached_tokens": 0},
            nullable["usage"]["input_tokens_details"],
        )
        self.assertEqual(
            {"reasoning_tokens": 0},
            nullable["usage"]["output_tokens_details"],
        )

        invalid_usage_values = (
            {},
            {
                "input_tokens": 1,
                "output_tokens": 1,
                "output_tokens_details": {"thinking_tokens": 2},
            },
        )
        for usage in invalid_usage_values:
            with self.subTest(usage=usage), self.assertRaises(ValueError):
                anthropic_message_to_openai_response(
                    {**valid, "usage": usage}, strict_source
                )

        without_details = anthropic_message_to_openai_response(
            {
                **valid,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
            strict_source,
        )
        self.assertEqual(
            {"reasoning_tokens": 0},
            without_details["usage"]["output_tokens_details"],
        )

    def test_anthropic_signed_reasoning_round_trips_through_responses_envelope(self):
        original = {
            "type": "thinking",
            "thinking": "inspect first",
            "signature": "opaque-signature",
        }
        response = anthropic_message_to_openai_response(
            {
                "id": "msg_reasoning",
                "type": "message",
                "role": "assistant",
                "model": "claude",
                "content": [original],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 2,
                    "output_tokens": 1,
                    "output_tokens_details": {"thinking_tokens": 1},
                },
            },
            {"_ciel_remote_bridge_request": True},
        )
        next_request = openai_responses_to_anthropic_messages(
            {
                "model": "claude",
                "input": [
                    *response["output"],
                    {"type": "message", "role": "user", "content": "continue"},
                ],
            },
            "claude",
        )

        self.assertEqual(original, next_request["messages"][0]["content"][0])

    def test_commentary_is_visible_and_round_trips_without_becoming_final_text(self):
        commentary = {
            "id": "msg_commentary",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "phase": "commentary",
            "content": [
                {"type": "output_text", "text": "Checking now", "annotations": []}
            ],
        }
        final_answer = {
            "id": "msg_final",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "phase": "final_answer",
            "content": [
                {"type": "output_text", "text": "Finished", "annotations": []}
            ],
        }
        message = openai_response_to_anthropic_message(
            {
                "id": "resp_phase",
                "object": "response",
                "status": "completed",
                "model": "gpt-5.6-sol",
                "output": [commentary, final_answer],
                "usage": {
                    "input_tokens": 1,
                    "input_tokens_details": {
                        "cached_tokens": 0,
                        "cache_write_tokens": 0,
                    },
                    "output_tokens": 2,
                    "output_tokens_details": {"reasoning_tokens": 0},
                    "total_tokens": 3,
                },
            },
            strict=True,
        )

        self.assertEqual(
            ["Checking now", "Finished"],
            [block["text"] for block in message["content"] if block["type"] == "text"],
        )
        envelope = next(
            block for block in message["content"] if block["type"] == "redacted_thinking"
        )
        self.assertTrue(envelope["data"].startswith("ciel-responses-commentary-v1:"))

        follow_up = anthropic_messages_to_openai_responses(
            {
                "max_tokens": 32,
                "messages": [
                    {"role": "assistant", "content": message["content"]},
                    {"role": "user", "content": "continue"},
                ],
            },
            strict=True,
        )
        self.assertEqual("commentary", follow_up["input"][0]["phase"])
        self.assertEqual("Checking now", follow_up["input"][0]["content"][0]["text"])
        self.assertEqual("Finished", follow_up["input"][1]["content"][0]["text"])

    def test_strict_responses_history_accepts_both_assistant_phases(self):
        request = openai_responses_to_anthropic_messages(
            {
                "_ciel_remote_bridge_request": True,
                "model": "claude",
                "input": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "phase": "commentary",
                        "content": [{"type": "output_text", "text": "Checking"}],
                    },
                    {
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "phase": "final_answer",
                        "content": [{"type": "output_text", "text": "Done"}],
                    },
                    {"type": "message", "role": "user", "content": "continue"},
                ],
            },
            "claude",
        )

        self.assertEqual(
            ["Checking", "Done"],
            [block["text"] for block in request["messages"][0]["content"]],
        )

    def test_file_images_and_text_documents_project_in_both_directions(self):
        responses = anthropic_messages_to_openai_responses(
            {
                "max_tokens": 32,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {"type": "file", "file_id": "file_image"},
                            },
                            {
                                "type": "document",
                                "source": {
                                    "type": "text",
                                    "media_type": "text/plain",
                                    "data": "hello",
                                },
                                "title": "note.txt",
                            },
                        ],
                    }
                ],
            },
            strict=True,
        )
        self.assertEqual("file_image", responses["input"][0]["content"][0]["file_id"])
        self.assertEqual(
            "data:text/plain;base64,aGVsbG8=",
            responses["input"][0]["content"][1]["file_data"],
        )

        anthropic = openai_responses_to_anthropic_messages(
            {
                "_ciel_remote_bridge_request": True,
                "model": "claude",
                "input": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {"type": "input_image", "file_id": "file_image"},
                            {
                                "type": "input_file",
                                "file_data": "data:text/plain;base64,aGVsbG8=",
                                "filename": "note.txt",
                            },
                        ],
                    }
                ],
            },
            "claude",
        )
        self.assertEqual(
            {"type": "file", "file_id": "file_image"},
            anthropic["messages"][0]["content"][0]["source"],
        )
        self.assertEqual(
            {"type": "text", "media_type": "text/plain", "data": "hello"},
            anthropic["messages"][0]["content"][1]["source"],
        )

    def test_namespaced_tool_history_flattens_without_spoofing_anthropic_toolsets(self):
        anthropic = openai_responses_to_anthropic_messages(
            {
                "_ciel_remote_bridge_request": True,
                "model": "claude",
                "input": [
                    {
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "lookup",
                        "namespace": "crm",
                        "arguments": "{}",
                        "status": "completed",
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call_1",
                        "output": "ok",
                        "status": "completed",
                    },
                ],
            },
            "claude",
        )
        self.assertEqual("crm__lookup", anthropic["messages"][0]["content"][0]["name"])
        self.assertNotIn("toolset_name", anthropic["messages"][0]["content"][0])
        self.assertNotIn("toolset_name", anthropic["messages"][1]["content"][0])

        restored = anthropic_messages_to_openai_responses(
            {"max_tokens": 32, "messages": anthropic["messages"]},
            strict=True,
        )
        self.assertEqual("crm__lookup", restored["input"][0]["name"])
        self.assertNotIn("namespace", restored["input"][0])

    def test_tool_history_rejects_orphans_dangling_calls_and_intervening_text(self):
        with self.assertRaisesRegex(ValueError, "no matching"):
            anthropic_messages_to_openai_responses(
                {
                    "max_tokens": 32,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "missing",
                                    "content": "x",
                                }
                            ],
                        }
                    ],
                },
                strict=True,
            )
        with self.assertRaisesRegex(ValueError, "matching tool_result"):
            anthropic_messages_to_openai_responses(
                {
                    "max_tokens": 32,
                    "messages": [
                        {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "call_1",
                                    "name": "lookup",
                                    "input": {},
                                }
                            ],
                        }
                    ],
                },
                strict=True,
            )
        with self.assertRaisesRegex(ValueError, "immediately following"):
            openai_responses_to_anthropic_messages(
                {
                    "_ciel_remote_bridge_request": True,
                    "model": "claude",
                    "input": [
                        {
                            "type": "function_call",
                            "call_id": "call_1",
                            "name": "lookup",
                            "arguments": "{}",
                        },
                        {"type": "message", "role": "user", "content": "later"},
                        {
                            "type": "function_call_output",
                            "call_id": "call_1",
                            "output": "ok",
                        },
                    ],
                },
                "claude",
            )

    def test_usage_service_tier_and_anthropic_cache_metadata_are_preserved(self):
        message = openai_response_to_anthropic_message(
            {
                "id": "resp_tier",
                "object": "response",
                "status": "completed",
                "model": "gpt-5.6-sol",
                "service_tier": "priority",
                "output": [
                    {
                        "id": "msg_1",
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
            },
            strict=True,
        )
        self.assertEqual("priority", message["usage"]["service_tier"])

        response = anthropic_message_to_openai_response(
            {
                "id": "msg_cache",
                "type": "message",
                "role": "assistant",
                "model": "claude",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "cache_creation": {"ephemeral_5m_input_tokens": 1},
                    "inference_geo": "us",
                },
            },
            {"_ciel_remote_bridge_request": True},
        )
        self.assertEqual(
            '{"ephemeral_5m_input_tokens":1}',
            response["metadata"]["ciel_anthropic_cache_creation"],
        )
        self.assertEqual("us", response["metadata"]["ciel_anthropic_inference_geo"])

    def test_adaptive_thinking_defaults_to_summarized_and_keeps_explicit_display(self):
        defaulted = anthropic_messages_to_openai_responses(
            {
                "max_tokens": 32,
                "messages": [{"role": "user", "content": "hello"}],
                "thinking": {"type": "adaptive"},
            },
            strict=True,
        )
        summarized = anthropic_messages_to_openai_responses(
            {
                "max_tokens": 32,
                "messages": [{"role": "user", "content": "hello"}],
                "thinking": {"type": "adaptive", "display": "summarized"},
                "output_config": {"effort": "high"},
            },
            strict=True,
        )
        omitted = anthropic_messages_to_openai_responses(
            {
                "max_tokens": 32,
                "messages": [{"role": "user", "content": "hello"}],
                "thinking": {"type": "adaptive", "display": "omitted"},
                "output_config": {"effort": "low"},
            },
            strict=True,
        )

        self.assertEqual({"summary": "auto"}, defaulted["reasoning"])
        self.assertEqual(
            {"effort": "high", "summary": "auto"}, summarized["reasoning"]
        )
        self.assertEqual({"effort": "low"}, omitted["reasoning"])

    def test_strict_media_types_reject_invalid_anthropic_payloads(self):
        with self.assertRaisesRegex(ValueError, "image media_type"):
            anthropic_messages_to_openai_responses(
                {
                    "max_tokens": 32,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "text/plain",
                                        "data": "aA==",
                                    },
                                }
                            ],
                        }
                    ],
                },
                strict=True,
            )
        with self.assertRaisesRegex(ValueError, "base64 document media_type"):
            anthropic_messages_to_openai_responses(
                {
                    "max_tokens": 32,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "document",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/png",
                                        "data": "aA==",
                                    },
                                }
                            ],
                        }
                    ],
                },
                strict=True,
            )
        with self.assertRaisesRegex(ValueError, "media type"):
            openai_responses_to_anthropic_messages(
                {
                    "_ciel_remote_bridge_request": True,
                    "model": "claude",
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_image",
                                    "image_url": "data:text/plain;base64,aA==",
                                }
                            ],
                        }
                    ],
                },
                "claude",
            )

    def test_strict_response_rejects_unprojectable_top_level_and_tool_shapes(self):
        valid = {
            "id": "resp_tool",
            "object": "response",
            "status": "completed",
            "model": "gpt-5.6-sol",
            "output": [
                {
                    "id": "fc_1",
                    "type": "function_call",
                    "status": "completed",
                    "call_id": "call_1",
                    "name": "lookup",
                    "arguments": "{}",
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
        for mutation in (
            {**valid, "foo": {"secret": 1}},
            {**valid, "metadata": {"x": "y"}},
        ):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                openai_response_to_anthropic_message(mutation, strict=True)
        for field in ("id", "status"):
            malformed = {**valid, "output": [dict(valid["output"][0])]}
            malformed["output"][0].pop(field)
            with self.subTest(field=field), self.assertRaises(ValueError):
                openai_response_to_anthropic_message(malformed, strict=True)
        for item_type, payload_field, payload in (
            ("function_call", "arguments", "{}"),
            ("custom_tool_call", "input", "raw"),
        ):
            for field, value in (
                ("call_id", 123),
                ("call_id", " call_1"),
                ("call_id", "call_1 "),
                ("name", 123),
                ("name", " lookup"),
                ("name", "lookup "),
            ):
                malformed = {
                    **valid,
                    "output": [
                        {
                            "id": "tc_1",
                            "type": item_type,
                            "status": "completed",
                            "call_id": "call_1",
                            "name": "lookup",
                            payload_field: payload,
                            field: value,
                        }
                    ],
                }
                with (
                    self.subTest(item_type=item_type, field=field, value=value),
                    self.assertRaisesRegex(ValueError, "call_id/name"),
                ):
                    openai_response_to_anthropic_message(malformed, strict=True)
        custom = {
            **valid,
            "output": [
                {
                    "id": "ctc_1",
                    "type": "custom_tool_call",
                    "status": "completed",
                    "call_id": "call_1",
                    "name": "shell",
                    "input": {"cmd": "whoami"},
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "must be a string"):
            openai_response_to_anthropic_message(custom, strict=True)

    def test_strict_anthropic_tool_error_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "is_error"):
            anthropic_messages_to_openai_responses(
                {
                    "max_tokens": 32,
                    "messages": [
                        {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "call_1",
                                    "name": "lookup",
                                    "input": {},
                                }
                            ],
                        },
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "call_1",
                                    "content": "failed",
                                    "is_error": True,
                                }
                            ],
                        },
                    ],
                },
                strict=True,
            )


class AnthropicResponsesBridgeTests(unittest.TestCase):
    def _bridge(self, *, post_json=None, to_responses=None, to_anthropic=None):
        post_json = post_json or mock.Mock(
            return_value={
                "id": "resp_1",
                "status": "completed",
                "model": "upstream-model",
                "output": [
                    {
                        "type": "message",
                        "status": "completed",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "done"}],
                    }
                ],
            }
        )
        write_message = mock.Mock()
        write_json = mock.Mock()
        open_request = mock.Mock()
        normalize_options = mock.Mock(side_effect=lambda _p, _c, body, _w: body)
        bridge = AnthropicResponsesBridge(
            AnthropicResponsesBridgePorts(
                projection=AnthropicResponsesProjectionPorts(
                    to_responses=to_responses
                    or anthropic_messages_to_openai_responses,
                    to_anthropic=to_anthropic
                    or openai_response_to_anthropic_message,
                    normalize_model=lambda _p, _c, _m: "upstream-model",
                    normalize_options=normalize_options,
                ),
                transport=AnthropicResponsesTransportPorts(
                    endpoint=lambda _p, _c, _w: "https://provider.test/responses",
                    headers=lambda _p, _c, _h, _w: {
                        "authorization": "Bearer router-owned"
                    },
                    post_json=post_json,
                    open_request=open_request,
                    timeout_seconds=lambda _c: 45.0,
                ),
                output=AnthropicResponsesOutputPorts(
                    write_message=write_message,
                    write_json=write_json,
                    stream_response=mock.Mock(),
                ),
            )
        )
        return SimpleNamespace(
            bridge=bridge,
            post_json=post_json,
            open_request=open_request,
            write_message=write_message,
            write_json=write_json,
            normalize_options=normalize_options,
        )

    @staticmethod
    def _handler():
        return SimpleNamespace(headers={"x-client-header": "client-value"})

    def test_nonstream_bridge_posts_responses_and_writes_anthropic_message(self):
        service = self._bridge()
        handler = self._handler()

        service.bridge.forward(
            handler,
            "github-copilot-oauth",
            {"request_timeout_ms": 45_000},
            {
                "model": "client-alias",
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 64,
                "stream": False,
            },
            "client-alias",
        )

        service.open_request.assert_not_called()
        service.post_json.assert_called_once()
        args = service.post_json.call_args.args
        self.assertEqual("https://provider.test/responses", args[0])
        self.assertEqual("upstream-model", args[1]["model"])
        self.assertFalse(args[1]["stream"])
        self.assertEqual(["reasoning.encrypted_content"], args[1]["include"])
        self.assertEqual("Bearer router-owned", args[2]["authorization"])
        self.assertEqual(45.0, args[3])
        service.write_json.assert_not_called()
        service.write_message.assert_called_once()
        written = service.write_message.call_args.args
        self.assertIs(handler, written[0])
        self.assertEqual("done", written[1]["content"][0]["text"])
        self.assertFalse(written[2])

    def test_invalid_request_is_anthropic_400_without_transport(self):
        service = self._bridge()

        service.bridge.forward(
            self._handler(),
            "provider",
            {},
            {
                "max_tokens": 64,
                "messages": [
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "",
                                "name": "tool",
                                "input": {},
                            }
                        ],
                    }
                ]
            },
            "model",
        )

        service.post_json.assert_not_called()
        service.write_message.assert_not_called()
        self.assertEqual(400, service.write_json.call_args.kwargs["status"])
        error = service.write_json.call_args.args[1]
        self.assertEqual("error", error["type"])
        self.assertEqual("invalid_request_error", error["error"]["type"])
        self.assertIn("tool_use", error["error"]["message"])

    def test_bridge_requires_positive_max_tokens_before_transport(self):
        for max_tokens in (mock.sentinel.missing, None, False, -1, 0, 1.0):
            with self.subTest(max_tokens=max_tokens):
                service = self._bridge()
                body = {"messages": [{"role": "user", "content": "hello"}]}
                if max_tokens is not mock.sentinel.missing:
                    body["max_tokens"] = max_tokens

                service.bridge.forward(
                    self._handler(),
                    "provider",
                    {},
                    body,
                    "model",
                )

                service.post_json.assert_not_called()
                service.open_request.assert_not_called()
                service.write_message.assert_not_called()
                self.assertEqual(400, service.write_json.call_args.kwargs["status"])
                error = service.write_json.call_args.args[1]
                self.assertEqual("invalid_request_error", error["error"]["type"])
                self.assertIn("max_tokens", error["error"]["message"])

    def test_stop_sequences_return_anthropic_400_without_transport(self):
        service = self._bridge()

        service.bridge.forward(
            self._handler(),
            "provider",
            {},
            {
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 64,
                "stop_sequences": ["SECRET_END"],
            },
            "model",
        )

        service.post_json.assert_not_called()
        service.open_request.assert_not_called()
        service.write_message.assert_not_called()
        self.assertEqual(400, service.write_json.call_args.kwargs["status"])
        error = service.write_json.call_args.args[1]
        self.assertIn("stop_sequences", error["error"]["message"])

    def test_invalid_upstream_response_is_anthropic_502(self):
        service = self._bridge(
            post_json=mock.Mock(
                return_value={"id": "resp_1", "status": "in_progress", "output": []}
            )
        )

        service.bridge.forward(
            self._handler(),
            "provider",
            {},
            {
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 64,
            },
            "model",
        )

        service.write_message.assert_not_called()
        self.assertEqual(502, service.write_json.call_args.kwargs["status"])
        self.assertIn(
            "non-terminal", service.write_json.call_args.args[1]["error"]["message"]
        )

    def test_remote_bridge_requires_strict_responses_envelope(self):
        service = self._bridge(
            post_json=mock.Mock(
                return_value={
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "status": "completed",
                            "content": [
                                {"type": "output_text", "text": "not official"}
                            ],
                        }
                    ],
                }
            )
        )

        service.bridge.forward(
            self._handler(),
            "provider",
            {REMOTE_BRIDGE_CONFIG_MARKER: True},
            {
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 64,
            },
            "model",
        )

        service.write_message.assert_not_called()
        self.assertEqual(502, service.write_json.call_args.kwargs["status"])
        self.assertIn(
            "response.id", service.write_json.call_args.args[1]["error"]["message"]
        )

    def test_nonobject_upstream_response_is_anthropic_502(self):
        service = self._bridge(post_json=mock.Mock(return_value=["not", "an", "object"]))

        service.bridge.forward(
            self._handler(),
            "provider",
            {},
            {
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 64,
            },
            "model",
        )

        service.write_message.assert_not_called()
        self.assertEqual(502, service.write_json.call_args.kwargs["status"])
        self.assertIn(
            "non-object", service.write_json.call_args.args[1]["error"]["message"]
        )

    def test_http_error_status_and_provider_message_reach_anthropic_client(self):
        raw = b'{"error":{"message":"monthly quota exhausted"}}'
        error = urllib.error.HTTPError(
            "https://provider.test/responses",
            429,
            "Too Many Requests",
            {},
            io.BytesIO(raw),
        )
        service = self._bridge(post_json=mock.Mock(side_effect=error))

        service.bridge.forward(
            self._handler(),
            "provider",
            {},
            {
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 64,
            },
            "model",
        )

        service.write_message.assert_not_called()
        self.assertEqual(429, service.write_json.call_args.kwargs["status"])
        payload = service.write_json.call_args.args[1]
        self.assertEqual("rate_limit_error", payload["error"]["type"])
        self.assertEqual("monthly quota exhausted", payload["error"]["message"])


if __name__ == "__main__":
    unittest.main()
