from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .architecture import ProviderOptionPresentationPolicy


@dataclass(frozen=True)
class OptionPanelPolicy:
    presentation: ProviderOptionPresentationPolicy
    context_strategy: str
    shows_workflows: bool
    timeout_default: str


@dataclass(frozen=True)
class OptionPanelText:
    compact_text: Callable[[Any, int], str]
    ui_text: Callable[[str, str], str]
    context_status: Callable[[str, dict[str, Any]], str]
    applied_preset: Callable[[str, dict[str, Any]], str]
    preset_text: Callable[[str, str], tuple[str, str]]
    timeout_status: Callable[[dict[str, Any], str], str]


@dataclass(frozen=True)
class OptionPanelRuntime:
    router_debug_external: Callable[[], bool]
    message_preview_chars: Callable[[], int]
    direct_native: Callable[[str, dict[str, Any]], bool]
    capability_string: Callable[[str, dict[str, Any], str], str]
    current_model: Callable[[str, dict[str, Any]], str]
    workflows_enabled: Callable[[str, dict[str, Any]], bool]
    ultracode_enabled: Callable[[str, dict[str, Any]], bool]


@dataclass(frozen=True)
class OptionPanelProvider:
    ollama_options: Callable[[dict[str, Any]], dict[str, Any]]
    ollama_context_status: Callable[[dict[str, Any]], str]
    ollama_think_status: Callable[[str, dict[str, Any]], str]
    query_status: Callable[[str, dict[str, Any]], str]
    tool_choice_status: Callable[[str, dict[str, Any]], str]
    rate_limit_status: Callable[[str, dict[str, Any]], str]
    rate_limit_rpm: Callable[[str, dict[str, Any]], str]
    ip_family: Callable[[str, dict[str, Any]], str]
    parse_bool: Callable[..., bool]


@dataclass(frozen=True)
class OptionPanelServices:
    text: OptionPanelText
    runtime: OptionPanelRuntime
    provider: OptionPanelProvider


def build_option_panel_rows(
    provider: str,
    config: dict[str, Any],
    policy: OptionPanelPolicy,
    services: OptionPanelServices,
    *,
    language: str,
) -> tuple[list[str], list[str]]:
    text = services.text
    runtime = services.runtime
    rows: list[str] = []
    values: list[str] = []

    def add(label: str, key: str, value: Any) -> None:
        rows.append(f"{label:<24} [{text.compact_text(value, 56)}]")
        values.append(key)

    add(text.ui_text("context_setup", language), "context_setup", text.context_status(provider, config))
    preset = text.applied_preset(provider, config)
    add(text.ui_text("apply_preset", language), "preset", text.preset_text(preset, language)[0])
    add(text.ui_text("timeout_preset", language), "timeout_profile", text.timeout_status(config, language))
    add("Router debug external", "router_debug_external_access", "on" if runtime.router_debug_external() else "off")
    add("Event message preview", "router_debug_message_preview_chars", runtime.message_preview_chars())
    if not runtime.direct_native(provider, config) and policy.shows_workflows:
        model = runtime.current_model(provider, config)
        capabilities = runtime.capability_string(provider, config, model)
        add("Claude Code workflows", "workflows_enabled", "on" if runtime.workflows_enabled(provider, config) else "off")
        add("Claude Code ultracode", "ultracode_enabled", "on" if runtime.ultracode_enabled(provider, config) else "off")
        add("Claude Code capabilities", "claude_code_supported_capabilities", capabilities or "auto/none")
    if policy.context_strategy == "ollama":
        _add_ollama_controls(add, provider, config, services)
    else:
        _add_standard_controls(add, provider, config, policy, services)
    rows.append(text.ui_text("back", language))
    values.append("back")
    return rows, values


def _add_ollama_controls(
    add: Callable[[str, str, Any], None],
    provider: str,
    config: dict[str, Any],
    services: OptionPanelServices,
) -> None:
    projection = services.provider
    options = projection.ollama_options(config)
    add("Context window", "num_ctx", projection.ollama_context_status(config))
    add("Context min", "num_ctx_min", config.get("num_ctx_min", "default"))
    add("Context max", "num_ctx_max", config.get("num_ctx_max", "default"))
    add("Max output tokens", "num_predict", options.get("num_predict", "default"))
    add("Query string", "force_query_string", projection.query_status(provider, config))
    add("Tool choice", "supports_tool_choice", projection.tool_choice_status(provider, config))
    add("Temperature", "temperature", options.get("temperature", "default"))
    add("Top P", "top_p", options.get("top_p", "default"))
    add("Top K", "top_k", options.get("top_k", "default"))
    model = services.runtime.current_model(provider, config)
    add("Think", "think", projection.ollama_think_status(model, config))
    add("Keep alive", "keep_alive", config.get("keep_alive", "default"))
    add("Timeout ms", "request_timeout_ms", config.get("request_timeout_ms", "default"))
    _add_stream_controls(add, config)
    add("RPM limiter", "rate_limit_enabled", projection.rate_limit_status(provider, config))
    add("Rate limit RPM", "rate_limit_rpm", projection.rate_limit_rpm(provider, config))
    add("Rate limit status", "rate_limit_status", "on" if bool(config.get("rate_limit_status", False)) else "off")
    add("IP family", "ip_family", projection.ip_family(provider, config))


def _add_standard_controls(
    add: Callable[[str, str, Any], None],
    provider: str,
    config: dict[str, Any],
    policy: OptionPanelPolicy,
    services: OptionPanelServices,
) -> None:
    presentation = policy.presentation
    projection = services.provider
    if policy.context_strategy == "standard":
        add("Context window", "context_window", config.get("context_window", "default"))
        add("Context reserve", "context_reserve_tokens", config.get("context_reserve_tokens", "default"))
    add("Max output tokens", "max_output_tokens", config.get("max_output_tokens", "default"))
    add("Query string", "force_query_string", projection.query_status(provider, config))
    if presentation.show_tool_choice:
        add("Tool choice", "supports_tool_choice", projection.tool_choice_status(provider, config))
    if policy.context_strategy == "standard":
        add("Timeout ms", "request_timeout_ms", config.get("request_timeout_ms", "default"))
        if presentation.show_rate_limit_controls:
            add("RPM limiter", "rate_limit_enabled", projection.rate_limit_status(provider, config))
            add("Rate limit RPM", "rate_limit_rpm", projection.rate_limit_rpm(provider, config))
            add("Rate limit status", "rate_limit_status", "on" if bool(config.get("rate_limit_status", False)) else "off")
        if presentation.show_sampling_controls:
            for label, key in (("Temperature", "temperature"), ("Top P", "top_p"), ("Top K", "top_k")):
                add(label, key, config.get(key, "default"))
        if presentation.show_native:
            add("Native compatibility", "native_compat", bool(config.get("native_compat", True)))
        if presentation.show_stream:
            _add_stream_controls(add, config)
        if presentation.show_ip_family_control:
            add("IP family", "ip_family", projection.ip_family(provider, config))
    elif presentation.show_route:
        routed = projection.parse_bool(config.get("route_through_router"), default=False)
        add("Route through router", "route_through_router", "on" if routed else "off")
        add("Timeout ms", "request_timeout_ms", config.get("request_timeout_ms", policy.timeout_default))
        if presentation.show_ip_family_control and routed:
            add("IP family", "ip_family", projection.ip_family(provider, config))


def _add_stream_controls(
    add: Callable[[str, str, Any], None], config: dict[str, Any]
) -> None:
    enabled = bool(config.get("stream_enabled", True))
    add("Stream", "stream_enabled", "on" if enabled else "off")
    if enabled:
        add("Stream idle timeout ms", "stream_idle_timeout_ms", config.get("stream_idle_timeout_ms", "auto"))
        add("Stream word chunking", "stream_word_chunking", "on" if bool(config.get("stream_word_chunking", False)) else "off")


__all__ = [
    "OptionPanelPolicy",
    "OptionPanelProvider",
    "OptionPanelRuntime",
    "OptionPanelServices",
    "OptionPanelText",
    "build_option_panel_rows",
]
