"""Raw checkpoints precede lossy chat projection."""
import copy
import unittest
from unittest import mock
from ciel_runtime_support.provider_request_builder import (
    OllamaRequestPorts, OpenAIRequestPorts, ProviderOptionPorts,
    ProviderRequestBudget, ProviderRequestBuilder,
)
from ciel_runtime_support.remote_bridge import REMOTE_BRIDGE_CONFIG_MARKER


class SemanticProviderRequestTests(unittest.TestCase):
    def builder(self, kind="claude"):
        events = []
        def compact(body, *args, **kwargs):
            events.append(("semantic", copy.deepcopy(body), kwargs))
            return {**body, "messages": [{"role": "user", "content": "reducer"}]}
        def project(body, **kwargs):
            events.append(("projection", copy.deepcopy(body), kwargs))
            return copy.deepcopy(body["messages"])
        semantic = mock.Mock(side_effect=compact)
        chat = mock.Mock(side_effect=lambda messages, *a, **k: messages)
        builder = ProviderRequestBuilder(
            ProviderRequestBudget(
                context_limit=lambda *a: 32768,
                positive_int=lambda value: value if isinstance(value, int) else 0,
                configured_output=lambda *a: 2048, cap_output_ratio=lambda *a: 2048,
                reserve=lambda *a: 1024, compact_anthropic=semantic,
                compact_messages=chat, compact_kind=lambda body: kind,
                cap_output=lambda *a, **k: 2048, write_usage=lambda *a: None,
            ),
            OllamaRequestPorts(project, lambda value: value or [], lambda c: 32768,
                lambda *a, **k: 32768, lambda request, *a, **k: request),
            OpenAIRequestPorts(project, lambda value: value or [], lambda *a: 32768,
                lambda *a: False, lambda messages: messages, lambda *a: None,
                lambda *a: True, lambda *a: False, lambda value: value,
                lambda provider, config, request: request),
            ProviderOptionPorts(frozenset(), (), lambda model: {}, lambda *a: None),
        )
        return builder, semantic, chat, events

    def body(self):
        return {"messages": [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "call-1",
                "name": "Write", "input": {"content": "A" * 24000 + "ARG-END"}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "call-1", "content": "R" * 24000 + "RESULT-END"},
                {"type": "text", "text": "original user text"},
                {"type": "text", "text": "<command-name>/compact</command-name>"}]}],
            "tools": [{"name": "Write"}], "tool_choice": {"type": "any"},
            "parallel_tool_calls": True}

    def request(self, builder, wire, body, config):
        return (builder.ollama_chat("model", body, config) if wire == "ollama"
                else builder.openai_chat("provider", "model", body, config))

    def test_raw_native_blocks_reach_semantic_hook_once_before_conversion(self):
        for wire in ("ollama", "openai"):
            for kind in ("claude", "codex"):
                with self.subTest(wire=wire, kind=kind):
                    builder, semantic, chat, events = self.builder(kind)
                    body = self.body()
                    original = copy.deepcopy(body)
                    request = self.request(builder, wire, body, {})
                    self.assertEqual(body, original)
                    self.assertEqual(events[0][1], original)
                    self.assertEqual([event[0] for event in events], ["semantic", "projection"])
                    self.assertEqual(semantic.call_args.kwargs["wire"], wire)
                    self.assertEqual(semantic.call_args.kwargs["compact_runtime"], kind)
                    self.assertEqual(semantic.call_args.args[1], 29696)
                    self.assertTrue(semantic.call_args.kwargs["full_compact_request"])
                    semantic.assert_called_once()
                    chat.assert_not_called()
                    self.assertEqual(request["messages"], [{"role": "user", "content": "reducer"}])
                    for key in ("tools", "tool_choice", "parallel_tool_calls"):
                        self.assertNotIn(key, request)
                        self.assertNotIn(key, events[1][1])

    def test_normal_turn_keeps_existing_chat_budget_path(self):
        for wire in ("ollama", "openai"):
            with self.subTest(wire=wire):
                builder, semantic, chat, events = self.builder(None)
                body = {"messages": [{"role": "user", "content": "hello"}], "tools": [{"name": "Write"}]}
                request = self.request(builder, wire, body, {})
                semantic.assert_not_called()
                chat.assert_called_once()
                self.assertEqual(request["messages"], body["messages"])
                self.assertEqual(request["tools"], body["tools"])

    def test_remote_bridge_bypasses_semantic_and_chat_compaction(self):
        for wire in ("ollama", "openai"):
            with self.subTest(wire=wire):
                builder, semantic, chat, events = self.builder()
                body = self.body()
                request = self.request(builder, wire, body, {REMOTE_BRIDGE_CONFIG_MARKER: True})
                semantic.assert_not_called()
                chat.assert_not_called()
                self.assertEqual(request["tools"], body["tools"])
                self.assertEqual(events[0][1], body)
                self.assertFalse(events[0][2]["include_memory"])

    def test_native_anthropic_owns_compaction_except_translated_codex(self):
        builder, semantic, chat, events = self.builder()
        body = self.body()
        self.assertEqual(builder.cap_anthropic_body("anthropic", {}, body), body)
        semantic.assert_not_called()
        builder, semantic, chat, events = self.builder("codex")
        builder.cap_anthropic_body("anthropic", {}, body)
        semantic.assert_called_once()

    def test_semantic_failure_never_runs_lossy_projection(self):
        for wire in ("ollama", "openai"):
            with self.subTest(wire=wire):
                builder, semantic, chat, events = self.builder()
                semantic.side_effect = RuntimeError("semantic failed")
                with self.assertRaisesRegex(RuntimeError, "semantic failed"):
                    self.request(builder, wire, self.body(), {})
                self.assertEqual(events, [])
                chat.assert_not_called()


class SemanticProviderFacadeIntegrationTests(unittest.TestCase):
    def test_confirmed_rejection_semantic_map_sees_full_tool_payload_and_auto_block_history(self):
        import ciel_runtime
        from ciel_runtime_support.context_summary_policy import (
            CLAUDE_COMPACT_TEXT_PREFIX, CLAUDE_COMPACT_TASK, CLAUDE_COMPACT_END,
        )
        instruction = (CLAUDE_COMPACT_TEXT_PREFIX + chr(10) + CLAUDE_COMPACT_TASK
                       + chr(10) + "Your summary should include the following sections:"
                       + chr(10) + CLAUDE_COMPACT_END)
        for wire in ("ollama", "openai"):
            with self.subTest(wire=wire):
                body = SemanticProviderRequestTests().body()
                body["messages"][-1]["content"][-1]["text"] = instruction
                original = copy.deepcopy(body)
                summaries = mock.Mock(return_value="semantic segment")
                config = {"context_window": 16384, "max_model_len": 16384,
                          "num_ctx": 16384, "current_model": "model",
                          "_context_compact_context_rejected": True}
                with mock.patch.object(ciel_runtime, "context_compact_request_summary", summaries),                      mock.patch.object(ciel_runtime, "_latest_remote_instruction", return_value=""):
                    if wire == "ollama":
                        request = ciel_runtime.ollama_chat_request("model", body, config)
                    else:
                        request = ciel_runtime.openai_compatible_chat_request("openai", "model", body, config)
                prompts = "".join(call.args[3] for call in summaries.call_args_list)
                self.assertGreater(summaries.call_count, 1)
                self.assertIn("ARG-END", prompts)
                self.assertIn("RESULT-END", prompts)
                self.assertIn("original user text", prompts)
                self.assertNotIn(CLAUDE_COMPACT_TEXT_PREFIX, prompts)
                self.assertTrue(all(call.kwargs["wire"] == wire for call in summaries.call_args_list))
                self.assertIn("[ciel-runtime segmented compact]", request["messages"][-1]["content"])
                self.assertIn(instruction, request["messages"][-1]["content"])
                self.assertNotIn("tools", request)
                self.assertNotIn("tool_choice", request)
                self.assertEqual(body, original)
