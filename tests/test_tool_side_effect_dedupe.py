import threading
import unittest

from ciel_runtime_support.tool_side_effect_dedupe import (
    ToolSideEffectDedupePolicy,
    ToolSideEffectDedupePorts,
    ToolSideEffectDedupeRepository,
    ToolSideEffectDedupeService,
)


def _service(policy, audits=None, now=10.0):
    return ToolSideEffectDedupeService(
        policy,
        ToolSideEffectDedupeRepository({}, threading.Lock()),
        ToolSideEffectDedupePorts(
            now=lambda: now,
            audit=lambda event, payload: (audits.append((event, payload)) if audits is not None else None),
            log=lambda _level, _message: None,
        ),
    )


def _history(tool_name, tool_input, results):
    """Build an assistant tool_use / user tool_result transcript, one pair per result."""
    messages = []
    for index, result in enumerate(results, 1):
        call_id = f"call_{index}"
        messages.append(
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": call_id, "name": tool_name, "input": tool_input}],
            }
        )
        block = {"type": "tool_result", "tool_use_id": call_id, "content": result}
        messages.append({"role": "user", "content": [block]})
    return {"messages": messages}


# Observed result texts.  Claude Code 2.1.316 (claude.exe strings) and the
# installed Codex binary (codex.exe strings) were inspected for these formats.
CODEX_EXEC_SUCCESS = (
    "Chunk ID: 7f2a\nWall time: 0.2 seconds\nProcess exited with code 0\n"
    "Original token count: 5\nOutput:\n{\"status\":\"ok\"}"
)
CODEX_EXEC_RUNNING = (
    "Chunk ID: 7f2b\nWall time: 30.0 seconds\nProcess running with session ID 4242\n"
    "Original token count: 0\nOutput:\n"
)
CODEX_SHELL_SUCCESS = "Exit code: 0\nWall time: 1.4 seconds\nOutput:\nok"
CLAUDE_BASH_BACKGROUND = (
    "Command running in background with ID: b7. Output is being written to: C:/tmp/b7.output."
)
CLAUDE_WRITE_CREATED = "File created successfully at: C:/work/notes.txt"
CLAUDE_WRITE_UPDATED = "The file C:/work/notes.txt has been updated successfully."
CLAUDE_EDIT_UPDATED = "The file C:/work/app.py has been updated successfully."
CODEX_APPLY_PATCH_SUCCESS = "Success. Updated the following files:\nM src/app.py\n"


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

    def test_send_file_ttl_dedupe_is_preserved_and_expires(self):
        recent = {}
        audits = []
        clock = iter((10.0, 11.0, 700.0))
        service = ToolSideEffectDedupeService(
            ToolSideEffectDedupePolicy(frozenset({"send_message", "send_file"}), ttl_seconds=600),
            ToolSideEffectDedupeRepository(recent, threading.Lock()),
            ToolSideEffectDedupePorts(
                now=lambda: next(clock),
                audit=lambda event, payload: audits.append((event, payload)),
                log=lambda _level, _message: None,
            ),
        )
        tool_input = {"channel": "web", "path": "C:/out/report.txt"}

        self.assertFalse(service.should_drop("mcp__ciel-runtime-router__send_file", tool_input))
        self.assertTrue(service.should_drop("mcp__ciel-runtime-router__send_file", tool_input))
        self.assertFalse(service.should_drop("mcp__ciel-runtime-router__send_file", tool_input))

        self.assertEqual(["dropped_duplicate_side_effect_tool_call"], [event for event, _ in audits])
        self.assertEqual(1.0, audits[0][1]["age_seconds"])

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
        service = _service(
            ToolSideEffectDedupePolicy(
                frozenset(),
                repeated_execution_suffixes=frozenset({"shell_command"}),
            ),
            audits,
        )
        body = _history(
            "shell_command",
            {"command": "cargo check", "timeout_ms": 120000},
            ["Process exited with code 0"],
        )

        self.assertFalse(
            service.should_drop(
                "shell_command",
                {"command": "cargo check", "timeout_ms": 120000.0},
                source_body=body,
            )
        )
        self.assertEqual([], audits)

    def test_custom_execution_suffix_drops_third_explicit_zero_exit(self):
        audits = []
        service = _service(
            ToolSideEffectDedupePolicy(
                frozenset(), repeated_execution_suffixes=frozenset({"shell_command"})
            ),
            audits,
        )
        body = _history(
            "shell_command",
            {"command": "cargo check"},
            ["Process exited with code 0", CODEX_SHELL_SUCCESS],
        )

        self.assertTrue(
            service.should_drop("shell_command", {"command": "cargo check"}, source_body=body)
        )
        self.assertEqual("dropped_repeated_completed_tool_call", audits[0][0])
        self.assertEqual(2, audits[0][1]["completed_repeats"])

    def test_default_policy_allows_repeated_exec_command_health_poll(self):
        audits = []
        service = _service(ToolSideEffectDedupePolicy(frozenset()), audits)
        tool_input = {
            "cmd": "wsl -d UbuntuV -- curl -s http://127.0.0.1:18080/health",
            "yield_time_ms": 30000,
        }
        for prior_polls in (2, 3, 6):
            with self.subTest(prior_polls=prior_polls):
                body = _history("exec_command", tool_input, [CODEX_EXEC_SUCCESS] * prior_polls)
                self.assertFalse(service.should_drop("exec_command", tool_input, source_body=body))
        self.assertEqual([], audits)

    def test_default_policy_never_blocks_shell_tools_for_identical_results(self):
        audits = []
        service = _service(ToolSideEffectDedupePolicy(frozenset()), audits)
        cases = (
            ("Bash", {"command": "curl -s http://127.0.0.1:8080/health"}, "{\"status\":\"ok\"}"),
            ("Bash", {"command": "git status --short"}, "Exit code 0"),
            ("shell_command", {"command": "cargo check"}, CODEX_SHELL_SUCCESS),
            ("exec_command", {"cmd": "npm test"}, CODEX_EXEC_SUCCESS),
            ("exec", {"cmd": "ls"}, "Process exited with code 0"),
            ("execute", {"cmd": "ls"}, "Process exited with code 0"),
            ("run_command", {"cmd": "ls"}, "Process exited with code 0"),
            ("bash", {"command": "ls"}, "Process exited with code 0"),
            ("mcp__shell__exec_command", {"cmd": "ls"}, "Process exited with code 0"),
        )
        for tool_name, tool_input, result in cases:
            with self.subTest(tool=tool_name, result=result):
                body = _history(tool_name, tool_input, [result] * 3)
                self.assertFalse(service.should_drop(tool_name, tool_input, source_body=body))
        self.assertEqual([], audits)

    def test_custom_execution_suffix_does_not_count_pending_results(self):
        service = _service(
            ToolSideEffectDedupePolicy(
                frozenset(), repeated_execution_suffixes=frozenset({"exec_command", "bash"})
            )
        )
        cases = (
            ("exec_command", {"cmd": "npm run dev"}, CODEX_EXEC_RUNNING),
            ("exec_command", {"cmd": "npm run dev"}, "Process running with session ID 9"),
            ("Bash", {"command": "npm run dev", "run_in_background": True}, CLAUDE_BASH_BACKGROUND),
            ("exec_command", {"cmd": "job"}, "{\"status\": \"running\", \"exit_code\": 0}"),
            ("exec_command", {"cmd": "job"}, "status: pending"),
            ("exec_command", {"cmd": "job"}, "command timed out after 30000 milliseconds"),
        )
        for tool_name, tool_input, result in cases:
            with self.subTest(tool=tool_name, result=result):
                body = _history(tool_name, tool_input, [result] * 3)
                self.assertFalse(service.should_drop(tool_name, tool_input, source_body=body))

    def test_custom_execution_suffix_does_not_count_nonzero_exit_codes(self):
        service = _service(
            ToolSideEffectDedupePolicy(
                frozenset(), repeated_execution_suffixes=frozenset({"exec_command"})
            )
        )
        tool_input = {"cmd": "cargo test"}
        for result in (
            "Process exited with code 2",
            "Chunk ID: 1\nWall time: 3.0 seconds\nProcess exited with code 101\nOutput:\nerror",
            "Exit code: 127\nWall time: 0.1 seconds\nOutput:\nnot found",
            "Exit code 3\nboom",
            "Process exited with code -1",
            "{\"output\":\"x\",\"metadata\":{\"exit_code\":2}}",
            "exit status 255",
        ):
            with self.subTest(result=result):
                body = _history("exec_command", tool_input, [result] * 3)
                self.assertFalse(service.should_drop("exec_command", tool_input, source_body=body))

    def test_custom_execution_suffix_does_not_count_unknown_results(self):
        service = _service(
            ToolSideEffectDedupePolicy(
                frozenset(), repeated_execution_suffixes=frozenset({"shell_command"})
            )
        )
        tool_input = {"command": "curl -s http://127.0.0.1:8080/health"}
        for result in ("ok", "", "HTTP/1.1 200 OK", "{\"status\":\"ok\"}"):
            with self.subTest(result=result):
                body = _history("shell_command", tool_input, [result] * 3)
                self.assertFalse(service.should_drop("shell_command", tool_input, source_body=body))

    def test_pending_result_between_successes_breaks_the_streak(self):
        service = _service(
            ToolSideEffectDedupePolicy(
                frozenset(), repeated_execution_suffixes=frozenset({"exec_command"})
            )
        )
        tool_input = {"cmd": "npm test"}
        body = _history(
            "exec_command",
            tool_input,
            ["Process exited with code 0", CODEX_EXEC_RUNNING, "Process exited with code 0"],
        )

        self.assertFalse(service.should_drop("exec_command", tool_input, source_body=body))

    def test_default_policy_drops_third_identical_write_after_two_successes(self):
        audits = []
        service = _service(ToolSideEffectDedupePolicy(frozenset()), audits)
        tool_input = {"file_path": "C:/work/notes.txt", "content": "hello\n"}
        body = _history("Write", tool_input, [CLAUDE_WRITE_CREATED, CLAUDE_WRITE_UPDATED])

        self.assertTrue(service.should_drop("Write", tool_input, source_body=body))
        self.assertEqual("dropped_repeated_completed_tool_call", audits[0][0])

    def test_default_policy_drops_third_identical_edit_after_two_successes(self):
        audits = []
        service = _service(ToolSideEffectDedupePolicy(frozenset()), audits)
        tool_input = {"file_path": "C:/work/app.py", "old_string": "a", "new_string": "b"}
        body = _history(
            "Edit",
            tool_input,
            [
                CLAUDE_EDIT_UPDATED,
                "The file C:/work/app.py has been updated successfully. "
                "All occurrences were successfully replaced.",
            ],
        )

        self.assertTrue(service.should_drop("Edit", tool_input, source_body=body))
        self.assertEqual("dropped_repeated_completed_tool_call", audits[0][0])

    def test_default_policy_drops_third_identical_apply_patch_after_two_successes(self):
        audits = []
        service = _service(ToolSideEffectDedupePolicy(frozenset()), audits)
        tool_input = {"input": "*** Begin Patch\n*** Update File: src/app.py\n*** End Patch"}
        body = _history(
            "apply_patch",
            tool_input,
            [
                CODEX_APPLY_PATCH_SUCCESS,
                "Exit code: 0\nWall time: 0 seconds\nOutput:\n" + CODEX_APPLY_PATCH_SUCCESS,
            ],
        )

        self.assertTrue(service.should_drop("apply_patch", tool_input, source_body=body))
        self.assertEqual("dropped_repeated_completed_tool_call", audits[0][0])

    def test_mutation_without_positive_completion_evidence_is_not_counted(self):
        service = _service(ToolSideEffectDedupePolicy(frozenset()))
        write_input = {"file_path": "C:/work/notes.txt", "content": "hello\n"}
        edit_input = {"file_path": "C:/work/app.py", "old_string": "a", "new_string": "b"}
        patch_input = {"input": "*** Begin Patch\n*** End Patch"}
        cases = (
            ("Write", write_input, "ok"),
            ("Edit", edit_input, ""),
            (
                "Write",
                write_input,
                "File has been modified since read, either by the user or by a linter. "
                "Read it again before attempting to write it.",
            ),
            ("apply_patch", patch_input, "{\"output\":\"x\",\"metadata\":{\"exit_code\":2}}"),
            ("apply_patch", patch_input, "Exit code: 2\nOutput:\nerror: patch did not apply"),
        )
        for tool_name, tool_input, result in cases:
            with self.subTest(tool=tool_name, result=result):
                body = _history(tool_name, tool_input, [result] * 3)
                self.assertFalse(service.should_drop(tool_name, tool_input, source_body=body))

    def test_empty_apply_patch_is_never_counted_as_completed(self):
        service = _service(
            ToolSideEffectDedupePolicy(
                frozenset(), repeated_execution_suffixes=frozenset({"apply_patch"})
            )
        )
        body = _history("apply_patch", {}, ["ok", "ok"])

        self.assertFalse(service.should_drop("apply_patch", {}, source_body=body))

    def test_failed_result_without_is_error_is_not_counted_as_completed(self):
        service = _service(
            ToolSideEffectDedupePolicy(
                frozenset(), repeated_execution_suffixes=frozenset({"apply_patch"})
            )
        )
        patch_input = {"patch": "*** Begin Patch\n*** End Patch"}
        body = _history(
            "apply_patch",
            patch_input,
            ["apply_patch failed: invalid patch", "tool call aborted"],
        )

        self.assertFalse(service.should_drop("apply_patch", patch_input, source_body=body))

    def test_new_user_intent_allows_same_execution_again(self):
        service = _service(
            ToolSideEffectDedupePolicy(
                frozenset(), repeated_execution_suffixes=frozenset({"shell_command"})
            )
        )
        body = _history(
            "shell_command",
            {"command": "cargo check"},
            ["Process exited with code 0", "Process exited with code 0"],
        )
        body["messages"].append({"role": "user", "content": [{"type": "text", "text": "run it again"}]})

        self.assertFalse(
            service.should_drop(
                "shell_command", {"command": "cargo check"}, source_body=body
            )
        )

    def test_runtime_control_nudge_does_not_reset_completed_repeat_history(self):
        audits = []
        service = _service(ToolSideEffectDedupePolicy(frozenset()), audits)
        tool_input = {"file_path": "C:/work/app.py", "old_string": "a", "new_string": "b"}
        body = _history("Edit", tool_input, [CLAUDE_EDIT_UPDATED, CLAUDE_EDIT_UPDATED])
        body["messages"].append(
            {
                "role": "user",
                "ciel_runtime_control": "repeated_tool_call_recovery",
                "content": [{"type": "text", "text": "Choose a different action."}],
            }
        )

        self.assertTrue(service.should_drop("Edit", tool_input, source_body=body))
        self.assertEqual("dropped_repeated_completed_tool_call", audits[0][0])


if __name__ == "__main__":
    unittest.main()
