"""Request-scoped routing for the network-facing Ciel LLM bridge."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


REMOTE_LLM_PATHS = frozenset(
    {
        "/v1/chat/completions",
        "/v1/messages",
        "/v1/messages/count_tokens",
        "/v1/responses",
        "/v1/responses/compact",
    }
)
REMOTE_GENERATION_PATHS = frozenset(
    {"/v1/chat/completions", "/v1/messages", "/v1/responses"}
)

PROVIDER_HEADER = "x-ciel-runtime-provider"
MODEL_HEADER = "x-ciel-runtime-model"
API_KEY_HEADER = "x-ciel-runtime-api-key"
REQUEST_API_KEY_MARKER = "_ciel_remote_request_api_key"
REMOTE_BRIDGE_CONFIG_MARKER = "_ciel_remote_bridge_request"
REMOTE_BRIDGE_CONTEXT_ATTRIBUTE = "_ciel_runtime_remote_bridge_request"
MODEL_PICKER_ENABLED_METADATA_KEY = "model_picker_enabled"
PUBLIC_MODEL_ID_METADATA_KEY = "public_model_id"
ROUTER_MANAGED_CREDENTIAL_PROVIDERS = frozenset({"github-copilot-oauth"})
REMOTE_BRIDGE_INCOMPATIBLE_PROVIDERS = frozenset(
    {"agy", "codex", "zai-start-plan"}
)


class RemoteBridgeRouteError(ValueError):
    """A request selected a route the bridge cannot serve."""


@dataclass(frozen=True, slots=True)
class RemoteBridgeRoute:
    provider: str
    provider_config: dict[str, Any]
    body: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RemoteBridgeRoutingService:
    normalize_provider: Callable[[str], str]
    parse_bool: Callable[[Any, bool], bool]
    environ: Mapping[str, str]

    def enabled(self, config: Mapping[str, Any]) -> bool:
        override = str(self.environ.get("CIEL_RUNTIME_REMOTE_BRIDGE") or "").strip()
        if override:
            return self.parse_bool(override, False)
        settings = config.get("remote_bridge")
        return bool(
            isinstance(settings, Mapping)
            and self.parse_bool(settings.get("enabled"), False)
        )

    def resolve(
        self,
        config: dict[str, Any],
        headers: Any | None,
        body: dict[str, Any],
        path: str = "",
    ) -> RemoteBridgeRoute:
        providers = config.get("providers")
        if not isinstance(providers, dict):
            raise RemoteBridgeRouteError("Ciel Runtime has no configured providers")

        default_provider = self._normalized_provider(
            str(config.get("current_provider") or "")
        )
        if default_provider not in providers:
            raise RemoteBridgeRouteError(
                f"Default provider is not configured: {default_provider}"
            )

        projected_body = copy.deepcopy(body)
        controls = projected_body.pop("ciel", None)
        if not isinstance(controls, Mapping):
            controls = {}
        if path in REMOTE_GENERATION_PATHS and "stream" not in projected_body:
            projected_body["stream"] = False

        provider_value = self._header(headers, PROVIDER_HEADER) or str(
            controls.get("provider") or ""
        ).strip()
        requested_model = self._header(headers, MODEL_HEADER) or str(
            controls.get("model") or projected_body.get("model") or ""
        ).strip()

        routed_provider, routed_model = self._split_model_route(
            requested_model,
            providers,
        )
        if provider_value:
            provider = self._normalized_provider(provider_value)
        elif routed_provider:
            provider = routed_provider
        else:
            provider = default_provider
        if provider not in providers:
            raise RemoteBridgeRouteError(f"Provider is not configured: {provider}")
        if provider in REMOTE_BRIDGE_INCOMPATIBLE_PROVIDERS:
            raise RemoteBridgeRouteError(
                f"Provider depends on client-local runtime authentication and "
                f"is not available through Remote Bridge: {provider}"
            )

        model = (
            routed_model
            if routed_provider and (not provider_value or routed_provider == provider)
            else requested_model
        )

        provider_config = copy.deepcopy(providers[provider])
        if not isinstance(provider_config, dict):
            raise RemoteBridgeRouteError(
                f"Provider configuration is invalid: {provider}"
            )
        provider_config[REMOTE_BRIDGE_CONFIG_MARKER] = True
        if model:
            provider_config["current_model"] = model
            projected_body["model"] = model

        api_key = self._header(headers, API_KEY_HEADER) or str(
            controls.get("api_key") or ""
        ).strip()
        if api_key:
            if provider in ROUTER_MANAGED_CREDENTIAL_PROVIDERS:
                raise RemoteBridgeRouteError(
                    f"Provider credentials are managed by the router host: {provider}"
                )
            provider_config["api_key"] = api_key
            provider_config["api_keys"] = [api_key]
            provider_config[REQUEST_API_KEY_MARKER] = True

        return RemoteBridgeRoute(provider, provider_config, projected_body)

    def route_from_model(
        self,
        config: dict[str, Any],
        model: str,
    ) -> tuple[str, dict[str, Any], str]:
        route = self.resolve(config, None, {"model": model})
        return (
            route.provider,
            route.provider_config,
            str(route.body.get("model") or ""),
        )

    def _split_model_route(
        self,
        model: str,
        providers: Mapping[str, Any],
    ) -> tuple[str, str]:
        if "/" not in model:
            return "", model
        candidate, upstream_model = model.split("/", 1)
        if not candidate or not upstream_model:
            return "", model
        try:
            provider = self._normalized_provider(candidate)
        except RemoteBridgeRouteError:
            return "", model
        return (provider, upstream_model) if provider in providers else ("", model)

    def _normalized_provider(self, value: str) -> str:
        try:
            return self.normalize_provider(value)
        except SystemExit as exc:
            message = str(exc).splitlines()[0].strip()
            raise RemoteBridgeRouteError(message) from None

    @staticmethod
    def _header(headers: Any | None, name: str) -> str:
        if headers is None:
            return ""
        try:
            value = headers.get(name)
            if value is not None:
                return str(value).strip()
            for raw_name, raw_value in headers.items():
                if str(raw_name).casefold() == name:
                    return str(raw_value).strip()
        except (AttributeError, TypeError):
            return ""
        return ""


def remote_bridge_path_allowed(path: str) -> bool:
    normalized = str(path or "").split("?", 1)[0]
    return (
        normalized in REMOTE_LLM_PATHS
        or normalized in {"/ca/bridge", "/v1/models"}
        or normalized.startswith("/v1/models/")
    )


def is_remote_bridge_request(handler: Any) -> bool:
    return getattr(handler, REMOTE_BRIDGE_CONTEXT_ATTRIBUTE, False) is True


__all__ = [
    "API_KEY_HEADER",
    "MODEL_PICKER_ENABLED_METADATA_KEY",
    "MODEL_HEADER",
    "PROVIDER_HEADER",
    "PUBLIC_MODEL_ID_METADATA_KEY",
    "REMOTE_BRIDGE_CONTEXT_ATTRIBUTE",
    "REMOTE_BRIDGE_CONFIG_MARKER",
    "REQUEST_API_KEY_MARKER",
    "REMOTE_BRIDGE_INCOMPATIBLE_PROVIDERS",
    "REMOTE_GENERATION_PATHS",
    "REMOTE_LLM_PATHS",
    "ROUTER_MANAGED_CREDENTIAL_PROVIDERS",
    "RemoteBridgeRoute",
    "RemoteBridgeRouteError",
    "RemoteBridgeRoutingService",
    "is_remote_bridge_request",
    "remote_bridge_path_allowed",
]
