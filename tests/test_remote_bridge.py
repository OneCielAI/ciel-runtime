import copy
import unittest
from types import SimpleNamespace
from unittest import mock

from ciel_runtime_support.config_value_codec import parse_bool
from ciel_runtime_support.ollama_wire_projection import (
    OllamaWireProjection,
    OllamaWireProjectionPorts,
)
from ciel_runtime_support.provider_request_builder import (
    OllamaRequestPorts,
    OpenAIRequestPorts,
    ProviderOptionPorts,
    ProviderRequestBudget,
    ProviderRequestBuilder,
)
from ciel_runtime_support.remote_bridge import (
    API_KEY_HEADER,
    MODEL_PICKER_ENABLED_METADATA_KEY,
    MODEL_HEADER,
    PROVIDER_HEADER,
    PUBLIC_MODEL_ID_METADATA_KEY,
    REMOTE_BRIDGE_CONFIG_MARKER,
    REQUEST_API_KEY_MARKER,
    RemoteBridgeRouteError,
    RemoteBridgeRoutingService,
)
from ciel_runtime_support.remote_bridge_cli import (
    RemoteBridgeCliController,
    RemoteBridgeCliPorts,
)
from ciel_runtime_support.remote_bridge_runtime import RemoteBridgeRuntimeApi


ALIASES = {
    "anthropic": "anthropic",
    "openrouter": "openrouter",
    "ollama-cloud": "ollama-cloud",
    "ollama_cloud": "ollama-cloud",
    "github-copilot-oauth": "github-copilot-oauth",
    "copilot-oauth": "github-copilot-oauth",
    "codex": "codex",
    "agy": "agy",
    "zai-start-plan": "zai-start-plan",
}


def normalize_provider(value: str) -> str:
    key = value.strip().lower()
    if key not in ALIASES:
        raise SystemExit(f"Unknown provider: {value}\nKnown: {', '.join(ALIASES)}")
    return ALIASES[key]


def config() -> dict:
    return {
        "current_provider": "anthropic",
        "remote_bridge": {"enabled": True, "host": "0.0.0.0"},
        "providers": {
            "anthropic": {"current_model": "claude-default", "api_key": "stored-a"},
            "openrouter": {"current_model": "default/model", "api_key": "stored-o"},
            "ollama-cloud": {"current_model": "kimi-k3", "api_key": "stored-k"},
            "github-copilot-oauth": {"current_model": "gpt-5.6-sol"},
            "codex": {"current_model": "gpt-5.6-sol"},
            "agy": {"current_model": "gemini-default"},
            "zai-start-plan": {"current_model": "glm-4.7"},
        },
    }


class RemoteBridgeRoutingTests(unittest.TestCase):
    def service(self, environ=None) -> RemoteBridgeRoutingService:
        return RemoteBridgeRoutingService(
            normalize_provider,
            parse_bool,
            environ or {},
        )

    def test_enabled_uses_config_and_environment_override(self):
        self.assertTrue(self.service().enabled(config()))
        self.assertFalse(
            self.service({"CIEL_RUNTIME_REMOTE_BRIDGE": "off"}).enabled(config())
        )

    def test_model_route_selects_provider_without_mutating_inputs(self):
        source_config = config()
        source_body = {"model": "ollama-cloud/kimi-k3", "messages": []}
        snapshot = copy.deepcopy(source_config)

        route = self.service().resolve(source_config, {}, source_body)

        self.assertEqual("ollama-cloud", route.provider)
        self.assertEqual("kimi-k3", route.provider_config["current_model"])
        self.assertEqual("kimi-k3", route.body["model"])
        self.assertTrue(route.provider_config[REMOTE_BRIDGE_CONFIG_MARKER])
        self.assertEqual(snapshot, source_config)
        self.assertEqual("ollama-cloud/kimi-k3", source_body["model"])

    def test_headers_override_route_and_api_key_is_request_scoped(self):
        source_config = config()
        route = self.service().resolve(
            source_config,
            {
                PROVIDER_HEADER: "ollama_cloud",
                MODEL_HEADER: "kimi-k2.6",
                API_KEY_HEADER: "request-secret",
            },
            {"model": "ignored"},
        )

        self.assertEqual("ollama-cloud", route.provider)
        self.assertEqual("kimi-k2.6", route.body["model"])
        self.assertEqual("request-secret", route.provider_config["api_key"])
        self.assertEqual(["request-secret"], route.provider_config["api_keys"])
        self.assertTrue(route.provider_config[REQUEST_API_KEY_MARKER])
        self.assertEqual("stored-k", source_config["providers"]["ollama-cloud"]["api_key"])

    def test_ciel_body_controls_are_removed_before_forwarding(self):
        route = self.service().resolve(
            config(),
            {},
            {
                "model": "ignored",
                "ciel": {
                    "provider": "anthropic",
                    "model": "claude-selected",
                    "api_key": "request-secret",
                },
            },
        )

        self.assertNotIn("ciel", route.body)
        self.assertEqual("claude-selected", route.body["model"])
        self.assertEqual("request-secret", route.provider_config["api_key"])

    def test_generation_protocol_defaults_to_non_streaming_when_omitted(self):
        for path in (
            "/v1/chat/completions",
            "/v1/messages",
            "/v1/responses",
        ):
            with self.subTest(path=path):
                route = self.service().resolve(
                    config(),
                    {},
                    {"model": "anthropic/claude-default"},
                    path,
                )
                self.assertIs(False, route.body["stream"])

        explicit = self.service().resolve(
            config(),
            {},
            {"model": "anthropic/claude-default", "stream": True},
            "/v1/responses",
        )
        self.assertIs(True, explicit.body["stream"])

        token_count = self.service().resolve(
            config(),
            {},
            {"model": "anthropic/claude-default"},
            "/v1/messages/count_tokens",
        )
        self.assertNotIn("stream", token_count.body)

    def test_explicit_provider_keeps_slash_model_id_opaque(self):
        route = self.service().resolve(
            config(),
            {PROVIDER_HEADER: "openrouter"},
            {"model": "anthropic/claude-sonnet-4"},
        )

        self.assertEqual("openrouter", route.provider)
        self.assertEqual("anthropic/claude-sonnet-4", route.body["model"])

    def test_unknown_provider_is_rejected_without_echoing_key(self):
        with self.assertRaisesRegex(RemoteBridgeRouteError, "Unknown provider") as raised:
            self.service().resolve(
                config(),
                {PROVIDER_HEADER: "missing", API_KEY_HEADER: "request-secret"},
                {},
            )
        self.assertNotIn("request-secret", str(raised.exception))

    def test_copilot_oauth_credential_must_come_from_router_host(self):
        route = self.service().resolve(
            config(),
            {},
            {"model": "github-copilot-oauth/gpt-5.6-sol"},
        )

        self.assertEqual("github-copilot-oauth", route.provider)
        self.assertNotIn("api_key", route.provider_config)
        with self.assertRaisesRegex(
            RemoteBridgeRouteError,
            "managed by the router host",
        ):
            self.service().resolve(
                config(),
                {API_KEY_HEADER: "remote-oauth-token"},
                {"model": "github-copilot-oauth/gpt-5.6-sol"},
            )

    def test_client_local_runtime_providers_are_rejected(self):
        source = config()
        for provider, model in (
            ("codex", "gpt-5.6-sol"),
            ("agy", "gemini-default"),
            ("zai-start-plan", "glm-4.7"),
        ):
            with self.subTest(provider=provider), self.assertRaisesRegex(
                RemoteBridgeRouteError,
                "client-local runtime authentication",
            ):
                self.service().resolve(
                    source,
                    {},
                    {"model": f"{provider}/{model}"},
                )


class RemoteBridgeRequestIsolationTests(unittest.TestCase):
    def builder(self, *, configured_output=None, ollama_apply_optional=None):
        compact_anthropic = mock.Mock(side_effect=AssertionError("must not compact"))
        compact_messages = mock.Mock(side_effect=AssertionError("must not compact"))
        cap_output = mock.Mock(side_effect=AssertionError("must not cap"))
        write_usage = mock.Mock(side_effect=AssertionError("must not persist usage"))
        repair_tools = mock.Mock(side_effect=AssertionError("must not repair"))
        configured_output = configured_output or (
            lambda _config, body, *_args: int(body.get("max_tokens") or 0)
        )
        ollama_apply_optional = ollama_apply_optional or (
            lambda request, *_args, **_kwargs: request
        )
        builder = ProviderRequestBuilder(
            ProviderRequestBudget(
                context_limit=lambda *_args, **_kwargs: 32768,
                positive_int=lambda value: value if isinstance(value, int) else 0,
                configured_output=configured_output,
                cap_output_ratio=lambda *_args, **_kwargs: 1,
                reserve=lambda *_args, **_kwargs: 1024,
                compact_anthropic=compact_anthropic,
                compact_messages=compact_messages,
                compact_kind=lambda _body: "codex",
                cap_output=cap_output,
                write_usage=write_usage,
            ),
            OllamaRequestPorts(
                messages=lambda body, **_kwargs: copy.deepcopy(body["messages"]),
                tools=lambda value: copy.deepcopy(value or []),
                context_limit=lambda _config: 32768,
                num_ctx=lambda *_args, **_kwargs: 32768,
                apply_optional=ollama_apply_optional,
            ),
            OpenAIRequestPorts(
                messages=lambda body, **_kwargs: copy.deepcopy(body["messages"]),
                tools=lambda value: copy.deepcopy(value or []),
                context_limit=lambda *_args: 32768,
                reasoning_passback=lambda *_args: False,
                repair_tools=repair_tools,
                reasoning_effort=lambda *_args: None,
                sampling_allowed=lambda *_args: True,
                omit_tool_choice=lambda *_args: False,
                tool_choice=lambda value: value,
                normalize_request=lambda _provider, _config, request: request,
            ),
            ProviderOptionPorts(
                sampling_providers=frozenset(),
                sampling_options=(),
                anthropic_runtime_hints=lambda _model: {},
                log=lambda *_args: None,
            ),
        )
        return builder, {
            "compact_anthropic": compact_anthropic,
            "compact_messages": compact_messages,
            "cap_output": cap_output,
            "write_usage": write_usage,
            "repair_tools": repair_tools,
        }

    def test_remote_provider_builds_never_compact_cap_or_persist_host_state(self):
        builder, spies = self.builder()
        config_value = {REMOTE_BRIDGE_CONFIG_MARKER: True}
        body = {
            "model": "remote-model",
            "messages": [{"role": "user", "content": "ORIGINAL"}],
            "max_tokens": 2048,
        }

        anthropic = builder.cap_anthropic_body("openrouter", config_value, body)
        ollama = builder.ollama_chat(
            "remote-model", body, config_value, provider="ollama-cloud"
        )
        chat = builder.openai_chat(
            "openrouter", "remote-model", body, config_value
        )

        self.assertEqual("ORIGINAL", anthropic["messages"][0]["content"])
        self.assertEqual("ORIGINAL", ollama["messages"][0]["content"])
        self.assertEqual("ORIGINAL", chat["messages"][0]["content"])
        for spy in spies.values():
            spy.assert_not_called()

    def test_remote_output_budget_uses_explicit_client_value_not_host_cap(self):
        configured_output = mock.Mock(return_value=100)
        wire = OllamaWireProjection(
            OllamaWireProjectionPorts(
                think_value=lambda *_args: None,
                positive_int=lambda value: (
                    value if isinstance(value, int) and value > 0 else None
                ),
            )
        )
        builder, _spies = self.builder(
            configured_output=configured_output,
            ollama_apply_optional=wire.apply,
        )
        config_value = {
            REMOTE_BRIDGE_CONFIG_MARKER: True,
            "max_output_tokens": 100,
            "output_tokens_explicit": True,
            "ollama_options": {"num_predict": 100},
            "ollama_explicit_options": ["num_predict"],
        }
        body = {
            "model": "remote-model",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1000,
        }

        chat = builder.openai_chat(
            "openrouter", "remote-model", body, config_value
        )
        ollama = builder.ollama_chat(
            "remote-model", body, config_value, provider="ollama-cloud"
        )

        self.assertEqual(1000, chat["max_tokens"])
        self.assertEqual(1000, ollama["options"]["num_predict"])
        configured_output.assert_not_called()

    def test_remote_output_budget_omission_does_not_inject_host_default(self):
        configured_output = mock.Mock(return_value=100)
        wire = OllamaWireProjection(
            OllamaWireProjectionPorts(
                think_value=lambda *_args: None,
                positive_int=lambda value: (
                    value if isinstance(value, int) and value > 0 else None
                ),
            )
        )
        builder, _spies = self.builder(
            configured_output=configured_output,
            ollama_apply_optional=wire.apply,
        )
        config_value = {
            REMOTE_BRIDGE_CONFIG_MARKER: True,
            "max_output_tokens": 100,
            "output_tokens_explicit": True,
            "ollama_options": {"num_predict": 100},
            "ollama_explicit_options": ["num_predict"],
        }
        body = {
            "model": "remote-model",
            "messages": [{"role": "user", "content": "hi"}],
        }

        chat = builder.openai_chat(
            "openrouter", "remote-model", body, config_value
        )
        ollama = builder.ollama_chat(
            "remote-model", body, config_value, provider="ollama-cloud"
        )

        self.assertNotIn("max_tokens", chat)
        self.assertNotIn("options", ollama)
        configured_output.assert_not_called()


class RemoteBridgeCliTests(unittest.TestCase):
    def controller(self, effective_enabled=None):
        state = {"config": config(), "token": "", "served": 0, "output": []}

        def save(value):
            state["config"] = copy.deepcopy(value)

        def ensure_token():
            state["token"] = state["token"] or "bridge-token"
            return state["token"]

        controller = RemoteBridgeCliController(
            RemoteBridgeCliPorts(
                load_config=lambda: copy.deepcopy(state["config"]),
                save_config=save,
                ensure_token=ensure_token,
                token=lambda: state["token"],
                serve=lambda _args: state.__setitem__("served", state["served"] + 1),
                output=state["output"].append,
                port=9467,
                effective_enabled=(
                    effective_enabled
                    or (lambda source: bool(source["remote_bridge"]["enabled"]))
                ),
            )
        )
        return controller, state

    def test_enable_persists_host_and_generates_token(self):
        controller, state = self.controller()
        state["config"]["remote_bridge"]["enabled"] = False

        self.assertEqual(
            0,
            controller.run(SimpleNamespace(action="enable", host="192.0.2.4")),
        )

        self.assertTrue(state["config"]["remote_bridge"]["enabled"])
        self.assertEqual("192.0.2.4", state["config"]["remote_bridge"]["host"])
        self.assertEqual("bridge-token", state["token"])
        self.assertEqual(0, state["served"])

    def test_serve_enters_foreground_server(self):
        controller, state = self.controller()

        controller.run(SimpleNamespace(action="serve", host=None))

        self.assertEqual(1, state["served"])

    def test_status_never_prints_token_value(self):
        controller, state = self.controller()
        state["token"] = "bridge-token"

        controller.run(SimpleNamespace(action="status", host=None))

        rendered = "\n".join(state["output"])
        self.assertIn("configured", rendered)
        self.assertNotIn("bridge-token", rendered)

    def test_status_uses_effective_environment_override(self):
        controller, state = self.controller(effective_enabled=lambda _config: True)
        state["config"]["remote_bridge"]["enabled"] = False

        controller.run(SimpleNamespace(action="status", host=None))

        self.assertIn("remote bridge: enabled", "\n".join(state["output"]))

    def test_enable_reports_environment_override(self):
        controller, state = self.controller(effective_enabled=lambda _config: False)
        state["config"]["remote_bridge"]["enabled"] = False

        controller.run(SimpleNamespace(action="enable", host=None))

        rendered = "\n".join(state["output"])
        self.assertIn("remote bridge: disabled", rendered)
        self.assertIn("environment override", rendered)

    def test_disable_reports_environment_override(self):
        controller, state = self.controller(effective_enabled=lambda _config: True)

        controller.run(SimpleNamespace(action="disable", host=None))

        rendered = "\n".join(state["output"])
        self.assertIn("remote bridge: enabled", rendered)
        self.assertIn("environment override", rendered)


class RemoteBridgeRuntimeApiTests(unittest.TestCase):
    @staticmethod
    def copilot_picker_metadata():
        return {
            "gpt-5.6-sol": {
                MODEL_PICKER_ENABLED_METADATA_KEY: True,
                "supported_endpoints": ["/responses", "ws:/responses"],
            },
            "copilot-search-a": {
                MODEL_PICKER_ENABLED_METADATA_KEY: False,
                "supported_endpoints": ["/chat/completions"],
            },
            "mai-code-1-flash-picker": {
                MODEL_PICKER_ENABLED_METADATA_KEY: True,
                PUBLIC_MODEL_ID_METADATA_KEY: "mai-code-1-flash",
                "supported_endpoints": ["/responses"],
            },
            "mai-code-1-flash": {
                MODEL_PICKER_ENABLED_METADATA_KEY: False,
                "supported_endpoints": ["/responses"],
            },
        }

    def api(self, *, cached_models=None, model_info=None):
        cached_by_provider = cached_models or {}
        info_by_provider = (
            {
                "github-copilot-oauth": {
                    "gpt-5.6-sol": {
                        "supported_endpoints": [
                            "/responses",
                            "ws:/responses",
                        ]
                    }
                }
            }
            if model_info is None
            else model_info
        )
        return RemoteBridgeRuntimeApi(
            normalize_provider,
            parse_bool,
            {},
            {
                "anthropic": "Anthropic",
                "openrouter": "OpenRouter",
                "github-copilot-oauth": "GitHub Copilot OAuth",
                "codex": "Codex Native",
                "agy": "AGY",
            },
            lambda provider, provider_config: cached_by_provider.get(
                provider,
                [provider_config["current_model"]],
            ),
            lambda provider, model, _config: {
                "id": model,
                "object": "model",
                "ciel_runtime": {
                    "provider": provider,
                    "upstream_model": model,
                },
            },
            lambda provider, model: f"{provider}-{model}",
            lambda source: (
                source["current_provider"],
                source["providers"][source["current_provider"]],
            ),
            lambda _provider, provider_config: bool(provider_config.get("api_key")),
            lambda provider, _config: info_by_provider.get(provider, {}),
        )

    def test_model_catalog_uses_provider_prefixed_ids(self):
        objects = self.api().model_objects(config())

        self.assertIn("anthropic/claude-default", [item["id"] for item in objects])
        self.assertIn(
            "openrouter/default/model",
            [item["id"] for item in objects],
        )
        self.assertFalse(
            any(
                item["id"].startswith(("codex/", "agy/"))
                for item in objects
            )
        )
        self.assertEqual(
            [],
            self.api().model_objects(
                config(),
                {PROVIDER_HEADER: "codex"},
            ),
        )
        copilot = next(
            item
            for item in objects
            if item["id"] == "github-copilot-oauth/gpt-5.6-sol"
        )
        self.assertEqual(
            ["/responses", "ws:/responses"],
            copilot["ciel_runtime"]["supported_endpoints"],
        )

    def test_route_projects_cached_endpoint_metadata_into_request_config(self):
        route = self.api().resolve(
            config(),
            {},
            {"model": "github-copilot-oauth/gpt-5.6-sol"},
            "/v1/chat/completions",
        )

        self.assertEqual(
            ["/responses", "ws:/responses"],
            route.provider_config["_ciel_model_metadata"][
                "supported_endpoints"
            ],
        )

    def test_picker_catalog_exposes_only_visible_models_and_public_aliases(self):
        api = self.api(
            cached_models={
                "github-copilot-oauth": [
                    "gpt-5.6-sol",
                    "copilot-search-a",
                    "mai-code-1-flash-picker",
                    "mai-code-1-flash",
                ]
            },
            model_info={
                "github-copilot-oauth": self.copilot_picker_metadata()
            },
        )

        objects = api.model_objects(
            config(),
            {PROVIDER_HEADER: "github-copilot-oauth"},
        )

        self.assertEqual(
            [
                "github-copilot-oauth/gpt-5.6-sol",
                "github-copilot-oauth/mai-code-1-flash",
            ],
            [item["id"] for item in objects],
        )
        self.assertFalse(
            any("copilot-search" in item["id"] for item in objects)
        )
        self.assertFalse(any("-picker" in item["id"] for item in objects))
        mai = objects[1]
        self.assertEqual(
            "mai-code-1-flash-picker",
            mai["ciel_runtime"]["upstream_model"],
        )

    def test_picker_public_alias_resolves_to_upstream_wire_model(self):
        api = self.api(
            cached_models={
                "github-copilot-oauth": [
                    "gpt-5.6-sol",
                    "copilot-search-a",
                    "mai-code-1-flash-picker",
                    "mai-code-1-flash",
                ]
            },
            model_info={
                "github-copilot-oauth": self.copilot_picker_metadata()
            },
        )

        route = api.resolve(
            config(),
            {},
            {"model": "github-copilot-oauth/mai-code-1-flash"},
            "/v1/responses",
        )

        self.assertEqual("mai-code-1-flash-picker", route.body["model"])
        self.assertEqual(
            "mai-code-1-flash-picker",
            route.provider_config["current_model"],
        )
        self.assertTrue(
            route.provider_config["_ciel_model_metadata"][
                MODEL_PICKER_ENABLED_METADATA_KEY
            ]
        )

        detail_route = api.resolve(
            config(),
            {},
            {"model": "github-copilot-oauth/mai-code-1-flash"},
            "/v1/models/github-copilot-oauth/mai-code-1-flash",
        )
        self.assertEqual("mai-code-1-flash", detail_route.body["model"])

    def test_picker_catalog_rejects_hidden_and_unknown_direct_models(self):
        api = self.api(
            cached_models={
                "github-copilot-oauth": [
                    "gpt-5.6-sol",
                    "copilot-search-a",
                    "mai-code-1-flash-picker",
                    "mai-code-1-flash",
                ]
            },
            model_info={
                "github-copilot-oauth": self.copilot_picker_metadata()
            },
        )

        for model, path in (
            ("copilot-search-a", "/v1/responses"),
            ("unknown-model", "/v1/responses"),
            (
                "copilot-search-a",
                "/v1/models/github-copilot-oauth/copilot-search-a",
            ),
        ):
            with self.subTest(model=model, path=path):
                with self.assertRaisesRegex(
                    RemoteBridgeRouteError,
                    "not available through Remote Bridge",
                ):
                    api.resolve(
                        config(),
                        {},
                        {"model": f"github-copilot-oauth/{model}"},
                        path,
                    )

    def test_catalog_without_picker_metadata_keeps_cold_cache_fallback(self):
        api = self.api(
            cached_models={
                "github-copilot-oauth": [
                    "gpt-5.6-sol",
                    "mai-code-1-flash",
                ]
            },
            model_info={"github-copilot-oauth": {}},
        )

        objects = api.model_objects(
            config(),
            {PROVIDER_HEADER: "github-copilot-oauth"},
        )
        route = api.resolve(
            config(),
            {},
            {"model": "github-copilot-oauth/mai-code-1-flash"},
            "/v1/responses",
        )

        self.assertEqual(
            [
                "github-copilot-oauth/gpt-5.6-sol",
                "github-copilot-oauth/mai-code-1-flash",
            ],
            [item["id"] for item in objects],
        )
        self.assertEqual("mai-code-1-flash", route.body["model"])

    def test_status_reports_credential_presence_without_values(self):
        payload = self.api().status_payload(config())

        self.assertEqual("remote_bridge", payload["mode"])
        self.assertTrue(payload["enabled"])
        self.assertTrue(payload["providers"][0]["credential_configured"])
        self.assertNotIn("stored-a", str(payload))
        copilot = next(
            item
            for item in payload["providers"]
            if item["id"] == "github-copilot-oauth"
        )
        self.assertEqual("router_host_oauth", copilot["credential_source"])
        self.assertFalse(
            {"codex", "agy"}
            & {item["id"] for item in payload["providers"]}
        )


if __name__ == "__main__":
    unittest.main()
