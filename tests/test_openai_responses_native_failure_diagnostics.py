"""Native Responses failures persist metadata-only endpoint diagnostics."""

import itertools
import ssl
import unittest
import urllib.error
from http.client import IncompleteRead
from types import SimpleNamespace
from unittest import mock

from ciel_runtime_support.cielairouter_gateway_overlay import DISCOVERY_BASE_URL_KEY
from ciel_runtime_support.openai_responses_router import (
    OpenAIResponsesCore,
    OpenAIResponsesDelivery,
    OpenAIResponsesOutput,
    OpenAIResponsesRouting,
    OpenAIResponsesServices,
    _endpoint_origin,
    handle_openai_responses_request,
)
from ciel_runtime_support.upstream_error_policy import UpstreamStreamReadError

SECRETS = (
    "gw-user",
    "gw-password",
    "sk-query-secret",
    "sk-config-secret",
    "frag-secret",
    "secret-path",
    "central-user",
    "central-query",
    "tls-error-secret",
    "partial-body-secret",
)


def _gateway_config() -> dict:
    return {
        "base_url": (
            "http://gw-user:gw-password@127.0.0.1:20128/v1/secret-path"
            "?api_key=sk-query-secret#frag-secret"
        ),
        "api_key": "sk-config-secret",
        DISCOVERY_BASE_URL_KEY: "https://central-user@router.example.com/v1?k=central-query",
        "ciel_gateway_route": "r7",
        "current_model": "cx/gpt-6.1-sol-medium",
    }


class _Harness:
    def __init__(self, forward_error: BaseException) -> None:
        ids = itertools.count(1)
        self.log = mock.Mock()
        self.event_bus = mock.Mock()
        self.write_error = mock.Mock()
        self.delivery = OpenAIResponsesDelivery(
            begin=mock.Mock(),
            mark_success=mock.Mock(),
            mark_failed=mock.Mock(),
            commit=mock.Mock(),
        )
        self.services = OpenAIResponsesServices(
            core=OpenAIResponsesCore(
                event_bus=self.event_bus,
                request_id=lambda: f"req-{next(ids)}",
                input_as_list=lambda value: list(value or []),
                is_client_disconnect=lambda _error: False,
                log=self.log,
            ),
            conversion=mock.Mock(),
            routing=OpenAIResponsesRouting(
                maybe_import_session=lambda *_args, **_kwargs: False,
                codex_routed_enabled=lambda *_args: False,
                forward_codex=mock.Mock(),
                select_protocol=lambda *_args: "openai_responses",
                forward_provider_responses=mock.Mock(side_effect=forward_error),
                dump_request=mock.Mock(),
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
                event_preview=lambda *_args: {},
            ),
        )

    def run(self, handler, pcfg) -> None:
        handle_openai_responses_request(
            handler,
            {},
            "cielairouter",
            pcfg,
            {"model": "cx/gpt-6.1-sol-medium", "stream": True, "input": []},
            self.services,
        )

    def diagnostic(self) -> str:
        lines = [
            call.args
            for call in self.log.call_args_list
            if call.args[1].startswith("provider_responses_failure ")
        ]
        assert len(lines) == 1, lines
        level, message = lines[0]
        assert level == "ERROR", level
        return message

    def fields(self) -> dict[str, str]:
        message = self.diagnostic()
        return dict(item.split("=", 1) for item in message.split()[1:])

    def request_event_id(self) -> str:
        requests = [
            call.kwargs["request_id"]
            for call in self.event_bus.publish.call_args_list
            if call.kwargs["category"] == "router.request"
        ]
        assert len(requests) == 1, requests
        return requests[0]


class EndpointOriginTests(unittest.TestCase):
    def test_userinfo_path_query_and_fragment_are_removed(self):
        origin = _endpoint_origin(
            "https://gw-user:gw-password@Gateway.Example.com:8443/v1/secret-path"
            "?api_key=sk-query-secret#frag-secret"
        )
        self.assertEqual("https://gateway.example.com:8443", origin)

    def test_password_containing_at_sign_does_not_leak(self):
        origin = _endpoint_origin("https://user:p@ss-secret@host.example/v1")
        self.assertEqual("https://host.example", origin)

    def test_ipv6_loopback_keeps_brackets_and_port(self):
        self.assertEqual(
            "http://[::1]:20128", _endpoint_origin("http://[::1]:20128/v1?x=1")
        )

    def test_default_port_is_not_invented(self):
        self.assertEqual(
            "https://api.example.com", _endpoint_origin("https://api.example.com/v1")
        )

    def test_unparseable_values_are_not_echoed(self):
        for value in (
            "api.example.com/v1?key=sk-query-secret",
            "https://host.example:99999/v1?key=sk-query-secret",
            "https://host.example:port/v1",
            "https://[::1/v1",
            "localhost:8080/v1",
        ):
            with self.subTest(value=value):
                self.assertEqual("invalid", _endpoint_origin(value))

    def test_missing_value_is_unset(self):
        self.assertEqual("unset", _endpoint_origin(None))
        self.assertEqual("unset", _endpoint_origin("  "))


class NativeResponsesFailureDiagnosticTests(unittest.TestCase):
    def assert_no_secret(self, message: str) -> None:
        for secret in SECRETS:
            self.assertNotIn(secret, message)
        for fragment in ("api_key", "/v1", "?", "#", "@"):
            self.assertNotIn(fragment, message)

    @mock.patch("ciel_runtime_support.openai_responses_router.maybe_fail_over")
    def test_open_stage_tls_eof_records_gateway_endpoint_without_secrets(
        self, fail_over
    ):
        error = urllib.error.URLError(
            ssl.SSLEOFError(8, "EOF occurred tls-error-secret sk-query-secret")
        )
        harness = _Harness(error)
        handler = SimpleNamespace(
            path="/v1/responses", _ciel_runtime_response_status=None
        )

        harness.run(handler, _gateway_config())

        message = harness.diagnostic()
        fields = harness.fields()
        self.assertEqual(harness.request_event_id(), fields["request_id"])
        self.assertEqual("req-1", fields["request_id"])
        self.assertEqual(
            {
                "request_id": "req-1",
                "provider": "cielairouter",
                "stage": "open",
                "error": "URLError",
                "cause": "SSLEOFError",
                "downstream_started": "false",
                "received_bytes": "unknown",
                "config_base_url": "http://127.0.0.1:20128",
                "gateway_selected": "true",
                "gateway_discovery_base_url": "https://router.example.com",
            },
            fields,
        )
        self.assert_no_secret(message)
        self.assertNotIn(str(error.reason), message)
        self.assertNotIn("EOF occurred", message)
        self.assertNotIn("r7", fields.values())
        # Unchanged outcome: same failover, delivery state and client error.
        fail_over.assert_called_once()
        harness.delivery.mark_failed.assert_called_once_with(
            handler, "provider_responses:transport:URLError"
        )
        harness.write_error.assert_called_once_with(
            handler,
            mock.ANY,
            stream=True,
            status=502,
            error_type="api_error",
        )
        transport_event = harness.event_bus.publish.call_args.kwargs
        self.assertEqual("router.transport_error", transport_event["category"])
        self.assertEqual("req-1", transport_event["request_id"])

    @mock.patch("ciel_runtime_support.openai_responses_router.maybe_fail_over")
    def test_raw_tls_eof_after_response_start_is_not_claimed_as_open(
        self, _fail_over
    ):
        harness = _Harness(ssl.SSLEOFError(8, "tls-error-secret"))
        handler = SimpleNamespace(
            path="/v1/responses", _ciel_runtime_response_status=200
        )

        harness.run(handler, {"base_url": "https://api.example.com/v1"})

        fields = harness.fields()
        self.assertEqual("unknown", fields["stage"])
        self.assertEqual("SSLEOFError", fields["error"])
        self.assertEqual("-", fields["cause"])
        self.assertEqual("true", fields["downstream_started"])
        self.assertEqual("https://api.example.com", fields["config_base_url"])
        self.assertEqual("false", fields["gateway_selected"])
        self.assertNotIn("gateway_discovery_base_url", fields)
        self.assertNotIn("tls-error-secret", harness.diagnostic())

    @mock.patch("ciel_runtime_support.openai_responses_router.maybe_fail_over")
    def test_handler_without_response_state_reports_unknown(self, _fail_over):
        harness = _Harness(urllib.error.URLError(ConnectionRefusedError(10061, "x")))

        harness.run(SimpleNamespace(path="/v1/responses"), {})

        fields = harness.fields()
        self.assertEqual("unknown", fields["downstream_started"])
        self.assertEqual("ConnectionRefusedError", fields["cause"])
        self.assertEqual("unset", fields["config_base_url"])

    def test_buffered_truncation_records_stream_stage_and_request_id(self):
        source = UpstreamStreamReadError(
            "cielairouter",
            "cx/gpt-6.1-sol-medium",
            IncompleteRead(b"partial-body-secret", 10),
            attempts=1,
            downstream_started=False,
            response_id="resp_cut",
            received_bytes=4096,
        )
        harness = _Harness(source)
        handler = SimpleNamespace(
            path="/v1/responses", _ciel_runtime_response_status=None
        )

        harness.run(handler, _gateway_config())

        message = harness.diagnostic()
        fields = harness.fields()
        self.assertEqual(harness.request_event_id(), fields["request_id"])
        self.assertEqual("stream", fields["stage"])
        self.assertEqual("UpstreamStreamReadError", fields["error"])
        self.assertEqual("IncompleteRead", fields["cause"])
        self.assertEqual("false", fields["downstream_started"])
        # UpstreamStreamReadError keeps the count only inside its message text,
        # which is never parsed or logged.
        self.assertEqual("unknown", fields["received_bytes"])
        self.assertEqual("http://127.0.0.1:20128", fields["config_base_url"])
        self.assertEqual("true", fields["gateway_selected"])
        self.assert_no_secret(message)
        self.assertNotIn(str(source), message)
        self.assertNotIn("resp_cut", message)
        harness.delivery.mark_failed.assert_called_once_with(
            handler, "provider_responses_upstream_stream_truncated"
        )
        harness.write_error.assert_called_once_with(
            handler,
            str(source),
            stream=True,
            status=502,
            error_type="upstream_stream_truncated",
            response_started=False,
            response_id="resp_cut",
        )

    def test_started_truncation_reports_downstream_started_and_exposed_bytes(self):
        source = UpstreamStreamReadError(
            "cielairouter",
            "cx/gpt-6.1-sol-medium",
            EOFError("missing terminal event"),
            attempts=1,
            downstream_started=True,
            response_id="resp_cut",
        )
        source.received_bytes = 12345
        harness = _Harness(source)
        # The reader's own downstream state wins over the handler attribute.
        handler = SimpleNamespace(
            path="/v1/responses", _ciel_runtime_response_status=None
        )

        harness.run(handler, {"base_url": "https://api.example.com"})

        fields = harness.fields()
        self.assertEqual("stream", fields["stage"])
        self.assertEqual("EOFError", fields["cause"])
        self.assertEqual("true", fields["downstream_started"])
        self.assertEqual("12345", fields["received_bytes"])
        self.assertNotIn("missing terminal event", harness.diagnostic())
        harness.write_error.assert_called_once_with(
            handler,
            str(source),
            stream=True,
            status=502,
            error_type="upstream_stream_truncated",
            response_started=True,
            response_id="resp_cut",
        )

    def test_non_transport_failure_writes_no_endpoint_diagnostic(self):
        harness = _Harness(ValueError("sk-config-secret"))

        harness.run(SimpleNamespace(path="/v1/responses"), _gateway_config())

        self.assertFalse(
            any(
                call.args[1].startswith("provider_responses_failure ")
                for call in harness.log.call_args_list
            )
        )
        harness.write_error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
