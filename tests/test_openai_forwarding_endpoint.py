import io
import urllib.error
import unittest
from types import SimpleNamespace
from unittest import mock

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
from ciel_runtime_support.remote_bridge import (
    REMOTE_BRIDGE_CONFIG_MARKER,
    REMOTE_BRIDGE_CONTEXT_ATTRIBUTE,
)


class OpenAIForwardingEndpointTests(unittest.TestCase):
    @staticmethod
    def _failure_services(open_with_retry):
        response = mock.Mock()
        write_start = mock.Mock()
        write_blocks = mock.Mock(
            side_effect=lambda _handler, blocks, index: index + len(blocks)
        )
        write_stop = mock.Mock()
        services = OpenAIForwardServices(
            policy=OpenAIForwardPolicy("X-Test", lambda *_args: False),
            request=OpenAIForwardRequest(
                lambda *_args: None,
                lambda _provider, _config, body: body,
                lambda _provider, _config, model: str(model),
                lambda _provider, _config, model: model,
                lambda body, _config: body,
                lambda _provider: False,
                lambda *_args: "https://example.test/chat/completions",
                lambda _provider, model, _body, _config, stream: {
                    "model": model,
                    "messages": [],
                    "stream": stream,
                },
                lambda *_args: {},
            ),
            rate_limit=OpenAIForwardRateLimit(
                lambda *_args: (0.0, 0, 0),
                lambda *_args: "",
                lambda _body: 1,
                lambda _config: 1.0,
            ),
            advisor=OpenAIForwardAdvisor(
                lambda _config: False,
                lambda *_args: False,
                lambda *_args: "",
                lambda _provider, _config, _body, message, _model: message,
            ),
            streaming=OpenAIForwardStreaming(
                write_start,
                write_blocks,
                open_with_retry,
                lambda *_args, **_kwargs: {},
                lambda *_args, **_kwargs: True,
                write_stop,
            ),
            response=OpenAIForwardResponse(
                response.mark_success,
                response.mark_failed,
                response.write_activity,
                response.chat_to_anthropic,
                response.remember_tool_uses,
                response.prepend_text,
                response.write_message,
                response.write_json,
            ),
            hosted_tools=SimpleNamespace(
                prepare=lambda _provider, _config, body, _headers, _timeout: (
                    body,
                    SimpleNamespace(enabled=False),
                )
            ),
            log=lambda *_args: None,
        )
        return services, response, write_start, write_blocks, write_stop

    def test_forwarding_uses_provider_resolved_chat_endpoint(self):
        endpoint = "https://zcode.z.ai/api/v1/zcode-plan/chat/completions"
        opened_urls = []

        def open_with_retry(url, *_args, **_kwargs):
            opened_urls.append(url)
            raise urllib.error.HTTPError(url, 404, "not found", {}, io.BytesIO(b"404"))

        handler = SimpleNamespace(headers={}, wfile=io.BytesIO())
        response = mock.Mock()
        services = OpenAIForwardServices(
            policy=OpenAIForwardPolicy("X-Test", lambda *_args: True),
            request=OpenAIForwardRequest(
                lambda *_args: None,
                lambda _provider, _config, body: body,
                lambda _provider, _config, model: str(model),
                lambda _provider, _config, model: model,
                lambda body, _config: body,
                lambda _provider: False,
                lambda provider, _config, operation: (
                    endpoint
                    if (provider, operation) == ("zai-start-plan", "openai_chat")
                    else "unexpected"
                ),
                lambda _provider, model, _body, _config, stream: {
                    "model": model,
                    "stream": stream,
                },
                lambda *_args: {},
            ),
            rate_limit=OpenAIForwardRateLimit(
                lambda *_args: (0.0, 0, 0),
                lambda *_args: "",
                lambda _body: 1,
                lambda _config: 1.0,
            ),
            advisor=OpenAIForwardAdvisor(
                lambda _config: False,
                lambda *_args: False,
                lambda *_args: "",
                lambda _provider, _config, _body, message, _model: message,
            ),
            streaming=OpenAIForwardStreaming(
                lambda *_args, **_kwargs: None,
                lambda _handler, _blocks, index: index,
                open_with_retry,
                lambda *_args, **_kwargs: {},
                lambda *_args, **_kwargs: True,
                lambda *_args, **_kwargs: None,
            ),
            response=OpenAIForwardResponse(
                response.mark_success,
                response.mark_failed,
                response.write_activity,
                response.chat_to_anthropic,
                response.remember_tool_uses,
                response.prepend_text,
                response.write_message,
                response.write_json,
            ),
            hosted_tools=SimpleNamespace(
                prepare=lambda _provider, _config, body, _headers, _timeout: (
                    body,
                    SimpleNamespace(enabled=False),
                )
            ),
            log=lambda *_args: None,
        )

        forward_openai_compatible_chat(
            handler,
            "zai-start-plan",
            {"stream_enabled": True},
            {"model": "glm-5.3", "stream": True},
            services=services,
        )

        self.assertEqual([endpoint], opened_urls)

    def test_remote_stream_open_failure_returns_502_without_successful_sse(self):
        for error in (RuntimeError("transport broke"), ValueError("invalid stream")):
            with self.subTest(error=type(error).__name__):
                services, response, write_start, write_blocks, write_stop = (
                    self._failure_services(
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(error)
                    )
                )
                handler = SimpleNamespace(headers={}, wfile=io.BytesIO())
                setattr(handler, REMOTE_BRIDGE_CONTEXT_ATTRIBUTE, True)

                forward_openai_compatible_chat(
                    handler,
                    "kimi",
                    {
                        REMOTE_BRIDGE_CONFIG_MARKER: True,
                        "stream_initial_retries": 0,
                    },
                    {"model": "k3", "messages": [], "stream": True},
                    services=services,
                )

                response.write_json.assert_called_once()
                payload = response.write_json.call_args.args[1]
                self.assertEqual(502, response.write_json.call_args.args[2])
                self.assertEqual("api_error", payload["error"]["type"])
                write_start.assert_not_called()
                write_blocks.assert_not_called()
                write_stop.assert_not_called()
                self.assertEqual(b"", handler.wfile.getvalue())

    def test_remote_started_stream_failure_emits_error_without_message_stop(self):
        for error in (RuntimeError("transport broke"), ValueError("invalid stream")):
            with self.subTest(error=type(error).__name__):
                def fail_after_retry_notice(*args, **_kwargs):
                    args[7]("retrying")
                    raise error

                services, response, write_start, write_blocks, write_stop = (
                    self._failure_services(fail_after_retry_notice)
                )
                handler = SimpleNamespace(headers={}, wfile=io.BytesIO())
                setattr(handler, REMOTE_BRIDGE_CONTEXT_ATTRIBUTE, True)

                forward_openai_compatible_chat(
                    handler,
                    "kimi",
                    {
                        REMOTE_BRIDGE_CONFIG_MARKER: True,
                        "stream_initial_retries": 0,
                    },
                    {"model": "k3", "messages": [], "stream": True},
                    services=services,
                )

                response.write_json.assert_not_called()
                write_start.assert_called_once()
                write_blocks.assert_called_once()
                write_stop.assert_not_called()
                output = handler.wfile.getvalue().decode("utf-8")
                self.assertIn("event: error", output)
                self.assertIn('"type":"api_error"', output)
                self.assertNotIn("message_stop", output)


if __name__ == "__main__":
    unittest.main()
