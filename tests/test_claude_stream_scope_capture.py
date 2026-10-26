"""Request-start scopes survive an epoch change while opening upstream."""
import copy
import io
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from ciel_runtime_support import claude_router as claude
from ciel_runtime_support import claude_stream_recovery_runtime as recovery
from ciel_runtime_support.runtime_compaction_control import CompactionScope
import test_claude_stream_recovery_runtime as recovery_fixtures
import test_router_raw_context_observers as route_fixtures


class ClaudeStreamScopeCaptureTests(unittest.TestCase):
    def exercise_stream(self, *, switch_epoch, main_session=True):
        scope_a = CompactionScope("claude", recovery_fixtures.SESSION, "epoch-A")
        scope_b = CompactionScope("claude", recovery_fixtures.SESSION, "epoch-B")
        binding = SimpleNamespace(scope=scope_a)
        events = []
        logs = mock.Mock()
        failure_metadata = recovery_fixtures.metadata(upstream_message_id="msg_scope_capture")
        body = {"model": "model", "stream": True,
                "messages": [{"role": "user", "content": "original"}]}
        raw_body = copy.deepcopy(body)
        handler = SimpleNamespace(path="/v1/messages", headers={}, wfile=io.BytesIO(),
                                  send_response=mock.Mock(), send_header=mock.Mock(),
                                  end_headers=mock.Mock())
        response = SimpleNamespace(status=200, headers={"content-type": "text/event-stream"})

        def identity(_provider, _config, request):
            return request

        def capture_scope(_headers, _body):
            events.append("scope")
            return binding.scope if main_session else None

        def select_protocol(*_args):
            events.append("dispatch")
            return "anthropic_messages"

        def open_request(*_args, **_kwargs):
            events.append("open")
            if switch_epoch:
                binding.scope = scope_b
            return response

        with tempfile.TemporaryDirectory() as root:
            ledger = recovery.StreamRecoveryLedger(Path(root) / recovery.LEDGER_NAME, logs)
            record = mock.Mock(wraps=recovery.ClaudeStreamFailureRecorder(
                ledger, lambda: binding.scope, logs))
            scope = mock.Mock(side_effect=capture_scope)

            def relay(actual_handler, actual_response, _model, **kwargs):
                events.append("relay")
                self.assertIs(handler, actual_handler)
                self.assertIs(response, actual_response)
                kwargs["stream_failure_observer"](failure_metadata)
                if main_session:
                    kwargs["stream_model_observer"]("claude-opus-5-5")
                else:
                    self.assertNotIn("stream_model_observer", kwargs)

            native = mock.Mock()
            services = route_fixtures.RawContextObserverTests().claude_services(None, events)
            services = replace(services,
                routing=replace(services.routing, select_protocol=select_protocol,
                    request_policy=lambda *_a: SimpleNamespace(model_alias_strategy="")),
                normalization=route_fixtures.ports(claude.ClaudeRouterNativeNormalization,
                    normalize_provider_wire=identity, normalize_thinking=identity,
                    normalize_system_roles=identity, cap_body=identity,
                    apply_request_options=identity, rehydrate_thinking=identity,
                    resolve_tool_models=identity,
                    normalize_model_options=lambda _p, _c, request, _m: request,
                    strip_internal_metadata=lambda request, **_kw: request),
                transport=route_fixtures.ports(claude.ClaudeRouterTransport,
                    provider_endpoint=lambda *_a: "https://example.test/v1/messages",
                    upstream_query=lambda *_a: "", provider_headers=lambda *_a: {},
                    apply_rate_limit=lambda *_a: (0, 0, 0), open_request=mock.Mock(side_effect=open_request)),
                response=route_fixtures.ports(claude.ClaudeRouterResponse,
                    rebatch_sse=mock.Mock(side_effect=relay)),
                context_recovery=replace(services.context_recovery,
                    reject_request=lambda *_a: None, stream_scope=scope,
                    observe_stream_failure=record, observe_native_model=native))
            with mock.patch.object(claude, "anthropic_request_run"), mock.patch.object(
                claude, "open_with_oauth", side_effect=lambda _run, headers, opener: opener(headers)
            ), mock.patch.object(ledger, "record_failure", wraps=ledger.record_failure) as write:
                claude.handle_claude_messages_post(
                    services, handler, {}, "test", {}, "/v1/messages", body)
                self.assertEqual(["observe", "scope", "inject", "dispatch", "open", "relay"], events)
                services.context_recovery.observe_request.assert_called_once_with(
                    "test", {}, raw_body, "claude", handler.headers)
                scope.assert_called_once_with(handler.headers, raw_body)
                record.assert_called_once_with(scope_a if main_session else None, failure_metadata)
                services.transport.open_request.assert_called_once()
                services.response.rebatch_sse.assert_called_once()
                services.core.try_write_json.assert_not_called()
                handler.send_response.assert_called_once_with(200)
                if not main_session:
                    native.assert_not_called()
                    return
                # The request-start scope and the unmodified request model, even after an epoch switch.
                native.assert_called_once_with(scope_a, "model", "claude-opus-5-5")
                if switch_epoch:
                    self.assertEqual(scope_b, binding.scope)
                    write.assert_not_called()
                    self.assertIsNone(ledger.failure(scope_a, "msg_scope_capture"))
                    self.assertIsNone(ledger.failure(scope_b, "msg_scope_capture"))
                    logs.assert_called_once_with("INFO",
                        "claude_stream_recovery_failure_skipped reason=scope_changed message=msg_scope_capture")
                else:
                    write.assert_called_once()
                    self.assertIsNotNone(ledger.failure(scope_a, "msg_scope_capture"))
                    self.assertIsNone(ledger.failure(scope_b, "msg_scope_capture"))

    def test_opening_upstream_cannot_rebind_the_captured_request_scope(self):
        self.exercise_stream(switch_epoch=True)

    def test_unchanged_request_scope_records_stream_failure(self):
        self.exercise_stream(switch_epoch=False)

    def test_request_outside_main_session_reports_no_native_model(self):
        self.exercise_stream(switch_epoch=False, main_session=False)


if __name__ == "__main__":
    unittest.main()
