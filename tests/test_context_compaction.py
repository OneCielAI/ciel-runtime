import unittest
from unittest import mock

from ciel_runtime_support.context_compaction import (
    AutomaticContextCompactionCompleted,
    ContextCompactionProjection,
    ContextCompactionServices,
    ContextCompactionTransport,
    ContextCompactionWorkflow,
    build_llm_compacted_messages,
    request_context_summary,
)
from ciel_runtime_support.context_summary_policy import (
    CODEX_CONTEXT_CHECKPOINT_PROMPT,
    is_codex_context_checkpoint_prompt,
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
            chunk_target_tokens=lambda _config, _budget: 100,
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
            "provider", "model", {}, "prompt", services, wire="openai", budget_tokens=1000
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
            1000,
            services,
            wire="openai",
        )
        self.assertIsNone(result)
        services.workflow.request_summary.assert_not_called()

    def test_unavailable_provider_completes_automatic_codex_checkpoint_locally(self):
        services = self.services(available=False)

        with self.assertRaises(AutomaticContextCompactionCompleted) as raised:
            build_llm_compacted_messages(
                "provider",
                "model",
                {},
                [
                    {"role": "assistant", "content": "history"},
                    {"role": "user", "content": CODEX_CONTEXT_CHECKPOINT_PROMPT},
                ],
                1000,
                services,
                wire="openai",
            )

        self.assertIn("local context checkpoint", raised.exception.summary)
        services.workflow.request_summary.assert_not_called()

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
            1000,
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
            1000,
            services,
            wire="openai",
        )
        self.assertEqual([{"role": "user", "content": "compact:summary"}], result)
        services.workflow.request_summary.assert_called_once()

    def test_codex_checkpoint_uses_segmented_compaction_by_default(self):
        services = self.services()
        with self.assertRaises(AutomaticContextCompactionCompleted) as raised:
            build_llm_compacted_messages(
                "provider",
                "model",
                {},
                [
                    {"role": "user", "content": "history"},
                    {"role": "user", "content": CODEX_CONTEXT_CHECKPOINT_PROMPT},
                ],
                1000,
                services,
                wire="openai",
            )

        self.assertIn("local context checkpoint", raised.exception.summary)
        self.assertIn("summary", raised.exception.summary)
        services.workflow.request_summary.assert_called_once()

    def test_automatic_codex_compaction_disables_gateway_retries(self):
        services = self.services()
        config = {"gateway_retries": 10}

        with self.assertRaises(AutomaticContextCompactionCompleted):
            build_llm_compacted_messages(
                "provider",
                "model",
                config,
                [
                    {"role": "user", "content": "history"},
                    {"role": "user", "content": CODEX_CONTEXT_CHECKPOINT_PROMPT},
                ],
                1000,
                services,
                wire="openai",
            )

        summary_config = services.workflow.request_summary.call_args.args[2]
        self.assertEqual(0, summary_config["gateway_retries"])
        self.assertEqual(10, config["gateway_retries"])

    def test_automatic_codex_compaction_stops_after_first_summary_failure(self):
        services = self.services(
            split_messages=lambda messages, _target: [
                (index, [message]) for index, message in enumerate(messages)
            ]
        )
        services.workflow.request_summary.side_effect = RuntimeError("high demand")

        with self.assertRaises(AutomaticContextCompactionCompleted) as raised:
            build_llm_compacted_messages(
                "provider",
                "model",
                {"gateway_retries": 10},
                [
                    {"role": "assistant", "content": "history one"},
                    {"role": "assistant", "content": "history two"},
                    {"role": "user", "content": CODEX_CONTEXT_CHECKPOINT_PROMPT},
                ],
                1000,
                services,
                wire="openai",
            )

        services.workflow.request_summary.assert_called_once()
        self.assertIn("local context checkpoint", raised.exception.summary)
        self.assertEqual(2, raised.exception.summary.count("## Segment"))
        self.assertTrue(
            any(
                "context_compact_auto_fallback" in call.args[1]
                for call in services.projection.log.call_args_list
            )
        )

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
            1000,
            services,
            wire="openai",
        )

        self.assertIsNone(result)
        services.workflow.request_summary.assert_not_called()

    def test_automatic_codex_compaction_refuses_more_than_eight_map_calls(self):
        services = self.services(
            split_messages=lambda messages, _target: [
                (index, [message]) for index, message in enumerate(messages)
            ]
        )
        messages = [
            {"role": "assistant", "content": f"history {index}"}
            for index in range(9)
        ]
        messages.append({"role": "user", "content": CODEX_CONTEXT_CHECKPOINT_PROMPT})

        with self.assertRaises(AutomaticContextCompactionCompleted) as raised:
            build_llm_compacted_messages(
                "provider", "model", {}, messages, 1000, services, wire="openai"
            )

        self.assertIn("local context checkpoint", raised.exception.summary)
        services.workflow.request_summary.assert_not_called()
        self.assertTrue(
            any(
                "context_compact_auto_chunk_limit" in call.args[1]
                for call in services.projection.log.call_args_list
            )
        )

    def test_ollama_summary_omits_unproven_output_option(self):
        services = self.services()
        request_context_summary(
            "local",
            "model",
            {"keep_alive": 300},
            "prompt",
            services,
            wire="ollama",
            budget_tokens=1000,
        )
        url, request = services.transport.post_json.call_args.args[:2]
        self.assertEqual("https://test/ollama_chat", url)
        self.assertNotIn("options", request)
        self.assertEqual("300", request["keep_alive"])

    def test_anthropic_summary_uses_provider_messages_endpoint(self):
        services = self.services()
        request_context_summary(
            "remote", "model", {}, "prompt", services, wire="anthropic", budget_tokens=1000
        )
        url, request = services.transport.post_json.call_args.args[:2]
        self.assertEqual("https://test/anthropic_messages", url)
        self.assertEqual("compact", request["system"])
        self.assertFalse(
            services.transport.post_json.call_args.kwargs["retry_rate_limits"]
        )


if __name__ == "__main__":
    unittest.main()
