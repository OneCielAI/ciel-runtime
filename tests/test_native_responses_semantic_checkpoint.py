"""Native compatible Responses checkpoint keeps semantic and backend ownership."""
import io
import json
import urllib.error
import urllib.request
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

from ciel_runtime_support.context_compaction import (
    ContextCompactionFailed, ContextCompactionProjection, ContextCompactionServices,
    ContextCompactionTransport, ContextCompactionWorkflow, build_llm_compacted_messages,
    request_context_summary,
)
from ciel_runtime_support.context_summary_policy import CODEX_CONTEXT_CHECKPOINT_PROMPT, ContextSummaryPolicy
from ciel_runtime_support.prompt_compaction import (
    PromptCompactionRuntime, PromptCompactionServices, PromptCompactionText,
    compact_responses_input_for_budget,
)
from ciel_runtime_support.provider_responses_passthrough import (
    ProviderResponsesPassthrough, ProviderResponsesPassthroughPorts,
)


def estimate(value):
    return len(json.dumps(value, ensure_ascii=False)) // 4


def policy():
    return ContextSummaryPolicy(estimate, lambda value: int(value) if value else None,
                                lambda value: str(value), lambda value, **kw: json.dumps(value), lambda body: "")


def summary_services(post):
    p = policy()
    services = ContextCompactionServices(
        ContextCompactionTransport(
            p.summary_output_tokens, lambda cfg: 30,
            lambda provider, cfg, operation: (
                "https://provider.test/v1/responses" if operation == "openai_responses" else operation
            ),
            post, lambda provider, cfg: {}, p.extract_response_text),
        ContextCompactionWorkflow(
            lambda cfg, instruction: None if cfg.get("context_compact_llm") is False else "auto_codex",
            lambda *args: True, p.instruction_index, str, p.chunk_target_tokens,
            p.split_messages, lambda *args: 1, mock.Mock(), estimate, None),
        ContextCompactionProjection(p.chunk_prompt, lambda *args: "FORBIDDEN GUARD", p.reduce_prompt,
                                    mock.Mock(), lambda body, *args, **kw: body),
        "Summarize all source fragments; do not call tools.",
    )
    return replace(services, workflow=replace(
        services.workflow,
        request_summary=lambda provider, model, cfg, prompt, **kw: request_context_summary(
            provider, model, cfg, prompt, services, **kw),
    ))


def prompt_services(summary):
    return PromptCompactionServices(
        PromptCompactionText(str, str, mock.Mock(side_effect=AssertionError("deterministic guard used")),
                             lambda system, texts: system, lambda value, limit: value[:limit], lambda *args: 0),
        PromptCompactionRuntime(
            estimate,
            lambda provider, model, cfg, messages, budget, **kw: build_llm_compacted_messages(
                provider, model, cfg, messages, budget, summary, **kw),
            mock.Mock(), mock.Mock()),
    )


def checkpoint():
    command = json.dumps({"command": "MARKER-FIRST" + "x" * 22000 + "MARKER-LAST"})
    return {
        "model": "test-model", "stream": True, "instructions": "Keep original system instruction",
        "tools": [{"type": "function", "name": "shell"}],
        "input": [
            {"type": "function_call", "call_id": "c1", "name": "shell", "arguments": command},
            {"type": "function_call_output", "call_id": "c1",
             "output": {"rows": list(range(40)), "unicode": "가" * 1000}},
            {"role": "user", "content": [{"type": "input_text", "text": CODEX_CONTEXT_CHECKPOINT_PROMPT}]},
        ],
    }


def ports(compact, urlopen=None, byte_limit=None):
    return ProviderResponsesPassthroughPorts(
        project_channel_context=lambda body: (body, {}), begin_channel_delivery=mock.Mock(),
        normalize_model=lambda provider, cfg, model: "upstream-" + model,
        normalize_request=lambda provider, cfg, body: body,
        upstream_base=lambda *args: "https://provider.test", join_url=lambda base, path: base + path,
        headers=lambda *args: {}, urlopen=urlopen or mock.Mock(), timeout_seconds=lambda cfg: 30,
        copy_response_headers=lambda *args: None, compact_responses=compact, estimate_tokens=estimate,
        request_max_bytes=lambda *args: byte_limit,
    )


class NativeResponsesSemanticCheckpointTests(unittest.TestCase):
    def test_native_checkpoint_is_forwarded_without_local_summary_when_upstream_has_not_rejected(self):
        compact = mock.Mock()
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.headers = {}
        terminal = b'event: response.completed\ndata: {"type":"response.completed","response":{"id":"real-final","status":"completed","output":[],"usage":{"input_tokens":12,"output_tokens":7}}}\n\n'
        response.read.side_effect = [terminal, b""]
        urlopen = mock.Mock(return_value=response)
        handler = SimpleNamespace(headers={}, wfile=io.BytesIO(), send_response=mock.Mock(),
                                  end_headers=mock.Mock())
        body = checkpoint()
        ProviderResponsesPassthrough(ports(compact, urlopen)).forward(
            handler, "cielairouter", {"context_window": 16384, "max_output_tokens": 1024}, body)
        compact.assert_not_called()
        final = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual("upstream-test-model", final["model"])
        self.assertEqual(body["input"], final["input"])
        self.assertEqual(terminal, handler.wfile.getvalue())

    def test_confirmed_context_recovery_uses_full_raw_source_and_real_responses_summary(self):
        seen = []
        def post(url, request, *args, **kwargs):
            self.assertEqual("upstream-test-model", request["model"])
            self.assertFalse(request["stream"])
            self.assertFalse(kwargs["retry_rate_limits"])
            seen.append(request["input"][0]["content"][0]["text"])
            return {"status": "completed", "output": [{"type": "message", "content": [
                {"type": "output_text", "text": "durable segment"}]}]}
        services = prompt_services(summary_services(post))
        compact = mock.Mock(side_effect=lambda body, budget, **kw: compact_responses_input_for_budget(
            body, budget, services=services, **kw))
        raw = json.dumps({"error": {"type": "context_length_exceeded", "message": "context"}}).encode()
        rejected = urllib.error.HTTPError("https://provider.test", 400, "Bad Request", {}, io.BytesIO(raw))
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.headers = {}
        response.read.side_effect = [b'event: response.completed' + bytes([10]) +
            b'data: {"type":"response.completed","response":{"status":"completed","output":[]}}' + bytes([10, 10]), b""]
        upstream = mock.Mock(side_effect=[rejected, response])
        route = ProviderResponsesPassthrough(ports(compact, upstream))
        body = checkpoint()
        body["input"].insert(0, {"type": "function_call_output", "call_id": "orphan", "output": "RAW-ORPHAN"})
        handler = SimpleNamespace(headers={}, wfile=io.BytesIO(), send_response=mock.Mock(), end_headers=mock.Mock())
        route.forward(handler, "cielairouter", {"context_window": 16384, "max_output_tokens": 1024}, body)
        self.assertGreater(len(seen), 0)
        joined = "".join(seen)
        for marker in ("RAW-ORPHAN", "MARKER-FIRST", "MARKER-LAST", "function_call_output", "38,39]"):
            self.assertIn(marker, joined)
        compact.assert_called_once()
        self.assertEqual(2, upstream.call_count)
        final = json.loads(upstream.call_args.args[0].data)
        self.assertEqual("upstream-test-model", final["model"])
        self.assertIn("durable segment", final["input"][0]["content"][0]["text"])

    def test_confirmed_no_output_rejection_runs_one_bounded_recovery_with_normalized_model(self):
        compact = mock.Mock(side_effect=lambda body, budget, **kw: {
            **body, "input": [{"role": "user", "content": "reduced"}]})
        raw = json.dumps({"error": {"type": "context_length_exceeded", "message": "context"}}).encode()
        rejected = urllib.error.HTTPError("https://provider.test", 400, "Bad Request", {}, io.BytesIO(raw))
        response = mock.Mock()
        upstream = mock.Mock(side_effect=[rejected, response])
        route = ProviderResponsesPassthrough(ports(compact, upstream))
        body = checkpoint()
        body["model"] = "upstream-test-model"
        request = urllib.request.Request("https://provider.test", data=route._encode(body))
        self.assertIs(response, route._open_checkpoint_request(request, "cielairouter", {"context_window": 16384}, body))
        self.assertEqual(2, upstream.call_count)
        compact.assert_called_once()
        self.assertEqual("upstream-test-model", compact.call_args.kwargs["model"])
        self.assertTrue(compact.call_args.kwargs["pcfg"]["_context_compact_context_rejected"])
        self.assertEqual("reduced", json.loads(request.data)["input"][0]["content"])

    def test_second_context_rejection_is_relayed_without_second_summary(self):
        raw = json.dumps({"error": {"type": "context_length_exceeded", "message": "context"}}).encode()
        def error():
            return urllib.error.HTTPError("https://test", 400, "Rejected", {}, io.BytesIO(raw))
        compact = mock.Mock(side_effect=lambda body, budget, **kw: {**body, "input": [{"role": "user", "content": "reduced"}]})
        upstream = mock.Mock(side_effect=[error(), error()])
        route = ProviderResponsesPassthrough(ports(compact, upstream))
        body = checkpoint()
        request = urllib.request.Request("https://test", data=route._encode(body))
        with self.assertRaises(urllib.error.HTTPError):
            route._open_checkpoint_request(request, "cielairouter", {"context_window": 16384}, body)
        self.assertEqual(2, upstream.call_count)
        compact.assert_called_once()

    def test_ambiguous_error_does_not_authorize_recovery(self):
        for status, kind in ((400, "invalid_request_error"), (413, "request_too_large"), (503, "context_length_exceeded")):
            with self.subTest(status=status, kind=kind):
                raw = json.dumps({"error": {"type": kind, "message": "rejected"}}).encode()
                error = urllib.error.HTTPError("https://test", status, "Rejected", {}, io.BytesIO(raw))
                compact = mock.Mock()
                upstream = mock.Mock(side_effect=error)
                route = ProviderResponsesPassthrough(ports(compact, upstream))
                body = checkpoint()
                request = urllib.request.Request("https://test", data=route._encode(body))
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    route._open_checkpoint_request(request, "cielairouter", {"context_window": 16384}, body)
                self.assertEqual(raw, caught.exception.read())
                compact.assert_not_called()
                upstream.assert_called_once()

    def test_unknown_token_count_and_large_unicode_never_trigger_preflight_summary(self):
        for estimate_value in (0, 10**9):
            compact = mock.Mock()
            route = ProviderResponsesPassthrough(replace(ports(compact), estimate_tokens=lambda _: estimate_value))
            body = checkpoint()
            body["input"][1]["output"] = "가" * 50000
            self.assertIs(body, route._fit_provider_request("https://test", "cielairouter", {"context_window": 16384}, body)[0])
            compact.assert_not_called()

    def test_remote_and_native_backend_never_semantically_compact(self):
        compact = mock.Mock()
        route = ProviderResponsesPassthrough(ports(compact))
        body = checkpoint()
        cfg = {"context_window": 16384}
        for provider, remote in (("cielairouter", True), ("codex", False)):
            self.assertIs(body, route._fit_provider_request(
                "https://test", provider, cfg, body, remote_bridge=remote)[0])
        compact.assert_not_called()

    def test_previous_response_id_delta_is_not_a_complete_semantic_source(self):
        compact = mock.Mock()
        body = {**checkpoint(), "previous_response_id": "server-held-history"}
        route = ProviderResponsesPassthrough(ports(compact))
        self.assertIs(body, route._fit_provider_request(
            "https://test", "cielairouter", {"context_window": 16384}, body)[0])
        compact.assert_not_called()
        llm = mock.Mock()
        services = PromptCompactionServices(
            PromptCompactionText(str, str, lambda omitted, budget: "guard", lambda system, texts: system,
                                 lambda value, limit: value[:limit], lambda *args: 0),
            PromptCompactionRuntime(estimate, llm, mock.Mock(), mock.Mock()))
        compact_responses_input_for_budget(body, 8192, provider="cielairouter", model="m",
                                           pcfg={}, full_compact_request=True, services=services)
        llm.assert_not_called()

    def test_ordinary_turn_and_older_checkpoint_never_enter_semantic_path(self):
        compact = mock.Mock()
        route = ProviderResponsesPassthrough(ports(compact))
        for newest in ({"role": "user", "content": "continue"},
                       {"type": "function_call_output", "output": "done"}):
            body = checkpoint()
            body["input"].append(newest)
            self.assertIs(body, route._fit_provider_request(
                "https://test", "cielairouter", {"context_window": 16384}, body)[0])
        compact.assert_not_called()

    def test_summary_rejects_missing_status_truncation_tools_and_refusal(self):
        cases = [
            {"output": []}, {"status": "incomplete", "output": []},
            {"status": "completed", "output": [{"type": "function_call"}]},
            {"status": "completed", "output": [{"type": "message",
                                                "content": [{"type": "refusal", "refusal": "no"}]}]},
            {"status": "completed", "output": [{"type": "message", "content": None}]},
        ]
        for response in cases:
            with self.subTest(response=response):
                services = summary_services(mock.Mock(return_value=response))
                with self.assertRaises(ContextCompactionFailed):
                    request_context_summary("cielairouter", "test", {}, "prompt", services,
                                            wire="responses", budget_tokens=8192)

    def test_extraction_keeps_only_output_text_not_reasoning(self):
        data = {"output": [
            {"type": "reasoning", "summary": [{"text": "hidden"}]},
            {"type": "message", "content": [{"type": "output_text", "text": "one"},
                                            {"type": "output_text", "text": "two"}]},
        ]}
        self.assertEqual("one\ntwo", policy().extract_response_text(data, "responses"))


if __name__ == "__main__":
    unittest.main()
