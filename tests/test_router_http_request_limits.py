import io
import json
import unittest
from contextlib import nullcontext

from ciel_runtime_support.request_body_policy import (
    CHAT_FILE_REQUEST_MAX_BYTES,
    DEFAULT_INFLIGHT_REQUEST_BYTES,
    GENERAL_REQUEST_MAX_BYTES,
    INFLIGHT_REQUEST_HARD_MAX_BYTES,
    MODEL_REQUEST_DEFAULT_BYTES,
    MODEL_REQUEST_HARD_MAX_BYTES,
    REQUEST_BODY_MEMORY_MULTIPLIER,
    RequestBodyCapacityExceeded,
    RequestBodyTooLarge,
    RouterRequestBodyPolicy,
    SPEECH_BATCH_REQUEST_MAX_BYTES,
    SPEECH_REFERENCE_REQUEST_MAX_BYTES,
    WEBHOOK_REQUEST_MAX_BYTES,
)
from ciel_runtime_support.otlp_logs import OTLP_LOG_REQUEST_MAX_BYTES
from ciel_runtime_support.router_http import (
    RouterHttpCore,
    RouterHttpErrors,
    RouterHttpGetEndpoints,
    RouterHttpHandler,
    RouterHttpPostEndpoints,
    RouterHttpPresentation,
    RouterHttpServices,
)


class _Headers:
    def __init__(self, values=()):
        self._values = list(values.items()) if isinstance(values, dict) else list(values)

    def get(self, name, default=None):
        values = self.get_all(name)
        return values[-1] if values else default

    def get_all(self, name, default=None):
        values = [
            value
            for header_name, value in self._values
            if str(header_name).casefold() == str(name).casefold()
        ]
        return values if values else default


class _NeverRead:
    def __init__(self):
        self.read_calls = 0

    def read(self, *_args, **_kwargs):
        self.read_calls += 1
        raise AssertionError("the rejected request body must not be read")


class _TrackedReader(io.BytesIO):
    def __init__(self, value):
        super().__init__(value)
        self.read_calls = 0

    def read(self, *args, **kwargs):
        self.read_calls += 1
        return super().read(*args, **kwargs)


class _RouterFixture:
    def __init__(self, policy=None, reject_external=None):
        self.policy = policy or RouterRequestBodyPolicy(environment={})
        self.responses = []
        self.runtime_calls = []
        self.endpoint_calls = []
        self.logs = []

        def false_get(*_args, **_kwargs):
            return False

        def false_post(*_args, **_kwargs):
            self.endpoint_calls.append("post")
            return False

        def external_raw(*_args, **_kwargs):
            self.endpoint_calls.append("external_raw")
            return False

        def telemetry_raw(_handler, path, raw, content_type):
            if path != "/v1/logs":
                return False
            self.endpoint_calls.append(("telemetry_raw", raw, content_type))
            return True

        def runtime_post(_handler, _cfg, provider, _pcfg, path, body):
            self.runtime_calls.append((provider, path, body))
            return True

        def write_json(_handler, value, status=200):
            self.responses.append((status, value))

        def write_responses_error(
            _handler,
            message,
            *,
            stream=True,
            status=500,
            error_type="api_error",
            **_kwargs,
        ):
            del stream
            self.responses.append(
                (
                    status,
                    {
                        "type": "error",
                        "error": {"type": error_type, "message": message},
                    },
                )
            )

        self.services = RouterHttpServices(
            core=RouterHttpCore(
                load_config=lambda: {},
                reject_external=reject_external or (lambda *_args: False),
                get_current_provider=lambda _cfg: (
                    "kimi",
                    {"current_model": "k3"},
                ),
                parse_json_body=lambda raw: json.loads(raw),
                is_client_disconnect=lambda _error: False,
                log=lambda level, message: self.logs.append((level, message)),
                observe_runtime=lambda *_args, **_kwargs: nullcontext(),
                request_body_policy=self.policy,
            ),
            get=RouterHttpGetEndpoints(
                tui=false_get,
                events=false_get,
                llm_config=false_get,
                channel_mcp=false_get,
                web=false_get,
                speech=false_get,
                chat=false_get,
                plan=false_get,
                runtime=false_get,
            ),
            post=RouterHttpPostEndpoints(
                speech=false_post,
                llm_config=false_post,
                channel_mcp=false_post,
                chat=false_post,
                plan=false_post,
                runtime=runtime_post,
                external_events_raw=external_raw,
                external_events_config=false_post,
                telemetry_raw=telemetry_raw,
            ),
            presentation=RouterHttpPresentation(
                home_html=lambda *_args: "",
                health_payload=lambda *_args: {},
                write_text=lambda *_args, **_kwargs: None,
                write_json=write_json,
                list_models=lambda *_args: [],
                resolve_model=lambda *_args: "",
                model_object=lambda *_args: {},
            ),
            errors=RouterHttpErrors(
                write_responses_error=write_responses_error,
                try_write_json=write_json,
            ),
        )

    def handler(self, path, headers, reader):
        services = self.services

        class Handler(RouterHttpHandler):
            services_factory = staticmethod(lambda: services)

            def send_response(self, code, _message=None):
                self.direct_status = code

            def send_header(self, *_args):
                return

            def end_headers(self):
                return

        handler = object.__new__(Handler)
        handler.path = path
        handler.headers = _Headers(headers)
        handler.rfile = reader
        handler.wfile = io.BytesIO()
        handler.requestline = f"POST {path} HTTP/1.1"
        handler.request_version = "HTTP/1.1"
        handler.command = "POST"
        return handler


class RouterHttpRequestLimitTests(unittest.TestCase):
    def test_otlp_logs_route_uses_telemetry_raw_handler_before_json_parsing(self):
        fixture = _RouterFixture()
        raw = b"not parsed by router core"
        handler = fixture.handler(
            "/v1/logs",
            {"content-length": str(len(raw)), "content-type": "application/json"},
            _TrackedReader(raw),
        )

        handler.do_POST()

        self.assertEqual(
            [("telemetry_raw", raw, "application/json")],
            fixture.endpoint_calls,
        )
        self.assertEqual([], fixture.runtime_calls)
        self.assertEqual(OTLP_LOG_REQUEST_MAX_BYTES, fixture.policy.limit_for("/v1/logs"))

    def test_otlp_route_uses_its_dedicated_auth_instead_of_router_admin_auth(self):
        fixture = _RouterFixture(reject_external=lambda *_args: True)
        raw = b"{}"
        handler = fixture.handler(
            "/v1/logs",
            {"content-length": str(len(raw)), "content-type": "application/json"},
            _TrackedReader(raw),
        )

        handler.do_POST()

        self.assertEqual([("telemetry_raw", raw, "application/json")], fixture.endpoint_calls)

    def test_model_request_larger_than_legacy_four_mib_reaches_runtime(self):
        fixture = _RouterFixture()
        raw = json.dumps(
            {
                "model": "k3",
                "input": [{"role": "user", "content": "x" * (4 * 1024 * 1024)}],
            }
        ).encode()
        self.assertGreater(len(raw), GENERAL_REQUEST_MAX_BYTES)
        self.assertLess(len(raw), MODEL_REQUEST_DEFAULT_BYTES)
        reader = _TrackedReader(raw)
        handler = fixture.handler(
            "/v1/responses",
            {"content-length": str(len(raw)), "content-type": "application/json"},
            reader,
        )

        handler.do_POST()

        self.assertEqual(1, reader.read_calls)
        self.assertEqual(1, len(fixture.runtime_calls))
        provider, path, body = fixture.runtime_calls[0]
        self.assertEqual(("kimi", "/v1/responses"), (provider, path))
        self.assertEqual("k3", body["model"])
        self.assertEqual(4 * 1024 * 1024, len(body["input"][0]["content"]))
        self.assertEqual([], fixture.responses)

    def test_provider_post_namespaces_larger_than_four_mib_reach_dispatch(self):
        raw = json.dumps({"payload": "x" * (4 * 1024 * 1024)}).encode()
        self.assertGreater(len(raw), GENERAL_REQUEST_MAX_BYTES)
        for path in (
            "/backend-api/codex/models",
            "/backend-api/codex/provider-operation",
            "/v1/audio/voices",
        ):
            with self.subTest(path=path):
                fixture = _RouterFixture()
                reader = _TrackedReader(raw)
                handler = fixture.handler(
                    path,
                    {
                        "content-length": str(len(raw)),
                        "content-type": "application/json",
                    },
                    reader,
                )

                handler.do_POST()

                self.assertEqual(1, reader.read_calls)
                self.assertEqual(1, len(fixture.runtime_calls))
                self.assertEqual(path, fixture.runtime_calls[0][1])
                self.assertEqual([], fixture.responses)

    def test_model_limit_plus_one_is_structured_413_before_read(self):
        fixture = _RouterFixture()
        reader = _NeverRead()
        handler = fixture.handler(
            "/v1/responses",
            {"content-length": str(MODEL_REQUEST_DEFAULT_BYTES + 1)},
            reader,
        )

        handler.do_POST()

        self.assertEqual(0, reader.read_calls)
        self.assertEqual([], fixture.runtime_calls)
        self.assertEqual(1, len(fixture.responses))
        status, payload = fixture.responses[0]
        self.assertEqual(413, status)
        self.assertEqual("error", payload["type"])
        self.assertEqual("request_too_large", payload["error"]["type"])
        self.assertIn(str(MODEL_REQUEST_DEFAULT_BYTES), payload["error"]["message"])

    def test_webhook_limit_plus_one_is_rejected_before_read_without_fallthrough(self):
        fixture = _RouterFixture()
        reader = _NeverRead()
        handler = fixture.handler(
            "/ca/events/webhooks/default",
            {"content-length": str(WEBHOOK_REQUEST_MAX_BYTES + 1)},
            reader,
        )

        handler.do_POST()

        self.assertEqual(0, reader.read_calls)
        self.assertEqual([], fixture.endpoint_calls)
        self.assertEqual([], fixture.runtime_calls)
        self.assertEqual(1, len(fixture.responses))
        status, payload = fixture.responses[0]
        self.assertEqual(413, status)
        self.assertFalse(payload.get("ok", True))
        self.assertIn(payload.get("error"), {"request_too_large", "event_too_large"})

    def test_webhook_handler_declining_is_terminal_and_never_reaches_runtime(self):
        fixture = _RouterFixture()
        raw = b"{}"
        reader = _TrackedReader(raw)
        handler = fixture.handler(
            "/ca/events/webhooks/missing",
            {"content-length": str(len(raw))},
            reader,
        )

        handler.do_POST()

        self.assertEqual(1, reader.read_calls)
        self.assertEqual(["external_raw"], fixture.endpoint_calls)
        self.assertEqual([], fixture.runtime_calls)
        self.assertEqual(
            (404, {"ok": False, "error": "receiver_not_available"}),
            fixture.responses[0],
        )

    def test_missing_content_length_preserves_empty_post_compatibility(self):
        fixture = _RouterFixture()
        reader = _NeverRead()
        handler = fixture.handler("/v1/responses", [], reader)

        handler.do_POST()

        self.assertEqual(0, reader.read_calls)
        self.assertEqual(1, len(fixture.runtime_calls))
        self.assertEqual({}, fixture.runtime_calls[0][2])
        self.assertEqual([], fixture.responses)

    def test_malformed_negative_duplicate_and_extreme_content_length_are_pre_read_errors(self):
        cases = (
            ([('content-length', 'not-a-number')], 400),
            ([('content-length', '-1')], 400),
            ([('content-length', '2'), ('Content-Length', '2')], 400),
            ([('content-length', '9' * 5000)], 400),
        )
        for headers, expected_status in cases:
            with self.subTest(headers=headers):
                fixture = _RouterFixture()
                reader = _NeverRead()
                handler = fixture.handler("/v1/responses", headers, reader)

                handler.do_POST()

                self.assertEqual(0, reader.read_calls)
                self.assertEqual([], fixture.runtime_calls)
                self.assertEqual(expected_status, fixture.responses[0][0])
                payload = fixture.responses[0][1]
                self.assertEqual("error", payload["type"])
                self.assertEqual("invalid_request_error", payload["error"]["type"])

    def test_non_identity_transfer_and_content_encodings_are_rejected_before_read(self):
        cases = (
            ({"content-length": "2", "transfer-encoding": "chunked"}, 501),
            ({"content-length": "2", "content-encoding": "gzip"}, 415),
        )
        for headers, expected_status in cases:
            with self.subTest(headers=headers):
                fixture = _RouterFixture()
                reader = _NeverRead()
                handler = fixture.handler("/v1/responses", headers, reader)

                handler.do_POST()

                self.assertEqual(0, reader.read_calls)
                self.assertEqual([], fixture.runtime_calls)
                self.assertEqual(expected_status, fixture.responses[0][0])
                self.assertEqual(
                    "invalid_request_error",
                    fixture.responses[0][1]["error"]["type"],
                )

    def test_short_body_is_rejected_instead_of_parsed_or_dispatched(self):
        fixture = _RouterFixture()
        raw = b"{}"
        reader = _TrackedReader(raw)
        handler = fixture.handler(
            "/v1/responses",
            {"content-length": str(len(raw) + 5)},
            reader,
        )

        handler.do_POST()

        self.assertEqual(1, reader.read_calls)
        self.assertEqual([], fixture.runtime_calls)
        self.assertEqual(400, fixture.responses[0][0])
        self.assertEqual("error", fixture.responses[0][1]["type"])
        self.assertEqual(
            "invalid_request_error",
            fixture.responses[0][1]["error"]["type"],
        )
        self.assertIn("body", fixture.responses[0][1]["error"]["message"].lower())


class RouterRequestBodyPolicyTests(unittest.TestCase):
    def test_default_limits_are_endpoint_specific(self):
        policy = RouterRequestBodyPolicy(environment={})
        for path in (
            "/v1/responses",
            "/backend-api/codex/responses",
            "/backend-api/codex/models",
            "/backend-api/codex/other/provider-operation",
            "/v1/messages",
            "/v1/audio/voices",
        ):
            with self.subTest(path=path):
                self.assertEqual(MODEL_REQUEST_DEFAULT_BYTES, policy.limit_for(path))
        self.assertEqual(
            WEBHOOK_REQUEST_MAX_BYTES,
            policy.limit_for("/ca/events/webhooks/default"),
        )
        for path in ("/ca/channel/files", "/ca/chat/files"):
            with self.subTest(path=path):
                self.assertEqual(CHAT_FILE_REQUEST_MAX_BYTES, policy.limit_for(path))
        self.assertEqual(
            SPEECH_REFERENCE_REQUEST_MAX_BYTES,
            policy.limit_for("/v1/audio/speech"),
        )
        self.assertEqual(
            SPEECH_BATCH_REQUEST_MAX_BYTES,
            policy.limit_for("/v1/audio/speech/batch"),
        )
        self.assertEqual(
            GENERAL_REQUEST_MAX_BYTES,
            policy.limit_for("/ca/events/receivers/default"),
        )

    def test_model_environment_limit_is_clamped_and_invalid_values_use_default(self):
        variable = "CIEL_RUNTIME_ROUTER_MODEL_REQUEST_MAX_BYTES"
        cases = (
            ({}, MODEL_REQUEST_DEFAULT_BYTES),
            ({variable: "invalid"}, MODEL_REQUEST_DEFAULT_BYTES),
            ({variable: "1"}, 1),
            (
                {variable: str(MODEL_REQUEST_HARD_MAX_BYTES + 1)},
                MODEL_REQUEST_HARD_MAX_BYTES,
            ),
            ({variable: str(96 * 1024 * 1024)}, 96 * 1024 * 1024),
        )
        for environment, expected in cases:
            with self.subTest(environment=environment):
                self.assertEqual(
                    expected,
                    RouterRequestBodyPolicy(environment=environment).limit_for(
                        "/v1/responses"
                    ),
                )

    def test_inflight_admission_is_byte_weighted_and_releases_capacity(self):
        policy = RouterRequestBodyPolicy(environment={})
        self.assertEqual(4 * 1024 * 1024 * 1024, DEFAULT_INFLIGHT_REQUEST_BYTES)
        maximum = policy.limit_for("/v1/audio/speech/batch")

        with policy.admit("/v1/audio/speech/batch", maximum):
            with self.assertRaises(RequestBodyCapacityExceeded):
                with policy.admit("/v1/responses", 1):
                    pass

        with policy.admit("/v1/audio/speech/batch", maximum):
            pass

    def test_inflight_environment_limit_is_clamped_and_invalid_values_use_default(self):
        variable = "CIEL_RUNTIME_ROUTER_INFLIGHT_REQUEST_BYTES"
        cases = (
            ({}, DEFAULT_INFLIGHT_REQUEST_BYTES),
            ({variable: "invalid"}, DEFAULT_INFLIGHT_REQUEST_BYTES),
            (
                {variable: "1"},
                REQUEST_BODY_MEMORY_MULTIPLIER * SPEECH_BATCH_REQUEST_MAX_BYTES,
            ),
            (
                {variable: str(INFLIGHT_REQUEST_HARD_MAX_BYTES + 1)},
                INFLIGHT_REQUEST_HARD_MAX_BYTES,
            ),
            (
                {variable: str(192 * 1024 * 1024)},
                REQUEST_BODY_MEMORY_MULTIPLIER * SPEECH_BATCH_REQUEST_MAX_BYTES,
            ),
        )
        for environment, expected in cases:
            with self.subTest(environment=environment):
                self.assertEqual(
                    expected,
                    RouterRequestBodyPolicy(
                        environment=environment
                    ).inflight_request_max_bytes,
                )

    def test_inflight_limit_is_never_lower_than_largest_wire_route(self):
        policy = RouterRequestBodyPolicy(
            environment={
                "CIEL_RUNTIME_ROUTER_MODEL_REQUEST_MAX_BYTES": str(96 * 1024 * 1024),
                "CIEL_RUNTIME_ROUTER_INFLIGHT_REQUEST_BYTES": str(32 * 1024 * 1024),
            }
        )

        self.assertEqual(
            REQUEST_BODY_MEMORY_MULTIPLIER * SPEECH_BATCH_REQUEST_MAX_BYTES,
            policy.inflight_request_max_bytes,
        )
        with policy.admit("/v1/responses", 96 * 1024 * 1024):
            pass

    def test_one_max_wire_request_is_admitted_second_is_rejected_and_release_restores(self):
        policy = RouterRequestBodyPolicy(environment={})
        maximum = policy.limit_for("/v1/audio/speech/batch")

        with policy.admit("/v1/audio/speech/batch", maximum):
            self.assertEqual(REQUEST_BODY_MEMORY_MULTIPLIER * maximum, policy.inflight_bytes)
            with self.assertRaises(RequestBodyCapacityExceeded):
                with policy.admit("/v1/audio/speech/batch", maximum):
                    pass

        self.assertEqual(0, policy.inflight_bytes)
        with policy.admit("/v1/audio/speech/batch", maximum):
            self.assertEqual(REQUEST_BODY_MEMORY_MULTIPLIER * maximum, policy.inflight_bytes)
        self.assertEqual(0, policy.inflight_bytes)

    def test_effective_capacity_always_admits_one_fixed_general_control_request(self):
        policy = RouterRequestBodyPolicy(
            environment={
                "CIEL_RUNTIME_ROUTER_MODEL_REQUEST_MAX_BYTES": "1",
                "CIEL_RUNTIME_CHAT_FILE_MAX_BYTES": "1",
                "CIEL_RUNTIME_SPEECH_AUDIO_MAX_BYTES": "1",
                "CIEL_RUNTIME_TTS_REFERENCE_AUDIO_MAX_BYTES": "1",
                "CIEL_RUNTIME_ROUTER_INFLIGHT_REQUEST_BYTES": "1",
            }
        )

        self.assertEqual(
            REQUEST_BODY_MEMORY_MULTIPLIER * SPEECH_BATCH_REQUEST_MAX_BYTES,
            policy.inflight_request_max_bytes,
        )
        with policy.admit("/ca/control/unknown", GENERAL_REQUEST_MAX_BYTES):
            self.assertEqual(
                REQUEST_BODY_MEMORY_MULTIPLIER * GENERAL_REQUEST_MAX_BYTES,
                policy.inflight_bytes,
            )
        self.assertEqual(0, policy.inflight_bytes)

    def test_retired_mcp_path_uses_the_general_control_ceiling(self):
        policy = RouterRequestBodyPolicy(environment={})
        oversized_control = GENERAL_REQUEST_MAX_BYTES + 1
        self.assertEqual(GENERAL_REQUEST_MAX_BYTES, policy.limit_for("/ca/mcp"))
        with self.assertRaises(RequestBodyTooLarge):
            with policy.admit("/ca/mcp", oversized_control):
                pass

    def test_admission_rejects_a_request_above_its_endpoint_limit(self):
        configured = 8 * 1024 * 1024
        policy = RouterRequestBodyPolicy(
            environment={
                "CIEL_RUNTIME_ROUTER_MODEL_REQUEST_MAX_BYTES": str(configured),
            }
        )

        with self.assertRaises(RequestBodyTooLarge):
            with policy.admit("/v1/responses", configured + 1):
                pass


if __name__ == "__main__":
    unittest.main()
