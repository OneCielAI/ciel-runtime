import io
import unittest
from types import SimpleNamespace
from unittest import mock

from ciel_runtime_support.architecture import ProviderConfig
from ciel_runtime_support.provider_adapters import PROVIDER_ADAPTERS, PROVIDER_ALIASES
from ciel_runtime_support.provider_responses_passthrough import (
    ProviderResponsesPassthrough,
    ProviderResponsesPassthroughPorts,
)
from ciel_runtime_support.providers.xai import XAI_MODEL_FALLBACK_IDS, XaiProviderAdapter


def config(model: str = "grok-4.6", **options: object) -> ProviderConfig:
    return ProviderConfig(
        name="xai",
        base_url="https://api.x.ai/v1",
        model=model,
        api_keys=("secret",),
        options=options,
    )


class XaiProviderAdapterTests(unittest.TestCase):
    def test_descriptor_uses_first_class_adapter_and_current_models(self) -> None:
        self.assertEqual("xai", PROVIDER_ALIASES["grok"])
        adapter = PROVIDER_ADAPTERS.create("xai")
        self.assertIsInstance(adapter, XaiProviderAdapter)
        defaults = adapter.default_configuration()
        self.assertEqual("grok-4.6", defaults["current_model"])
        self.assertEqual(list(XAI_MODEL_FALLBACK_IDS), defaults["custom_models"])

    def test_model_profiles_are_model_specific(self) -> None:
        adapter = XaiProviderAdapter()
        cases = {
            "grok-build-0.1": 256_000,
            "grok-4.6": 500_000,
            "grok-4.5": 500_000,
            "grok-4.3": 1_000_000,
            "grok-4.20-0309-reasoning": 1_000_000,
        }
        for model, expected in cases.items():
            with self.subTest(model=model):
                profile, _message = adapter.model_configuration_profile(config(model))
                self.assertEqual(expected, profile["context_window"])

    def test_responses_is_native_and_chat_remains_available(self) -> None:
        adapter = XaiProviderAdapter()
        self.assertEqual(
            frozenset({"openai_chat", "openai_responses"}),
            adapter.supported_protocols(config()),
        )
        self.assertEqual(
            "openai_responses",
            adapter.select_protocol("openai_responses", config()),
        )
        self.assertEqual(
            "openai_chat",
            adapter.select_protocol("anthropic_messages", config()),
        )
        self.assertEqual(
            "/v1/responses/compact",
            adapter.resolve_endpoint("openai_responses_compact", config()),
        )

    def test_46_supports_xhigh_but_45_clamps_to_high(self) -> None:
        adapter = XaiProviderAdapter()
        self.assertEqual(
            "xhigh",
            adapter.openai_reasoning_effort(
                config("grok-4.6"), "grok-4.6", {"reasoning_effort": "xhigh"}
            ),
        )
        self.assertEqual(
            "high",
            adapter.openai_reasoning_effort(
                config("grok-4.5"), "grok-4.5", {"reasoning_effort": "xhigh"}
            ),
        )

    def test_optional_conversation_affinity_header(self) -> None:
        headers = XaiProviderAdapter().build_headers(
            config(conversation_id="workspace-session"), "secret"
        )
        self.assertEqual("Bearer secret", headers["Authorization"])
        self.assertEqual("workspace-session", headers["x-grok-conv-id"])


class XaiResponsesCompactionPassthroughTests(unittest.TestCase):
    def test_compaction_payload_and_response_are_opaque(self) -> None:
        response_payload = b'{"output":[{"type":"compaction","encrypted_content":"sealed"}]}'
        response = mock.MagicMock()
        response.status = 200
        response.headers = {"content-type": "application/json"}
        response.read.side_effect = [response_payload, b""]
        response.__enter__.return_value = response
        captured: dict[str, object] = {}

        def urlopen(request, **_kwargs):
            captured["url"] = request.full_url
            captured["body"] = request.data
            return response

        service = ProviderResponsesPassthrough(
            ProviderResponsesPassthroughPorts(
                project_channel_context=lambda body: (body, body),
                begin_channel_delivery=lambda *_args: None,
                normalize_model=lambda _provider, _config, model: model,
                normalize_request=lambda _provider, _config, body: body,
                upstream_base=lambda _provider, _config: "https://api.x.ai/v1",
                join_url=lambda base, path: base.rstrip("/")
                + (path[3:] if base.rstrip("/").endswith("/v1") and path.startswith("/v1/") else path),
                headers=lambda *_args: {"Authorization": "Bearer secret"},
                urlopen=urlopen,
                timeout_seconds=lambda _config: 600.0,
                copy_response_headers=lambda *_args: None,
            )
        )
        handler = SimpleNamespace(
            headers={},
            wfile=io.BytesIO(),
            send_response=mock.Mock(),
            end_headers=mock.Mock(),
        )
        source = {
            "model": "grok-4.6",
            "input": [{"type": "reasoning", "encrypted_content": "original"}],
        }
        service.forward_compact(handler, "xai", {}, source)

        self.assertEqual("https://api.x.ai/v1/responses/compact", captured["url"])
        self.assertIn(b'"encrypted_content":"original"', captured["body"])
        self.assertEqual(response_payload, handler.wfile.getvalue())
        self.assertEqual(source["input"][0]["encrypted_content"], "original")


if __name__ == "__main__":
    unittest.main()
