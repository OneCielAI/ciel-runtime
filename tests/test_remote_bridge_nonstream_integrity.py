import unittest

from ciel_runtime_support.response_collection import (
    AnthropicCollectionProjection,
    AnthropicCollectionRequest,
    AnthropicCollectionServices,
    AnthropicCollectionTransport,
    collect_anthropic_message_for_responses,
)


class Handler:
    headers = {}
    path = "/v1/messages"

    def __init__(self, *, remote):
        self._ciel_runtime_remote_bridge_request = remote


class Response:
    def __init__(self, body):
        self.body = body
        self.closed = False

    def read(self):
        return self.body

    def close(self):
        self.closed = True


def services(response):
    return AnthropicCollectionServices(
        request=AnthropicCollectionRequest(
            normalize_thinking=lambda _provider, _config, body: body,
            normalize_system_roles=lambda _provider, _config, body: body,
            cap_body=lambda _provider, _config, body: body,
            apply_options=lambda _provider, _config, body: body,
            rehydrate_thinking=lambda _provider, _config, body: body,
            resolve_model=lambda _provider, _config, model: model,
            normalize_upstream_model=lambda _provider, _config, model: model,
            resolve_tool_models=lambda _provider, _config, body: body,
            normalize_model_options=lambda _provider, _config, body, _model: body,
            strip_internal_metadata=lambda body: body,
        ),
        transport=AnthropicCollectionTransport(
            provider_endpoint=lambda *_args: "https://provider.invalid/v1/messages",
            messages_query=lambda *_args: "",
            provider_headers=lambda *_args: {},
            apply_rate_limit=lambda *_args: (0.0, 0, 0),
            open_request_with_retry=lambda *_args, **_kwargs: response,
            request_timeout_seconds=lambda _config: 30.0,
        ),
        projection=AnthropicCollectionProjection(
            normalize_response_thinking=lambda _provider,
            _config,
            payload,
            _model: payload,
            append_synthetic_tasklist=lambda payload, *_args, **_kwargs: payload,
            prepend_text=lambda payload, _notice: payload,
            rate_limit_notice=lambda *_args: "",
        ),
        forwarded_headers=(),
    )


class RemoteBridgeNonstreamIntegrityTests(unittest.TestCase):
    body = {"model": "model", "messages": [{"role": "user", "content": "hi"}]}

    def collect(self, response, *, remote):
        return collect_anthropic_message_for_responses(
            Handler(remote=remote),
            "anthropic",
            {},
            dict(self.body),
            services=services(response),
        )

    def test_remote_invalid_utf8_fails_closed_and_closes_response(self):
        response = Response(
            b'{"type":"message","content":[{"type":"text","text":"bad '
            + bytes([0xFF])
            + b'"}]}'
        )

        with self.assertRaisesRegex(RuntimeError, "invalid UTF-8") as raised:
            self.collect(response, remote=True)

        self.assertIsInstance(raised.exception.__cause__, UnicodeDecodeError)
        self.assertTrue(response.closed)

    def test_local_invalid_utf8_keeps_legacy_replacement_decode(self):
        response = Response(
            b'{"type":"message","content":[{"type":"text","text":"bad '
            + bytes([0xFF])
            + b'"}]}'
        )

        payload = self.collect(response, remote=False)

        self.assertEqual("bad \ufffd", payload["content"][0]["text"])
        self.assertTrue(response.closed)

    def test_remote_valid_utf8_nonstream_response_is_unchanged(self):
        response = Response(
            b'{"type":"message","content":[{"type":"text","text":"ok"}]}'
        )

        payload = self.collect(response, remote=True)

        self.assertEqual("ok", payload["content"][0]["text"])
        self.assertTrue(response.closed)


if __name__ == "__main__":
    unittest.main()
