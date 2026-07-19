"""LLM option configuration application service."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class LlmOptionRepository:
    clear_model_cache: Callable[..., Any]
    load_config: Callable[..., Any]
    save_config: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class LlmOptionMutation:
    apply_ollama_option: Callable[..., Any]
    apply_provider_option: Callable[..., Any]
    configuration_policy: Callable[..., Any]
    normalize_capabilities: Callable[..., Any]
    parse_bool: Callable[..., Any]
    positive_int: Callable[..., Any]
    routing_mode_update: Callable[..., tuple[str, ...]]
    set_router_debug_external_access: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class LlmOptionPolicy:
    apply_recommended_timeout: Callable[..., Any]
    cap_context_settings: Callable[..., Any]
    cap_output_settings: Callable[..., Any]
    configured_rate_limit_rpm: Callable[..., Any]
    provider_labels: dict[str, str]


@dataclass(frozen=True, slots=True)
class LlmOptionConfigServices:
    repository: LlmOptionRepository
    mutation: LlmOptionMutation
    policy: LlmOptionPolicy


def set_llm_option_config(
    provider: str,
    key: str,
    raw_value: str,
    *,
    services: LlmOptionConfigServices,
) -> list[str]:
    repository = services.repository
    mutation = services.mutation
    policy = services.policy
    PROVIDER_LABELS = policy.provider_labels
    apply_ollama_option = mutation.apply_ollama_option
    apply_provider_option = mutation.apply_provider_option
    configuration_policy = mutation.configuration_policy
    apply_recommended_timeout_for_model_context = policy.apply_recommended_timeout
    cap_context_settings_to_model_capacity = policy.cap_context_settings
    cap_output_settings_to_context_ratio = policy.cap_output_settings
    clear_model_cache = repository.clear_model_cache
    load_config = repository.load_config
    normalize_claude_code_supported_capabilities = mutation.normalize_capabilities
    parse_bool = mutation.parse_bool
    positive_int = mutation.positive_int
    routing_mode_update = mutation.routing_mode_update
    router_rate_limit_configured_rpm = policy.configured_rate_limit_rpm
    save_config = repository.save_config
    set_router_debug_external_access_config = mutation.set_router_debug_external_access
    cfg = load_config()
    pcfg = cfg["providers"][provider]
    value = raw_value.strip()
    if not value:
        return ["Option unchanged."]
    if key == "router_debug_external_access":
        return set_router_debug_external_access_config(value)
    if key == "router_debug_message_preview_chars":
        fixed = positive_int(value)
        if str(value).lower() in ("0", "false", "off", "disable", "disabled", "none", "unset"):
            fixed = 0
        if fixed is None:
            return ["router_debug_message_preview_chars: enter digits only, or 0/off to disable."]
        cfg["router_debug_message_preview_chars"] = min(fixed, 4000)
        save_config(cfg)
        return ["Router event message preview updated.", f"preview chars: {cfg['router_debug_message_preview_chars']}"]
    if key == "route_through_router":
        pcfg["route_through_router"] = parse_bool(value, default=False)
        save_config(cfg)
        clear_model_cache()
        return list(routing_mode_update(provider, pcfg["route_through_router"]))
    if key == "rate_limit_enabled":
        enabled = parse_bool(value, default=False)
        if enabled:
            if not router_rate_limit_configured_rpm(provider, pcfg):
                pcfg["rate_limit_rpm"] = 60
            save_config(cfg)
            clear_model_cache()
            return ["RPM limiter enabled.", f"rate_limit_rpm: {router_rate_limit_configured_rpm(provider, pcfg)}"]
        pcfg["rate_limit_rpm"] = 0
        pcfg["rate_limit_status"] = False
        save_config(cfg)
        clear_model_cache()
        return ["RPM limiter disabled.", "rate_limit_rpm: 0", "rate_limit_status: off"]
    if key in ("workflows_enabled", "workflow", "workflows"):
        pcfg["workflows_enabled"] = parse_bool(value, default=False)
        save_config(cfg)
        clear_model_cache()
        return ["Claude Code workflow support updated.", f"workflows_enabled: {pcfg['workflows_enabled']}"]
    if key in ("ultracode_enabled", "ultracode"):
        pcfg["ultracode_enabled"] = parse_bool(value, default=False)
        if pcfg["ultracode_enabled"]:
            pcfg["workflows_enabled"] = True
        save_config(cfg)
        clear_model_cache()
        return ["Claude Code ultracode setting updated.", f"ultracode_enabled: {pcfg['ultracode_enabled']}"]
    if key in ("claude_code_supported_capabilities", "supported_capabilities", "capabilities"):
        caps = normalize_claude_code_supported_capabilities(value)
        pcfg["claude_code_supported_capabilities"] = caps
        save_config(cfg)
        clear_model_cache()
        return ["Claude Code model capabilities updated.", f"capabilities: {','.join(caps) or 'none'}"]
    numeric_keys = {
        "context_window",
        "context",
        "max_model_len",
        "context_reserve_tokens",
        "reserve",
        "max_output_tokens",
        "max_tokens",
        "maxtoken",
        "max_token",
        "num_ctx_min",
        "num_ctx_max",
        "num_predict",
        "timeout",
        "timeout_ms",
        "request_timeout",
        "request_timeout_ms",
        "stream_idle_timeout",
        "stream_idle_timeout_ms",
        "idle_timeout",
        "idle_timeout_ms",
        "rate_limit",
        "rate_limit_rpm",
        "rpm",
        "top_k",
    }
    disable_words = ("0", "false", "off", "disable", "disabled")
    rate_limit_keys = ("rate_limit", "rate_limit_rpm", "rpm")
    if (
        key in numeric_keys
        and value.lower() not in ("default", "unset", "none", "null", "0")
        and not (key in rate_limit_keys and value.lower() in disable_words)
    ):
        if not re.fullmatch(r"\d+", value):
            return [f"{key}: enter digits only, or use default/unset to clear."]
    clear_words = ("default", "unset", "none", "null")
    token = f"unset:{key}" if value.lower() in clear_words else f"{key}={value}"
    capabilities = configuration_policy(provider, pcfg)
    context_changed = key in ("context_window", "context", "max_model_len", "num_ctx", "ctx", "num_ctx_min", "ctx_min", "min", "num_ctx_max", "ctx_max", "max")
    explicit_timeout = key in ("timeout", "timeout_ms", "request_timeout", "request_timeout_ms", "stream_idle_timeout", "stream_idle_timeout_ms", "idle_timeout", "idle_timeout_ms")
    if key in ("force_query_string", "force_query", "upstream_query", "test_query_string"):
        apply_provider_option(provider, pcfg, token)
    elif key in ("supports_tool_choice", "tool_choice", "tool-choice", "auto_tool_choice"):
        apply_provider_option(provider, pcfg, token)
    elif capabilities.mutation_strategy == "ollama":
        apply_ollama_option(pcfg, token)
    elif capabilities.restricts_runtime_options:
        if key in ("route", "routed", "route_through_router", "router"):
            pcfg["route_through_router"] = parse_bool(value, default=False)
        elif key in ("max_output_tokens", "max_tokens", "maxtoken", "max_token"):
            apply_provider_option(provider, pcfg, token)
        elif key in ("timeout", "timeout_ms", "request_timeout", "request_timeout_ms"):
            apply_provider_option(provider, pcfg, token)
        else:
            raise SystemExit(f"Unknown {PROVIDER_LABELS.get(provider, provider)} option: {key}")
    else:
        apply_provider_option(provider, pcfg, token)
    cap_lines = cap_context_settings_to_model_capacity(provider, pcfg)
    cap_lines.extend(cap_output_settings_to_context_ratio(provider, pcfg))
    timeout_lines = apply_recommended_timeout_for_model_context(provider, pcfg) if context_changed and not explicit_timeout else []
    save_config(cfg)
    clear_model_cache()
    return [f"{PROVIDER_LABELS.get(provider, provider)} option updated.", f"{key}: {value}", *cap_lines, *timeout_lines]
