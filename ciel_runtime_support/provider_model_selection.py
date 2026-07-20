"""Provider model selection, alias resolution, and request projection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ModelIdentityPorts:
    normalize: Callable[[str, str], str]
    model_map: Callable[[str, dict[str, Any]], dict[str, str]]
    unslug: Callable[[str, str, dict[str, str]], str | None]
    api_model_id: Callable[[str, str], str]
    strip_context_suffix: Callable[[str | None], str]
    alias: Callable[[str, str], str]


@dataclass(frozen=True, slots=True)
class ModelSelectionPorts:
    adapter: Callable[[str, dict[str, Any]], Any]
    contract: Callable[[str, dict[str, Any]], Any]
    placeholders: Callable[[str], set[str]]
    upstream_ids: Callable[..., list[str]]
    unique_ids: Callable[[str, list[str]], list[str]]
    apply_specs: Callable[[str, dict[str, Any]], list[str]]
    apply_timeout: Callable[..., list[str]]


@dataclass(frozen=True, slots=True)
class ModelCatalogPorts:
    model_object: Callable[[str, str, dict[str, Any]], dict[str, Any]]
    headers: Callable[[str, dict[str, Any], Any | None], dict[str, str]]
    fetch_anthropic: Callable[..., tuple[list[str], str]]
    sorted_ids: Callable[[list[str]], list[str]]
    routed_anthropic: Callable[[str, dict[str, Any]], bool]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class ModelMutationConfigPorts:
    load_config: Callable[[], dict[str, Any]]
    current_provider: Callable[[dict[str, Any]], tuple[str, dict[str, Any]]]
    save_config: Callable[[dict[str, Any]], None]
    clear_model_cache: Callable[[], None]


@dataclass(frozen=True, slots=True)
class ModelMutationPolicyPorts:
    model_map: Callable[..., dict[str, str]]
    unslug: Callable[[str, str, dict[str, str]], str | None]
    normalize: Callable[[str, str], str]
    apply_profile: Callable[[str, dict[str, Any]], list[str]]
    read_model_info: Callable[[str, dict[str, Any]], dict[str, Any]]
    positive_int: Callable[[Any], int | None]
    model_preset: Callable[[str], dict[str, Any]]
    apply_selection_updates: Callable[[str, dict[str, Any], str], None]
    alias: Callable[[str, str], str]
    format_context: Callable[[int], str]


@dataclass(frozen=True, slots=True)
class ModelMutationEffectPorts:
    sync_context_limit: Callable[[str, dict[str, Any], str], list[str]]
    cap_context_settings: Callable[[str, dict[str, Any]], list[str]]
    apply_recommended_preset: Callable[[str, dict[str, Any], str], list[str]]
    apply_recommended_timeout: Callable[..., list[str]]
    read_model_list: Callable[[str, dict[str, Any]], list[str] | None]


class ModelSelectionController:
    def __init__(
        self,
        config: ModelMutationConfigPorts,
        policy: ModelMutationPolicyPorts,
        effects: ModelMutationEffectPorts,
    ) -> None:
        self._config = config
        self._policy = policy
        self._effects = effects

    def select(self, value: str) -> list[str]:
        config = self._config.load_config()
        provider, provider_config = self._config.current_provider(config)
        model_map = self._policy.model_map(provider, provider_config, fetch=False)
        model_id = self._policy.normalize(
            provider,
            self._policy.unslug(provider, value, model_map) or value,
        )
        provider_config["current_model"] = model_id
        profile_messages = self._policy.apply_profile(provider, provider_config)
        self._policy.apply_selection_updates(provider, provider_config, model_id)
        selected_info = self._policy.read_model_info(provider, provider_config).get(model_id) or {}
        selected_context = self._policy.positive_int(selected_info.get("max_model_len"))
        if selected_context:
            provider_config["max_model_len"] = selected_context
        preset = self._policy.model_preset(model_id)
        if preset.get("num_ctx_min"):
            provider_config["num_ctx_min"] = preset["num_ctx_min"]
        if preset.get("num_ctx_max"):
            provider_config["num_ctx_max"] = preset["num_ctx_max"]
        context_messages = self._effects.sync_context_limit(provider, provider_config, model_id)
        context_messages.extend(self._effects.cap_context_settings(provider, provider_config))
        preset_messages = self._effects.apply_recommended_preset(
            provider,
            provider_config,
            config.get("language", "en"),
        )
        timeout_messages = self._effects.apply_recommended_timeout(
            provider,
            provider_config,
            use_context_fallback=False,
        )
        known = self._effects.read_model_list(provider, provider_config) or []
        custom = provider_config.setdefault("custom_models", [])
        if model_id not in custom and model_id not in known:
            custom.append(model_id)
        self._config.save_config(config)
        self._config.clear_model_cache()
        messages = [
            f"Model for {provider} set to {model_id}.",
            f"Claude Code alias: {self._policy.alias(provider, model_id)}",
            *profile_messages,
        ]
        if selected_context:
            messages.append(
                f"Model context size: {self._policy.format_context(selected_context)} "
                f"({selected_context:,} tokens)."
            )
        messages.extend(context_messages)
        messages.extend(preset_messages)
        messages.extend(timeout_messages)
        if preset.get("thinking"):
            messages.append("Note: this is a thinking model; compatibility test uses extended token budget.")
        return messages


class ProviderModelSelection:
    def __init__(
        self,
        identity: ModelIdentityPorts,
        selection: ModelSelectionPorts,
        catalog: ModelCatalogPorts,
    ) -> None:
        self.identity = identity
        self.selection = selection
        self.catalog = catalog

    def current_upstream_id(self, provider: str, config: dict[str, Any]) -> str:
        current = self.identity.normalize(provider, config.get("current_model") or "model")
        if current.startswith(f"ciel-runtime-{provider}-"):
            try:
                return (
                    self.identity.unslug(
                        provider, current, self.identity.model_map(provider, config)
                    )
                    or current
                )
            except Exception:
                return current
        return current

    def needs_selection(self, provider: str, config: dict[str, Any]) -> bool:
        adapter = self.selection.adapter(provider, config)
        if not adapter.requires_catalog_model_selection(
            self.selection.contract(provider, config)
        ):
            return False
        current = self.identity.normalize(provider, str(config.get("current_model") or ""))
        return current in self.selection.placeholders(provider)

    def ensure_selected(
        self,
        provider: str,
        config: dict[str, Any],
        *,
        force_refresh: bool = False,
    ) -> tuple[bool, list[str]]:
        adapter = self.selection.adapter(provider, config)
        if not adapter.requires_catalog_model_selection(
            self.selection.contract(provider, config)
        ):
            return True, []
        current = self.identity.normalize(provider, str(config.get("current_model") or ""))
        placeholders = self.selection.placeholders(provider)
        if current and current not in placeholders:
            return True, []
        try:
            ids = self.selection.unique_ids(
                provider,
                self.selection.upstream_ids(provider, config, force_refresh=force_refresh),
            )
        except Exception as exc:
            if current:
                return True, [
                    f"Model list unavailable for {provider}; keeping configured model "
                    f"{current} ({type(exc).__name__}: {exc})."
                ]
            return False, [
                f"Model selection required for {provider}: model list unavailable "
                f"({type(exc).__name__}: {exc})."
            ]
        if current and current in ids:
            return True, []
        candidates = [
            model_id
            for model_id in ids
            if self.identity.normalize(provider, model_id) not in placeholders
        ]
        if len(candidates) == 1:
            selected = self.identity.normalize(provider, candidates[0])
            config["current_model"] = selected
            context = self.selection.apply_specs(provider, config)
            timeout = self.selection.apply_timeout(
                provider, config, use_context_fallback=False
            )
            return True, [
                f"Model auto-selected from provider list: {selected}.",
                *context,
                *timeout,
            ]
        if len(candidates) > 1:
            return False, [
                f"Model selection required for {provider}: provider returned "
                f"{len(candidates)} models; choose one before launch/test."
            ]
        if current:
            return True, [
                f"Model list for {provider} did not include a non-placeholder model; "
                f"keeping configured model {current}."
            ]
        return False, [
            f"Model selection required for {provider}: provider returned no usable model ids."
        ]

    def launch_id(self, provider: str, config: dict[str, Any]) -> str:
        current = self.identity.normalize(provider, config.get("current_model") or "model")
        adapter = self.selection.adapter(provider, config)
        contract = self.selection.contract(provider, config)
        if adapter.launch_model_strategy(contract) != "ollama_unslug":
            return current if adapter.preserves_claude_model_alias(current) else self.identity.alias(
                provider, current
            )
        if not current.startswith(f"ciel-runtime-{provider}-"):
            return current
        try:
            return (
                self.identity.unslug(
                    provider, current, self.identity.model_map(provider, config)
                )
                or current
            )
        except Exception:
            return current

    def resolve_requested(
        self, provider: str, config: dict[str, Any], requested: str | None
    ) -> str:
        requested = self.identity.strip_context_suffix(requested)
        fallback = self.identity.normalize(provider, config.get("current_model") or "model")
        adapter = self.selection.adapter(provider, config)
        model_map = self.identity.model_map(provider, config)
        if not requested:
            return self.identity.api_model_id(provider, fallback)
        resolved = self.identity.unslug(provider, requested, model_map)
        if resolved:
            return self.identity.api_model_id(provider, resolved)
        if requested in set(model_map.values()):
            return self.identity.api_model_id(provider, requested)
        if adapter.preserves_claude_model_alias(requested):
            return self.identity.api_model_id(provider, requested)
        if requested.startswith(("claude-", "ciel-runtime-")):
            return self.identity.api_model_id(provider, fallback)
        return self.identity.api_model_id(
            provider, self.identity.normalize(provider, requested)
        )

    def resolve_tool_models(
        self, provider: str, config: dict[str, Any], body: dict[str, Any]
    ) -> dict[str, Any]:
        tools = body.get("tools")
        if not isinstance(tools, list) or not tools:
            return body
        projected: list[Any] = []
        changed = 0
        for tool in tools:
            model = tool.get("model") if isinstance(tool, dict) else None
            if not isinstance(model, str) or not model.strip():
                projected.append(tool)
                continue
            resolved = self.resolve_requested(provider, config, model)
            if resolved and resolved != model:
                projected.append({**tool, "model": resolved})
                changed += 1
            else:
                projected.append(tool)
        if not changed:
            return body
        self.catalog.log(
            "INFO", f"resolved upstream tool model references for {provider}: {changed}"
        )
        return {**body, "tools": projected}

    def list_objects(self, provider: str, config: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            self.catalog.model_object(provider, model_id, config)
            for model_id in self.selection.upstream_ids(provider, config)
        ]

    def list_objects_for_request(
        self, provider: str, config: dict[str, Any], inbound_headers: Any | None = None
    ) -> list[dict[str, Any]]:
        if self.catalog.routed_anthropic(provider, config):
            try:
                ids, source = self.catalog.fetch_anthropic(
                    config,
                    self.catalog.headers(provider, config, inbound_headers),
                    timeout=6.0,
                )
                if ids:
                    ids.extend(
                        model_id
                        for value in config.get("custom_models", []) or []
                        if (model_id := self.identity.normalize(provider, value))
                        and model_id not in ids
                    )
                    current = self.identity.normalize(
                        provider, config.get("current_model") or ""
                    )
                    if current and current not in ids:
                        ids.append(current)
                    self.catalog.log(
                        "DEBUG",
                        f"anthropic routed model discovery source={source} count={len(ids)}",
                    )
                    models = self.catalog.sorted_ids(
                        self.selection.unique_ids(provider, ids)
                    )
                    return [
                        self.catalog.model_object(provider, model_id, config)
                        for model_id in models
                    ]
            except Exception as exc:
                self.catalog.log(
                    "DEBUG",
                    "anthropic routed model discovery fallback "
                    f"error={type(exc).__name__}: {exc}",
                )
        return self.list_objects(provider, config)
