"""Runtime assembly for the remote bridge routing and control planes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .remote_bridge import (
    API_KEY_HEADER,
    MODEL_PICKER_ENABLED_METADATA_KEY,
    MODEL_HEADER,
    PROVIDER_HEADER,
    PUBLIC_MODEL_ID_METADATA_KEY,
    REMOTE_BRIDGE_INCOMPATIBLE_PROVIDERS,
    ROUTER_MANAGED_CREDENTIAL_PROVIDERS,
    RemoteBridgeRouteError,
    RemoteBridgeRoutingService,
)
from .remote_bridge_cli import RemoteBridgeCliController, RemoteBridgeCliPorts


@dataclass(frozen=True, slots=True)
class _PublishedModel:
    public_id: str
    upstream_id: str
    metadata: dict[str, Any]


class RemoteBridgeRuntimeApi:
    def __init__(
        self,
        normalize_provider: Callable[[str], str],
        parse_bool: Callable[[Any, bool], bool],
        environ: Mapping[str, str],
        provider_labels: Mapping[str, str],
        cached_models: Callable[..., list[str]],
        model_object: Callable[..., dict[str, Any]],
        alias_for: Callable[[str, str], str],
        current_provider: Callable[..., tuple[str, dict[str, Any]]],
        has_api_key: Callable[[str, dict[str, Any]], bool],
        model_info: Callable[..., dict[str, dict[str, Any]]] = (
            lambda _provider, _config: {}
        ),
    ) -> None:
        self.routing = RemoteBridgeRoutingService(
            normalize_provider,
            parse_bool,
            environ,
        )
        self.normalize_provider = normalize_provider
        self.provider_labels = provider_labels
        self.cached_models = cached_models
        self.model_object = model_object
        self.alias_for = alias_for
        self.current_provider = current_provider
        self.has_api_key = has_api_key
        self.model_info = model_info

    def enabled(self, config: Mapping[str, Any]) -> bool:
        return self.routing.enabled(config)

    def resolve(self, *args: Any, **kwargs: Any) -> Any:
        route = self.routing.resolve(*args, **kwargs)
        requested_model = str(
            route.body.get("model")
            or route.provider_config.get("current_model")
            or ""
        )
        picker_catalog, published = self._published_models(
            route.provider,
            route.provider_config,
        )
        selected = next(
            (
                item
                for item in published
                if item.public_id == requested_model
            ),
            None,
        )
        path = str(kwargs.get("path") or (args[3] if len(args) > 3 else ""))
        if picker_catalog and selected is None:
            raise RemoteBridgeRouteError(
                "Model is not available through Remote Bridge: "
                f"{route.provider}/{requested_model}"
            )
        if selected is not None:
            if selected.metadata:
                route.provider_config["_ciel_model_metadata"] = dict(
                    selected.metadata
                )
            if not path.startswith("/v1/models/"):
                route.provider_config["current_model"] = selected.upstream_id
                route.body["model"] = selected.upstream_id
        return route

    def model_objects(
        self,
        config: dict[str, Any],
        headers: Any | None = None,
    ) -> list[dict[str, Any]]:
        providers = (
            config.get("providers")
            if isinstance(config.get("providers"), dict)
            else {}
        )
        requested_provider = self._requested_provider(headers)
        if requested_provider:
            try:
                selected = self.normalize_provider(requested_provider)
            except SystemExit:
                return []
            provider_names = (
                [selected]
                if selected in providers
                and selected not in REMOTE_BRIDGE_INCOMPATIBLE_PROVIDERS
                else []
            )
        else:
            provider_names = [
                name
                for name in self.provider_labels
                if name in providers
                and name not in REMOTE_BRIDGE_INCOMPATIBLE_PROVIDERS
            ]
        objects: list[dict[str, Any]] = []
        for provider in provider_names:
            provider_config = providers.get(provider)
            if not isinstance(provider_config, dict):
                continue
            _picker_catalog, published = self._published_models(
                provider,
                provider_config,
            )
            for item in published:
                model = self.model_object(
                    provider,
                    item.upstream_id,
                    provider_config,
                )
                model["id"] = f"{provider}/{item.public_id}"
                metadata = model.get("ciel_runtime")
                if isinstance(metadata, dict):
                    metadata["alias"] = self.alias_for(
                        provider,
                        item.public_id,
                    )
                    metadata.update(item.metadata)
                objects.append(model)
        return objects

    def _published_models(
        self,
        provider: str,
        provider_config: dict[str, Any],
    ) -> tuple[bool, list[_PublishedModel]]:
        raw_info = self.model_info(provider, provider_config)
        model_info = raw_info if isinstance(raw_info, Mapping) else {}
        picker_catalog = any(
            isinstance(metadata, Mapping)
            and isinstance(
                metadata.get(MODEL_PICKER_ENABLED_METADATA_KEY),
                bool,
            )
            for metadata in model_info.values()
        )
        published: list[_PublishedModel] = []
        seen_public_ids: set[str] = set()
        for raw_model_id in self.cached_models(provider, provider_config):
            upstream_id = str(raw_model_id or "").strip()
            if not upstream_id:
                continue
            raw_metadata = model_info.get(upstream_id)
            metadata = (
                dict(raw_metadata)
                if isinstance(raw_metadata, Mapping)
                else {}
            )
            if picker_catalog and (
                metadata.get(MODEL_PICKER_ENABLED_METADATA_KEY) is not True
            ):
                continue
            public_id = str(
                metadata.get(PUBLIC_MODEL_ID_METADATA_KEY) or upstream_id
            ).strip()
            if not public_id or public_id in seen_public_ids:
                continue
            seen_public_ids.add(public_id)
            published.append(
                _PublishedModel(public_id, upstream_id, metadata)
            )
        return picker_catalog, published

    def status_payload(self, config: dict[str, Any]) -> dict[str, Any]:
        providers = (
            config.get("providers")
            if isinstance(config.get("providers"), dict)
            else {}
        )
        default_provider, default_config = self.current_provider(config)
        entries = []
        for provider in self.provider_labels:
            if provider in REMOTE_BRIDGE_INCOMPATIBLE_PROVIDERS:
                continue
            provider_config = providers.get(provider)
            if not isinstance(provider_config, dict):
                continue
            entries.append(
                {
                    "id": provider,
                    "label": self.provider_labels.get(provider, provider),
                    "current_model": str(
                        provider_config.get("current_model") or ""
                    ),
                    "credential_configured": self.has_api_key(
                        provider,
                        provider_config,
                    ),
                    "credential_source": (
                        "router_host_oauth"
                        if provider in ROUTER_MANAGED_CREDENTIAL_PROVIDERS
                        else "router_host_or_request"
                    ),
                }
            )
        return {
            "ok": True,
            "mode": "remote_bridge",
            "enabled": self.enabled(config),
            "default_route": {
                "provider": default_provider,
                "model": str(default_config.get("current_model") or ""),
                "available": (
                    default_provider
                    not in REMOTE_BRIDGE_INCOMPATIBLE_PROVIDERS
                ),
            },
            "endpoints": {
                "openai_chat": "/v1/chat/completions",
                "openai_responses": "/v1/responses",
                "anthropic_messages": "/v1/messages",
                "models": "/v1/models",
            },
            "routing": {
                "model": "provider/model",
                "provider_header": PROVIDER_HEADER,
                "model_header": MODEL_HEADER,
                "api_key_header": API_KEY_HEADER,
                "router_token_header": "authorization: Bearer <bridge-token>",
            },
            "providers": entries,
        }

    def bind_cli(
        self,
        load_config: Callable[[], dict[str, Any]],
        save_config: Callable[[dict[str, Any]], None],
        ensure_token: Callable[[], str],
        token: Callable[[], str],
        serve: Callable[[Any], None],
        output: Callable[[str], None],
        port: int,
    ) -> Callable[[Any], int]:
        return RemoteBridgeCliController(
            RemoteBridgeCliPorts(
                load_config,
                save_config,
                ensure_token,
                token,
                serve,
                output,
                port,
                self.enabled,
            )
        ).run

    @staticmethod
    def _requested_provider(headers: Any | None) -> str:
        try:
            return str(headers.get(PROVIDER_HEADER) or "").strip()
        except (AttributeError, TypeError):
            return ""


__all__ = ["RemoteBridgeRuntimeApi"]
