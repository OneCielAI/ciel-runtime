import asyncio
import unittest
import json
import re
from dataclasses import replace
from unittest import mock

from ciel_runtime_support.upstream_error_policy import UpstreamFailure

from ciel_runtime_support.context_compaction import (
    ContextCompactionFailed,
    ContextCompactionProjection,
    ContextCompactionServices,
    ContextCompactionTransport,
    ContextCompactionWorkflow,
    build_llm_compacted_messages,
    request_context_summary,
)
from ciel_runtime_support.context_summary_policy import (
    CODEX_CONTEXT_CHECKPOINT_PROMPT,
    ContextSummaryPolicy,
    is_codex_context_checkpoint_prompt,
    semantic_payload_cost,
)
from ciel_runtime_support.prompt_compaction import (
    PromptCompactionText, PromptCompactionRuntime, PromptCompactionServices,
    compact_chat_messages_for_budget, compact_anthropic_body_for_budget,
)
from ciel_runtime_support.ollama_wire_projection import (
    OllamaWireProjection,
    OllamaWireProjectionPorts,
)


class ContextCompactionTests(unittest.TestCase):
    def services(self, *, available=True, split_messages=None):
        post_json = mock.Mock(return_value={})
        transport = ContextCompactionTransport(
            summary_output_tokens=lambda _config, _budget: 512,
            request_timeout=lambda _config: 30.0,
            endpoint=lambda _provider, _config, operation: f"https://test/{operation}",
            post_json=post_json,
            headers=lambda _provider, _config: {"Authorization": "test"},
            extract_text=lambda _data, _wire: "summary",
        )
        workflow = ContextCompactionWorkflow(
            segmented_mode=lambda config, instruction: (
                ("explicit" if bool(config["context_compact_llm"]) else None)
                if "context_compact_llm" in config
                else (
                    "auto_codex"
                    if is_codex_context_checkpoint_prompt(instruction)
                    else None
                )
            ),
            compaction_available=lambda _provider, _config: available,
            instruction_index=lambda messages: next(
                (
                    index
                    for index in range(len(messages) - 1, -1, -1)
                    if is_codex_context_checkpoint_prompt(
                        str(messages[index].get("content") or "")
                    )
                ),
                0,
            ),
            content_to_text=lambda value: str(value),
            chunk_target_tokens=lambda _config, _budget: 512,
            split_messages=(
                split_messages
                if split_messages is not None
                else (lambda messages, _target: [(0, messages)])
            ),
            parallel_sessions=lambda _config, _chunks: 1,
            write_activity=mock.Mock(),
            estimate_tokens=lambda value: len(str(value)),
            request_summary=mock.Mock(return_value="summary"),
        )
        ollama_projection = OllamaWireProjection(
            OllamaWireProjectionPorts(
                think_value=lambda *_args: None,
                positive_int=lambda value: int(value) if value else None,
            )
        )
        return ContextCompactionServices(
            transport=transport,
            workflow=workflow,
            projection=ContextCompactionProjection(
                build_chunk_prompt=lambda *_args: "chunk",
                build_fallback_summary=lambda *_args, **_kwargs: "fallback",
                build_reduce_prompt=lambda summaries, instruction, **_kwargs: f"{instruction}:{summaries[0]}",
                log=mock.Mock(),
                apply_ollama_optional=ollama_projection.apply,
            ),
            map_system_prompt="compact",
        )

    def test_openai_summary_uses_protocol_endpoint_and_shape(self):
        services = self.services()
        summary = request_context_summary(
            "provider", "model", {}, "prompt", services, wire="openai", budget_tokens=8192
        )
        self.assertEqual("summary", summary)
        url, request = services.transport.post_json.call_args.args[:2]
        self.assertEqual("https://test/openai_chat", url)
        self.assertEqual(512, request["max_tokens"])

    def test_provider_capability_can_disable_compaction(self):
        services = self.services(available=False)
        result = build_llm_compacted_messages(
            "provider",
            "model",
            {},
            [{"role": "user", "content": "compact"}],
            8192,
            services,
            wire="openai",
        )
        self.assertIsNone(result)
        services.workflow.request_summary.assert_not_called()

    def test_unavailable_provider_does_not_complete_checkpoint(self):
        services = self.services(available=False)
        with self.assertRaisesRegex(ContextCompactionFailed, "provider_unavailable"):
            self.compact(services)
        services.workflow.request_summary.assert_not_called()
        self.assertEqual("failed", services.workflow.write_activity.call_args.kwargs["phase"])

    def compact(self, services, config=None, history=None):
        return build_llm_compacted_messages(
            "provider", "model", config or {},
            [*(history or [{"role": "assistant", "content": "history"}]),
             {"role": "user", "content": CODEX_CONTEXT_CHECKPOINT_PROMPT}],
            8192, services, wire="openai",
        )

    def test_segmented_llm_compaction_is_disabled_by_default(self):
        services = self.services()
        result = build_llm_compacted_messages(
            "provider",
            "model",
            {},
            [
                {"role": "user", "content": "compact"},
                {"role": "assistant", "content": "history"},
            ],
            8192,
            services,
            wire="openai",
        )
        self.assertIsNone(result)
        services.workflow.request_summary.assert_not_called()

    def test_segmented_llm_compaction_remains_explicit_opt_in(self):
        services = self.services()
        result = build_llm_compacted_messages(
            "provider",
            "model",
            {"context_compact_llm": True},
            [
                {"role": "user", "content": "compact"},
                {"role": "assistant", "content": "history"},
            ],
            8192,
            services,
            wire="openai",
        )
        self.assertEqual([{"role": "user", "content": "compact:summary"}], result)
        services.workflow.request_summary.assert_called_once()

    def test_codex_checkpoint_returns_final_reducer_not_local_success(self):
        services = self.services()
        result = self.compact(services)
        self.assertIn("summary", result[-1]["content"])
        self.assertIn(CODEX_CONTEXT_CHECKPOINT_PROMPT, result[-1]["content"])
        services.workflow.request_summary.assert_called_once()
        self.assertEqual("reduce", services.workflow.write_activity.call_args.kwargs["phase"])

    def test_automatic_compaction_disables_gateway_retries(self):
        services = self.services()
        config = {"gateway_retries": 10}
        self.compact(services, config)
        self.assertEqual(0, services.workflow.request_summary.call_args.args[2]["gateway_retries"])
        self.assertEqual(10, config["gateway_retries"])

    def test_failures_empty_timeout_and_cancel_never_complete_or_retry(self):
        for failure in (RuntimeError("high demand"), TimeoutError("ambiguous"), asyncio.CancelledError(), KeyboardInterrupt(), ""):
            with self.subTest(failure=repr(failure)):
                services = self.services()
                if isinstance(failure, BaseException):
                    services.workflow.request_summary.side_effect = failure
                    expected = type(failure)
                else:
                    services.workflow.request_summary.return_value = failure
                    expected = ContextCompactionFailed
                with self.assertRaises(expected):
                    self.compact(services)
                services.workflow.request_summary.assert_called_once()
                self.assertEqual("failed", services.workflow.write_activity.call_args.kwargs["phase"])

    def test_explicit_false_disables_codex_segmented_compaction(self):
        services = self.services()
        result = build_llm_compacted_messages(
            "provider",
            "model",
            {"context_compact_llm": False},
            [
                {"role": "user", "content": "history"},
                {"role": "user", "content": CODEX_CONTEXT_CHECKPOINT_PROMPT},
            ],
            8192,
            services,
            wire="openai",
        )

        self.assertIsNone(result)
        services.workflow.request_summary.assert_not_called()

    def test_more_than_eight_map_chunks_are_covered(self):
        services = self.services(split_messages=lambda messages, _target: [(i, [m]) for i, m in enumerate(messages)])
        self.compact(services, history=[{"role": "assistant", "content": f"history {i}"} for i in range(12)])
        self.assertEqual(12, services.workflow.request_summary.call_count)

    def test_chunk_limit_fails_before_any_generation(self):
        services = self.services(split_messages=lambda messages, _target: [(i, messages) for i in range(257)])
        with self.assertRaisesRegex(ContextCompactionFailed, "map_chunk_limit"):
            self.compact(services)
        services.workflow.request_summary.assert_not_called()

    def real_services(self):
        services = self.services()
        policy = ContextSummaryPolicy(
            estimate_tokens=lambda value: max(1, len(json.dumps(value, ensure_ascii=False)) // 4),
            positive_int=lambda value: int(value) if value else None,
            content_to_text=lambda value: value if isinstance(value, str) else " ".join(block.get("text", "") for block in value or []),
            compact_json=lambda value, **_kwargs: json.dumps(value, ensure_ascii=False),
            latest_user_text=lambda body: str(body["messages"][-1]["content"]),
        )
        workflow = replace(
            services.workflow, instruction_index=policy.instruction_index,
            segmented_mode=lambda config, text: policy.segmented_compaction_mode(config, text, lambda value, **_kwargs: bool(value)),
            split_messages=policy.split_messages, chunk_target_tokens=policy.chunk_target_tokens,
            estimate_tokens=policy.estimate_tokens,
        )
        projection = replace(
            services.projection, build_chunk_prompt=policy.chunk_prompt,
            build_reduce_prompt=policy.reduce_prompt, build_fallback_summary=mock.Mock(),
        )
        return replace(services, workflow=workflow, projection=projection)

    def test_claude_native_checkpoint_uses_semantic_map_by_default(self):
        services = self.real_services()
        result = build_llm_compacted_messages(
            "provider", "model", {}, [{"role": "assistant", "content": "prior history"},
            {"role": "user", "content": "<command-name>/compact</command-name>"}],
            8192, services, wire="openai",
        )
        services.workflow.request_summary.assert_called_once()
        self.assertIn("[ciel-runtime segmented compact]", result[-1]["content"])
        self.assertNotIn("local context checkpoint", result[-1]["content"])

    def test_real_semantic_map_covers_huge_tool_block_and_late_facts(self):
        services = self.real_services()
        message = {"role": "assistant", "content": [
            {"type": "text", "text": "first fact"},
            {"type": "tool_use", "id": "call-4", "name": "Write", "input": {"content": "한글" * 10000 + "FINAL_FACT"}},
            {"type": "tool_result", "tool_use_id": "call-4", "content": "RESULT_FACT"},
        ]}
        self.compact(services, history=[message])
        prompts = [call.args[3] for call in services.workflow.request_summary.call_args_list]
        self.assertGreater(len(prompts), 1)
        self.assertTrue(any("FINAL_FACT" in prompt for prompt in prompts))
        self.assertTrue(any("RESULT_FACT" in prompt for prompt in prompts))
        self.assertTrue(all("truncated" not in prompt for prompt in prompts))
        services.projection.build_fallback_summary.assert_not_called()

    def test_hierarchical_reduce_covers_every_map_summary_in_order(self):
        services = self.real_services()
        services = replace(services, workflow=replace(services.workflow,
            split_messages=lambda messages, _target: [(i, [message]) for i, message in enumerate(messages)]))
        def generate(_provider, _model, _config, prompt, **_kwargs):
            if prompt.startswith("Segment "):
                number = int(prompt.split()[1].split("/")[0].rstrip("."))
                return f"fact-{number}:" + "x" * 2200
            return "reduced:" + ",".join(f"fact-{i}:" for i in range(1, 19) if f"fact-{i}:" in prompt)
        services.workflow.request_summary.side_effect = generate
        result = self.compact(services, history=[{"role": "assistant", "content": f"history {i}"} for i in range(18)])
        calls = services.workflow.request_summary.call_args_list
        self.assertGreater(len(calls), 18)
        reducers = [call.args[3] for call in calls[18:]]
        for i in range(1, 19):
            self.assertTrue(any(f"fact-{i}:" in prompt for prompt in reducers))
            self.assertIn(f"fact-{i}:", result[-1]["content"])
        self.assertLessEqual(semantic_payload_cost({"messages": result}, services.workflow.estimate_tokens) + 512, 8192)

    def test_reducer_failure_after_full_map_never_becomes_completed(self):
        services = self.real_services()
        services = replace(services, workflow=replace(services.workflow,
            split_messages=lambda messages, _target: [(i, [message]) for i, message in enumerate(messages)]))
        services.workflow.request_summary.side_effect = ["x" * 4000] * 10 + [TimeoutError("reduce")]
        with self.assertRaises(TimeoutError):
            self.compact(services, history=[{"role": "assistant", "content": "history"}] * 10)
        self.assertEqual(11, services.workflow.request_summary.call_count)
        self.assertEqual("failed", services.workflow.write_activity.call_args.kwargs["phase"])

    def test_deadline_expires_before_generation(self):
        services = self.real_services()
        with mock.patch("ciel_runtime_support.context_compaction.COMPACTION_TIMEOUT_SECONDS", -1):
            with self.assertRaisesRegex(ContextCompactionFailed, "deadline_exceeded"):
                self.compact(services)
        services.workflow.request_summary.assert_not_called()

    def test_full_map_request_checks_protocol_system_and_output_budget(self):
        services = self.real_services()
        services = replace(services, map_system_prompt="system" * 6000)
        with self.assertRaisesRegex(ContextCompactionFailed, "insufficient_summary_budget"):
            self.compact(services)
        services.workflow.request_summary.assert_not_called()

    def test_transport_preflights_unicode_and_clamps_output(self):
        services = self.real_services()
        transport = replace(services.transport, summary_output_tokens=lambda _config, _budget: 999999)
        services = replace(services, transport=transport)
        request_context_summary("provider", "model", {}, "prompt", services, wire="openai", budget_tokens=8192)
        self.assertEqual(1024, services.transport.post_json.call_args.args[1]["max_tokens"])
        services.transport.post_json.reset_mock()
        with self.assertRaisesRegex(ContextCompactionFailed, "summary_request_exceeds_budget"):
            request_context_summary("provider", "model", {}, "한글" * 16000, services, wire="openai", budget_tokens=8192)
        services.transport.post_json.assert_not_called()

    def test_summary_transport_resolves_internal_alias_before_sending(self):
        services = self.real_services()
        resolve = mock.Mock(return_value="provider/upstream")
        services = replace(services, transport=replace(services.transport, resolve_model=resolve))
        request_context_summary("provider", "ciel-runtime-provider-alias", {}, "prompt", services,
                                wire="openai", budget_tokens=8192)
        request = services.transport.post_json.call_args.args[1]
        self.assertEqual("provider/upstream", request["model"])
        self.assertEqual("provider/upstream", services.transport.post_json.call_args.args[6])

    def test_cancelled_summary_run_stops_before_billable_call(self):
        services = self.real_services()
        with self.assertRaisesRegex(ContextCompactionFailed, "client_cancelled"):
            self.compact(services, {"_context_compact_cancelled": lambda: True})
        services.workflow.request_summary.assert_not_called()

    def test_transport_rejects_partial_generation_and_empty_output(self):
        for data in ({"choices": [{"finish_reason": "length"}]}, {"stop_reason": "max_tokens"}, {"status": "incomplete"}, {"error": {"message": "failed"}}):
            services = self.real_services()
            services.transport.post_json.return_value = data
            with self.assertRaises((ContextCompactionFailed, UpstreamFailure)):
                request_context_summary("provider", "model", {}, "source", services, wire="openai", budget_tokens=8192)
            services.transport.post_json.assert_called_once()
        services = self.real_services()
        services = replace(services, transport=replace(services.transport, extract_text=lambda *_args: ""))
        with self.assertRaisesRegex(ContextCompactionFailed, "empty_summary"):
            request_context_summary("provider", "model", {}, "source", services, wire="openai", budget_tokens=8192)

    def test_only_proven_no_output_context_rejection_splits_without_replay(self):
        services = self.real_services()
        rejected = UpstreamFailure("provider", "model", status=400,
            error_type="context_length_exceeded", message="too much context", source="http_status")
        services.workflow.request_summary.side_effect = [rejected, "left summary", "right summary"]
        self.compact(services, history=[{"role": "assistant", "content": "left " * 80},
                                        {"role": "assistant", "content": "right " * 80}])
        calls = services.workflow.request_summary.call_args_list
        self.assertEqual(3, len(calls))
        self.assertNotEqual(calls[0].args[3], calls[1].args[3])
        self.assertNotEqual(calls[0].args[3], calls[2].args[3])
        self.assertEqual(2, services.workflow.write_activity.call_args.kwargs["completed_chunks"])

    def test_ambiguous_rejections_and_partial_output_are_not_split_or_retried(self):
        for status, source, output_started in ((502, "http_status", False), (400, "sse", False), (400, "http_status", True)):
            services = self.real_services()
            services.workflow.request_summary.side_effect = UpstreamFailure("provider", "model",
                status=status, error_type="context_length_exceeded", message="context",
                source=source, output_started=output_started)
            with self.assertRaises(UpstreamFailure):
                self.compact(services)
            services.workflow.request_summary.assert_called_once()

    def prompt_services(self, result):
        return PromptCompactionServices(
            text=PromptCompactionText(
                lambda value: str(value), lambda value: value, mock.Mock(return_value="guard"),
                lambda system, texts: system, lambda value, limit: value[:limit], lambda *_args: 1,
            ),
            runtime=PromptCompactionRuntime(
                lambda value: len(json.dumps(value)) // 4,
                mock.Mock(return_value=result), mock.Mock(), mock.Mock(),
            ),
        )

    def test_small_native_requests_make_no_auxiliary_generation(self):
        services = self.prompt_services([])
        messages = [{"role": "user", "content": CODEX_CONTEXT_CHECKPOINT_PROMPT}]
        result = compact_chat_messages_for_budget(messages, [], 8192, provider="provider", model="model",
            pcfg={"_context_compact_context_rejected": True}, full_compact_request=True, services=services)
        self.assertIs(messages, result)
        services.runtime.llm_compact_messages.assert_not_called()

    def test_oversized_semantic_reducer_never_falls_through_to_guard(self):
        for wire in ("openai", "anthropic"):
            services = self.prompt_services([{"role": "user", "content": "bad reducer " * 10000}])
            messages = [{"role": "user", "content": "old history " * 10000}]
            with self.assertRaisesRegex(ContextCompactionFailed, "final_reduce_exceeds_budget"):
                if wire == "openai":
                    compact_chat_messages_for_budget(messages, [], 8192, provider="provider", model="model",
                        pcfg={"_context_compact_context_rejected": True}, full_compact_request=True, services=services)
                else:
                    compact_anthropic_body_for_budget({"messages": messages}, 8192, provider="provider", model="model",
                        pcfg={"_context_compact_context_rejected": True}, full_compact_request=True, services=services)
            services.text.build_summary.assert_not_called()

    def test_anthropic_system_envelope_is_reserved_before_semantic_work(self):
        services = self.prompt_services([{"role": "user", "content": "final reducer"}])
        body = {"system": "system constraints " * 150, "messages": [{"role": "user", "content": "history " * 10000}], "max_tokens": 512}
        compact_anthropic_body_for_budget(body, 8192, provider="provider", model="model", pcfg={"_context_compact_context_rejected": True},
            full_compact_request=True, services=services)
        supplied = services.runtime.llm_compact_messages.call_args.args[4]
        self.assertLess(supplied, 8192 - len(body["system"]) // 4)
        self.assertGreater(supplied, 1024)

    def test_one_oversized_map_summary_is_split_completely_for_reduce(self):
        services = self.real_services()
        services = replace(services, transport=replace(services.transport, summary_output_tokens=lambda *_: 8192))
        original = "BEGIN " + "word " * 6200 + " END"
        observed = []
        def generate(_provider, _model, _config, prompt, **_kwargs):
            if prompt.startswith("Segment "):
                return original
            observed.append(prompt)
            return "short reduced summary"
        services.workflow.request_summary.side_effect = generate
        result = self.compact(services)
        self.assertGreater(len(observed), 1)
        self.assertTrue(any("BEGIN " in prompt for prompt in observed))
        self.assertTrue(any(" END" in prompt for prompt in observed))
        segments = [text for prompt in observed for text in re.findall(
            r"## Segment [0-9]+" + chr(10) + r"(.*?)(?=" + chr(10) * 2 + r"(?:## Segment|Client compact instruction:))",
            prompt, re.S,
        )]
        self.assertEqual(original, "".join(segments))
        self.assertIn("short reduced summary", result[-1]["content"])

    def test_appended_claude_auto_instruction_keeps_the_original_user_text(self):
        from ciel_runtime_support.context_summary_policy import CLAUDE_COMPACT_TEXT_PREFIX, CLAUDE_COMPACT_TASK, CLAUDE_COMPACT_END
        services = self.real_services()
        instruction = (CLAUDE_COMPACT_TEXT_PREFIX + chr(10) + CLAUDE_COMPACT_TASK + chr(10) +
            "Your summary should include the following sections:" + chr(10) + CLAUDE_COMPACT_END)
        messages = [
            {"role": "assistant", "content": "earlier history"},
            {"role": "user", "content": [{"type": "text", "text": "LAST_USER_FACT"}, {"type": "text", "text": instruction}]},
        ]
        result = build_llm_compacted_messages("provider", "model", {}, messages, 32768, services, wire="openai")
        self.assertIn(instruction, result[-1]["content"])
        prompts = [call.args[3] for call in services.workflow.request_summary.call_args_list]
        self.assertTrue(any("LAST_USER_FACT" in prompt for prompt in prompts))

    def test_ollama_summary_omits_unproven_output_option(self):
        services = self.services()
        request_context_summary(
            "local",
            "model",
            {"keep_alive": 300},
            "prompt",
            services,
            wire="ollama",
            budget_tokens=8192,
        )
        url, request = services.transport.post_json.call_args.args[:2]
        self.assertEqual("https://test/ollama_chat", url)
        self.assertNotIn("options", request)
        self.assertEqual("300", request["keep_alive"])

    def test_anthropic_summary_uses_provider_messages_endpoint(self):
        services = self.services()
        request_context_summary(
            "remote", "model", {}, "prompt", services, wire="anthropic", budget_tokens=8192
        )
        url, request = services.transport.post_json.call_args.args[:2]
        self.assertEqual("https://test/anthropic_messages", url)
        self.assertEqual("compact", request["system"])
        self.assertFalse(
            services.transport.post_json.call_args.kwargs["retry_rate_limits"]
        )


if __name__ == "__main__":
    unittest.main()
