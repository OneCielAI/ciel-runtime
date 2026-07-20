"""Provider endpoint mutation and runtime status projection services."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ProviderEndpointPolicy:
    keep_native_default: Callable[[str], bool]


@dataclass(frozen=True, slots=True)
class ProviderEndpointPorts:
    load_config: Callable[[], dict[str, Any]]
    save_config: Callable[[dict[str, Any]], None]
    clear_model_cache: Callable[[], None]
    normalize_base_url: Callable[[str, dict[str, Any], str], str]
    detect_native_compat: Callable[..., tuple[bool | None, str]]
    ensure_current_model: Callable[..., tuple[str | None, list[str]]]


@dataclass(frozen=True, slots=True)
class ProviderEndpointService:
    policy: ProviderEndpointPolicy
    ports: ProviderEndpointPorts

    def set_base_url(self, provider: str, url: str) -> list[str]:
        config = self.ports.load_config()
        provider_config = config["providers"][provider]
        old_url = str(provider_config.get("base_url") or "").rstrip("/")
        new_url = self.ports.normalize_base_url(provider, provider_config, url)
        provider_config["base_url"] = new_url
        endpoint_changed = old_url != new_url
        lines = [f"Base URL for {provider} set to {new_url}."]
        if endpoint_changed:
            lines.extend(self._reset_endpoint_model(provider, provider_config))
        self.ports.save_config(config)
        if not endpoint_changed:
            self.ports.clear_model_cache()
        return lines

    def _reset_endpoint_model(self, provider: str, provider_config: dict[str, Any]) -> list[str]:
        provider_config["current_model"] = ""
        provider_config["custom_models"] = []
        self.ports.clear_model_cache()
        lines = ["Model selection was reset because the provider endpoint changed."]
        detected, reason = self.ports.detect_native_compat(provider, provider_config)
        if detected is None:
            if self.policy.keep_native_default(provider):
                provider_config["native_compat"] = True
                lines.append(
                    f"Endpoint auto-detect inconclusive ({reason}); "
                    "Native compatibility kept on as the Anthropic default."
                )
        else:
            provider_config["native_compat"] = bool(detected)
            mode = "enabled" if detected else "disabled"
            lines.append(f"Endpoint auto-detected ({reason}); Native compatibility {mode}.")
        selected, selection_lines = self.ports.ensure_current_model(
            provider, provider_config, force_refresh=True
        )
        lines.extend(selection_lines)
        if not selected:
            lines.append("Choose a model before running compatibility test or Launch Claude Code.")
        return lines


@dataclass(frozen=True, slots=True)
class ProviderStatusProjectionPorts:
    get_current_provider: Callable[[dict[str, Any]], tuple[str, dict[str, Any]]]
    mode_label: Callable[[str, dict[str, Any]], str]
    direct_native_anthropic: Callable[[str, dict[str, Any]], bool]
    configured_adapter: Callable[..., Any]
    contract_config: Callable[..., Any]
    ollama_num_ctx_status: Callable[[dict[str, Any]], str]
    ollama_options_status: Callable[[dict[str, Any]], str]
    ollama_think_status: Callable[[str, dict[str, Any]], str]
    current_upstream_model: Callable[[str, dict[str, Any]], str]
    current_alias: Callable[[dict[str, Any]], str]


@dataclass(frozen=True, slots=True)
class RuntimeStatusPorts:
    load_config: Callable[[], dict[str, Any]]
    log_level_status: Callable[[], str]
    channel_status_text: Callable[[dict[str, Any]], str]
    channel_delivery_mode: Callable[[dict[str, Any]], str]
    router_up: Callable[[], bool]
    router_base: str
    config_path: Any


@dataclass(frozen=True, slots=True)
class ProviderStatusService:
    projection: ProviderStatusProjectionPorts
    runtime: RuntimeStatusPorts

    def lines(self) -> list[str]:
        config = self.runtime.load_config()
        provider, provider_config = self.projection.get_current_provider(config)
        direct_native = self.projection.direct_native_anthropic(provider, provider_config)
        policy = self.projection.configured_adapter(provider, provider_config).configuration_policy(
            self.projection.contract_config(provider, provider_config)
        )
        provider_lines = self._provider_lines(provider, provider_config, policy)
        claude_model = self._claude_model(config, provider, provider_config, direct_native, policy)
        router = (
            "bypassed for native provider compatibility"
            if direct_native
            else f"{'up' if self.runtime.router_up() else 'down'} {self.runtime.router_base}"
        )
        return [
            f"provider: {provider}",
            f"language: {config.get('language', 'en')}",
            f"mode: {self.projection.mode_label(provider, provider_config)}",
            f"base_url: {provider_config.get('base_url')}",
            f"model: {provider_config.get('current_model')}",
            *provider_lines,
            f"claude_model: {claude_model}",
            f"log_level: {self.runtime.log_level_status()}",
            f"channels: {self.runtime.channel_status_text(config)}",
            f"channel_delivery: {self.runtime.channel_delivery_mode(config)}",
            f"router: {router}",
            f"config: {self.runtime.config_path}",
        ]

    def _provider_lines(self, provider: str, provider_config: dict[str, Any], policy: Any) -> list[str]:
        lines: list[str] = []
        if policy.uses_ollama_status:
            model = self.projection.current_upstream_model(provider, provider_config)
            lines.extend(
                (
                    f"num_ctx: {self.projection.ollama_num_ctx_status(provider_config)}",
                    f"ollama_options: {self.projection.ollama_options_status(provider_config)}",
                    f"keep_alive: {provider_config.get('keep_alive', 'default')}",
                    f"think: {self.projection.ollama_think_status(model, provider_config)}",
                    f"request_timeout_ms: {provider_config.get('request_timeout_ms', 'default')}",
                    f"stream_idle_timeout_ms: {provider_config.get('stream_idle_timeout_ms', 'auto')}",
                )
            )
        for field_name in policy.status_fields:
            default = "auto" if field_name == "stream_idle_timeout_ms" else "default"
            lines.append(f"{field_name}: {provider_config.get(field_name, default)}")
        return lines

    def _claude_model(
        self, config: dict[str, Any], provider: str, provider_config: dict[str, Any], direct_native: bool, policy: Any
    ) -> str:
        if policy.runtime_owns_model:
            return "disabled for native runtime provider"
        if direct_native:
            return self.projection.current_upstream_model(provider, provider_config)
        return self.projection.current_alias(config)


__all__ = [
    "ProviderEndpointPolicy", "ProviderEndpointPorts", "ProviderEndpointService",
    "ProviderStatusProjectionPorts", "ProviderStatusService", "RuntimeStatusPorts",
]
