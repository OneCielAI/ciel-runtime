import io
import unittest
from types import SimpleNamespace
from unittest import mock

from ciel_runtime_support.cielairouter_gateway_overlay import (
    ROUTE_TOKEN_KEY,
    CielAiRouterGatewayOverlay,
    discovery_provider_config,
)
from ciel_runtime_support.initial_stream_retry import InitialStreamRetry
from ciel_runtime_support.openai_forwarding import (
    OpenAIForwardAdvisor,
    OpenAIForwardPolicy,
    OpenAIForwardRateLimit,
    OpenAIForwardRequest,
    OpenAIForwardResponse,
    OpenAIForwardServices,
    OpenAIForwardStreaming,
    forward_openai_compatible_chat,
)
from ciel_runtime_support.remote_bridge import REMOTE_BRIDGE_CONFIG_MARKER
from ciel_runtime_support.upstream_error_policy import (
    initial_stream_retries,
    retryable_exception,
)

CENTRAL = "https://central.example.test"
GATEWAY = "https://gw-2.example.test"


class _GatewaySelector:
    """Selector double: always ranks one gateway, so ``apply`` mints a route."""

    def select(self, _base_url, _api_key, _model="", fresh=False):
        del fresh
        return SimpleNamespace(
            endpoint=GATEWAY,
            candidate=SimpleNamespace(node_id="gw-2", available_until=float("inf")),
            reason="ranked",
            degraded=False,
            generation=1,
        )


def selected_gateway_config():
    """A provider config copy exactly as the overlay hands it to a protocol handler."""

    overlay = CielAiRouterGatewayOverlay(_GatewaySelector)
    pcfg = overlay.apply(
        "cielairouter",
        {"base_url": CENTRAL, "api_key": "router-key", "current_model": "ASTRA"},
    )
    assert pcfg.get(ROUTE_TOKEN_KEY), "the overlay did not select a gateway route"
    assert pcfg["base_url"] == GATEWAY
    return pcfg


class ScriptedResponse:
    def __init__(self, events):
        self.events = list(events)
        self.closed = False

    def __iter__(self):
        for event in self.events:
            if isinstance(event, BaseException):
                raise event
            yield event

    def close(self):
        self.closed = True


class InitialStreamRetryTests(unittest.TestCase):
    def wrapper(self, response, reopen, retries=2):
        notices = []
        sleeps = []
        return (
            InitialStreamRetry(
                response,
                reopen,
                retries,
                retryable_exception,
                lambda attempt: float(attempt),
                sleeps.append,
                lambda attempt, error: notices.append((attempt, type(error).__name__)),
            ),
            notices,
            sleeps,
        )

    def test_reset_before_first_byte_reopens_and_recovers(self):
        first = ScriptedResponse([ConnectionResetError(104, "Connection reset by peer")])
        second = ScriptedResponse([b"data: ok\n", b"data: [DONE]\n"])
        reopen = mock.Mock(return_value=second)
        wrapper, notices, sleeps = self.wrapper(first, reopen)

        self.assertEqual([b"data: ok\n", b"data: [DONE]\n"], list(wrapper))
        reopen.assert_called_once_with(1)
        self.assertTrue(first.closed)
        self.assertEqual([(1, "ConnectionResetError")], notices)
        self.assertEqual([1.0], sleeps)

    def test_reset_after_first_byte_is_never_replayed(self):
        first = ScriptedResponse(
            [b"data: partial\n", ConnectionResetError(104, "Connection reset by peer")]
        )
        reopen = mock.Mock()
        wrapper, _notices, _sleeps = self.wrapper(first, reopen)

        iterator = iter(wrapper)
        self.assertEqual(b"data: partial\n", next(iterator))
        with self.assertRaises(ConnectionResetError):
            next(iterator)
        reopen.assert_not_called()

    def test_empty_response_uses_the_same_bounded_reconnect(self):
        first = ScriptedResponse([])
        second = ScriptedResponse([b"data: ok\n"])
        wrapper, notices, _sleeps = self.wrapper(first, mock.Mock(return_value=second))

        self.assertEqual([b"data: ok\n"], list(wrapper))
        self.assertEqual([(1, "EOFError")], notices)

    def test_configured_reconnects_are_bounded(self):
        self.assertEqual(2, initial_stream_retries({}))
        self.assertEqual(0, initial_stream_retries({"stream_initial_retries": -1}))
        self.assertEqual(5, initial_stream_retries({"stream_initial_retries": 99}))
        self.assertEqual(2, initial_stream_retries({"stream_initial_retries": "bad"}))

    def test_remote_bridge_disables_initial_stream_reconnects(self):
        self.assertEqual(
            0,
            initial_stream_retries(
                {
                    "stream_initial_retries": 5,
                    REMOTE_BRIDGE_CONFIG_MARKER: True,
                }
            ),
        )


class GatewayRouteSelectedTests(unittest.TestCase):
    def test_only_an_overlay_route_token_pins_the_call(self):
        from ciel_runtime_support.initial_stream_retry import gateway_route_selected

        selected = selected_gateway_config()
        self.assertTrue(gateway_route_selected(selected))
        self.assertFalse(gateway_route_selected(discovery_provider_config(selected)))
        self.assertFalse(gateway_route_selected({"base_url": CENTRAL}))
        self.assertFalse(gateway_route_selected({ROUTE_TOKEN_KEY: ""}))
        self.assertFalse(gateway_route_selected(None))


class SelectedGatewayInitialStreamReplayTests(unittest.TestCase):
    """The OpenAI Chat streaming call site never reopens a selected gateway route."""

    def forward(self, provider, pcfg, first_response):
        healthy = ScriptedResponse([b"data: ok\n", b"data: [DONE]\n"])
        responses = [first_response, healthy]
        opened_routes = []
        delivered = []
        logs = []

        def open_with_retry(_url, _body, _headers, _timeout, _provider, config, *_args, **_kwargs):
            opened_routes.append(config.get(ROUTE_TOKEN_KEY))
            return responses[len(opened_routes) - 1]

        def stream_to_anthropic_sse(_handler, upstream, *_args, **_kwargs):
            delivered.extend(upstream)
            return True

        response_port = OpenAIForwardResponse(
            mark_delivery_success=mock.Mock(),
            mark_delivery_failed=mock.Mock(),
            write_activity=mock.Mock(),
            chat_to_anthropic=mock.Mock(),
            remember_tool_uses=mock.Mock(),
            prepend_text=mock.Mock(),
            write_message=mock.Mock(),
            write_json=mock.Mock(),
        )
        streaming = OpenAIForwardStreaming(
            write_open_start=mock.Mock(),
            write_blocks=mock.Mock(
                side_effect=lambda _handler, blocks, index: index + len(blocks)
            ),
            open_with_retry=mock.Mock(side_effect=open_with_retry),
            post_json_with_retry=mock.Mock(),
            stream_to_anthropic_sse=stream_to_anthropic_sse,
            write_open_stop=mock.Mock(),
        )
        services = OpenAIForwardServices(
            policy=OpenAIForwardPolicy(
                compatibility_test_header="x-ciel-runtime-compatibility-test",
                provider_requires_streaming=lambda _provider, _config: True,
            ),
            request=OpenAIForwardRequest(
                update_tool_schema_registry=lambda _tools: None,
                normalize_thinking=lambda _provider, _config, body: body,
                resolve_model=lambda _provider, _config, _model: "ASTRA",
                provider_upstream_model=lambda _provider, _config, model: model,
                body_with_advisor_tool=lambda body, _config: body,
                advisor_provider_supported=lambda _provider: False,
                provider_endpoint=lambda _provider, config, _operation: (
                    str(config.get("base_url") or CENTRAL) + "/v1/chat/completions"
                ),
                build_chat_request=lambda _provider, model, _body, _config, *, stream: {
                    "model": model,
                    "stream": stream,
                },
                provider_headers=lambda _provider, _config, _inbound, _operation: {},
            ),
            rate_limit=OpenAIForwardRateLimit(
                apply=lambda _provider, _config, _model: (0.0, 0, 0),
                notice=lambda *_args: "",
                estimate_tokens=lambda _body: 1,
                request_timeout_seconds=lambda _config: 30.0,
            ),
            advisor=OpenAIForwardAdvisor(
                model_enabled=lambda _config: False,
                gate_possible_for_body=lambda *_args: False,
                gate_reason_for_body=lambda *_args: "",
                refine_message=lambda *_args: None,
            ),
            streaming=streaming,
            response=response_port,
            hosted_tools=SimpleNamespace(
                prepare=lambda _provider, _config, request, _headers, _timeout: (
                    request,
                    None,
                )
            ),
            log=lambda level, message: logs.append((level, message)),
        )
        handler = SimpleNamespace(headers={}, wfile=io.BytesIO())
        with mock.patch("ciel_runtime_support.openai_forwarding.time.sleep") as sleep:
            forward_openai_compatible_chat(
                handler,
                provider,
                pcfg,
                {"model": "ASTRA", "messages": [], "stream": True},
                services=services,
            )
        retry_activity = [
            call
            for call in response_port.write_activity.call_args_list
            if call.args and call.args[0] == "retry"
        ]
        return SimpleNamespace(
            opened_routes=opened_routes,
            delivered=delivered,
            logs=logs,
            retry_activity=retry_activity,
            sleep=sleep,
            response=response_port,
            streaming=streaming,
        )

    def test_selected_gateway_reset_before_first_byte_is_not_reopened(self):
        pcfg = selected_gateway_config()
        token = pcfg[ROUTE_TOKEN_KEY]

        result = self.forward(
            "cielairouter",
            pcfg,
            ScriptedResponse([ConnectionResetError(104, "Connection reset by peer")]),
        )

        # One dispatch on the selected route; a reopen would carry the same token.
        self.assertEqual([token], result.opened_routes)
        self.assertEqual([], result.delivered)
        self.assertEqual([], result.retry_activity)
        self.assertFalse(
            [line for line in result.logs if "upstream_stream_initial_retry" in line[1]]
        )
        result.sleep.assert_not_called()
        # The failure leaves through the existing stream error branch.
        result.response.mark_delivery_failed.assert_called_once_with(
            mock.ANY, "openai_stream_error:ConnectionResetError"
        )
        result.response.mark_delivery_success.assert_not_called()

    def test_selected_gateway_empty_stream_is_not_reopened(self):
        pcfg = selected_gateway_config()

        result = self.forward("cielairouter", pcfg, ScriptedResponse([]))

        self.assertEqual([pcfg[ROUTE_TOKEN_KEY]], result.opened_routes)
        self.assertEqual([], result.retry_activity)
        result.sleep.assert_not_called()

    def test_unoverlaid_cielairouter_config_does_not_retry_ambiguous_transport(self):
        restored = discovery_provider_config(selected_gateway_config())
        self.assertNotIn(ROUTE_TOKEN_KEY, restored)

        result = self.forward(
            "cielairouter",
            restored,
            ScriptedResponse([ConnectionResetError(104, "Connection reset by peer")]),
        )

        self.assertEqual([None], result.opened_routes)
        self.assertEqual([], result.delivered)
        self.assertEqual([], result.retry_activity)
        result.sleep.assert_not_called()
        result.response.mark_delivery_success.assert_not_called()
        result.response.mark_delivery_failed.assert_called_once_with(
            mock.ANY, "openai_stream_error:ConnectionResetError"
        )

    def test_other_provider_keeps_initial_reconnects(self):
        result = self.forward(
            "openai",
            {"base_url": "https://api.example.test", "api_key": "sk-test"},
            ScriptedResponse([ConnectionResetError(104, "Connection reset by peer")]),
        )

        self.assertEqual([None, None], result.opened_routes)
        self.assertEqual([b"data: ok\n", b"data: [DONE]\n"], result.delivered)
        self.assertEqual(1, len(result.retry_activity))
        self.assertTrue(
            [line for line in result.logs if "upstream_stream_initial_retry" in line[1]]
        )


if __name__ == "__main__":
    unittest.main()
