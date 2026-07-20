import unittest
from types import SimpleNamespace

from ciel_runtime_support.advisor_policy import (
    AdvisorShortcutController,
    AdvisorShortcutPorts,
)
from ciel_runtime_support.architecture import ProviderConfig
from ciel_runtime_support.providers.anthropic import AnthropicProviderAdapter
from ciel_runtime_support.providers.openrouter import OpenRouterProviderAdapter
from ciel_runtime_support.session_import import (
    ImportSessionHttpController,
    ImportSessionHttpPorts,
)


class LocalRequestControllerTests(unittest.TestCase):
    def test_import_session_anthropic_response_and_event(self):
        writes = []
        events = []
        controller = ImportSessionHttpController(
            ImportSessionHttpPorts(
                is_request=lambda _body: True,
                response_text=lambda runtime, _body: f"imported from {runtime}",
                load_config=lambda: {},
                current_alias=lambda _config: "alias-model",
                current_provider=lambda _config: ("provider", {}),
                estimate_tokens=lambda _value: 4,
                write_openai=lambda *args, **kwargs: None,
                write_anthropic=lambda *args: writes.append(args),
                publish_event=lambda **kwargs: events.append(kwargs),
            )
        )
        self.assertTrue(
            controller.handle(object(), {"stream": False}, client_runtime="claude")
        )
        self.assertEqual("alias-model", writes[0][1])
        self.assertEqual("imported from claude", writes[0][2])
        self.assertEqual("import_session.short_circuit", events[0]["category"])

    def test_import_session_openai_response_preserves_source_body(self):
        writes = []
        controller = ImportSessionHttpController(
            ImportSessionHttpPorts(
                is_request=lambda _body: True,
                response_text=lambda _runtime, _body: "imported",
                load_config=lambda: {},
                current_alias=lambda _config: "alias-model",
                current_provider=lambda _config: ("provider", {}),
                estimate_tokens=lambda _value: 4,
                write_openai=lambda *args, **kwargs: writes.append((args, kwargs)),
                write_anthropic=lambda *args: None,
                publish_event=lambda **kwargs: None,
            )
        )
        source = {"stream": False}
        self.assertTrue(
            controller.handle(
                object(),
                {},
                client_runtime="codex",
                response_format="openai",
                source_body=source,
            )
        )
        self.assertIs(source, writes[0][1]["source_body"])
        self.assertFalse(writes[0][1]["stream"])
        self.assertEqual(4, writes[0][0][1]["usage"]["output_tokens"])

    def test_advisor_controller_obeys_adapter_owned_interception_policy(self):
        config = ProviderConfig(
            name="anthropic", base_url="https://api.anthropic.com", model="model"
        )
        self.assertFalse(AnthropicProviderAdapter().intercepts_advisor_shortcut(config))
        self.assertTrue(OpenRouterProviderAdapter().intercepts_advisor_shortcut(config))

    def test_advisor_controller_maps_upstream_error_to_local_response(self):
        writes = []

        def fail(*_args, **_kwargs):
            raise RuntimeError("unavailable")

        controller = AdvisorShortcutController(
            AdvisorShortcutPorts(
                should_intercept=lambda _provider, _config: True,
                is_request=lambda _body: True,
                provider_supported=lambda _provider: True,
                call_text=fail,
                write_anthropic=lambda *args: writes.append(args),
                load_config=lambda: {},
                current_alias=lambda _config: "alias",
            )
        )
        handler = SimpleNamespace(headers={})
        self.assertTrue(
            controller.handle(
                handler,
                "provider",
                {"advisor_model": "advisor-model"},
                {"stream": False},
            )
        )
        self.assertIn("RuntimeError: unavailable", writes[0][2])


if __name__ == "__main__":
    unittest.main()
