import io
import os
import unittest
from unittest import mock

import ciel_runtime


class ThinkingPassthroughTests(unittest.TestCase):
    def setUp(self):
        ciel_runtime.clear_suppressed_thinking_passback_cache()

    def test_do_not_defer_plan_mode_synthesis_for_non_anthropic_thinking(self):
        body = {
            "thinking": {"type": "enabled", "budget_tokens": 1024},
            "tool_choice": {"type": "tool", "name": "EnterPlanMode"},
            "tools": [{"name": "EnterPlanMode", "input_schema": {"type": "object"}}],
            "messages": [{"role": "user", "content": [{"type": "text", "text": "plan"}]}],
        }

        self.assertFalse(
            ciel_runtime.should_defer_forced_tool_choice_for_thinking(
                "vllm",
                {"native_compat": True},
                body,
                "EnterPlanMode",
            )
        )

    def test_do_not_defer_plan_mode_synthesis_without_thinking(self):
        body = {
            "tool_choice": {"type": "tool", "name": "EnterPlanMode"},
            "tools": [{"name": "EnterPlanMode", "input_schema": {"type": "object"}}],
            "messages": [{"role": "user", "content": [{"type": "text", "text": "plan"}]}],
        }

        self.assertFalse(
            ciel_runtime.should_defer_forced_tool_choice_for_thinking(
                "vllm",
                {"native_compat": True},
                body,
                "EnterPlanMode",
            )
        )

    def test_strip_thinking_for_ciel_runtime_synthetic_tool_history(self):
        body = {
            "thinking": {"type": "enabled", "budget_tokens": 1024},
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_ciel_runtime_EnterPlanMode_123",
                            "name": "EnterPlanMode",
                            "input": {},
                        }
                    ],
                }
            ],
        }

        out = ciel_runtime.normalize_thinking_for_non_anthropic_native_provider(
            "vllm",
            {"native_compat": True},
            body,
        )

        self.assertIn("thinking", body)
        self.assertNotIn("thinking", out)

    def test_strip_thinking_for_tool_continuation_without_passback_blocks(self):
        body = {
            "thinking": {"type": "enabled", "budget_tokens": 1024},
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_real_123",
                            "name": "Read",
                            "input": {"file_path": "x"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_real_123",
                            "content": "file contents",
                        }
                    ],
                },
            ],
        }

        out = ciel_runtime.normalize_thinking_for_non_anthropic_native_provider(
            "lm-studio",
            {"native_compat": True},
            body,
        )

        self.assertNotIn("thinking", out)
        self.assertEqual(2, ciel_runtime.anthropic_tool_continuation_block_count(body))

    def test_strip_thinking_blocks_even_without_top_level_thinking_for_tool_continuation(self):
        body = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "hidden", "signature": "sig"},
                        {
                            "type": "tool_use",
                            "id": "toolu_real_123",
                            "name": "Read",
                            "input": {"file_path": "x"},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_real_123",
                            "content": "file contents",
                        }
                    ],
                },
            ],
        }

        out = ciel_runtime.normalize_thinking_for_non_anthropic_native_provider(
            "deepseek",
            {"native_compat": True},
            body,
        )

        self.assertIsNot(out, body)
        self.assertNotIn("thinking", out)
        self.assertEqual(0, ciel_runtime.anthropic_thinking_block_count(out))
        self.assertEqual(1, ciel_runtime.anthropic_thinking_block_count(body))

    def test_strip_initial_thinking_request_for_non_anthropic_native_provider(self):
        body = {
            "thinking": {"type": "enabled", "budget_tokens": 1024},
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
        }

        out = ciel_runtime.normalize_thinking_for_non_anthropic_native_provider(
            "self-hosted-nim",
            {"native_compat": True},
            body,
        )

        self.assertIsNot(out, body)
        self.assertIn("thinking", body)
        self.assertNotIn("thinking", out)

    def test_strip_adaptive_thinking_request_for_non_anthropic_native_provider(self):
        body = {
            "thinking": {"type": "adaptive"},
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
        }

        out = ciel_runtime.normalize_thinking_for_non_anthropic_native_provider(
            "deepseek",
            {"native_compat": True},
            body,
        )

        self.assertIsNot(out, body)
        self.assertIn("thinking", body)
        self.assertNotIn("thinking", out)

    def test_disabled_thinking_request_is_not_treated_as_enabled(self):
        body = {
            "thinking": {"type": "disabled"},
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
        }

        out = ciel_runtime.normalize_thinking_for_non_anthropic_native_provider(
            "deepseek",
            {"native_compat": True},
            body,
        )

        self.assertIs(out, body)
        self.assertIn("thinking", out)

    def test_strip_thinking_after_assistant_history_without_tool_continuation(self):
        body = {
            "thinking": {"type": "enabled", "budget_tokens": 1024},
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "continue implementation"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "I will continue implementation now."}]},
                {
                    "role": "user",
                    "content": [],
                    "attachment": {"type": "plan_mode", "filePath": "/tmp/plan.md"},
                },
            ],
        }

        out = ciel_runtime.normalize_thinking_for_non_anthropic_native_provider(
            "deepseek",
            {"native_compat": True},
            body,
        )

        self.assertIsNot(out, body)
        self.assertIn("thinking", body)
        self.assertNotIn("thinking", out)

    def test_strip_thinking_blocks_for_real_tool_continuation(self):
        body = {
            "thinking": {"type": "enabled", "budget_tokens": 1024},
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "hidden", "signature": "sig"},
                        {
                            "type": "tool_use",
                            "id": "toolu_real_123",
                            "name": "Read",
                            "input": {"file_path": "x"},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_real_123",
                            "content": "file contents",
                        }
                    ],
                }
            ],
        }

        out = ciel_runtime.normalize_thinking_for_non_anthropic_native_provider(
            "vllm",
            {"native_compat": True},
            body,
        )

        self.assertIsNot(out, body)
        self.assertNotIn("thinking", out)
        self.assertEqual(0, ciel_runtime.anthropic_thinking_block_count(out))
        self.assertEqual(1, ciel_runtime.anthropic_thinking_block_count(body))

    def test_preserve_thinking_for_anthropic_native_history(self):
        body = {
            "thinking": {"type": "enabled", "budget_tokens": 1024},
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "hidden", "signature": "sig"},
                        {
                            "type": "tool_use",
                            "id": "toolu_real_123",
                            "name": "Read",
                            "input": {"file_path": "x"},
                        },
                    ],
                }
            ],
        }

        out = ciel_runtime.normalize_thinking_for_non_anthropic_native_provider(
            "anthropic",
            {"native_compat": True},
            body,
        )

        self.assertIs(out, body)
        self.assertIn("thinking", out)
        self.assertEqual(1, ciel_runtime.anthropic_thinking_block_count(out))

    def test_strip_thinking_response_blocks_for_non_anthropic_provider(self):
        message = {
            "content": [
                {"type": "thinking", "thinking": "private reasoning", "signature": "sig"},
                {"type": "text", "text": "visible answer"},
            ],
            "stop_reason": "end_turn",
        }

        out = ciel_runtime.normalize_response_thinking_for_non_anthropic_provider(
            "vllm",
            {"native_compat": True},
            message,
        )

        self.assertIsNot(out, message)
        self.assertEqual([{"type": "text", "text": "visible answer"}], out["content"])
        self.assertEqual(1, ciel_runtime.anthropic_thinking_block_count({"messages": [{"role": "assistant", "content": message["content"]}]}))
        self.assertEqual(0, ciel_runtime.anthropic_thinking_block_count({"messages": [{"role": "assistant", "content": out["content"]}]}))

    def test_rehydrate_suppressed_thinking_passback_for_non_anthropic_provider(self):
        ciel_runtime.remember_suppressed_thinking_passback(
            "deepseek",
            "model",
            [{"type": "thinking", "thinking": "private reasoning", "signature": "sig"}],
        )
        body = {
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "continue"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "visible answer"}]},
            ]
        }

        out = ciel_runtime.rehydrate_suppressed_thinking_passback(
            "deepseek",
            {"native_compat": True},
            body,
        )

        self.assertIsNot(out, body)
        self.assertEqual("thinking", out["messages"][1]["content"][0]["type"])
        self.assertEqual("visible answer", out["messages"][1]["content"][1]["text"])
        self.assertEqual(1, ciel_runtime.anthropic_thinking_block_count(out))

    def test_rehydrate_more_than_initial_small_passback_window(self):
        old_limit = ciel_runtime.SUPPRESSED_THINKING_PASSBACK_MAX
        ciel_runtime.SUPPRESSED_THINKING_PASSBACK_MAX = 64
        try:
            for index in range(32):
                ciel_runtime.remember_suppressed_thinking_passback(
                    "deepseek",
                    "model",
                    [{"type": "thinking", "thinking": f"private reasoning {index}", "signature": f"sig-{index}"}],
                )
            body = {
                "messages": [
                    {"role": "assistant", "content": [{"type": "text", "text": f"visible {index}"}]}
                    for index in range(32)
                ]
            }

            out = ciel_runtime.rehydrate_suppressed_thinking_passback(
                "deepseek",
                {"native_compat": True},
                body,
            )

            self.assertEqual(32, ciel_runtime.anthropic_thinking_block_count(out))
            self.assertEqual("private reasoning 0", out["messages"][0]["content"][0]["thinking"])
            self.assertEqual("private reasoning 31", out["messages"][-1]["content"][0]["thinking"])
        finally:
            ciel_runtime.SUPPRESSED_THINKING_PASSBACK_MAX = old_limit

    def test_do_not_rehydrate_suppressed_thinking_passback_for_anthropic_provider(self):
        ciel_runtime.remember_suppressed_thinking_passback(
            "deepseek",
            "model",
            [{"type": "thinking", "thinking": "private reasoning", "signature": "sig"}],
        )
        body = {"messages": [{"role": "assistant", "content": [{"type": "text", "text": "visible answer"}]}]}

        out = ciel_runtime.rehydrate_suppressed_thinking_passback("anthropic", {}, body)

        self.assertIs(out, body)
        self.assertEqual(0, ciel_runtime.anthropic_thinking_block_count(out))

    def test_preserve_thinking_response_blocks_for_anthropic_provider(self):
        message = {
            "content": [
                {"type": "thinking", "thinking": "private reasoning", "signature": "sig"},
                {"type": "text", "text": "visible answer"},
            ],
        }

        out = ciel_runtime.normalize_response_thinking_for_non_anthropic_provider(
            "anthropic",
            {},
            message,
        )

        self.assertIs(out, message)
        self.assertEqual(2, len(out["content"]))

    def test_rebatch_suppresses_thinking_stream_blocks_for_non_anthropic_provider(self):
        class FakeHandler:
            def __init__(self):
                self.wfile = io.BytesIO()

        def sse(event_name, payload):
            return f"event: {event_name}\ndata: {ciel_runtime.json.dumps(payload, ensure_ascii=False)}\n\n".encode()

        chunks = [
            sse("message_start", {"type": "message_start", "message": {"id": "msg", "type": "message", "role": "assistant", "content": [], "model": "model"}}),
            sse("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}}),
            sse("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "private reasoning"}}),
            sse("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig"}}),
            sse("content_block_stop", {"type": "content_block_stop", "index": 0}),
            sse("content_block_start", {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}}),
            sse("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "visible answer"}}),
            sse("content_block_stop", {"type": "content_block_stop", "index": 1}),
            sse("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 3}}),
            sse("message_stop", {"type": "message_stop"}),
        ]
        resp = io.BytesIO(b"".join(chunks))
        handler = FakeHandler()

        ciel_runtime._rebatch_anthropic_sse_text(
            handler,
            resp,
            "model",
            word_chunking=False,
            preserve_thinking=False,
            provider="deepseek",
        )

        output = handler.wfile.getvalue().decode("utf-8")
        self.assertNotIn("thinking_delta", output)
        self.assertNotIn("private reasoning", output)
        self.assertIn("visible answer", output)
        self.assertIn('"index": 0', output)
        self.assertNotIn('"index": 1', output)
        rehydrated = ciel_runtime.rehydrate_suppressed_thinking_passback(
            "deepseek",
            {"native_compat": True},
            {"messages": [{"role": "assistant", "content": [{"type": "text", "text": "visible answer"}]}]},
        )
        self.assertEqual(1, ciel_runtime.anthropic_thinking_block_count(rehydrated))

    def test_rebatch_normalizes_native_stream_tool_use_for_non_anthropic_provider(self):
        class FakeHandler:
            def __init__(self):
                self.wfile = io.BytesIO()

        def sse(event_name, payload):
            return f"event: {event_name}\ndata: {ciel_runtime.json.dumps(payload, ensure_ascii=False)}\n\n".encode()

        chunks = [
            sse("message_start", {"type": "message_start", "message": {"id": "msg", "type": "message", "role": "assistant", "content": [], "model": "model"}}),
            sse("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "toolu_bad", "name": "Bash", "input": {}}}),
            sse("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": "echo hello"}}),
            sse("content_block_stop", {"type": "content_block_stop", "index": 0}),
            sse("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None}, "usage": {"output_tokens": 3}}),
            sse("message_stop", {"type": "message_stop"}),
        ]
        handler = FakeHandler()

        ciel_runtime._rebatch_anthropic_sse_text(
            handler,
            io.BytesIO(b"".join(chunks)),
            "model",
            source_body={"tools": [{"name": "Bash", "input_schema": {"type": "object"}}]},
            preserve_thinking=False,
            normalize_tool_use=True,
        )

        payloads = []
        for event_block in handler.wfile.getvalue().decode("utf-8").split("\n\n"):
            data_lines = [line[5:].strip() for line in event_block.splitlines() if line.startswith("data:")]
            if data_lines:
                payloads.append(ciel_runtime.json.loads("\n".join(data_lines)))
        tool_starts = [
            payload
            for payload in payloads
            if payload.get("type") == "content_block_start"
            and isinstance(payload.get("content_block"), dict)
            and payload["content_block"].get("type") == "tool_use"
        ]
        tool_deltas = [
            payload
            for payload in payloads
            if payload.get("type") == "content_block_delta"
            and isinstance(payload.get("delta"), dict)
            and payload["delta"].get("type") == "input_json_delta"
        ]

        self.assertEqual("Bash", tool_starts[0]["content_block"]["name"])
        emitted_input = ciel_runtime.json.loads(tool_deltas[0]["delta"]["partial_json"])
        self.assertEqual("echo hello", emitted_input["command"])

    def test_rebatch_caps_wait_tool_timeout_in_anthropic_passthrough_stream(self):
        self.addCleanup(ciel_runtime._MCP_NOTIFICATION_WAIT_RECENT.clear)

        class FakeHandler:
            def __init__(self):
                self.wfile = io.BytesIO()

        def sse(event_name, payload):
            return f"event: {event_name}\ndata: {ciel_runtime.json.dumps(payload, ensure_ascii=False)}\n\n".encode()

        chunks = [
            sse("message_start", {"type": "message_start", "message": {"id": "msg", "type": "message", "role": "assistant", "content": [], "model": "model"}}),
            sse(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {
                        "type": "tool_use",
                        "id": "toolu_wait",
                        "name": "mcp__ai-net-http__wait_for_notifications",
                        "input": {},
                    },
                },
            ),
            sse(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "input_json_delta", "partial_json": '{"timeout_ms":300000}'},
                },
            ),
            sse("content_block_stop", {"type": "content_block_stop", "index": 0}),
            sse("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None}, "usage": {"output_tokens": 3}}),
            sse("message_stop", {"type": "message_stop"}),
        ]
        handler = FakeHandler()

        with (
            mock.patch.dict(os.environ, {"CIEL_RUNTIME_MCP_NOTIFICATION_WAIT_TIMEOUT_MS": "1000"}, clear=False),
            mock.patch.object(ciel_runtime, "router_log"),
        ):
            ciel_runtime._MCP_NOTIFICATION_WAIT_RECENT.clear()
            ciel_runtime._rebatch_anthropic_sse_text(
                handler,
                io.BytesIO(b"".join(chunks)),
                "model",
                source_body={
                    "tools": [
                        {
                            "name": "mcp__ai-net-http__wait_for_notifications",
                            "input_schema": {
                                "type": "object",
                                "properties": {"timeout_ms": {"type": "number"}},
                            },
                        }
                    ]
                },
                preserve_thinking=True,
                normalize_tool_use=False,
            )

        payloads = []
        for event_block in handler.wfile.getvalue().decode("utf-8").split("\n\n"):
            data_lines = [line[5:].strip() for line in event_block.splitlines() if line.startswith("data:")]
            if data_lines:
                payloads.append(ciel_runtime.json.loads("\n".join(data_lines)))
        tool_deltas = [
            payload
            for payload in payloads
            if payload.get("type") == "content_block_delta"
            and isinstance(payload.get("delta"), dict)
            and payload["delta"].get("type") == "input_json_delta"
        ]

        emitted_input = ciel_runtime.json.loads(tool_deltas[0]["delta"]["partial_json"])
        self.assertEqual({"timeout_ms": 1000}, emitted_input)

    def test_openai_conversion_does_not_leak_thinking_text(self):
        body = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "private reasoning", "signature": "sig"},
                        {"type": "text", "text": "visible answer"},
                    ],
                }
            ]
        }

        messages = ciel_runtime.anthropic_messages_to_openai(body)

        assistant_messages = [message for message in messages if message.get("role") == "assistant"]
        self.assertTrue(assistant_messages)
        self.assertIn("visible answer", assistant_messages[-1]["content"])
        self.assertNotIn("private reasoning", assistant_messages[-1]["content"])


if __name__ == "__main__":
    unittest.main()
