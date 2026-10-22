import copy
import json
import unittest
from unittest import mock
from unittest.mock import Mock

from ciel_runtime_support.prompt_compaction import (
    PromptCompactionRuntime,
    PromptCompactionServices,
    PromptCompactionText,
    compact_anthropic_body_for_budget,
    compact_chat_messages_for_budget,
)
from ciel_runtime_support.context_summary_policy import CODEX_CONTEXT_CHECKPOINT_PROMPT


class NativeCompactPassthroughTests(unittest.TestCase):
    def services(self):
        auxiliary = Mock(return_value=[{"role": "user", "content": "summary"}])
        estimate = Mock(side_effect=lambda value: max(1, len(json.dumps(value, ensure_ascii=False)) // 4))
        text = PromptCompactionText(*(Mock(side_effect=AssertionError("deterministic guard must not run")) for _ in range(6)))
        runtime = PromptCompactionRuntime(estimate, auxiliary, Mock(), Mock())
        return PromptCompactionServices(text, runtime)

    def messages(self):
        return [
            {"role": "assistant", "content": "한글🙂" * 16000},
            {"role": "user", "content": CODEX_CONTEXT_CHECKPOINT_PROMPT},
        ]

    def test_large_unicode_chat_checkpoint_passes_unchanged_without_auxiliary_work(self):
        for config in (None, {}, {"context_compact_llm": True}):
            with self.subTest(config=config):
                services = self.services()
                messages, tools = self.messages(), [{"name": "tool", "description": "도구" * 8000}]
                before = copy.deepcopy(messages)
                result = compact_chat_messages_for_budget(messages, tools, 8192,
                    provider="provider", model="model", pcfg=config,
                    full_compact_request=True, services=services)
                self.assertIs(result, messages)
                self.assertEqual(before, messages)
                services.runtime.estimate_tokens.assert_not_called()
                services.runtime.llm_compact_messages.assert_not_called()
                services.runtime.write_activity.assert_not_called()

    def test_large_unicode_anthropic_checkpoint_passes_raw_tool_blocks_unchanged(self):
        services = self.services()
        body = {"messages": self.messages(), "system": "규칙" * 9000,
                "tools": [{"name": "tool", "input_schema": {"type": "object"}}]}
        body["messages"][0]["content"] = [{"type": "tool_use", "id": "call1",
            "name": "tool", "input": {"raw": "자료🙂" * 16000}}]
        before = copy.deepcopy(body)
        result = compact_anthropic_body_for_budget(body, 8192,
            provider="provider", model="model", pcfg={},
            full_compact_request=True, services=services)
        self.assertIs(result, body)
        self.assertEqual(before, body)
        services.runtime.estimate_tokens.assert_not_called()
        services.runtime.llm_compact_messages.assert_not_called()
        services.runtime.write_activity.assert_not_called()

    def test_confirmed_rejection_keeps_chat_semantic_recovery(self):
        services = self.services()
        result = compact_chat_messages_for_budget(self.messages(), [], 8192,
            provider="provider", model="model",
            pcfg={"_context_compact_context_rejected": True},
            full_compact_request=True, services=services)
        self.assertEqual([{"role": "user", "content": "summary"}], result)
        services.runtime.llm_compact_messages.assert_called_once()

    def test_confirmed_rejection_keeps_anthropic_semantic_recovery(self):
        services = self.services()
        body = {"messages": self.messages(), "tools": [{"name": "tool"}]}
        result = compact_anthropic_body_for_budget(body, 8192,
            provider="provider", model="model",
            pcfg={"_context_compact_context_rejected": True},
            full_compact_request=True, services=services)
        self.assertEqual([{"role": "user", "content": "summary"}], result["messages"])
        self.assertEqual([], result["tools"])
        services.runtime.llm_compact_messages.assert_called_once()

    def test_small_ordinary_request_keeps_existing_budget_path(self):
        services = self.services()
        messages = [{"role": "user", "content": "ordinary"}]
        self.assertIs(messages, compact_chat_messages_for_budget(messages, [], 8192, services=services))
        services.runtime.estimate_tokens.assert_called_once()
        services.runtime.llm_compact_messages.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class NativeCompactOverWindowTests(unittest.TestCase):
    """A checkpoint past the whole context window cannot succeed upstream."""

    def services(self):
        auxiliary = Mock(return_value=[{"role": "user", "content": "summary"}])
        estimate = Mock(side_effect=lambda value: max(1, len(json.dumps(value, ensure_ascii=False)) // 4))
        text = PromptCompactionText(*(Mock(side_effect=AssertionError("deterministic guard must not run")) for _ in range(6)))
        runtime = PromptCompactionRuntime(estimate, auxiliary, Mock(), Mock())
        return PromptCompactionServices(text, runtime)

    def body(self):
        return {"messages": [{"role": "user", "content": "x" * 40000}], "system": "rules",
                "tools": [{"name": "tool"}]}

    def test_window_sized_checkpoint_is_chunked_instead_of_forwarded(self):
        services = self.services()
        body = self.body()
        over_window = services.runtime.estimate_tokens(body) - 1

        result = compact_anthropic_body_for_budget(
            body, 8192, provider="cielairouter", model="model",
            pcfg={"context_window": over_window, "_context_compact_native_over_window": True},
            full_compact_request=True, services=services)

        self.assertEqual([{"role": "user", "content": "summary"}], result["messages"])
        self.assertEqual([], result["tools"])
        services.runtime.llm_compact_messages.assert_called_once()

    def test_checkpoint_inside_the_window_still_passes_without_estimation(self):
        for config in ({"context_window": 10_000_000, "_context_compact_native_over_window": True},
                       {"context_window": 10_000_000}, {}, {"_context_compact_native_over_window": True}):
            with self.subTest(config=config):
                services = self.services()
                body = self.body()
                before = copy.deepcopy(body)

                result = compact_anthropic_body_for_budget(
                    body, 8192, provider="cielairouter", model="model",
                    pcfg=config, full_compact_request=True, services=services)

                self.assertIs(result, body)
                self.assertEqual(before, body)
                services.runtime.llm_compact_messages.assert_not_called()
        audit = self.services()
        compact_anthropic_body_for_budget(self.body(), 8192, provider="p", model="m",
                                          pcfg={}, full_compact_request=True, services=audit)
        audit.runtime.estimate_tokens.assert_not_called()

    def test_translated_codex_checkpoint_keeps_its_rejection_rule(self):
        services = self.services()
        body = self.body()

        result = compact_anthropic_body_for_budget(
            body, 8192, provider="kimi", model="k3",
            pcfg={"context_window": 1}, full_compact_request=True, services=services)

        self.assertIs(result, body)
        services.runtime.llm_compact_messages.assert_not_called()

    def test_malformed_window_values_keep_the_passthrough(self):
        for value in (None, 0, -1, "bad", True):
            with self.subTest(value=value):
                services = self.services()
                body = self.body()
                result = compact_anthropic_body_for_budget(
                    body, 8192, provider="p", model="m",
                    pcfg={"context_window": value, "_context_compact_native_over_window": True},
                    full_compact_request=True, services=services)
                self.assertIs(result, body)
                services.runtime.llm_compact_messages.assert_not_called()


class ConfirmedRejectionForcesSemanticChunkingTests(unittest.TestCase):
    """A rejected request without a recognized checkpoint kind still chunks."""

    def test_rejection_marker_runs_the_semantic_reducer(self):
        import ciel_runtime
        body = {"model": "model", "max_tokens": 32000, "stream": True,
                "system": [{"type": "text", "text": "s"}],
                "messages": [{"role": "user", "content": [{"type": "text", "text": "x" * 20000}]}
                             for _ in range(200)]}
        config = {"current_model": "ASTRA", "context_window": 872000,
                  "_context_compact_context_rejected": True}
        summaries = mock.Mock(return_value=[{"role": "user", "content": "summary"}])
        with mock.patch.object(ciel_runtime, "maybe_build_llm_compacted_messages", summaries):
            out = ciel_runtime.cap_anthropic_body_for_provider("cielairouter", config, copy.deepcopy(body))

        summaries.assert_called_once()
        self.assertEqual([{"role": "user", "content": "summary"}], out["messages"])
        self.assertLess(len(json.dumps(out)), len(json.dumps(body)))

    def test_over_window_request_chunks_without_any_rejection(self):
        import ciel_runtime
        body = {"model": "model", "messages": [{"role": "user", "content": [{"type": "text", "text": "x" * 20000}]}
                                               for _ in range(200)]}
        config = {"current_model": "ASTRA", "context_window": 872000}
        summaries = mock.Mock(return_value=[{"role": "user", "content": "summary"}])
        with mock.patch.object(ciel_runtime, "maybe_build_llm_compacted_messages", summaries):
            out = ciel_runtime.cap_anthropic_body_for_provider("cielairouter", config, copy.deepcopy(body))
        summaries.assert_called_once()
        self.assertEqual([{"role": "user", "content": "summary"}], out["messages"])

    def test_request_inside_the_window_starts_no_semantic_work(self):
        import ciel_runtime
        body = {"model": "model", "messages": [{"role": "user", "content": [{"type": "text", "text": "x" * 200}]}]}
        config = {"current_model": "ASTRA", "context_window": 872000}
        summaries = mock.Mock(return_value=[])
        with mock.patch.object(ciel_runtime, "maybe_build_llm_compacted_messages", summaries):
            ciel_runtime.cap_anthropic_body_for_provider("cielairouter", config, copy.deepcopy(body))
        summaries.assert_not_called()


class PreflightOptOutStillGuardsTheWindowTests(unittest.TestCase):
    def test_disabled_preflight_still_chunks_a_body_past_the_window(self):
        import ciel_runtime
        body = {"model": "m", "messages": [{"role": "user", "content": [{"type": "text", "text": "x" * 20000}]}
                                           for _ in range(300)]}
        config = {"current_model": "ASTRA", "context_window": 872000,
                  "preflight_prompt_compaction": False}
        summaries = mock.Mock(return_value=[{"role": "user", "content": "summary"}])
        with mock.patch.object(ciel_runtime, "maybe_build_llm_compacted_messages", summaries):
            out = ciel_runtime.cap_anthropic_body_for_provider("cielairouter", config, copy.deepcopy(body))
        summaries.assert_called_once()
        self.assertEqual([{"role": "user", "content": "summary"}], out["messages"])

    def test_disabled_preflight_leaves_a_body_inside_the_window_alone(self):
        import ciel_runtime
        body = {"model": "m", "messages": [{"role": "user", "content": [{"type": "text", "text": "x" * 20000}]}
                                           for _ in range(10)]}
        config = {"current_model": "ASTRA", "context_window": 872000,
                  "preflight_prompt_compaction": False}
        summaries = mock.Mock(return_value=[])
        with mock.patch.object(ciel_runtime, "maybe_build_llm_compacted_messages", summaries):
            out = ciel_runtime.cap_anthropic_body_for_provider("cielairouter", config, copy.deepcopy(body))
        summaries.assert_not_called()
        self.assertEqual(len(body["messages"]), len(out["messages"]))
