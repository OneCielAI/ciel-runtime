"""Isolated router observer contracts; no live facade or persisted session state."""
import copy
import io
import unittest
import urllib.error
from dataclasses import fields, replace
from types import SimpleNamespace
from unittest import mock

from ciel_runtime_support import claude_router as claude
from ciel_runtime_support import openai_responses_router as responses
from ciel_runtime_support.remote_bridge import REMOTE_BRIDGE_CONTEXT_ATTRIBUTE
from ciel_runtime_support.upstream_error_policy import UpstreamFailure


def ports(kind, **overrides):
    return kind(**{field.name: overrides.get(field.name, mock.Mock()) for field in fields(kind)})


class RawContextObserverTests(unittest.TestCase):
    def responses_services(self, protocol, error, events):
        def compat(_cfg, _provider, _pcfg, body):
            body["instructions"] = "compat added"
            return body

        def inject(body):
            events.append("inject")
            body["messages"].append({"role": "user", "content": "pending"})
            return body

        def identity(_provider, _pcfg, body):
            return body
        return responses.OpenAIResponsesServices(
            core=ports(responses.OpenAIResponsesCore, input_as_list=lambda value: list(value or []),
                observe_context_request=mock.Mock(side_effect=lambda *_args: events.append("observe"))),
            conversion=ports(responses.OpenAIResponsesConversion,
                to_anthropic=lambda *_args: {"messages": []},
                normalize_thinking=identity, filter_blocked_tools=identity,
                normalize_tool_choice=identity, strip_advisor_tools=lambda _p, body: body,
                inject_channel_context=inject, inject_tool_result_context=lambda body: body),
            routing=ports(responses.OpenAIResponsesRouting,
                apply_codex_compat_instructions=compat,
                maybe_import_session=lambda *_args, **_kwargs: False,
                codex_routed_enabled=lambda *_args: protocol == "codex",
                select_protocol=lambda *_args: protocol,
                normalize_provider_wire=identity,
                collect_message=mock.Mock(side_effect=error),
                forward_codex=mock.Mock(side_effect=error),
                forward_provider_responses=mock.Mock(side_effect=error)),
            delivery=ports(responses.OpenAIResponsesDelivery),
            output=ports(responses.OpenAIResponsesOutput, event_preview=lambda *_args: {}),
        )

    @mock.patch.object(responses, "maybe_fail_over")
    def test_responses_all_routes_keep_raw_identity_and_failure_reason(self, _failover):
        for protocol in ("anthropic_messages", "openai_responses", "codex"):
            for structured in (False, True):
                with self.subTest(protocol=protocol, structured=structured):
                    reason = '{"error":{"message":"maximum context length exceeded"}}'
                    error = (UpstreamFailure("test", "model", status=400,
                        message="maximum context length exceeded", body=reason) if structured else
                        urllib.error.HTTPError("https://example.test", 400, "bad", {}, io.BytesIO(reason.encode())))
                    events = []
                    services = self.responses_services(protocol, error, events)
                    body = {"model": "model", "stream": False, "prompt_cache_key": "native-thread",
                        "input": [{"role": "user", "content": [{"type": "input_text", "text": "original"}]}]}
                    expected = copy.deepcopy(body)
                    headers = {"session_id": "native-thread"}
                    responses.handle_openai_responses_request(SimpleNamespace(path="/v1/responses", headers=headers), {}, "test", {}, body, services)
                    services.core.observe_context_request.assert_called_once_with("test", {}, expected, "codex", headers)
                    services.core.observe_context_failure.assert_called_once_with("test", {}, expected, reason, "codex", headers)
                    self.assertEqual("observe", events[0])
                    if protocol == "anthropic_messages":
                        self.assertEqual(["observe", "inject"], events)

    @mock.patch.object(responses, "maybe_fail_over")
    def test_remote_responses_bypasses_observers(self, _failover):
        error = urllib.error.HTTPError("https://example.test", 400, "bad", {}, io.BytesIO(b"overflow"))
        services = self.responses_services("openai_responses", error, [])
        handler = SimpleNamespace(path="/v1/responses")
        setattr(handler, REMOTE_BRIDGE_CONTEXT_ATTRIBUTE, True)
        responses.handle_openai_responses_request(handler, {}, "test", {}, {"input": []}, services)
        services.core.observe_context_request.assert_not_called()
        services.core.observe_context_failure.assert_not_called()

    def claude_services(self, error, events):
        def inject(body):
            events.append("inject")
            body["messages"].append({"role": "user", "content": "pending"})
            return body
        return claude.ClaudeRouterServices(
            core=ports(claude.ClaudeRouterCore), count_tokens=ports(claude.ClaudeRouterCountTokens),
            pipeline=ports(claude.ClaudeRouterPipeline,
                router_event_message_preview=lambda *_args: {},
                filter_blocked_tools=lambda _p, _c, body: body,
                normalize_tool_choice=lambda _p, _c, body: body,
                strip_advisor_tools=lambda _p, body: body,
                inject_channel_context=inject, inject_tool_result_context=lambda body: body),
            shortcuts=ports(claude.ClaudeRouterShortcuts, **{f.name: (lambda *_a, **_k: False) for f in fields(claude.ClaudeRouterShortcuts)}),
            delivery=ports(claude.ClaudeRouterDelivery, is_client_disconnect=lambda _e: False),
            routing=ports(claude.ClaudeRouterRouting,
                resolve_model=lambda *_a: "model", select_protocol=lambda *_a: "openai_responses",
                provider_labels={}, forward_responses=mock.Mock(side_effect=error)),
            normalization=ports(claude.ClaudeRouterNativeNormalization),
            transport=ports(claude.ClaudeRouterTransport), response=ports(claude.ClaudeRouterResponse),
            context_recovery=ports(claude.ClaudeRouterContextRecovery,
                observe_request=mock.Mock(side_effect=lambda *_a: events.append("observe"))),
        )

    @mock.patch.object(claude, "open_with_oauth")
    @mock.patch.object(claude, "anthropic_request_run")
    def test_claude_native_http_failure_observes_raw_body_once(self, _oauth, open_oauth):
        reason = b'{"error":{"message":"prompt too long"}}'
        open_oauth.side_effect = urllib.error.HTTPError("https://example.test", 400, "bad",
            {"content-type": "application/json"}, io.BytesIO(reason))
        events = []
        services = self.claude_services(None, events)
        def identity(_provider, _config, body):
            return body
        services = replace(services,
            routing=replace(services.routing,
                select_protocol=lambda *_a: "anthropic_messages",
                request_policy=lambda *_a: SimpleNamespace(model_alias_strategy="")),
            normalization=ports(claude.ClaudeRouterNativeNormalization,
                normalize_provider_wire=identity, normalize_thinking=identity,
                normalize_system_roles=identity, cap_body=identity,
                apply_request_options=identity, rehydrate_thinking=identity,
                resolve_tool_models=identity,
                normalize_model_options=lambda _p, _c, body, _m: body,
                strip_internal_metadata=lambda body, **_kw: body),
            transport=ports(claude.ClaudeRouterTransport,
                provider_endpoint=lambda *_a: "https://example.test", upstream_query=lambda *_a: "",
                provider_headers=lambda *_a: {}, apply_rate_limit=lambda *_a: (0, 0, 0)),
            context_recovery=replace(services.context_recovery,
                recover_output_budget=lambda *_a: None))
        handler = SimpleNamespace(path="/v1/messages", headers={}, wfile=io.BytesIO(),
            send_response=mock.Mock(), send_header=mock.Mock(), end_headers=mock.Mock())
        body = {"model": "model", "messages": [{"role": "user", "content": "original"}]}
        expected = copy.deepcopy(body)
        claude.handle_claude_messages_post(services, handler, {}, "test", {}, "/v1/messages", body)
        services.context_recovery.observe_request.assert_called_once_with("test", {}, expected, "claude", handler.headers)
        services.context_recovery.observe_failure.assert_called_once_with("test", {}, expected, reason, "claude", handler.headers)
        self.assertEqual(reason, handler.wfile.getvalue())
        handler.send_response.assert_called_once_with(400)

    def test_claude_translated_failures_observe_raw_body_once(self):
        for structured in (False, True):
            with self.subTest(structured=structured):
                reason = b'{"error":{"message":"prompt too long"}}'
                error = (UpstreamFailure("test", "model", status=400, message="prompt too long", body=reason) if structured else
                    urllib.error.HTTPError("https://example.test", 400, "bad", {}, io.BytesIO(reason)))
                events = []
                services = self.claude_services(error, events)
                body = {"model": "model", "messages": [{"role": "user", "content": [{"type": "text", "text": "original"}]}]}
                expected = copy.deepcopy(body)
                claude.handle_claude_messages_post(services, SimpleNamespace(), {}, "test", {}, "/v1/messages", body)
                services.context_recovery.observe_request.assert_called_once_with("test", {}, expected, "claude", None)
                services.context_recovery.observe_failure.assert_called_once_with("test", {}, expected, reason.decode() if structured else reason, "claude", None)
                self.assertEqual(["observe", "inject"], events)


if __name__ == "__main__":
    unittest.main()
