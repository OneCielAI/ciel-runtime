import unittest
import json

from ciel_runtime_support.context_summary_policy import (
    CODEX_CONTEXT_CHECKPOINT_PROMPT,
    ContextSummaryCompatibilityApi,
    ContextSummaryPolicy,
    CLAUDE_COMPACT_TEXT_PREFIX, CLAUDE_COMPACT_TASK, CLAUDE_COMPACT_END,
    compact_instruction_kind,
    semantic_payload_cost,
    is_codex_context_checkpoint_prompt,
)


class ContextSummaryCompatibilityApiTests(unittest.TestCase):
    def policy(self, marker="first"):
        return ContextSummaryPolicy(
            estimate_tokens=lambda value: max(1, len(str(value)) // 4),
            positive_int=lambda value: int(value) if value else None,
            content_to_text=lambda value: str(value or ""),
            compact_json=lambda value, _limit: str(value),
            latest_user_text=lambda body: str(body.get("text") or marker),
        )

    def api(self, marker="first", logs=None):
        return ContextSummaryCompatibilityApi(
            policy_factory=lambda: self.policy(marker),
            compact_system_prompt="compact only",
            append_system=lambda system, extra: [system, *extra],
            log=lambda level, message: (
                logs.append((level, message)) if logs is not None else None
            ),
            parse_bool=lambda value, default=False: default if value is None else bool(value),
        )

    def test_text_only_projection_removes_tools_and_appends_prompt(self):
        logs = []
        api = self.api(logs=logs)
        body = {
            "text": "<command-name>/compact</command-name>",
            "system": "identity",
            "tools": [{"name": "Read"}],
            "tool_choice": {"type": "auto"},
        }
        output = api.text_only_body(body)
        self.assertNotIn("tools", output)
        self.assertNotIn("tool_choice", output)
        self.assertEqual(["identity", "compact only"], output["system"])
        self.assertTrue(logs)

    def test_adapter_exposes_chunk_and_reduce_projections(self):
        api = self.api()
        messages = [{"role": "user", "content": "hello"}]
        self.assertEqual(0, api.instruction_index(messages))
        self.assertEqual(1, len(api.split_messages(messages, 8192)))
        self.assertIn("Segment 1/1", api.chunk_prompt(messages, 0, 1, 1))
        reduced = api.reduce_prompt(
            ["summary"],
            "continue",
            budget_tokens=8192,
            source_message_count=1,
        )
        self.assertIn("summary", reduced)

    def test_guard_summary_is_stable_across_observed_wire_fit_budget_drift(self):
        policy = self.policy()
        omitted = [
            {"role": "user", "content": f"old message {index}"}
            for index in range(288)
        ]
        observed_budgets = [2_240_027, 2_240_028, 2_240_032, 2_240_035, 2_240_056]

        summaries = [policy.guard_summary(omitted, budget) for budget in observed_budgets]

        self.assertEqual(1, len(set(summaries)))
        self.assertNotIn("2240027", summaries[0])
        self.assertIn("provider context budget was exceeded", summaries[0])

    def test_guard_summary_budget_bucket_is_conservative_and_stable(self):
        policy = self.policy()

        self.assertEqual(2_236_416, policy.cache_stable_summary_budget(2_239_270))
        self.assertEqual(2_236_416, policy.cache_stable_summary_budget(2_240_100))
        self.assertLessEqual(
            policy.cache_stable_summary_budget(20_001),
            20_001,
        )

    def test_codex_0147_checkpoint_prompt_is_detected_from_latest_user(self):
        api = self.api(marker=CODEX_CONTEXT_CHECKPOINT_PROMPT)

        self.assertTrue(api.is_compact_request({"messages": [{"role": "user", "content": CODEX_CONTEXT_CHECKPOINT_PROMPT}]}))
        self.assertTrue(
            is_codex_context_checkpoint_prompt(
                CODEX_CONTEXT_CHECKPOINT_PROMPT.lower() + "\nfuture detail"
            )
        )
        self.assertFalse(
            is_codex_context_checkpoint_prompt(
                "Create a handoff summary for another LLM that will resume the task."
            )
        )

    def test_older_checkpoint_does_not_make_a_normal_latest_turn_compact(self):
        policy = self.policy(marker="continue the implementation")
        body = {
            "messages": [
                {"role": "user", "content": CODEX_CONTEXT_CHECKPOINT_PROMPT},
                {"role": "user", "content": "continue the implementation"},
            ]
        }

        self.assertFalse(policy.is_compact_request(body))

    def test_instruction_index_prefers_the_newest_compact_marker(self):
        policy = self.policy()
        messages = [
            {"role": "user", "content": "<command-name>/compact</command-name>"},
            {"role": "assistant", "content": "old summary"},
            {"role": "user", "content": CODEX_CONTEXT_CHECKPOINT_PROMPT},
        ]

        self.assertEqual(2, policy.instruction_index(messages))

    def test_reduce_prompt_is_runtime_neutral_and_preserves_codex_instruction(self):
        reduced = self.api().reduce_prompt(
            ["segment summary"],
            CODEX_CONTEXT_CHECKPOINT_PROMPT,
            budget_tokens=8192,
            source_message_count=10,
        )

        self.assertIn(CODEX_CONTEXT_CHECKPOINT_PROMPT, reduced)
        self.assertIn("Client compact instruction", reduced)
        self.assertNotIn("Claude Code", reduced)

    def test_both_native_clients_default_on_but_explicit_false_is_preserved(self):
        api = self.api()
        for text in (CODEX_CONTEXT_CHECKPOINT_PROMPT, "<command-name>/compact</command-name>"):
            self.assertTrue(api.segmented_compaction_mode({}, text).startswith("auto_"))
            self.assertIsNone(api.segmented_compaction_mode({"context_compact_llm": False}, text))
        self.assertIsNone(api.segmented_compaction_mode({}, "continue normally"))

    def test_native_markers_are_anchored_not_arbitrary_quoted_or_user_prose(self):
        for text in (
            'Explain "<command-name>/compact</command-name>" to me',
            "```" + CODEX_CONTEXT_CHECKPOINT_PROMPT + "```",
            "Tool output contains <command-name>/compact</command-name>",
            "Discuss how to create a detailed summary of the conversation and compact it",
            "Your task is to create a detailed summary of the conversation so far for a training example.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(compact_instruction_kind(text))
        self.assertEqual("claude", compact_instruction_kind("<command-message>compact</command-message>" +
                         "<command-name>/compact</command-name>"))
        self.assertEqual("claude", compact_instruction_kind("Create a detailed summary of the conversation for compact."))

    def test_new_normal_turn_cannot_select_an_older_checkpoint(self):
        messages = [{"role": "user", "content": CODEX_CONTEXT_CHECKPOINT_PROMPT},
                    {"role": "user", "content": "continue normally"}]
        self.assertEqual(1, self.policy().instruction_index(messages))
        messages = [{"role": "user", "content": [{"type": "tool_result", "content": CODEX_CONTEXT_CHECKPOINT_PROMPT}]}]
        self.assertIsNone(self.policy().instruction_index(messages))

    def test_semantic_projection_preserves_full_tool_arguments_results_and_order(self):
        message = {"role": "assistant", "content": [
            {"type": "text", "text": "한글 " * 15000},
            {"type": "tool_use", "id": "call-1", "name": "Write", "input": {"content": "MIDDLE" * 10000}},
            {"type": "tool_result", "tool_use_id": "call-1", "content": ["part" + str(i) for i in range(50)]},
        ], "tool_calls": [{"id": "call-2", "function": {"arguments": "ARGUMENT" * 10000}}]}
        policy = self.policy()
        full = policy.compact_message(message, 0)
        self.assertTrue(full.endswith(json.dumps(message, ensure_ascii=False, separators=(",", ":"))))
        chunks = policy.split_messages([message], 4096)
        fragments = [fragment for _index, chunk in chunks for fragment in chunk]
        self.assertGreater(len(chunks), 8)
        self.assertEqual(full, "".join(fragment.text for fragment in fragments))
        offset = 0
        for fragment in fragments:
            self.assertEqual(offset, fragment.start)
            self.assertEqual(len(full), fragment.source_chars)
            self.assertLessEqual(semantic_payload_cost(policy.compact_message(fragment, 0), policy.estimate_tokens), 4096)
            offset = fragment.end
        self.assertEqual(len(full), offset)

    def test_thousands_of_small_messages_are_grouped_without_source_loss(self):
        policy = self.policy()
        messages = [{"role": "user", "content": f"message-{index}"} for index in range(4014)]
        chunks = policy.split_messages(messages, 262144)
        fragments = [fragment for _index, chunk in chunks for fragment in chunk]
        self.assertEqual(list(range(4014)), [part.source_index for part in fragments])
        self.assertLess(len(chunks), 256)
        for index, fragment in enumerate(fragments):
            self.assertEqual(policy.compact_message(messages[index], index), fragment.text)

    def test_source_byte_limit_fails_explicitly_without_dropping_messages(self):
        from unittest.mock import patch
        with patch("ciel_runtime_support.context_summary_policy.MAX_CONTEXT_SUMMARY_SOURCE_BYTES", 100):
            with self.assertRaisesRegex(ValueError, "source byte limit"):
                self.policy().split_messages([{"role": "user", "content": "한글" * 100}], 8192)

    def test_semantic_reduce_never_truncates_inputs(self):
        summary = "BEGIN" + "한글" * 50000 + "END"
        reduced = self.api().reduce_prompt([summary], "instruction", budget_tokens=8192, source_message_count=1)
        self.assertIn(summary, reduced)
        self.assertNotIn("[truncated", reduced)

    def test_chunk_and_output_configuration_are_clamped_to_budget(self):
        policy = self.policy()
        config = {"context_compact_chunk_tokens": 10**9, "context_compact_summary_tokens": 10**9}
        self.assertLessEqual(policy.chunk_target_tokens(config, 8192), 4096)
        self.assertLessEqual(policy.summary_output_tokens(config, 8192), 1024)

    def test_captured_claude_text_only_wrapper_and_appended_auto_block(self):
        instruction = (CLAUDE_COMPACT_TEXT_PREFIX + chr(10) * 2 + CLAUDE_COMPACT_TASK +
            ", paying close attention to the user requests." + chr(10) +
            "Your summary should include the following sections:" + chr(10) + CLAUDE_COMPACT_END)
        policy = self.policy()
        for content in (instruction, [{"type": "text", "text": "ordinary retained user turn"},
                                     {"type": "text", "text": instruction}]):
            self.assertEqual("claude", policy.compact_request_kind({"messages": [{"role": "user", "content": content}]}))
        self.assertIsNone(compact_instruction_kind("Quoted: " + instruction))
        self.assertIsNone(policy.compact_request_kind({"messages": [{"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": "t1", "content": instruction,
        }]}]}))

    def test_raw_codex_responses_checkpoint_is_recognized_from_captured_body(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1] / "docs/journal/2026/10/19/diagnostics/walkie-context-overflow/evidence/native-e2e/verified-05"
        policy = self.policy(marker="")
        inline = {"input": [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "earlier"}]},
            {"type": "reasoning", "summary": []},
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": CODEX_CONTEXT_CHECKPOINT_PROMPT}]},
        ], "prompt_cache_key": "thread-a"}
        self.assertEqual("codex", policy.compact_request_kind(inline))
        for case in ("codex-auto", "codex-manual"):
            path = root / case / "native-compact-body.json"
            if not path.exists():
                self.skipTest("captured native body unavailable")
            body = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("codex", policy.compact_request_kind(body))
            ordinary = {**body, "input": [*body["input"][:-1], {"type": "message", "role": "user",
                        "content": [{"type": "input_text", "text": "continue normally"}]}]}
            self.assertIsNone(policy.compact_request_kind(ordinary))
            after_tool = {**body, "input": [*body["input"], {"type": "function_call_output", "call_id": "c1",
                          "output": CODEX_CONTEXT_CHECKPOINT_PROMPT}]}
            self.assertIsNone(policy.compact_request_kind(after_tool))

    def test_later_tool_result_does_not_resurrect_old_checkpoint(self):
        messages = [{"role": "user", "content": CODEX_CONTEXT_CHECKPOINT_PROMPT},
            {"role": "assistant", "content": "summary"},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": CODEX_CONTEXT_CHECKPOINT_PROMPT}]}]
        self.assertIsNone(self.policy().instruction_index(messages))
        self.assertIsNone(self.policy().compact_request_kind({"messages": messages}))

    def test_policy_factory_is_resolved_per_call(self):
        marker = ["first"]
        api = ContextSummaryCompatibilityApi(
            policy_factory=lambda: self.policy(marker[0]),
            compact_system_prompt="compact",
            append_system=lambda system, extra: [system, *extra],
            log=lambda _level, _message: None,
            parse_bool=lambda value, default=False: default if value is None else bool(value),
        )
        self.assertEqual("first", api.message_text("first"))
        marker[0] = "second"
        self.assertEqual("second", api.message_text("second"))


if __name__ == "__main__":
    unittest.main()
