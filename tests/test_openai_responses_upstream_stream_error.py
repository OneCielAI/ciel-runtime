import io
import json
import re
import socket
import ssl
import threading
import unittest
import urllib.error
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest import mock

import ciel_runtime
from ciel_runtime_support.provider_responses_passthrough import (
    ProviderResponsesPassthrough,
    ProviderResponsesPassthroughPorts,
)
from ciel_runtime_support.router_http import CodexBackendHttpAdapter, RouterHttpHandler
from ciel_runtime_support.openai_responses_router import (
    OpenAIResponsesConversion,
    OpenAIResponsesCore,
    OpenAIResponsesDelivery,
    OpenAIResponsesOutput,
    OpenAIResponsesRouting,
    OpenAIResponsesServices,
    _write_transport_failure,
    handle_openai_responses_request,
)
from ciel_runtime_support.upstream_error_policy import UpstreamStreamReadError


class OpenAIResponsesUpstreamStreamErrorTests(unittest.TestCase):
    @mock.patch("ciel_runtime_support.openai_responses_router.maybe_fail_over")
    def test_native_route_maps_connection_refused_to_502(self, fail_over):
        event_bus = mock.Mock()
        delivery = OpenAIResponsesDelivery(
            begin=mock.Mock(),
            mark_success=mock.Mock(),
            mark_failed=mock.Mock(),
            commit=mock.Mock(),
        )
        write_error = mock.Mock()
        services = OpenAIResponsesServices(
            core=OpenAIResponsesCore(
                event_bus=event_bus,
                request_id=lambda: "request-native-transport",
                input_as_list=lambda value: list(value or []),
                is_client_disconnect=lambda _error: False,
                log=mock.Mock(),
            ),
            conversion=mock.Mock(),
            routing=OpenAIResponsesRouting(
                maybe_import_session=lambda *_args, **_kwargs: False,
                codex_routed_enabled=lambda *_args: False,
                forward_codex=mock.Mock(),
                select_protocol=lambda *_args: "openai_responses",
                forward_provider_responses=mock.Mock(
                    side_effect=urllib.error.URLError(
                        ConnectionRefusedError(10061, "connection refused")
                    )
                ),
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
                event_preview=mock.Mock(),
            ),
        )

        handle_openai_responses_request(
            SimpleNamespace(path="/v1/responses"),
            {},
            "cielairouter",
            {},
            {"model": "cx/gpt-6.1-sol-medium", "stream": True, "input": []},
            services,
        )

        fail_over.assert_called_once()
        write_error.assert_called_once_with(
            mock.ANY,
            mock.ANY,
            stream=True,
            status=502,
            error_type="api_error",
        )
        delivery.mark_failed.assert_called_once_with(
            mock.ANY, "provider_responses:transport:URLError"
        )

    @mock.patch("ciel_runtime_support.openai_responses_router.maybe_fail_over")
    def test_transport_failure_is_emitted_as_502_with_diagnostic_event(self, fail_over):
        event_bus = mock.Mock()
        delivery = mock.Mock()
        output = mock.Mock()
        core = mock.Mock()
        core.event_bus = event_bus
        core.request_id.return_value = "request-transport"
        handler = mock.Mock()

        handled = _write_transport_failure(
            handler=handler,
            provider="cielairouter",
            model="cx/gpt-6.1-sol-medium",
            stream=True,
            core=core,
            delivery=delivery,
            output=output,
            error=urllib.error.URLError(ConnectionRefusedError(10061, "connection refused")),
            reason="responses",
        )

        self.assertTrue(handled)
        fail_over.assert_called_once()
        output.write_error.assert_called_once_with(
            handler,
            mock.ANY,
            stream=True,
            status=502,
            error_type="api_error",
        )
        delivery.mark_failed.assert_called_once_with(
            handler, "responses:transport:URLError"
        )
        event = event_bus.publish.call_args.kwargs
        self.assertEqual("router.transport_error", event["category"])
        self.assertTrue(event["data"]["transport_error"])
        self.assertEqual(502, event["data"]["status"])

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


_TLS_EOF = "EOF occurred in violation of protocol (_ssl.c:2427)"
_CREATED = b'event: response.created\ndata: {"type":"response.created","response":{"id":"resp_wire"}}\n\n'
_BROKEN_TERMINAL = b'event: response.completed\ndata: {"type":"response.completed","response":{'


class _ScriptedUpstream:
    """Upstream HTTP 200 whose successive reads return bytes or raise."""

    status = 200
    headers = {"content-type": "text/event-stream", "x-request-id": "upstream-req"}

    def __init__(self, steps):
        self._steps = list(steps)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size):
        if not self._steps:
            return b""
        step = self._steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


class _WireRouter(RouterHttpHandler):
    """The router's own HTTP handler class, serving one native Responses route."""

    def log_message(self, *_args):
        return

    def log_error(self, *_args):
        return

    def send_response(self, code, message=None):
        self.server.statuses.append(int(code))
        super().send_response(code, message)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        handle_openai_responses_request(
            self, {}, "cielairouter", {}, body, self.server.services
        )


class NativeResponsesRouterWireTests(unittest.TestCase):
    """Real router socket and passthrough; only the upstream response is scripted."""

    def services(self, urlopen):
        self.delivery = OpenAIResponsesDelivery(
            begin=mock.Mock(),
            mark_success=mock.Mock(),
            mark_failed=mock.Mock(),
            commit=mock.Mock(),
        )
        self.event_bus = mock.Mock()
        self.write_error = mock.Mock(wraps=ciel_runtime.write_openai_responses_error)
        passthrough = ProviderResponsesPassthrough(
            ProviderResponsesPassthroughPorts(
                project_channel_context=lambda body: (body, {"delivery": True}),
                begin_channel_delivery=lambda _handler, _body: None,
                normalize_model=lambda _provider, _config, model: model,
                normalize_request=lambda _provider, _config, body: body,
                upstream_base=lambda _provider, _config: "https://upstream.invalid/v1",
                join_url=ciel_runtime.join_url,
                headers=lambda _provider, _config, _inbound: {},
                urlopen=urlopen,
                timeout_seconds=lambda _config: 30.0,
                copy_response_headers=CodexBackendHttpAdapter.copy_response_headers,
            )
        )
        return OpenAIResponsesServices(
            core=OpenAIResponsesCore(
                event_bus=self.event_bus,
                request_id=lambda: "request-wire",
                input_as_list=lambda value: list(value or []),
                is_client_disconnect=ciel_runtime.is_client_disconnect_error,
                log=lambda _level, _message: None,
            ),
            conversion=mock.Mock(),
            routing=OpenAIResponsesRouting(
                maybe_import_session=lambda *_args, **_kwargs: False,
                codex_routed_enabled=lambda *_args: False,
                forward_codex=mock.Mock(),
                select_protocol=lambda *_args: "openai_responses",
                forward_provider_responses=passthrough.forward,
                dump_request=lambda *_args: None,
                normalize_provider_wire=mock.Mock(),
                collect_message=mock.Mock(),
                apply_codex_compat_instructions=lambda _cfg, _provider, _pcfg, body: body,
                recover_preamble_only_turn=mock.Mock(),
            ),
            delivery=self.delivery,
            output=OpenAIResponsesOutput(
                write_response=mock.Mock(),
                write_error=self.write_error,
                upstream_error_message=mock.Mock(),
                codex_auth_error_message=lambda message: message,
                event_preview=mock.Mock(),
            ),
        )

    def exchange(self, urlopen):
        """POST one Codex-shaped request; return (server statuses, raw wire bytes)."""

        server = ThreadingHTTPServer(("127.0.0.1", 0), _WireRouter)
        server.statuses = []
        server.services = self.services(urlopen)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            # No tools: the request streams unbuffered, the route whose headers
            # are committed before the first upstream read.
            body = json.dumps(
                {"model": "cx/gpt-6.1-sol-medium", "stream": True, "input": []}
            ).encode("utf-8")
            with socket.create_connection(server.server_address, timeout=10) as client:
                client.sendall(
                    b"POST /v1/responses HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                    b"Content-Type: application/json\r\nConnection: close\r\n"
                    + b"Content-Length: %d\r\n\r\n" % len(body)
                    + body
                )
                received = bytearray()
                while chunk := client.recv(65_536):
                    received.extend(chunk)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        return server.statuses, bytes(received)

    @staticmethod
    def wire_statuses(raw):
        return [int(code) for code in re.findall(rb"(?m)^HTTP/1\.[01] (\d{3}) ", raw)]

    @staticmethod
    def sse_events(raw):
        events = []
        for name, data in re.findall(rb"(?m)^event: (\S+)\ndata: (.+)$", raw):
            try:
                payload = json.loads(data)
            except ValueError:
                payload = None  # a truncated upstream event, forwarded as received
            events.append((name.decode(), payload))
        return events

    def test_tls_eof_after_upstream_status_keeps_one_http_response(self):
        failure = ssl.SSLEOFError(8, _TLS_EOF)

        statuses, raw = self.exchange(
            lambda *_args, **_kwargs: _ScriptedUpstream([_CREATED, failure])
        )

        self.assertEqual([200], statuses)
        self.assertEqual([200], self.wire_statuses(raw))
        self.assertIn(_CREATED, raw)
        events = self.sse_events(raw)
        self.assertEqual(
            ["response.created", "response.failed"], [name for name, _ in events]
        )
        failed = events[-1][1]["response"]
        self.assertEqual("resp_wire", failed["id"])
        self.assertEqual("upstream_stream_truncated", failed["error"]["code"])
        self.assertIn(f"SSLEOFError: {_TLS_EOF}", failed["error"]["message"])
        self.delivery.mark_failed.assert_called_once_with(
            mock.ANY, "provider_responses_upstream_stream_truncated"
        )
        self.delivery.mark_success.assert_not_called()
        self.assertTrue(self.event_bus.publish.call_args.kwargs["data"]["downstream_started"])

    def test_upstream_reset_after_status_is_not_reported_as_client_disconnect(self):
        failure = ConnectionResetError(10054, "upstream reset")

        statuses, raw = self.exchange(
            lambda *_args, **_kwargs: _ScriptedUpstream([_CREATED, failure])
        )

        self.assertEqual([200], statuses)
        self.assertEqual([200], self.wire_statuses(raw))
        self.assertEqual(
            ["response.created", "response.failed"],
            [name for name, _ in self.sse_events(raw)],
        )
        self.delivery.mark_failed.assert_called_once_with(
            mock.ANY, "provider_responses_upstream_stream_truncated"
        )

    def test_missing_or_malformed_terminal_still_fails_in_the_started_stream(self):
        for name, chunks in (
            ("missing_terminal", [_CREATED]),
            ("malformed_terminal", [_CREATED, _BROKEN_TERMINAL]),
        ):
            with self.subTest(case=name):
                statuses, raw = self.exchange(
                    lambda *_args, chunks=chunks, **_kwargs: _ScriptedUpstream(chunks)
                )

                self.assertEqual([200], statuses)
                self.assertEqual([200], self.wire_statuses(raw))
                self.assertEqual(
                    "response.failed", self.sse_events(raw)[-1][0]
                )
                self.delivery.mark_failed.assert_called_once_with(
                    mock.ANY, "provider_responses_upstream_stream_truncated"
                )
                self.delivery.mark_success.assert_not_called()

    def test_tls_eof_before_upstream_response_is_a_single_502(self):
        # urllib wraps an OSError raised while sending the request in URLError.
        def urlopen(*_args, **_kwargs):
            raise urllib.error.URLError(ssl.SSLEOFError(8, _TLS_EOF))

        with mock.patch(
            "ciel_runtime_support.openai_responses_router.maybe_fail_over"
        ) as fail_over:
            statuses, raw = self.exchange(urlopen)

        fail_over.assert_called_once()
        self.assertEqual([502], statuses)
        self.assertEqual([502], self.wire_statuses(raw))
        events = self.sse_events(raw)
        self.assertEqual(["error"], [name for name, _ in events])
        self.assertEqual(
            {"type": "api_error", "message": f"SSLEOFError: {_TLS_EOF}"},
            events[0][1]["error"],
        )
        self.delivery.mark_failed.assert_called_once_with(
            mock.ANY, "provider_responses:transport:URLError"
        )

    def test_downstream_client_disconnect_stays_distinct_from_upstream_failure(self):
        complete = _CREATED + (
            b'event: response.completed\ndata: {"type":"response.completed",'
            b'"response":{"id":"resp_wire"}}\n\n'
        )

        class ClosedClient(io.BytesIO):
            def write(self, _data):
                raise ConnectionResetError(10054, "client reset")

        services = self.services(
            lambda *_args, **_kwargs: _ScriptedUpstream([complete])
        )
        handler = SimpleNamespace(
            path="/v1/responses",
            headers={},
            wfile=ClosedClient(),
            send_response=mock.Mock(),
            send_header=mock.Mock(),
            end_headers=mock.Mock(),
        )

        handle_openai_responses_request(
            handler,
            {},
            "cielairouter",
            {},
            {"model": "cx/gpt-6.1-sol-medium", "stream": True, "input": []},
            services,
        )

        self.write_error.assert_not_called()
        handler.send_response.assert_called_once_with(200)
        self.delivery.mark_failed.assert_called_once_with(
            handler, "provider_responses_client_disconnected:ConnectionResetError"
        )


if __name__ == "__main__":
    unittest.main()
