"""The open-time retry note reaches the client error message; type and status stay unchanged."""

from __future__ import annotations

import io
import json
import unittest
import urllib.error
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

from ciel_runtime_support import claude_router as claude
from ciel_runtime_support.claude_router import TRANSIENT_RETRY_MESSAGE_LIMIT, with_transient_retry_note
from ciel_runtime_support.upstream_error_policy import UpstreamFailure
import test_router_raw_context_observers as observers

NOTE = ("Ciel Runtime retried this request 3 time(s) over 281s (waited 7.00s) after transient upstream "
        "rejection or timeout before any output; giving up (attempt_ceiling).")


def http_503(note: str | None) -> urllib.error.HTTPError:
    error = urllib.error.HTTPError("http://upstream.test/v1/messages", 503, "Service Unavailable", {},
                                   io.BytesIO(b'{"error": {"type": "overloaded_error", "message": "busy"}}'))
    if note is not None:
        error._ciel_transient_retry_note = note
    return error


class TransientRetryNoteTests(unittest.TestCase):
    def payload_and_status(self, exc):
        failure = UpstreamFailure.from_http_error("cielairouter", "m", exc)
        return with_transient_retry_note(failure.anthropic_payload(), exc), failure.status_code

    def test_http_503_with_note_ends_with_it_and_keeps_type_and_status(self):
        plain, plain_status = self.payload_and_status(http_503(None))
        noted, noted_status = self.payload_and_status(http_503(NOTE))
        self.assertEqual(plain_status, noted_status)
        self.assertEqual(plain["error"]["type"], noted["error"]["type"])
        self.assertTrue(noted["error"]["message"].endswith(f"({NOTE})"))
        self.assertTrue(noted["error"]["message"].startswith(plain["error"]["message"]))

    def test_without_note_the_payload_is_byte_identical(self):
        for exc in (http_503(None), RuntimeError("boom")):
            payload = {"type": "error", "error": {"type": "api_error", "message": str(exc)}}
            before = json.dumps(payload, sort_keys=True)
            self.assertEqual(before, json.dumps(with_transient_retry_note(payload, exc), sort_keys=True))
        error = RuntimeError("x")
        error._ciel_transient_retry_note = 7  # not a string: ignored
        payload = {"type": "error", "error": {"type": "api_error", "message": "x"}}
        self.assertIs(payload, with_transient_retry_note(payload, error))

    def test_generic_500_path_and_bound(self):
        error = RuntimeError("transient retry attempt limit reached")
        error._ciel_transient_retry_note = NOTE
        payload = with_transient_retry_note({"type": "error", "error": {"type": "api_error", "message": str(error)}},
                                            error)
        self.assertEqual("api_error", payload["error"]["type"])
        self.assertEqual(f"transient retry attempt limit reached ({NOTE})", payload["error"]["message"])
        huge = RuntimeError("u" * 5000)
        huge._ciel_transient_retry_note = NOTE
        bounded = with_transient_retry_note({"type": "error", "error": {"type": "api_error", "message": str(huge)}},
                                            huge)["error"]["message"]
        self.assertLessEqual(len(bounded), TRANSIENT_RETRY_MESSAGE_LIMIT)
        self.assertTrue(bounded.endswith(f"({NOTE})"))

    def test_router_generic_500_path_fails_before_when_helper_symbol_is_missing(self):
        """The 06:22 router failure: call sites existed in a module lacking the helper."""

        helper = claude.with_transient_retry_note
        del claude.with_transient_retry_note
        try:
            with self.assertRaises(NameError) as raised:
                self._drive_router_generic_500()
        finally:
            claude.with_transient_retry_note = helper
        self.assertIn("with_transient_retry_note", str(raised.exception))

    def test_router_generic_500_path_writes_client_error_with_note_and_no_traceback(self):
        payload, status, logs = self._drive_router_generic_500()
        self.assertEqual(500, status)
        self.assertEqual("api_error", payload["error"]["type"])
        self.assertEqual(f"transient retry attempt limit reached ({NOTE})", payload["error"]["message"])
        self.assertTrue(any(line.startswith("anthropic_route_error ") for line in logs), logs)
        self.assertFalse(any("router_post_uncaught" in line for line in logs), logs)
        rendered = json.dumps(payload) + "\n" + "\n".join(logs)
        self.assertNotIn("NameError", rendered)
        self.assertNotIn("Traceback", rendered)

    def _drive_router_generic_500(self):
        """Drive handle_claude_messages_post's anthropic_route_error generic-500 branch."""

        error = RuntimeError("transient retry attempt limit reached")
        error._ciel_transient_retry_note = NOTE
        logs = []
        services = observers.RawContextObserverTests().claude_services(None, [])

        def identity(_provider, _config, body):
            return body

        normalization = observers.ports(
            claude.ClaudeRouterNativeNormalization,
            normalize_provider_wire=identity,
            normalize_thinking=identity,
            normalize_system_roles=identity,
            cap_body=identity,
            apply_request_options=identity,
            rehydrate_thinking=identity,
            resolve_tool_models=identity,
            normalize_model_options=lambda _p, _c, body, _m: body,
            strip_internal_metadata=lambda body, **_kw: body,
        )
        services = replace(
            services,
            core=replace(services.core, log=lambda _level, message: logs.append(message)),
            routing=replace(
                services.routing,
                select_protocol=lambda *_a: "anthropic_messages",
                request_policy=lambda *_a: SimpleNamespace(model_alias_strategy=""),
            ),
            normalization=normalization,
            transport=observers.ports(
                claude.ClaudeRouterTransport,
                provider_endpoint=lambda *_a: "https://example.invalid/v1/messages",
                upstream_query=lambda *_a: "",
                provider_headers=lambda *_a: {},
                apply_rate_limit=lambda *_a: (0, 0, 0),
                open_request=mock.Mock(side_effect=error),
                request_timeout=lambda _c: 1,
            ),
            context_recovery=replace(services.context_recovery, recover_output_budget=lambda *_a: None),
        )
        handler = SimpleNamespace(path="/v1/messages", headers={})
        body = {"model": "model", "messages": [{"role": "user", "content": "original"}], "stream": False}
        claude.handle_claude_messages_post(services, handler, {}, "cielairouter", {"stream_enabled": False},
                                           "/v1/messages", body)
        services.core.try_write_json.assert_called_once()
        called_handler, payload, status = services.core.try_write_json.call_args.args
        self.assertIs(handler, called_handler)
        return payload, status, logs


if __name__ == "__main__":
    unittest.main()
