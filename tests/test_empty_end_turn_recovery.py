import unittest
import json
from io import BytesIO
from unittest import mock

import ciel_runtime


def body_with_tools(user_text: str, tool_names: list[str]) -> dict:
    return {
        "model": "ciel-runtime-ollama-cloud-deepseek-v4-flash[1m]",
        "messages": [{"role": "user", "content": [{"type": "text", "text": user_text}]}],
        "tools": [{"name": name, "input_schema": {"type": "object"}} for name in tool_names],
    }


class EmptyEndTurnRecoveryTests(unittest.TestCase):
    def test_exit_plan_mode_backfills_allowed_prompts_from_schema(self):
        body = body_with_tools("implement feature", ["ExitPlanMode"])
        body["tools"][0]["input_schema"] = {
            "type": "object",
            "properties": {
                "allowedPrompts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "tool": {"type": "string", "enum": ["Bash"]},
                            "prompt": {"type": "string"},
                        },
                        "required": ["tool", "prompt"],
                    },
                }
            },
        }
        body["messages"].append(
            {
                "role": "user",
                "content": [],
                "attachment": {"type": "plan_mode", "planFilePath": "/tmp/plan.md"},
            }
        )

        name, fixed = ciel_runtime.plan_mode_tool_name_for_emit(body, "ExitPlanMode", {})

        self.assertEqual("ExitPlanMode", name)
        self.assertEqual(
            [{"tool": "Bash", "prompt": "use Bash as needed to implement and verify the approved plan"}],
            fixed["allowedPrompts"],
        )

    def test_exit_plan_mode_preserves_existing_allowed_prompts(self):
        body = body_with_tools("implement feature", ["ExitPlanMode"])
        body["tools"][0]["input_schema"] = {
            "type": "object",
            "properties": {
                "allowedPrompts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "tool": {"type": "string", "enum": ["Bash"]},
                            "prompt": {"type": "string"},
                        },
                    },
                }
            },
        }
        body["messages"].append(
            {
                "role": "user",
                "content": [],
                "attachment": {"type": "plan_mode", "planFilePath": "/tmp/plan.md"},
            }
        )
        existing = {"allowedPrompts": [{"tool": "Bash", "prompt": "run tests"}]}

        name, fixed = ciel_runtime.plan_mode_tool_name_for_emit(body, "ExitPlanMode", existing)

        self.assertEqual("ExitPlanMode", name)
        self.assertEqual(existing, fixed)

    def test_latest_user_text_ignores_system_reminder_blocks(self):
        body = body_with_tools("initial task", ["TaskList"])
        body["messages"].append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "<system-reminder>\n"
                            "The task tools haven't been used recently.\n"
                            "</system-reminder>\n"
                        ),
                    },
                    {"type": "text", "text": "계속"},
                ],
            }
        )

        self.assertEqual("계속", ciel_runtime.latest_user_text(body))

    def test_empty_resume_turn_synthesizes_tasklist(self):
        body = body_with_tools("continue implementation", ["TaskList", "Read", "Edit"])
        body["messages"].insert(
            0,
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "I will continue the Phase 2 implementation."}],
            },
        )
        data = {
            "message": {"content": ""},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 197561,
            "eval_count": 29,
        }

        message = ciel_runtime.ollama_chat_to_anthropic(data, "deepseek-v4-flash", source_body=body)

        self.assertEqual("tool_use", message["stop_reason"])
        self.assertEqual("TaskList", message["content"][0]["name"])
        self.assertEqual("tool_use", message["content"][0]["type"])

    def test_ultracode_workflow_prevents_auto_enter_plan_mode(self):
        body = body_with_tools(
            "implement the requested feature",
            ["Workflow", "EnterPlanMode", "TaskList"],
        )
        body["system"] = [
            {
                "type": "text",
                "text": "Ultracode is on: use the Workflow tool for every substantive task.",
            }
        ]

        self.assertFalse(ciel_runtime.should_auto_enter_plan_mode(body, "", []))

    def test_ultracode_still_on_workflow_prevents_auto_enter_plan_mode(self):
        body = body_with_tools(
            "implement the requested feature",
            ["Workflow", "EnterPlanMode", "TaskList"],
        )
        body["messages"].append(
            {
                "role": "system",
                "content": [{"type": "text", "text": "Ultracode is still on — use the Workflow tool; see its Ultracode section."}],
            }
        )

        self.assertFalse(ciel_runtime.should_auto_enter_plan_mode(body, "", []))

    def test_ultracode_runtime_drops_enter_plan_mode_emit(self):
        body = body_with_tools(
            "implement the requested feature",
            ["Workflow", "EnterPlanMode"],
        )
        body["system"] = "Ultracode is on: use the Workflow tool for every substantive task."

        name, fixed = ciel_runtime.plan_mode_tool_name_for_emit(body, "EnterPlanMode", {})

        self.assertIsNone(name)
        self.assertEqual({}, fixed)

    def test_ultracode_still_on_runtime_drops_enter_plan_mode_emit(self):
        body = body_with_tools(
            "implement the requested feature",
            ["Workflow", "EnterPlanMode"],
        )
        body["system"] = "Ultracode is still on — use the Workflow tool; see its Ultracode section."

        name, fixed = ciel_runtime.plan_mode_tool_name_for_emit(body, "EnterPlanMode", {})

        self.assertIsNone(name)
        self.assertEqual({}, fixed)

    def test_channel_inbox_runtime_drops_enter_plan_mode_emit(self):
        body = body_with_tools(
            "[ciel-runtime channel inbox]\n<< ai-net-http >> incoming channel message for the current agent.",
            ["EnterPlanMode", "TaskList"],
        )
        body["metadata"] = {"ciel_runtime_channel_injected": True}

        name, fixed = ciel_runtime.plan_mode_tool_name_for_emit(body, "EnterPlanMode", {})

        self.assertIsNone(name)
        self.assertEqual({}, fixed)

    def test_external_channel_runtime_drops_enter_plan_mode_emit(self):
        body = body_with_tools(
            "[ciel-runtime external channel message] channel=ai-net-http room=room1 from=agent id=42 text=\"hello\".",
            ["EnterPlanMode", "TaskList"],
        )

        name, fixed = ciel_runtime.plan_mode_tool_name_for_emit(body, "EnterPlanMode", {})

        self.assertIsNone(name)
        self.assertEqual({}, fixed)

    def test_channel_prompt_does_not_auto_synthesize_enter_plan_mode(self):
        body = body_with_tools(
            "[ciel-runtime channel inbox]\n<< ai-net-http >> incoming channel message for the current agent.",
            ["EnterPlanMode", "TaskList"],
        )
        body["metadata"] = {"ciel_runtime_channel_injected": True}

        self.assertFalse(ciel_runtime.should_auto_enter_plan_mode(body, "", []))

    def test_empty_turn_without_tasklist_returns_visible_notice(self):
        body = body_with_tools("continue implementation", ["Read", "Edit"])
        data = {
            "message": {"content": ""},
            "done": True,
            "done_reason": "stop",
            "eval_count": 29,
        }

        message = ciel_runtime.ollama_chat_to_anthropic(data, "deepseek-v4-flash", source_body=body)

        text_blocks = [block for block in message["content"] if block.get("type") == "text"]
        self.assertTrue(text_blocks)
        self.assertIn("empty end_turn", text_blocks[0]["text"])
        self.assertEqual("end_turn", message["stop_reason"])

    def test_empty_turn_after_empty_tasklist_returns_no_active_task_notice(self):
        body = body_with_tools("continue implementation", ["TaskList", "Read"])
        body["messages"].append(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_tasks",
                        "name": "TaskList",
                        "input": {},
                    }
                ],
            }
        )
        body["messages"].append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_tasks",
                        "content": "No tasks found",
                    }
                ],
            }
        )
        data = {
            "message": {"content": ""},
            "done": True,
            "done_reason": "stop",
            "eval_count": 1,
        }

        message = ciel_runtime.ollama_chat_to_anthropic(data, "deepseek-v4-flash", source_body=body)

        text_blocks = [block for block in message["content"] if block.get("type") == "text"]
        self.assertTrue(text_blocks)
        self.assertIn("TaskList returned no active tasks", text_blocks[0]["text"])
        self.assertNotIn("empty end_turn", text_blocks[0]["text"])
        self.assertEqual("end_turn", message["stop_reason"])

    def test_empty_plain_chat_does_not_synthesize_tasklist(self):
        body = body_with_tools("hi", ["TaskList", "Read"])
        data = {
            "message": {"content": ""},
            "done": True,
            "done_reason": "stop",
            "eval_count": 1,
        }

        message = ciel_runtime.ollama_chat_to_anthropic(data, "deepseek-v4-flash", source_body=body)

        self.assertEqual("end_turn", message["stop_reason"])
        self.assertEqual("text", message["content"][0]["type"])
        self.assertIn("empty end_turn", message["content"][0]["text"])

    def test_choice_question_in_plan_mode_synthesizes_tasklist(self):
        body = body_with_tools("continue implementation", ["TaskList", "Read", "ExitPlanMode"])
        body["messages"].append(
            {
                "role": "user",
                "content": [],
                "attachment": {"type": "plan_mode", "filePath": "/tmp/plan.md"},
            }
        )
        data = {
            "message": {
                "content": (
                    "Which implementation part should I start now? "
                    "Or should I proceed with every in-scope part?"
                )
            },
            "done": True,
            "done_reason": "stop",
            "eval_count": 40,
        }

        message = ciel_runtime.ollama_chat_to_anthropic(data, "deepseek-v4-flash", source_body=body)

        self.assertEqual("tool_use", message["stop_reason"])
        self.assertEqual("text", message["content"][0]["type"])
        self.assertEqual("TaskList", message["content"][-1]["name"])

    def test_native_json_choice_question_synthesizes_tasklist(self):
        body = body_with_tools("continue implementation", ["TaskList", "Read", "ExitPlanMode"])
        body["messages"].append(
            {
                "role": "user",
                "content": [],
                "attachment": {"type": "plan_mode", "filePath": "/tmp/plan.md"},
            }
        )
        message = {
            "id": "msg_native",
            "type": "message",
            "role": "assistant",
            "model": "deepseek-v4-flash",
            "content": [{"type": "text", "text": "Which part should I implement first?"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }

        patched = ciel_runtime.append_synthetic_tasklist_to_message(message, "deepseek-v4-flash", body, "test")

        self.assertEqual("tool_use", patched["stop_reason"])
        self.assertEqual("TaskList", patched["content"][-1]["name"])

    def test_native_stream_choice_question_synthesizes_tasklist(self):
        body = body_with_tools("continue implementation", ["TaskList", "Read", "ExitPlanMode"])
        body["messages"].append(
            {
                "role": "user",
                "content": [],
                "attachment": {"type": "plan_mode", "filePath": "/tmp/plan.md"},
            }
        )

        class Handler:
            def __init__(self):
                self.wfile = BytesIO()

        handler = Handler()
        events = [
            'event: message_start\ndata: {"type":"message_start","message":{"content":[]}}\n\n',
            'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n',
            'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Which part should I implement first?"}}\n\n',
            'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
            'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":1}}\n\n',
            'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]

        lines = []
        for event in events:
            lines.extend(f"{line}\n".encode("utf-8") for line in event.splitlines())
        ciel_runtime._rebatch_anthropic_sse_text(
            handler,
            lines,
            "deepseek-v4-flash",
            word_chunking=False,
            source_body=body,
        )
        output = handler.wfile.getvalue().decode("utf-8")

        self.assertIn("toolu_anthropic_choice_", output)
        self.assertIn('"name": "TaskList"', output)
        self.assertIn('"stop_reason": "tool_use"', output)

    def test_anthropic_routed_stream_does_not_synthesize_tasklist(self):
        body = body_with_tools("continue implementation", ["TaskList", "Read", "ExitPlanMode"])
        body["messages"].append(
            {
                "role": "user",
                "content": [],
                "attachment": {"type": "plan_mode", "filePath": "/tmp/plan.md"},
            }
        )

        class Handler:
            def __init__(self):
                self.wfile = BytesIO()

        handler = Handler()
        events = [
            'event: message_start\ndata: {"type":"message_start","message":{"content":[]}}\n\n',
            'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n',
            'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Which part should I implement first?"}}\n\n',
            'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
            'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":1}}\n\n',
            'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]

        lines = []
        for event in events:
            lines.extend(f"{line}\n".encode("utf-8") for line in event.splitlines())
        ciel_runtime._rebatch_anthropic_sse_text(
            handler,
            lines,
            "claude-opus-4-8",
            word_chunking=False,
            source_body=body,
            provider="anthropic",
        )
        output = handler.wfile.getvalue().decode("utf-8")

        self.assertNotIn("toolu_anthropic_choice_", output)
        self.assertNotIn('"name": "TaskList"', output)
        self.assertIn('"stop_reason": "end_turn"', output)

    def test_non_anthropic_stream_converts_bash_xml_pseudo_tool_text(self):
        body = body_with_tools("search memory", ["Bash", "Read"])

        class Handler:
            def __init__(self):
                self.wfile = BytesIO()

        handler = Handler()
        events = [
            'event: message_start\ndata: {"type":"message_start","message":{"content":[]}}\n\n',
            'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n',
            (
                'event: content_block_delta\ndata: '
                + json.dumps(
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {
                            "type": "text_delta",
                            "text": '1차 탐색\n\n<bash>\ngrep -rin "bb" /home/robert-any/.claude/projects/-home-robert-any/memory/ 2>/dev/null\n</bash>',
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n\n"
            ),
            'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
            'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":1}}\n\n',
            'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]
        lines = []
        for event in events:
            lines.extend(f"{line}\n".encode("utf-8") for line in event.splitlines())

        ciel_runtime._rebatch_anthropic_sse_text(
            handler,
            lines,
            "deepseek-v4-flash",
            word_chunking=False,
            source_body=body,
            preserve_thinking=False,
            provider="deepseek",
            normalize_tool_use=True,
        )

        output = handler.wfile.getvalue().decode("utf-8")
        self.assertIn("1차 탐색", output)
        self.assertNotIn("<bash>", output)
        self.assertIn('"name": "Bash"', output)
        self.assertIn("grep -rin", output)
        self.assertIn('"stop_reason": "tool_use"', output)

    def test_native_stream_hidden_only_response_synthesizes_tasklist(self):
        body = body_with_tools("continue implementation", ["TaskList", "Read", "Edit"])
        body["messages"].append(
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "I will continue the implementation."}],
            }
        )

        class Handler:
            def __init__(self):
                self.wfile = BytesIO()

        handler = Handler()
        events = [
            'event: message_start\ndata: {"type":"message_start","message":{"content":[]}}\n\n',
            'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}\n\n',
            'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"private reasoning"}}\n\n',
            'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"signature_delta","signature":"sig"}}\n\n',
            'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
            'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":1}}\n\n',
            'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]

        lines = []
        for event in events:
            lines.extend(f"{line}\n".encode("utf-8") for line in event.splitlines())
        ciel_runtime._rebatch_anthropic_sse_text(
            handler,
            lines,
            "deepseek-v4-flash",
            word_chunking=False,
            source_body=body,
            preserve_thinking=False,
            provider="deepseek",
            normalize_tool_use=True,
        )
        output = handler.wfile.getvalue().decode("utf-8")

        self.assertNotIn("private reasoning", output)
        self.assertNotIn("thinking_delta", output)
        self.assertIn('"name": "TaskList"', output)
        self.assertIn('"stop_reason": "tool_use"', output)

    def test_native_stream_dropped_tool_use_still_recovers_hidden_only_response(self):
        body = body_with_tools("continue implementation", ["TaskList", "EnterPlanMode", "Read"])
        body["messages"].append(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_enter_plan",
                        "name": "EnterPlanMode",
                        "input": {},
                    }
                ],
            }
        )
        body["messages"].append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_enter_plan",
                        "content": "entered plan mode",
                    }
                ],
            }
        )

        class Handler:
            def __init__(self):
                self.wfile = BytesIO()

        handler = Handler()
        events = [
            'event: message_start\ndata: {"type":"message_start","message":{"content":[]}}\n\n',
            'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}\n\n',
            'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"private reasoning"}}\n\n',
            'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
            'event: content_block_start\ndata: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"toolu_repeat_plan","name":"EnterPlanMode","input":{}}}\n\n',
            'event: content_block_delta\ndata: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{}"}}\n\n',
            'event: content_block_stop\ndata: {"type":"content_block_stop","index":1}\n\n',
            'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"tool_use","stop_sequence":null},"usage":{"output_tokens":1}}\n\n',
            'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]

        lines = []
        for event in events:
            lines.extend(f"{line}\n".encode("utf-8") for line in event.splitlines())
        ciel_runtime._rebatch_anthropic_sse_text(
            handler,
            lines,
            "deepseek-v4-flash",
            word_chunking=False,
            source_body=body,
            preserve_thinking=False,
            provider="deepseek",
            normalize_tool_use=True,
        )
        output = handler.wfile.getvalue().decode("utf-8")

        self.assertNotIn("private reasoning", output)
        self.assertNotIn("toolu_repeat_plan", output)
        self.assertIn('"name": "TaskList"', output)
        self.assertIn('"stop_reason": "tool_use"', output)

    def test_native_stream_hidden_only_after_tool_result_synthesizes_tasklist(self):
        body = body_with_tools("continue implementation", ["TaskList", "Read", "Bash"])
        body["messages"].append(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_health",
                        "name": "Bash",
                        "input": {"command": "check health"},
                    }
                ],
            }
        )
        body["messages"].append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_health",
                        "content": "healthy",
                    }
                ],
            }
        )

        class Handler:
            def __init__(self):
                self.wfile = BytesIO()

        handler = Handler()
        events = [
            'event: message_start\ndata: {"type":"message_start","message":{"content":[]}}\n\n',
            'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}\n\n',
            'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"private reasoning"}}\n\n',
            'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
            'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":1}}\n\n',
            'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]

        lines = []
        for event in events:
            lines.extend(f"{line}\n".encode("utf-8") for line in event.splitlines())
        ciel_runtime._rebatch_anthropic_sse_text(
            handler,
            lines,
            "deepseek-v4-flash",
            word_chunking=False,
            source_body=body,
            preserve_thinking=False,
            provider="deepseek",
            normalize_tool_use=True,
        )
        output = handler.wfile.getvalue().decode("utf-8")

        self.assertNotIn("private reasoning", output)
        self.assertIn('"name": "TaskList"', output)
        self.assertIn('"stop_reason": "tool_use"', output)

    def test_native_stream_hidden_only_without_message_delta_synthesizes_tasklist(self):
        body = body_with_tools("continue implementation", ["TaskList", "Read", "Bash"])
        body["messages"].append(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_health",
                        "name": "Bash",
                        "input": {"command": "check health"},
                    }
                ],
            }
        )
        body["messages"].append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_health",
                        "content": "healthy",
                    }
                ],
            }
        )

        class Handler:
            def __init__(self):
                self.wfile = BytesIO()

        handler = Handler()
        events = [
            'event: message_start\ndata: {"type":"message_start","message":{"content":[]}}\n\n',
            'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}\n\n',
            'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"private reasoning"}}\n\n',
            'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
            'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]

        lines = []
        for event in events:
            lines.extend(f"{line}\n".encode("utf-8") for line in event.splitlines())
        ciel_runtime._rebatch_anthropic_sse_text(
            handler,
            lines,
            "deepseek-v4-flash",
            word_chunking=False,
            source_body=body,
            preserve_thinking=False,
            provider="deepseek",
            normalize_tool_use=True,
        )
        output = handler.wfile.getvalue().decode("utf-8")

        self.assertNotIn("private reasoning", output)
        self.assertNotIn("thinking_delta", output)
        self.assertIn('"name": "TaskList"', output)
        self.assertIn('"stop_reason": "tool_use"', output)

    def test_native_stream_empty_end_turn_after_tool_result_synthesizes_tasklist(self):
        body = body_with_tools("continue implementation", ["TaskList", "Read", "Bash"])
        body["messages"].append(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_health",
                        "name": "Bash",
                        "input": {"command": "check health"},
                    }
                ],
            }
        )
        body["messages"].append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_health",
                        "content": "healthy",
                    }
                ],
            }
        )

        class Handler:
            def __init__(self):
                self.wfile = BytesIO()

        handler = Handler()
        events = [
            'event: message_start\ndata: {"type":"message_start","message":{"content":[]}}\n\n',
            'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":1}}\n\n',
            'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]

        lines = []
        for event in events:
            lines.extend(f"{line}\n".encode("utf-8") for line in event.splitlines())
        ciel_runtime._rebatch_anthropic_sse_text(
            handler,
            lines,
            "deepseek-v4-flash",
            word_chunking=False,
            source_body=body,
            preserve_thinking=False,
            provider="deepseek",
            normalize_tool_use=True,
        )
        output = handler.wfile.getvalue().decode("utf-8")

        self.assertIn('"name": "TaskList"', output)
        self.assertIn('"stop_reason": "tool_use"', output)

    def test_native_stream_resume_after_tool_result_with_prose_synthesizes_tasklist(self):
        body = body_with_tools("continue implementation", ["TaskList", "Read", "Bash"])
        body["messages"].append(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_status",
                        "name": "Bash",
                        "input": {"command": "check current status"},
                    }
                ],
            }
        )
        body["messages"].append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_status",
                        "content": "healthy\nno rows returned",
                    }
                ],
            }
        )
        body["messages"].append(
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "No response requested."}],
            }
        )
        body["messages"].append({"role": "user", "content": [{"type": "text", "text": "continue"}]})

        class Handler:
            def __init__(self):
                self.wfile = BytesIO()

        handler = Handler()
        long_prose = (
            "The status check completed and the system is healthy, but the "
            "queried records are still empty. I should continue by inspecting "
            "the relevant migration and service code before deciding the next "
            "implementation step."
        )
        events = [
            'event: message_start\ndata: {"type":"message_start","message":{"content":[]}}\n\n',
            'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n',
            f'event: content_block_delta\ndata: {{"type":"content_block_delta","index":0,"delta":{{"type":"text_delta","text":{json.dumps(long_prose)}}}}}\n\n',
            'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
            'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":80}}\n\n',
            'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]

        lines = []
        for event in events:
            lines.extend(f"{line}\n".encode("utf-8") for line in event.splitlines())
        ciel_runtime._rebatch_anthropic_sse_text(
            handler,
            lines,
            "deepseek-v4-flash",
            word_chunking=False,
            source_body=body,
            preserve_thinking=False,
            provider="deepseek",
            normalize_tool_use=True,
        )
        output = handler.wfile.getvalue().decode("utf-8")

        self.assertIn("The status check completed", output)
        self.assertIn('"name": "TaskList"', output)
        self.assertIn('"stop_reason": "tool_use"', output)

    def test_native_stream_resume_after_tool_result_with_prose_without_message_delta_synthesizes_tasklist(self):
        body = body_with_tools("continue implementation", ["TaskList", "Read", "Bash"])
        body["messages"].append(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_status",
                        "name": "Bash",
                        "input": {"command": "check current status"},
                    }
                ],
            }
        )
        body["messages"].append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_status",
                        "content": "healthy\nno rows returned",
                    }
                ],
            }
        )
        body["messages"].append({"role": "user", "content": [{"type": "text", "text": "continue"}]})

        class Handler:
            def __init__(self):
                self.wfile = BytesIO()

        handler = Handler()
        long_prose = (
            "The status check completed and the queried records are still empty. "
            "I should continue by reading the relevant files before deciding the "
            "next implementation step."
        )
        events = [
            'event: message_start\ndata: {"type":"message_start","message":{"content":[]}}\n\n',
            'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n',
            f'event: content_block_delta\ndata: {{"type":"content_block_delta","index":0,"delta":{{"type":"text_delta","text":{json.dumps(long_prose)}}}}}\n\n',
            'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
            'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]

        lines = []
        for event in events:
            lines.extend(f"{line}\n".encode("utf-8") for line in event.splitlines())
        ciel_runtime._rebatch_anthropic_sse_text(
            handler,
            lines,
            "deepseek-v4-flash",
            word_chunking=False,
            source_body=body,
            preserve_thinking=False,
            provider="deepseek",
            normalize_tool_use=True,
        )
        output = handler.wfile.getvalue().decode("utf-8")

        self.assertIn("The status check completed", output)
        self.assertIn('"name": "TaskList"', output)
        self.assertIn('"stop_reason": "tool_use"', output)

    def test_native_stream_fresh_resume_ignores_prior_synthetic_tasklist_limit(self):
        body = body_with_tools("continue implementation", ["TaskList", "Read", "Bash"])
        for index in range(3):
            tool_id = f"toolu_anthropic_choice_prior_{index}"
            body["messages"].append(
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": tool_id,
                            "name": "TaskList",
                            "input": {},
                        }
                    ],
                }
            )
            body["messages"].append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": "#1 [in progress] Existing task\n#2 [completed] Previous task",
                        }
                    ],
                }
            )
        body["messages"].append({"role": "assistant", "content": [{"type": "text", "text": "No response requested."}]})
        body["messages"].append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "<system-reminder>\n"
                            "The task tools haven't been used recently.\n"
                            "</system-reminder>\n"
                        ),
                    },
                    {"type": "text", "text": "계속"},
                ],
            }
        )

        class Handler:
            def __init__(self):
                self.wfile = BytesIO()

        handler = Handler()
        long_prose = (
            "I have the current status and should continue by checking the next "
            "implementation step before reporting completion."
        )
        events = [
            'event: message_start\ndata: {"type":"message_start","message":{"content":[]}}\n\n',
            'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n',
            f'event: content_block_delta\ndata: {{"type":"content_block_delta","index":0,"delta":{{"type":"text_delta","text":{json.dumps(long_prose)}}}}}\n\n',
            'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
            'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]

        lines = []
        for event in events:
            lines.extend(f"{line}\n".encode("utf-8") for line in event.splitlines())
        ciel_runtime._rebatch_anthropic_sse_text(
            handler,
            lines,
            "deepseek-v4-flash",
            word_chunking=False,
            source_body=body,
            preserve_thinking=False,
            provider="deepseek",
            normalize_tool_use=True,
        )
        output = handler.wfile.getvalue().decode("utf-8")

        self.assertIn("I have the current status", output)
        self.assertIn('"name": "TaskList"', output)
        self.assertIn('"stop_reason": "tool_use"', output)

    def test_native_stream_completed_tasklist_question_does_not_loop_tasklist(self):
        body = body_with_tools("continue implementation", ["TaskList", "Read", "Bash"])
        body["messages"].append(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_anthropic_choice_prior",
                        "name": "TaskList",
                        "input": {},
                    }
                ],
            }
        )
        body["messages"].append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_anthropic_choice_prior",
                        "content": (
                            "#9 [completed] Create DB tables for schema versioning\n"
                            "#10 [completed] LLM dynamic schema detection + schema service"
                        ),
                    }
                ],
            }
        )
        body["messages"].append({"role": "user", "content": [{"type": "text", "text": "계속"}]})

        class Handler:
            def __init__(self):
                self.wfile = BytesIO()

        handler = Handler()
        question = "The known tasks are complete. Should I verify the deployment or summarize the result?"
        events = [
            'event: message_start\ndata: {"type":"message_start","message":{"content":[]}}\n\n',
            'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n',
            f'event: content_block_delta\ndata: {{"type":"content_block_delta","index":0,"delta":{{"type":"text_delta","text":{json.dumps(question)}}}}}\n\n',
            'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
            'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":25}}\n\n',
            'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]

        lines = []
        for event in events:
            lines.extend(f"{line}\n".encode("utf-8") for line in event.splitlines())
        ciel_runtime._rebatch_anthropic_sse_text(
            handler,
            lines,
            "deepseek-v4-flash",
            word_chunking=False,
            source_body=body,
            preserve_thinking=False,
            provider="deepseek",
            normalize_tool_use=True,
        )
        output = handler.wfile.getvalue().decode("utf-8")

        self.assertIn("The known tasks are complete", output)
        self.assertNotIn('"name": "TaskList"', output)
        self.assertIn('"stop_reason": "end_turn"', output)

    def test_native_stream_suppressed_thinking_keeps_remapped_text_block_stop(self):
        body = body_with_tools("continue implementation", ["TaskList", "Read", "Bash"])
        body["messages"].append(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_anthropic_choice_prior",
                        "name": "TaskList",
                        "input": {},
                    }
                ],
            }
        )
        body["messages"].append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_anthropic_choice_prior",
                        "content": "#9 [completed] Create DB tables",
                    }
                ],
            }
        )
        body["messages"].append({"role": "user", "content": [{"type": "text", "text": "계속"}]})

        class Handler:
            def __init__(self):
                self.wfile = BytesIO()

        handler = Handler()
        text = "The known tasks are complete. I should summarize the result."
        events = [
            'event: message_start\ndata: {"type":"message_start","message":{"content":[]}}\n\n',
            'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}\n\n',
            'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"private reasoning"}}\n\n',
            'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
            'event: content_block_start\ndata: {"type":"content_block_start","index":1,"content_block":{"type":"text","text":""}}\n\n',
            f'event: content_block_delta\ndata: {{"type":"content_block_delta","index":1,"delta":{{"type":"text_delta","text":{json.dumps(text)}}}}}\n\n',
            'event: content_block_stop\ndata: {"type":"content_block_stop","index":1}\n\n',
            'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":25}}\n\n',
            'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]

        lines = []
        for event in events:
            lines.extend(f"{line}\n".encode("utf-8") for line in event.splitlines())
        ciel_runtime._rebatch_anthropic_sse_text(
            handler,
            lines,
            "deepseek-v4-flash",
            word_chunking=False,
            source_body=body,
            preserve_thinking=False,
            provider="deepseek",
            normalize_tool_use=True,
        )
        output = handler.wfile.getvalue().decode("utf-8")

        self.assertNotIn("private reasoning", output)
        self.assertIn('"index": 0, "content_block": {"type": "text"', output)
        self.assertIn('"index": 0, "delta": {"type": "text_delta"', output)
        self.assertIn('"type": "content_block_stop", "index": 0', output)
        self.assertIn('"stop_reason": "end_turn"', output)

    def test_native_stream_suppressed_thinking_emits_keepalive(self):
        body = body_with_tools("continue implementation", ["Read", "Bash"])

        class Handler:
            def __init__(self):
                self.wfile = BytesIO()

        handler = Handler()
        events = [
            'event: message_start\ndata: {"type":"message_start","message":{"content":[]}}\n\n',
            'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}\n\n',
            'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"private reasoning"}}\n\n',
            'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
            'event: content_block_start\ndata: {"type":"content_block_start","index":1,"content_block":{"type":"text","text":""}}\n\n',
            'event: content_block_delta\ndata: {"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"done"}}\n\n',
            'event: content_block_stop\ndata: {"type":"content_block_stop","index":1}\n\n',
            'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":1}}\n\n',
            'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]

        lines = []
        for event in events:
            lines.extend(f"{line}\n".encode("utf-8") for line in event.splitlines())
        ciel_runtime._rebatch_anthropic_sse_text(
            handler,
            lines,
            "deepseek-v4-flash",
            word_chunking=False,
            source_body=body,
            preserve_thinking=False,
            provider="deepseek",
            normalize_tool_use=True,
        )
        output = handler.wfile.getvalue().decode("utf-8")

        self.assertIn(": suppressed-thinking", output)
        self.assertNotIn("private reasoning", output)
        self.assertIn('"text": "done"', output)
        self.assertIn('"stop_reason": "end_turn"', output)

    def test_native_stream_client_disconnect_is_not_forward_error(self):
        body = body_with_tools("continue implementation", ["Read", "Bash"])

        class FailingWFile:
            def write(self, data):
                raise ConnectionResetError("client closed")

            def flush(self):
                pass

        class Handler:
            def __init__(self):
                self.wfile = FailingWFile()

        events = [
            'event: message_start\ndata: {"type":"message_start","message":{"content":[]}}\n\n',
            'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n',
            'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hello"}}\n\n',
        ]
        lines = []
        for event in events:
            lines.extend(f"{line}\n".encode("utf-8") for line in event.splitlines())

        with mock.patch.object(ciel_runtime, "router_log") as router_log:
            ciel_runtime._rebatch_anthropic_sse_text(
                Handler(),
                lines,
                "deepseek-v4-flash",
                word_chunking=False,
                source_body=body,
                preserve_thinking=False,
                provider="deepseek",
                normalize_tool_use=True,
            )

        log_messages = [str(call.args[1]) for call in router_log.call_args_list if len(call.args) > 1]
        self.assertTrue(any("anthropic_sse_client_disconnected" in item for item in log_messages))
        self.assertFalse(any("anthropic_sse_forward_error" in item for item in log_messages))

    def test_native_stream_suggestion_mode_does_not_synthesize_tasklist(self):
        body = body_with_tools("continue implementation", ["TaskList", "Read", "Bash"])
        body["messages"].append(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_status",
                        "name": "Bash",
                        "input": {"command": "check current status"},
                    }
                ],
            }
        )
        body["messages"].append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_status",
                        "content": "healthy",
                    }
                ],
            }
        )
        body["messages"].append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "[SUGGESTION MODE: Suggest what the user might naturally type next into Claude Code.]",
                    }
                ],
            }
        )

        class Handler:
            def __init__(self):
                self.wfile = BytesIO()

        handler = Handler()
        events = [
            'event: message_start\ndata: {"type":"message_start","message":{"content":[]}}\n\n',
            'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}\n\n',
            'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"private reasoning"}}\n\n',
            'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
            'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]

        lines = []
        for event in events:
            lines.extend(f"{line}\n".encode("utf-8") for line in event.splitlines())
        ciel_runtime._rebatch_anthropic_sse_text(
            handler,
            lines,
            "deepseek-v4-flash",
            word_chunking=False,
            source_body=body,
            preserve_thinking=False,
            provider="deepseek",
            normalize_tool_use=True,
        )
        output = handler.wfile.getvalue().decode("utf-8")

        self.assertNotIn("private reasoning", output)
        self.assertNotIn('"name": "TaskList"', output)
        self.assertIn("empty end_turn", output)
        self.assertIn('"stop_reason": "end_turn"', output)

    def test_native_stream_empty_max_tokens_after_resume_synthesizes_tasklist(self):
        body = body_with_tools("continue implementation", ["TaskList", "Read", "Bash"])
        body["messages"].append(
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "No response requested."}],
            }
        )

        class Handler:
            def __init__(self):
                self.wfile = BytesIO()

        handler = Handler()
        events = [
            'event: message_start\ndata: {"type":"message_start","message":{"content":[]}}\n\n',
            'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}\n\n',
            'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"private reasoning"}}\n\n',
            'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
            'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"max_tokens","stop_sequence":null},"usage":{"output_tokens":1}}\n\n',
            'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]

        lines = []
        for event in events:
            lines.extend(f"{line}\n".encode("utf-8") for line in event.splitlines())
        ciel_runtime._rebatch_anthropic_sse_text(
            handler,
            lines,
            "deepseek-v4-flash",
            word_chunking=False,
            source_body=body,
            preserve_thinking=False,
            provider="deepseek",
            normalize_tool_use=True,
        )
        output = handler.wfile.getvalue().decode("utf-8")

        self.assertNotIn("private reasoning", output)
        self.assertIn('"name": "TaskList"', output)
        self.assertIn('"stop_reason": "tool_use"', output)

    def test_native_stream_hidden_only_response_without_tasklist_shows_notice(self):
        body = body_with_tools("continue implementation", ["Read", "Edit"])

        class Handler:
            def __init__(self):
                self.wfile = BytesIO()

        handler = Handler()
        events = [
            'event: message_start\ndata: {"type":"message_start","message":{"content":[]}}\n\n',
            'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}\n\n',
            'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"private reasoning"}}\n\n',
            'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
            'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":1}}\n\n',
            'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]

        lines = []
        for event in events:
            lines.extend(f"{line}\n".encode("utf-8") for line in event.splitlines())
        ciel_runtime._rebatch_anthropic_sse_text(
            handler,
            lines,
            "deepseek-v4-flash",
            word_chunking=False,
            source_body=body,
            preserve_thinking=False,
            provider="deepseek",
        )
        output = handler.wfile.getvalue().decode("utf-8")

        self.assertIn("empty end_turn", output)
        self.assertIn('"stop_reason": "end_turn"', output)


if __name__ == "__main__":
    unittest.main()
