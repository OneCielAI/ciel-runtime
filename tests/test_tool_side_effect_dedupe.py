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

    def test_first_completed_execution_can_be_retried_after_interruption(self):
        audits = []
        service = ToolSideEffectDedupeService(
            ToolSideEffectDedupePolicy(
                frozenset(),
                repeated_execution_suffixes=frozenset({"shell_command"}),
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

        self.assertFalse(
            service.should_drop(
                "shell_command",
                {"command": "cargo check", "timeout_ms": 120000.0},
                source_body=body,
            )
        )
        self.assertEqual([], audits)

    def test_third_consecutive_execution_is_dropped_as_a_loop(self):
        audits = []
        service = ToolSideEffectDedupeService(
            ToolSideEffectDedupePolicy(
                frozenset(), repeated_execution_suffixes=frozenset({"shell_command"})
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
                    "content": [{"type": "tool_use", "id": "call_1", "name": "shell_command", "input": {"command": "cargo check"}}],
                },
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "ok"}]},
                {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "call_2", "name": "shell_command", "input": {"command": "cargo check"}}],
                },
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_2", "content": "ok"}]},
            ]
        }

        self.assertTrue(
            service.should_drop("shell_command", {"command": "cargo check"}, source_body=body)
        )
        self.assertEqual("dropped_repeated_completed_tool_call", audits[0][0])

    def test_default_policy_drops_third_consecutive_exec_command(self):
        audits = []
        service = ToolSideEffectDedupeService(
            ToolSideEffectDedupePolicy(frozenset()),
            ToolSideEffectDedupeRepository({}, threading.Lock()),
            ToolSideEffectDedupePorts(
                now=lambda: 10.0,
                audit=lambda event, payload: audits.append((event, payload)),
                log=lambda _level, _message: None,
            ),
        )
        tool_input = {
            "cmd": "wsl -d UbuntuV -- curl -s http://127.0.0.1:18080/health",
            "yield_time_ms": 30000,
        }
        body = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call_1",
                            "name": "exec_command",
                            "input": tool_input,
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
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call_2",
                            "name": "exec_command",
                            "input": tool_input,
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_2",
                            "content": "Process exited with code 0",
                        }
                    ],
                },
            ]
        }

        self.assertTrue(service.should_drop("exec_command", tool_input, source_body=body))
        self.assertEqual("dropped_repeated_completed_tool_call", audits[0][0])

    def test_empty_apply_patch_is_never_counted_as_completed(self):
        service = ToolSideEffectDedupeService(
            ToolSideEffectDedupePolicy(
                frozenset(), repeated_execution_suffixes=frozenset({"apply_patch"})
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
                    "content": [{"type": "tool_use", "id": "call_1", "name": "apply_patch", "input": {}}],
                },
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "ok"}],
                },
                {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "call_2", "name": "apply_patch", "input": {}}],
                },
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call_2", "content": "ok"}],
                },
            ]
        }

        self.assertFalse(service.should_drop("apply_patch", {}, source_body=body))

    def test_failed_result_without_is_error_is_not_counted_as_completed(self):
        service = ToolSideEffectDedupeService(
            ToolSideEffectDedupePolicy(
                frozenset(), repeated_execution_suffixes=frozenset({"apply_patch"})
            ),
            ToolSideEffectDedupeRepository({}, threading.Lock()),
            ToolSideEffectDedupePorts(
                now=lambda: 10.0,
                audit=lambda _event, _payload: None,
                log=lambda _level, _message: None,
            ),
        )
        patch_input = {"patch": "*** Begin Patch\n*** End Patch"}
        body = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "call_1", "name": "apply_patch", "input": patch_input}],
                },
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "apply_patch failed: invalid patch"}],
                },
                {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "call_2", "name": "apply_patch", "input": patch_input}],
                },
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call_2", "content": "tool call aborted"}],
                },
            ]
        }

        self.assertFalse(service.should_drop("apply_patch", patch_input, source_body=body))

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

    def test_runtime_control_nudge_does_not_reset_completed_repeat_history(self):
        audits = []
        service = ToolSideEffectDedupeService(
            ToolSideEffectDedupePolicy(frozenset()),
            ToolSideEffectDedupeRepository({}, threading.Lock()),
            ToolSideEffectDedupePorts(
                now=lambda: 10.0,
                audit=lambda event, payload: audits.append((event, payload)),
                log=lambda _level, _message: None,
            ),
        )
        tool_input = {"cmd": "curl.exe -i http://service.invalid/mcp"}
        body = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call_1",
                            "name": "exec_command",
                            "input": tool_input,
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_1",
                            "content": "HTTP/1.1 400 Bad Request",
                        }
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call_2",
                            "name": "exec_command",
                            "input": tool_input,
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_2",
                            "content": "HTTP/1.1 400 Bad Request",
                        }
                    ],
                },
                {
                    "role": "user",
                    "ciel_runtime_control": "repeated_tool_call_recovery",
                    "content": [{"type": "text", "text": "Choose a different action."}],
                },
            ]
        }

        self.assertTrue(service.should_drop("exec_command", tool_input, source_body=body))
        self.assertEqual("dropped_repeated_completed_tool_call", audits[0][0])


if __name__ == "__main__":
    unittest.main()
