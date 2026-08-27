import io
import json
import unittest
import urllib.error
from contextlib import nullcontext
from types import SimpleNamespace
from unittest import mock

from ciel_runtime_support.config_value_codec import parse_bool
from ciel_runtime_support.openai_chat_passthrough import (
    OpenAIChatPassthrough,
    OpenAIChatPassthroughPorts,
)
from ciel_runtime_support.provider_responses_passthrough import (
    ProviderResponsesPassthrough,
    ProviderResponsesPassthroughPorts,
)
from ciel_runtime_support.remote_bridge import (
    API_KEY_HEADER,
    MODEL_HEADER,
    PROVIDER_HEADER,
    REMOTE_BRIDGE_CONTEXT_ATTRIBUTE,
    REQUEST_API_KEY_MARKER,
    RemoteBridgeRouteError,
    RemoteBridgeRoutingService,
)
from ciel_runtime_support.request_body_policy import RouterRequestBodyPolicy
from ciel_runtime_support.router_http import (
    RouterHttpCore,
    RouterHttpErrors,
    RouterHttpGetEndpoints,
    RouterHttpHandler,
    RouterHttpPostEndpoints,
    RouterHttpPresentation,
    RouterHttpRemoteBridge,
    RouterHttpServices,
)
from ciel_runtime_support.upstream_error_policy import UpstreamStreamReadError


class Headers:
    def __init__(self, values):
        self.values = list(values.items())

    def get(self, name, default=None):
        folded = str(name).casefold()
        matches = [value for key, value in self.values if str(key).casefold() == folded]
        return matches[-1] if matches else default

    def get_all(self, name, default=None):
        folded = str(name).casefold()
        matches = [value for key, value in self.values if str(key).casefold() == folded]
        return matches or default

    def items(self):
        return list(self.values)


class RemoteBridgeHttpTests(unittest.TestCase):
    @staticmethod
    def _response(payload: bytes):
        class Response:
            status = 200
            headers = {"content-type": "application/json"}

            def __init__(self):
                self.payload = io.BytesIO(payload)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, size):
                return self.payload.read(size)

        return Response()

    def test_bridge_auth_and_control_headers_are_scrubbed_before_runtime(self):
        config = {
            "current_provider": "vllm",
            "remote_bridge": {"enabled": True},
            "providers": {
                "vllm": {
                    "current_model": "default-model",
                    "base_url": "http://127.0.0.1:19567/v1",
                }
            },
        }
        routing = RemoteBridgeRoutingService(
            lambda value: str(value).strip().lower(),
            parse_bool,
            {},
        )
        calls = []

        def false(*_args, **_kwargs):
            return False

        def runtime(handler, _config, provider, provider_config, path, body):
            calls.append(
                (
                    provider,
                    provider_config,
                    path,
                    body,
                    dict(handler.headers.items()),
                    bool(
                        getattr(
                            handler,
                            REMOTE_BRIDGE_CONTEXT_ATTRIBUTE,
                            False,
                        )
                    ),
                )
            )
            return True

        services = RouterHttpServices(
            core=RouterHttpCore(
                load_config=lambda: config,
                reject_external=lambda *_args: False,
                get_current_provider=lambda source: (
                    source["current_provider"],
                    source["providers"][source["current_provider"]],
                ),
                parse_json_body=json.loads,
                is_client_disconnect=lambda _error: False,
                log=lambda *_args: None,
                observe_runtime=lambda *_args, **_kwargs: nullcontext(),
                request_body_policy=RouterRequestBodyPolicy(environment={}),
                remote_bridge=RouterHttpRemoteBridge(
                    routing.enabled,
                    routing.resolve,
                    lambda _config: {},
                    lambda handler, _config: bool(handler.bridge_authenticated),
                ),
            ),
            get=RouterHttpGetEndpoints(
                false,
                false,
                false,
                false,
                false,
                false,
                false,
                false,
                false,
            ),
            post=RouterHttpPostEndpoints(
                false,
                false,
                false,
                false,
                false,
                runtime,
            ),
            presentation=RouterHttpPresentation(
                lambda *_args: "",
                lambda *_args: {},
                lambda *_args, **_kwargs: None,
                lambda *_args, **_kwargs: None,
                lambda *_args: [],
                lambda *_args: "",
                lambda *_args: {},
            ),
            errors=RouterHttpErrors(
                lambda *_args, **_kwargs: None,
                lambda *_args, **_kwargs: None,
            ),
        )

        class Handler(RouterHttpHandler):
            services_factory = staticmethod(lambda: services)

        body = json.dumps(
            {"model": "ignored", "messages": [{"role": "user", "content": "hi"}]}
        ).encode()
        handler = object.__new__(Handler)
        handler.path = "/v1/messages"
        handler.headers = Headers(
            {
                "Authorization": "Bearer bridge-client-token",
                "x-api-key": "must-not-reach-upstream",
                PROVIDER_HEADER: "vllm",
                MODEL_HEADER: "selected-model",
                API_KEY_HEADER: "request-provider-key",
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
                "content-length": str(len(body)),
            }
        )
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler.requestline = "POST /v1/messages HTTP/1.1"
        handler.request_version = "HTTP/1.1"
        handler.command = "POST"
        handler.bridge_authenticated = True

        handler.do_POST()

        self.assertEqual(1, len(calls))
        (
            provider,
            provider_config,
            path,
            routed_body,
            upstream_headers,
            remote_context,
        ) = calls[0]
        self.assertEqual(("vllm", "/v1/messages"), (provider, path))
        self.assertEqual("selected-model", routed_body["model"])
        self.assertIs(False, routed_body["stream"])
        self.assertEqual("request-provider-key", provider_config["api_key"])
        self.assertTrue(provider_config[REQUEST_API_KEY_MARKER])
        folded = {name.casefold(): value for name, value in upstream_headers.items()}
        self.assertNotIn("authorization", folded)
        self.assertNotIn("x-api-key", folded)
        self.assertFalse(any(name.startswith("x-ciel-runtime-") for name in folded))
        self.assertEqual("2023-06-01", folded["anthropic-version"])
        self.assertTrue(remote_context)

        local_body = json.dumps(
            {"model": "local-model", "messages": [{"role": "user", "content": "hi"}]}
        ).encode()
        local_handler = object.__new__(Handler)
        local_handler.path = "/v1/messages"
        local_handler.headers = Headers(
            {
                "Authorization": "Bearer client-local-oauth",
                "content-type": "application/json",
                "content-length": str(len(local_body)),
            }
        )
        local_handler.rfile = io.BytesIO(local_body)
        local_handler.wfile = io.BytesIO()
        local_handler.requestline = "POST /v1/messages HTTP/1.1"
        local_handler.request_version = "HTTP/1.1"
        local_handler.command = "POST"
        local_handler.bridge_authenticated = False

        local_handler.do_POST()

        self.assertEqual(2, len(calls))
        (
            local_provider,
            local_config,
            _,
            routed_local_body,
            local_headers,
            local_remote_context,
        ) = calls[1]
        self.assertEqual("vllm", local_provider)
        self.assertIs(config["providers"]["vllm"], local_config)
        self.assertEqual("local-model", routed_local_body["model"])
        self.assertNotIn("stream", routed_local_body)
        self.assertEqual(
            "Bearer client-local-oauth",
            {name.casefold(): value for name, value in local_headers.items()}[
                "authorization"
            ],
        )
        self.assertFalse(local_remote_context)

    def test_bridge_projection_value_error_is_returned_as_invalid_request(self):
        config = {
            "current_provider": "vllm",
            "remote_bridge": {"enabled": True},
            "providers": {
                "vllm": {
                    "current_model": "default-model",
                    "base_url": "http://127.0.0.1:19567/v1",
                }
            },
        }
        routing = RemoteBridgeRoutingService(
            lambda value: str(value).strip().lower(),
            parse_bool,
            {},
        )
        runtime = mock.Mock(
            side_effect=ValueError(
                "Responses hosted tool cannot be projected to Anthropic: tool_search"
            )
        )
        write_responses_error = mock.Mock()
        log = mock.Mock()

        def false(*_args, **_kwargs):
            return False

        services = RouterHttpServices(
            core=RouterHttpCore(
                load_config=lambda: config,
                reject_external=false,
                get_current_provider=lambda source: (
                    source["current_provider"],
                    source["providers"][source["current_provider"]],
                ),
                parse_json_body=json.loads,
                is_client_disconnect=lambda _error: False,
                log=log,
                observe_runtime=lambda *_args, **_kwargs: nullcontext(),
                request_body_policy=RouterRequestBodyPolicy(environment={}),
                remote_bridge=RouterHttpRemoteBridge(
                    routing.enabled,
                    routing.resolve,
                    lambda _config: {},
                    lambda _handler, _config: True,
                ),
            ),
            get=RouterHttpGetEndpoints(
                false,
                false,
                false,
                false,
                false,
                false,
                false,
                false,
                false,
            ),
            post=RouterHttpPostEndpoints(
                false,
                false,
                false,
                false,
                false,
                runtime,
            ),
            presentation=RouterHttpPresentation(
                lambda *_args: "",
                lambda *_args: {},
                lambda *_args, **_kwargs: None,
                lambda *_args, **_kwargs: None,
                lambda *_args: [],
                lambda *_args: "",
                lambda *_args: {},
            ),
            errors=RouterHttpErrors(
                write_responses_error,
                lambda *_args, **_kwargs: None,
            ),
        )

        class Handler(RouterHttpHandler):
            services_factory = staticmethod(lambda: services)

        body = json.dumps(
            {"model": "vllm/model", "input": "hello", "stream": True}
        ).encode()
        handler = object.__new__(Handler)
        handler.path = "/v1/responses"
        original_headers = Headers(
            {
                "Authorization": "Bearer bridge-client-token",
                "content-type": "application/json",
                "content-length": str(len(body)),
            }
        )
        handler.headers = original_headers
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler.requestline = "POST /v1/responses HTTP/1.1"
        handler.request_version = "HTTP/1.1"
        handler.command = "POST"

        handler.do_POST()

        runtime.assert_called_once()
        write_responses_error.assert_called_once_with(
            handler,
            "Responses hosted tool cannot be projected to Anthropic: tool_search",
            stream=False,
            status=400,
            error_type="invalid_request_error",
        )
        self.assertIs(original_headers, handler.headers)
        self.assertTrue(handler.close_connection)
        self.assertTrue(
            any(
                call.args
                and call.args[0] == "WARN"
                and "status=400" in call.args[1]
                for call in log.call_args_list
            )
        )
        self.assertFalse(
            any(call.args and call.args[0] == "ERROR" for call in log.call_args_list)
        )

    def test_bridge_chat_passthrough_does_not_inject_router_host_memory(self):
        captured = {}
        finalize = mock.Mock(
            side_effect=lambda body: {**body, "host_memory": "secret"}
        )

        def urlopen(request, **_kwargs):
            captured["body"] = json.loads(request.data)
            return self._response(b'{"choices":[]}')

        passthrough = OpenAIChatPassthrough(
            OpenAIChatPassthroughPorts(
                normalize_model=lambda _provider, _config, model: model,
                normalize_request=lambda _provider, _config, body: body,
                upstream_base=lambda _provider, _config: "https://example.test",
                join_url=lambda base, path: f"{base}{path}",
                headers=lambda *_args: {},
                urlopen=urlopen,
                timeout_seconds=lambda _config: 30.0,
                copy_response_headers=lambda *_args: None,
                finalize_body=finalize,
            )
        )
        handler = SimpleNamespace(
            headers={},
            wfile=io.BytesIO(),
            send_response=mock.Mock(),
            end_headers=mock.Mock(),
        )
        setattr(handler, REMOTE_BRIDGE_CONTEXT_ATTRIBUTE, True)

        passthrough.forward(
            handler,
            "vllm",
            {},
            {"model": "model", "messages": [], "stream": False},
        )

        finalize.assert_not_called()
        self.assertNotIn("host_memory", captured["body"])

    def test_bridge_responses_passthrough_does_not_inject_host_context(self):
        captured = {}
        project_channel = mock.Mock(
            return_value=({"host_channel": "secret"}, {"delivery": True})
        )
        begin_delivery = mock.Mock()
        finalize = mock.Mock(
            side_effect=lambda body: {**body, "host_memory": "secret"}
        )

        def urlopen(request, **_kwargs):
            captured["body"] = json.loads(request.data)
            return self._response(b'{"id":"resp","object":"response"}')

        passthrough = ProviderResponsesPassthrough(
            ProviderResponsesPassthroughPorts(
                project_channel_context=project_channel,
                begin_channel_delivery=begin_delivery,
                normalize_model=lambda _provider, _config, model: model,
                normalize_request=lambda _provider, _config, body: body,
                upstream_base=lambda _provider, _config: "https://example.test",
                join_url=lambda base, path: f"{base}{path}",
                headers=lambda *_args: {},
                urlopen=urlopen,
                timeout_seconds=lambda _config: 30.0,
                copy_response_headers=lambda *_args: None,
                finalize_body=finalize,
            )
        )
        handler = SimpleNamespace(
            headers={},
            wfile=io.BytesIO(),
            send_response=mock.Mock(),
            end_headers=mock.Mock(),
        )
        setattr(handler, REMOTE_BRIDGE_CONTEXT_ATTRIBUTE, True)

        passthrough.forward(
            handler,
            "github-copilot-oauth",
            {},
            {"model": "model", "input": "hello", "stream": False},
        )

        project_channel.assert_not_called()
        begin_delivery.assert_not_called()
        finalize.assert_not_called()
        self.assertNotIn("host_channel", captured["body"])
        self.assertNotIn("host_memory", captured["body"])

    def test_bridge_responses_passthrough_preserves_wire_items_without_compaction(self):
        captured = {}
        compact = mock.Mock(side_effect=AssertionError("must not compact bridge input"))
        source = {
            "model": "model",
            "stream": False,
            "input": [
                {
                    "type": "reasoning",
                    "id": "msg_foreign",
                    "summary": [{"type": "summary_text", "text": "opaque"}],
                    "encrypted_content": None,
                },
                {
                    "type": "message",
                    "id": "msg_user",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "preserve-" + ("x" * 400)}
                    ],
                },
            ],
        }
        encoded_size = len(
            json.dumps(source, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        hard_limit = encoded_size + 10
        self.assertGreater(encoded_size, (hard_limit * 9) // 10)

        def urlopen(request, **_kwargs):
            captured["body"] = json.loads(request.data)
            return self._response(b'{"id":"resp","object":"response"}')

        passthrough = ProviderResponsesPassthrough(
            ProviderResponsesPassthroughPorts(
                project_channel_context=lambda body: (body, {}),
                begin_channel_delivery=mock.Mock(),
                normalize_model=lambda _provider, _config, model: model,
                normalize_request=lambda _provider, _config, body: body,
                upstream_base=lambda _provider, _config: "https://example.test",
                join_url=lambda base, path: f"{base}{path}",
                headers=lambda *_args: {},
                urlopen=urlopen,
                timeout_seconds=lambda _config: 30.0,
                copy_response_headers=lambda *_args: None,
                request_max_bytes=lambda *_args: hard_limit,
                estimate_tokens=lambda _body: 1000,
                compact_responses=compact,
            )
        )
        handler = SimpleNamespace(
            headers={},
            wfile=io.BytesIO(),
            send_response=mock.Mock(),
            end_headers=mock.Mock(),
        )
        setattr(handler, REMOTE_BRIDGE_CONTEXT_ATTRIBUTE, True)

        passthrough.forward(handler, "vllm", {}, source)

        self.assertEqual(source, captured["body"])
        compact.assert_not_called()

    def test_bridge_responses_passthrough_rejects_oversize_without_compaction(self):
        compact = mock.Mock(side_effect=AssertionError("must not compact bridge input"))
        urlopen = mock.Mock()
        source = {
            "model": "model",
            "stream": False,
            "input": "x" * 1000,
        }
        encoded_size = len(
            json.dumps(source, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        passthrough = ProviderResponsesPassthrough(
            ProviderResponsesPassthroughPorts(
                project_channel_context=lambda body: (body, {}),
                begin_channel_delivery=mock.Mock(),
                normalize_model=lambda _provider, _config, model: model,
                normalize_request=lambda _provider, _config, body: body,
                upstream_base=lambda _provider, _config: "https://example.test",
                join_url=lambda base, path: f"{base}{path}",
                headers=lambda *_args: {},
                urlopen=urlopen,
                timeout_seconds=lambda _config: 30.0,
                copy_response_headers=lambda *_args: None,
                request_max_bytes=lambda *_args: encoded_size - 1,
                estimate_tokens=lambda _body: 1000,
                compact_responses=compact,
            )
        )
        handler = SimpleNamespace(
            headers={},
            wfile=io.BytesIO(),
            send_response=mock.Mock(),
            end_headers=mock.Mock(),
        )
        setattr(handler, REMOTE_BRIDGE_CONTEXT_ATTRIBUTE, True)

        with self.assertRaises(urllib.error.HTTPError) as caught:
            passthrough.forward(handler, "vllm", {}, source)

        self.assertEqual(413, caught.exception.code)
        payload = json.loads(caught.exception.read())
        self.assertEqual("request_too_large", payload["error"]["type"])
        self.assertIn("was not compacted", payload["error"]["message"])
        compact.assert_not_called()
        urlopen.assert_not_called()

    def test_bridge_responses_passthrough_does_not_retry_truncated_stream(self):
        payload = (
            b"event: response.created\n"
            b'data: {"type":"response.created","response":{"id":"resp_cut"}}\n\n'
        )

        class Response:
            status = 200
            headers = {"content-type": "text/event-stream"}

            def __init__(self):
                self.payload = io.BytesIO(payload)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, size):
                return self.payload.read(size)

        urlopen = mock.Mock(return_value=Response())
        record_usage = mock.Mock()
        passthrough = ProviderResponsesPassthrough(
            ProviderResponsesPassthroughPorts(
                project_channel_context=lambda body: (body, {}),
                begin_channel_delivery=mock.Mock(),
                normalize_model=lambda _provider, _config, model: model,
                normalize_request=lambda _provider, _config, body: body,
                upstream_base=lambda _provider, _config: "https://example.test",
                join_url=lambda base, path: f"{base}{path}",
                headers=lambda *_args: {},
                urlopen=urlopen,
                timeout_seconds=lambda _config: 30.0,
                copy_response_headers=lambda *_args: None,
                record_usage=record_usage,
            )
        )
        handler = SimpleNamespace(
            headers={},
            wfile=io.BytesIO(),
            send_response=mock.Mock(),
            end_headers=mock.Mock(),
        )
        setattr(handler, REMOTE_BRIDGE_CONTEXT_ATTRIBUTE, True)

        with self.assertRaises(UpstreamStreamReadError) as caught:
            passthrough.forward(
                handler,
                "alitoken",
                {"responses_stream_truncation_retries": 1},
                {"model": "model", "input": [], "stream": True},
            )

        self.assertTrue(caught.exception.downstream_started)
        self.assertEqual(1, urlopen.call_count)
        self.assertEqual(payload, handler.wfile.getvalue())
        record_usage.assert_not_called()

    def test_bridge_responses_passthrough_does_not_record_usage(self):
        payload = (
            b"event: response.completed\n"
            b'data: {"type":"response.completed","response":{"id":"resp_ok",'
            b'"usage":{"input_tokens":3,"output_tokens":2}}}\n\n'
        )

        class Response:
            status = 200
            headers = {"content-type": "text/event-stream"}

            def __init__(self):
                self.payload = io.BytesIO(payload)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, size):
                return self.payload.read(size)

        urlopen = mock.Mock(return_value=Response())
        record_usage = mock.Mock()
        passthrough = ProviderResponsesPassthrough(
            ProviderResponsesPassthroughPorts(
                project_channel_context=lambda body: (body, {}),
                begin_channel_delivery=mock.Mock(),
                normalize_model=lambda _provider, _config, model: model,
                normalize_request=lambda _provider, _config, body: body,
                upstream_base=lambda _provider, _config: "https://example.test",
                join_url=lambda base, path: f"{base}{path}",
                headers=lambda *_args: {},
                urlopen=urlopen,
                timeout_seconds=lambda _config: 30.0,
                copy_response_headers=lambda *_args: None,
                record_usage=record_usage,
            )
        )
        handler = SimpleNamespace(
            headers={},
            wfile=io.BytesIO(),
            send_response=mock.Mock(),
            end_headers=mock.Mock(),
        )
        setattr(handler, REMOTE_BRIDGE_CONTEXT_ATTRIBUTE, True)

        passthrough.forward(
            handler,
            "alitoken",
            {"responses_stream_truncation_retries": 1},
            {"model": "model", "input": [], "stream": True},
        )

        self.assertEqual(1, urlopen.call_count)
        self.assertEqual(payload, handler.wfile.getvalue())
        record_usage.assert_not_called()

    def test_local_responses_passthrough_keeps_replay_wire_repair(self):
        captured = {}
        source = {
            "model": "model",
            "stream": False,
            "input": [
                {
                    "type": "reasoning",
                    "id": "msg_foreign",
                    "summary": [{"type": "summary_text", "text": "opaque"}],
                    "encrypted_content": None,
                },
                {"type": "message", "id": "msg_user", "role": "user"},
            ],
        }

        def urlopen(request, **_kwargs):
            captured["body"] = json.loads(request.data)
            return self._response(b'{"id":"resp","object":"response"}')

        passthrough = ProviderResponsesPassthrough(
            ProviderResponsesPassthroughPorts(
                project_channel_context=lambda body: (body, {}),
                begin_channel_delivery=mock.Mock(),
                normalize_model=lambda _provider, _config, model: model,
                normalize_request=lambda _provider, _config, body: body,
                upstream_base=lambda _provider, _config: "https://example.test",
                join_url=lambda base, path: f"{base}{path}",
                headers=lambda *_args: {},
                urlopen=urlopen,
                timeout_seconds=lambda _config: 30.0,
                copy_response_headers=lambda *_args: None,
            )
        )
        handler = SimpleNamespace(
            headers={},
            wfile=io.BytesIO(),
            send_response=mock.Mock(),
            end_headers=mock.Mock(),
        )

        passthrough.forward(handler, "vllm", {}, source)

        self.assertEqual([source["input"][1]], captured["body"]["input"])

    def test_bridge_model_catalog_is_dual_compatible_and_detail_is_strict(self):
        config = {
            "current_provider": "vllm",
            "remote_bridge": {"enabled": True},
            "providers": {
                "vllm": {
                    "current_model": "known-model",
                    "base_url": "http://127.0.0.1:19567/v1",
                }
            },
        }
        routing = RemoteBridgeRoutingService(
            lambda value: str(value).strip().lower(),
            parse_bool,
            {},
        )

        def resolve_route(*args, **kwargs):
            body = args[2] if len(args) > 2 else kwargs.get("body", {})
            if str(body.get("model") or "").endswith("/rejected-model"):
                raise RemoteBridgeRouteError("model is hidden")
            return routing.resolve(*args, **kwargs)

        def false(*_args, **_kwargs):
            return False
        write_json = mock.Mock()
        services = RouterHttpServices(
            core=RouterHttpCore(
                load_config=lambda: config,
                reject_external=false,
                get_current_provider=lambda source: (
                    source["current_provider"],
                    source["providers"][source["current_provider"]],
                ),
                parse_json_body=json.loads,
                is_client_disconnect=lambda _error: False,
                log=lambda *_args: None,
                observe_runtime=lambda *_args, **_kwargs: nullcontext(),
                request_body_policy=RouterRequestBodyPolicy(environment={}),
                remote_bridge=RouterHttpRemoteBridge(
                    routing.enabled,
                    resolve_route,
                    lambda _config: {},
                    lambda _handler, _config: True,
                ),
            ),
            get=RouterHttpGetEndpoints(
                false,
                false,
                false,
                false,
                false,
                false,
                false,
                false,
                false,
            ),
            post=RouterHttpPostEndpoints(
                false,
                false,
                false,
                false,
                false,
                false,
            ),
            presentation=RouterHttpPresentation(
                lambda *_args: "",
                lambda *_args: {},
                lambda *_args, **_kwargs: None,
                write_json,
                lambda *_args: [],
                lambda _provider, _config, model: model,
                lambda _provider, model, *_args: {"id": model},
                lambda *_args: [
                    {"id": "vllm/known-model", "object": "model"}
                ],
            ),
            errors=RouterHttpErrors(
                lambda *_args, **_kwargs: None,
                lambda *_args, **_kwargs: None,
            ),
        )

        class Handler(RouterHttpHandler):
            services_factory = staticmethod(lambda: services)

        list_handler = object.__new__(Handler)
        list_handler.path = "/v1/models?client_version=0.150.1"
        list_handler.headers = Headers({})

        list_handler.do_GET()

        write_json.assert_called_once_with(
            mock.ANY,
            {
                "object": "list",
                "data": [
                    {"id": "vllm/known-model", "object": "model"}
                ],
                "has_more": False,
                "models": [],
            },
        )

        write_json.reset_mock()
        handler = object.__new__(Handler)
        handler.path = "/v1/models/vllm/unknown-model"
        handler.headers = Headers({})

        handler.do_GET()

        write_json.assert_called_once_with(mock.ANY, mock.ANY, 404)
        self.assertEqual(
            "model_not_found",
            write_json.call_args.args[1]["error"]["code"],
        )

        write_json.reset_mock()
        known_handler = object.__new__(Handler)
        known_handler.path = "/v1/models/vllm/known-model"
        known_handler.headers = Headers({})

        known_handler.do_GET()

        write_json.assert_called_once_with(
            mock.ANY,
            {"id": "vllm/known-model", "object": "model"},
        )

        write_json.reset_mock()
        rejected_handler = object.__new__(Handler)
        rejected_handler.path = "/v1/models/vllm/rejected-model"
        rejected_handler.headers = Headers({})

        rejected_handler.do_GET()

        write_json.assert_called_once_with(mock.ANY, mock.ANY, 404)
        self.assertEqual(
            "model_not_found",
            write_json.call_args.args[1]["error"]["code"],
        )


if __name__ == "__main__":
    unittest.main()
