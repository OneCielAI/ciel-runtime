import threading
import unittest

from ciel_runtime_support.tool_side_effect_dedupe import (
    ToolSideEffectDedupePolicy,
    ToolSideEffectDedupePorts,
    ToolSideEffectDedupeRepository,
    ToolSideEffectDedupeService,
)


class ToolSideEffectDedupeServiceTest(unittest.TestCase):
    def service(self):
        recent = {}
        audits = []
        logs = []
        clock = iter((10.0, 11.0, 700.0))
        service = ToolSideEffectDedupeService(
            ToolSideEffectDedupePolicy(frozenset({"send_message"}), ttl_seconds=600),
            ToolSideEffectDedupeRepository(recent, threading.Lock()),
            ToolSideEffectDedupePorts(
                now=lambda: next(clock),
                audit=lambda event, payload: audits.append((event, payload)),
                log=lambda level, message: logs.append((level, message)),
            ),
        )
        return service, recent, audits, logs

    def test_exact_repeat_is_dropped_and_audited(self):
        service, recent, audits, logs = self.service()
        tool_input = {"message": "hello", "room": "general"}

        self.assertFalse(service.should_drop("mcp__chat__send_message", tool_input))
        self.assertTrue(service.should_drop("mcp__chat__send_message", tool_input))

        self.assertEqual(1, len(recent))
        self.assertEqual("dropped_duplicate_side_effect_tool_call", audits[0][0])
        self.assertTrue(any("dropped duplicate" in message for _level, message in logs))

    def test_hash_is_stable_across_mapping_order(self):
        service, _recent, _audits, _logs = self.service()
        self.assertEqual(
            service.key("send_message", {"a": 1, "b": 2}),
            service.key("send_message", {"b": 2, "a": 1}),
        )

    def test_read_only_tool_is_not_deduplicated(self):
        service, recent, _audits, _logs = self.service()
        self.assertFalse(service.should_drop("mcp__chat__get_messages", {}))
        self.assertEqual({}, recent)

    def test_completed_execution_repeat_is_dropped_from_request_history(self):
        audits = []
        service = ToolSideEffectDedupeService(
            ToolSideEffectDedupePolicy(
                frozenset(),
                repeated_execution_suffixes=frozenset({"shell_command"}),
                completed_repeat_limit=1,
            ),
            ToolSideEffectDedupeRepository({}, threading.Lock()),
            ToolSideEffectDedupePorts(
                now=lambda: 10.0,
                audit=lambda event, payload: audits.append((event, payload)),
                log=lambda _level, _message: None,
            ),
        )
        body = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call_1",
                            "name": "shell_command",
                            "input": {"command": "cargo check", "timeout_ms": 120000},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_1",
                            "content": "Process exited with code 0",
                        }
                    ],
                },
            ]
        }

        self.assertTrue(
            service.should_drop(
                "shell_command",
                {"command": "cargo check", "timeout_ms": 120000.0},
                source_body=body,
            )
        )
        self.assertEqual("dropped_repeated_completed_tool_call", audits[0][0])

    def test_new_user_intent_allows_same_execution_again(self):
        service = ToolSideEffectDedupeService(
            ToolSideEffectDedupePolicy(
                frozenset(), repeated_execution_suffixes=frozenset({"shell_command"})
            ),
            ToolSideEffectDedupeRepository({}, threading.Lock()),
            ToolSideEffectDedupePorts(
                now=lambda: 10.0,
                audit=lambda _event, _payload: None,
                log=lambda _level, _message: None,
            ),
        )
        body = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call_1",
                            "name": "shell_command",
                            "input": {"command": "cargo check"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "call_1", "content": "ok"}
                    ],
                },
                {"role": "user", "content": [{"type": "text", "text": "run it again"}]},
            ]
        }

        self.assertFalse(
            service.should_drop(
                "shell_command", {"command": "cargo check"}, source_body=body
            )
        )


if __name__ == "__main__":
    unittest.main()
