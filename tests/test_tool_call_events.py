import unittest
import tempfile
from pathlib import Path
from unittest import mock

from ciel_runtime_support.router_observability_context import (
    RouterObservabilityContext,
    SseObservabilityPorts,
    SseTraceConfiguration,
)
from ciel_runtime_support.tool_call_events import project_transcript_tool_calls


class ToolCallEventProjectionTests(unittest.TestCase):
    def test_projects_codex_function_call(self):
        calls = project_transcript_tool_calls(
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "shell_command",
                    "arguments": '{"cmd":"rg TODO"}',
                },
            },
            "codex",
        )

        self.assertEqual(1, len(calls))
        self.assertEqual("call-1", calls[0]["call_id"])
        self.assertEqual("shell_command", calls[0]["name"])
        self.assertEqual({"cmd": "rg TODO"}, calls[0]["arguments"])
        self.assertEqual("codex", calls[0]["runtime"])

    def test_projects_claude_tool_use_blocks(self):
        calls = project_transcript_tool_calls(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "model": "claude-fable-5-1",
                    "content": [
                        {"type": "text", "text": "checking"},
                        {
                            "type": "tool_use",
                            "id": "toolu-1",
                            "name": "Read",
                            "input": {"file_path": "README.md"},
                        },
                    ],
                },
            },
            "claude",
        )

        self.assertEqual(1, len(calls))
        self.assertEqual("toolu-1", calls[0]["call_id"])
        self.assertEqual("Read", calls[0]["name"])
        self.assertEqual("claude-fable-5-1", calls[0]["model"])

    def test_projects_muse_runtime_session_tool_calls(self):
        calls = project_transcript_tool_calls(
            {
                "record_type": "event",
                "payload_type": "runtime.session",
                "payload": {
                    "kind": "runtime.session",
                    "event": {
                        "kind": "assistant.tool_calls",
                        "tool_calls": [
                            {
                                "id": "tool-1",
                                "call_id": "call-muse-1",
                                "name": "shell",
                                "args": '{"command":"pwd"}',
                            }
                        ],
                    },
                },
            },
            "muse",
        )

        self.assertEqual(1, len(calls))
        self.assertEqual("call-muse-1", calls[0]["call_id"])
        self.assertEqual("shell", calls[0]["name"])
        self.assertEqual({"command": "pwd"}, calls[0]["arguments"])
        self.assertEqual("muse_tool_call", calls[0]["call_type"])

    def test_router_stream_tool_call_is_persisted_and_published(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events = []
            context = RouterObservabilityContext(
                preview=mock.Mock(load_config=lambda: {}),
                request_config=mock.Mock(),
                request=mock.Mock(),
                sse_config=SseTraceConfiguration(
                    root,
                    root / "last.json",
                    root / "trace.jsonl",
                    root / "tool-calls.jsonl",
                    100,
                    1000,
                    100000,
                    10,
                ),
                sse=SseObservabilityPorts(
                    {},
                    lambda: 30,
                    lambda value, _limit: value,
                    lambda *_args: None,
                    lambda **event: events.append(event),
                ),
            )

            context.append_tool_call(
                "openai_stream_tool_call",
                {
                    "model": "gpt-test",
                    "tool_id": "call-router",
                    "matched_name": "exec_command",
                    "emitted_input": {"cmd": "pwd"},
                    "sse_index": 2,
                },
            )

            self.assertTrue((root / "tool-calls.jsonl").is_file())
            self.assertEqual("tool.call", events[0]["category"])
            self.assertEqual("call-router", events[0]["data"]["call_id"])
            self.assertEqual({"cmd": "pwd"}, events[0]["data"]["arguments"])


if __name__ == "__main__":
    unittest.main()
