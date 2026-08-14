import unittest
from types import SimpleNamespace
from unittest import mock

from ciel_runtime_support.openai_responses_router import (
    OpenAIResponsesConversion,
    OpenAIResponsesCore,
    OpenAIResponsesDelivery,
    OpenAIResponsesOutput,
    OpenAIResponsesRouting,
    OpenAIResponsesServices,
    handle_openai_responses_request,
)
from ciel_runtime_support.upstream_error_policy import UpstreamStreamReadError


class OpenAIResponsesUpstreamStreamErrorTests(unittest.TestCase):
    def test_collected_upstream_truncation_is_returned_as_plain_http_502(self):
        event_bus = mock.Mock()
        delivery = OpenAIResponsesDelivery(
            begin=mock.Mock(),
            mark_success=mock.Mock(),
            mark_failed=mock.Mock(),
            commit=mock.Mock(),
        )
        write_error = mock.Mock()
        source_error = UpstreamStreamReadError(
            "alitoken",
            "qwen3.8-max",
            EOFError("response body ended early"),
            attempts=2,
        )
        services = OpenAIResponsesServices(
            core=OpenAIResponsesCore(
                event_bus=event_bus,
                request_id=lambda: "request-1",
                input_as_list=lambda value: list(value or []),
                is_client_disconnect=lambda _error: False,
                log=lambda _level, _message: None,
            ),
            conversion=OpenAIResponsesConversion(
                to_anthropic=lambda body, _alias: {
                    "model": body.get("model"),
                    "messages": [],
                },
                current_alias=lambda _cfg: "qwen3.8-max",
                update_tool_schema=lambda _tools: None,
                normalize_thinking=lambda _provider, _pcfg, body: body,
                filter_blocked_tools=lambda _provider, _pcfg, body: body,
                normalize_tool_choice=lambda _provider, _pcfg, body: body,
                write_context_usage=lambda *_args: None,
                strip_advisor_tools=lambda _provider, body: body,
                inject_channel_context=lambda body: body,
                inject_tool_result_context=lambda body: body,
            ),
            routing=OpenAIResponsesRouting(
                maybe_import_session=lambda *_args, **_kwargs: False,
                codex_routed_enabled=lambda _provider, _pcfg: False,
                forward_codex=mock.Mock(),
                select_protocol=lambda *_args: "openai_chat",
                forward_provider_responses=mock.Mock(),
                dump_request=lambda *_args: None,
                normalize_provider_wire=lambda _provider, _pcfg, body: body,
                collect_message=mock.Mock(side_effect=source_error),
                apply_codex_compat_instructions=lambda _cfg, _provider, _pcfg, body: body,
                recover_preamble_only_turn=mock.Mock(),
            ),
            delivery=delivery,
            output=OpenAIResponsesOutput(
                write_response=mock.Mock(),
                write_error=write_error,
                upstream_error_message=mock.Mock(),
                codex_auth_error_message=lambda message: message,
                event_preview=lambda *_args: {},
            ),
        )
        handler = SimpleNamespace(path="/v1/responses")

        handle_openai_responses_request(
            handler,
            {},
            "alitoken",
            {},
            {"model": "qwen3.8-max", "stream": True, "input": []},
            services,
        )

        write_error.assert_called_once_with(
            handler,
            str(source_error),
            stream=False,
            status=502,
            error_type="api_error",
        )
        delivery.mark_failed.assert_called_once_with(
            handler, "responses_upstream_stream_truncated"
        )
        event = event_bus.publish.call_args.kwargs
        self.assertEqual("router.error", event["category"])
        self.assertTrue(event["data"]["upstream_stream_truncated"])
        self.assertEqual(2, event["data"]["attempts"])

    def test_native_started_stream_truncation_uses_in_stream_failure(self):
        event_bus = mock.Mock()
        delivery = OpenAIResponsesDelivery(
            begin=mock.Mock(),
            mark_success=mock.Mock(),
            mark_failed=mock.Mock(),
            commit=mock.Mock(),
        )
        write_error = mock.Mock()
        source_error = UpstreamStreamReadError(
            "alitoken",
            "qwen3.8-max",
            EOFError("missing terminal event"),
            attempts=1,
            downstream_started=True,
            response_id="resp_cut",
            received_bytes=12345,
        )
        services = OpenAIResponsesServices(
            core=OpenAIResponsesCore(
                event_bus=event_bus,
                request_id=lambda: "request-native",
                input_as_list=lambda value: list(value or []),
                is_client_disconnect=lambda _error: False,
                log=lambda _level, _message: None,
            ),
            conversion=mock.Mock(),
            routing=OpenAIResponsesRouting(
                maybe_import_session=lambda *_args, **_kwargs: False,
                codex_routed_enabled=lambda *_args: False,
                forward_codex=mock.Mock(),
                select_protocol=lambda *_args: "openai_responses",
                forward_provider_responses=mock.Mock(side_effect=source_error),
                dump_request=mock.Mock(),
                normalize_provider_wire=mock.Mock(),
                collect_message=mock.Mock(),
                apply_codex_compat_instructions=lambda _cfg, _provider, _pcfg, body: body,
                recover_preamble_only_turn=mock.Mock(),
            ),
            delivery=delivery,
            output=OpenAIResponsesOutput(
                write_response=mock.Mock(),
                write_error=write_error,
                upstream_error_message=mock.Mock(),
                codex_auth_error_message=lambda message: message,
                event_preview=lambda *_args: {},
            ),
        )

        handle_openai_responses_request(
            SimpleNamespace(path="/v1/responses"),
            {},
            "alitoken",
            {},
            {"model": "qwen3.8-max", "stream": True, "input": []},
            services,
        )

        write_error.assert_called_once_with(
            mock.ANY,
            str(source_error),
            stream=True,
            status=502,
            error_type="upstream_stream_truncated",
            response_started=True,
            response_id="resp_cut",
        )
        delivery.mark_success.assert_not_called()
        delivery.commit.assert_not_called()
        delivery.mark_failed.assert_called_once_with(
            mock.ANY, "provider_responses_upstream_stream_truncated"
        )


if __name__ == "__main__":
    unittest.main()
