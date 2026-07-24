import unittest
from unittest import mock

from ciel_runtime_support.context_compaction import (
    ContextCompactionProjection,
    ContextCompactionServices,
    ContextCompactionTransport,
    ContextCompactionWorkflow,
    build_llm_compacted_messages,
    request_context_summary,
)


class ContextCompactionTests(unittest.TestCase):
    def services(self, *, available=True, native_compat=False):
        post_json = mock.Mock(return_value={})
        transport = ContextCompactionTransport(
            summary_output_tokens=lambda _config, _budget: 512,
            request_timeout=lambda _config: 30.0,
            endpoint=lambda _provider, _config, operation: f"https://test/{operation}",
            post_json=post_json,
            headers=lambda _provider, _config: {"Authorization": "test"},
            extract_text=lambda _data, _wire: "summary",
            native_compat_enabled=lambda _provider, _config: native_compat,
            native_anthropic_base=lambda _provider, _config: "https://native",
            upstream_base=lambda _provider, _config: "https://upstream",
            join_url=lambda base, path: base + path,
        )
        workflow = ContextCompactionWorkflow(
            parse_bool=lambda value, default=True: default if value is None else bool(value),
            compaction_available=lambda _provider, _config: available,
            instruction_index=lambda _messages: 0,
            content_to_text=lambda value: str(value),
            chunk_target_tokens=lambda _config, _budget: 100,
            split_messages=lambda messages, _target: [(0, messages)],
            parallel_sessions=lambda _config, _chunks: 1,
            write_activity=mock.Mock(),
            estimate_tokens=lambda value: len(str(value)),
            request_summary=mock.Mock(return_value="summary"),
        )
        return ContextCompactionServices(
            transport=transport,
            workflow=workflow,
            projection=ContextCompactionProjection(
                build_chunk_prompt=lambda *_args: "chunk",
                build_fallback_summary=lambda *_args, **_kwargs: "fallback",
                build_reduce_prompt=lambda summaries, instruction, **_kwargs: f"{instruction}:{summaries[0]}",
                log=mock.Mock(),
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

    def test_ollama_summary_preserves_keep_alive_and_native_token_option(self):
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
        self.assertEqual({"num_predict": 512}, request["options"])
        self.assertEqual("300", request["keep_alive"])

    def test_anthropic_summary_uses_native_messages_endpoint(self):
        services = self.services(native_compat=True)
        request_context_summary(
            "remote", "model", {}, "prompt", services, wire="anthropic", budget_tokens=1000
        )
        url, request = services.transport.post_json.call_args.args[:2]
        self.assertEqual("https://native/v1/messages", url)
        self.assertEqual("compact", request["system"])
        self.assertFalse(
            services.transport.post_json.call_args.kwargs["retry_rate_limits"]
        )


if __name__ == "__main__":
    unittest.main()
