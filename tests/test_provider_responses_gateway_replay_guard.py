"""Native Responses truncation replays stop at an overlay-selected gateway route.

The buffered native Responses path replays a truncated upstream stream up to
``responses_stream_truncation_retries`` times.  A provider config produced by
the CielAiRouter gateway overlay is pinned to one gateway for the whole call,
so every such replay would reach the gateway that has just failed.  These tests
use the real overlay to build that config and the real passthrough to forward
it; only the upstream responses are scripted.
"""

import io
import json
import os
import ssl
import unittest
from http.client import IncompleteRead
from types import SimpleNamespace
from unittest import mock

from ciel_runtime_support.cielairouter_gateway_overlay import (
    ROUTE_TOKEN_KEY,
    CielAiRouterGatewayOverlay,
    discovery_provider_config,
)
from ciel_runtime_support.provider_responses_passthrough import (
    ProviderResponsesPassthrough,
    ProviderResponsesPassthroughPorts,
)
from ciel_runtime_support.upstream_error_policy import UpstreamStreamReadError

PROVIDER = "cielairouter"
CENTRAL = "https://central.example.test"
GATEWAY = "https://gw-2.example.test"
CONFIGURED_RETRIES = 2

CREATED = (
    b"event: response.created\n"
    b'data: {"type":"response.created","response":{"id":"resp_cut"}}\n\n'
)
COMPLETE = (
    b"event: response.created\n"
    b'data: {"type":"response.created","response":{"id":"resp_ok"}}\n\n'
    b"event: response.completed\n"
    b'data: {"type":"response.completed","response":{"id":"resp_ok",'
    b'"status":"completed","output":[]}}\n\n'
)
TLS_EOF_TEXT = "EOF occurred in violation of protocol"


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


def central_config():
    return {
        "base_url": CENTRAL,
        "api_key": "router-key",
        "current_model": "ASTRA",
        "responses_stream_truncation_retries": CONFIGURED_RETRIES,
    }


def selected_gateway_config():
    """The provider config copy exactly as the overlay hands it to a handler."""

    pcfg = CielAiRouterGatewayOverlay(_GatewaySelector).apply(PROVIDER, central_config())
    assert pcfg.get(ROUTE_TOKEN_KEY), "the overlay did not select a gateway route"
    assert pcfg["base_url"] == GATEWAY
    assert pcfg["responses_stream_truncation_retries"] == CONFIGURED_RETRIES
    return pcfg


class ScriptedResponse:
    """One upstream HTTP 200 response whose reads follow a fixed script."""

    status = 200
    headers = {"content-type": "text/event-stream"}

    def __init__(self, steps):
        self.steps = list(steps)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size):
        if not self.steps:
            return b""
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


def handler():
    return SimpleNamespace(
        headers={},
        wfile=io.BytesIO(),
        send_response=mock.Mock(),
        send_header=mock.Mock(),
        end_headers=mock.Mock(),
    )


def passthrough(urlopen, logs):
    return ProviderResponsesPassthrough(
        ProviderResponsesPassthroughPorts(
            project_channel_context=lambda body: (body, {"delivery": True}),
            begin_channel_delivery=mock.Mock(),
            normalize_model=lambda _provider, _config, model: model,
            normalize_request=lambda _provider, _config, body: body,
            upstream_base=lambda _provider, config: str(config["base_url"]),
            join_url=lambda base, path: base + path,
            headers=lambda _provider, _config, _inbound: {"Authorization": "Bearer k"},
            urlopen=urlopen,
            timeout_seconds=lambda _config: 30.0,
            copy_response_headers=lambda _handler, _headers: None,
            log=lambda level, message: logs.append((level, message)),
        )
    )


def body(*, tools=False):
    request = {"model": "ASTRA", "input": [], "stream": True}
    if tools:
        # Tools enable the completion gate, the other reason to buffer.
        request["tools"] = [{"type": "function", "name": "exec_command"}]
    return request


def truncations():
    """Each step list ends the stream without a terminal event."""

    return (
        ("eof_after_created", [CREATED]),
        ("incomplete_read_partial", [IncompleteRead(CREATED, 4096)]),
        ("tls_eof_after_created", [CREATED, ssl.SSLEOFError(8, TLS_EOF_TEXT)]),
    )


class ProviderResponsesGatewayReplayGuardTests(unittest.TestCase):
    def setUp(self):
        # Keep inherited CIEL_RUNTIME_* variables of the live session out.
        environ = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("CIEL_RUNTIME_")
        }
        patcher = mock.patch.dict(os.environ, environ, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def forward(self, config, responses, *, tools=False):
        logs = []
        downstream = handler()
        urlopen = mock.Mock(side_effect=[ScriptedResponse(r) for r in responses])
        outcome = SimpleNamespace(logs=logs, handler=downstream, urlopen=urlopen)
        try:
            outcome.delivery = passthrough(urlopen, logs).forward(
                downstream, PROVIDER, config, body(tools=tools)
            )
            outcome.error = None
        except UpstreamStreamReadError as error:
            outcome.delivery = None
            outcome.error = error
        return outcome

    def assert_nothing_sent(self, outcome):
        self.assertEqual(b"", outcome.handler.wfile.getvalue())
        outcome.handler.send_response.assert_not_called()
        outcome.handler.end_headers.assert_not_called()

    def test_selected_gateway_truncation_is_dispatched_once(self):
        for tools in (False, True):
            for name, steps in truncations():
                with self.subTest(case=name, completion_gate=tools):
                    config = selected_gateway_config()
                    outcome = self.forward(
                        config,
                        [list(steps) for _ in range(CONFIGURED_RETRIES + 1)],
                        tools=tools,
                    )

                    self.assertIsNotNone(outcome.error)
                    self.assertEqual(1, outcome.urlopen.call_count)
                    self.assertEqual(1, outcome.error.attempts)
                    self.assertFalse(outcome.error.downstream_started)
                    # Still buffered: the failure carries no partial body and
                    # no status line, so the route can write its own error.
                    self.assert_nothing_sent(outcome)
                    pcfg = outcome.urlopen.call_args.kwargs["pcfg"]
                    self.assertEqual(config[ROUTE_TOKEN_KEY], pcfg[ROUTE_TOKEN_KEY])
                    self.assertTrue(
                        outcome.urlopen.call_args.args[0].full_url.startswith(GATEWAY)
                    )
                    messages = [message for _, message in outcome.logs]
                    self.assertFalse(
                        [m for m in messages if "provider_responses_stream_retry" in m]
                    )
                    self.assertTrue(
                        [
                            m
                            for m in messages
                            if "provider_responses_stream_truncated" in m
                            and "attempts=1" in m
                        ]
                    )

    def test_unoverlaid_config_keeps_configured_replays(self):
        restored = discovery_provider_config(selected_gateway_config())
        self.assertNotIn(ROUTE_TOKEN_KEY, restored)
        for label, config in (("central", central_config()), ("restored", restored)):
            with self.subTest(config=label):
                outcome = self.forward(
                    config, [[CREATED] for _ in range(CONFIGURED_RETRIES + 1)]
                )

                self.assertIsNotNone(outcome.error)
                self.assertEqual(CONFIGURED_RETRIES + 1, outcome.urlopen.call_count)
                self.assertEqual(CONFIGURED_RETRIES + 1, outcome.error.attempts)
                self.assertFalse(outcome.error.downstream_started)
                self.assert_nothing_sent(outcome)
                retries = [
                    message
                    for _, message in outcome.logs
                    if "provider_responses_stream_retry" in message
                ]
                self.assertEqual(CONFIGURED_RETRIES, len(retries))

    def test_unoverlaid_config_recovers_after_partial_read_exception(self):
        outcome = self.forward(
            central_config(),
            [[IncompleteRead(CREATED, 4096)], [COMPLETE]],
        )

        self.assertIsNone(outcome.error)
        self.assertEqual({"delivery": True}, outcome.delivery)
        self.assertEqual(2, outcome.urlopen.call_count)
        self.assertEqual(COMPLETE, outcome.handler.wfile.getvalue())
        outcome.handler.send_response.assert_called_once_with(200)

    def test_selected_gateway_complete_stream_is_delivered_unchanged(self):
        tool_item = {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "type": "function_call",
                "call_id": "call_1",
                "name": "exec_command",
                "arguments": "{}",
            },
        }
        completed = (
            b"event: response.output_item.done\ndata: "
            + json.dumps(tool_item).encode("utf-8")
            + b"\n\n"
            + COMPLETE
        )
        for tools in (False, True):
            with self.subTest(completion_gate=tools):
                outcome = self.forward(
                    selected_gateway_config(), [[completed]], tools=tools
                )

                self.assertIsNone(outcome.error)
                self.assertEqual({"delivery": True}, outcome.delivery)
                self.assertEqual(1, outcome.urlopen.call_count)
                self.assertEqual(completed, outcome.handler.wfile.getvalue())
                outcome.handler.send_response.assert_called_once_with(200)


if __name__ == "__main__":
    unittest.main()
