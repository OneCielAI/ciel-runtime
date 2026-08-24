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


class OpenAIForwardingEndpointTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
