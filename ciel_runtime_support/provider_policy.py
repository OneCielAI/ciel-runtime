from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ProviderWireServices:
    normalize_model_id: Callable[..., Any]
    openai_chat_reasoning_passback_enabled_for_body: Callable[..., Any]
    preserves_anthropic_thinking_contract: Callable[..., Any]
    provider_supports_tool_choice: Callable[..., Any]
    resolve_requested_model: Callable[..., Any]
    select_provider_protocol: Callable[..., Any]


def resolve_provider_wire_profile(provider: str, pcfg: dict[str, Any], body: dict[str, Any] | None = None,
    *,
    services: ProviderWireServices,
) -> dict[str, str]:
    """Return the active provider/base-url wire profile for this request.

    The same model id can travel through different protocol adapters depending
    on provider and base URL. Keep this provider-scoped; model metadata only
    supplies limits and hints.
    """

    normalize_model_id = services.normalize_model_id
    openai_chat_reasoning_passback_enabled_for_body = services.openai_chat_reasoning_passback_enabled_for_body
    preserves_anthropic_thinking_contract = services.preserves_anthropic_thinking_contract
    provider_supports_tool_choice = services.provider_supports_tool_choice
    resolve_requested_model = services.resolve_requested_model
    select_provider_protocol = services.select_provider_protocol
    request_model = str((body or {}).get("model") or pcfg.get("current_model") or "")
    try:
        model = resolve_requested_model(provider, pcfg, request_model)
    except Exception:
        model = normalize_model_id(provider, request_model)

    selected_protocol = select_provider_protocol(provider, pcfg, "anthropic_messages", model)
    upstream_format = str(selected_protocol).replace("_", "-")
    endpoint_family = upstream_format

    if preserves_anthropic_thinking_contract(provider, pcfg):
        thinking_policy = "preserve"
    elif (
        upstream_format == "openai-chat"
        and body is not None
        and openai_chat_reasoning_passback_enabled_for_body(provider, pcfg, body)
    ):
        thinking_policy = "openai-reasoning-passback"
    else:
        thinking_policy = "strip"

    return {
        "provider": provider,
        "model": model,
        "endpoint_family": endpoint_family,
        "upstream_format": upstream_format,
        "thinking_policy": thinking_policy,
        "tool_choice_policy": "forward" if provider_supports_tool_choice(provider, pcfg, body or {}) else "strip",
        "metadata_policy": "strip-internal-upstream-only",
    }


@dataclass(frozen=True, slots=True)
class ProviderRequestServices:
    is_kimi_k3_model_id: Callable[..., Any]
    normalize_anthropic_system_role_messages: Callable[..., Any]
    normalize_anthropic_tool_turns_for_provider: Callable[..., Any]
    normalize_thinking_for_non_anthropic_provider: Callable[..., Any]
    normalize_tool_choice_for_provider: Callable[..., Any]
    provider_wire_profile: Callable[..., Any]
    sanitize_assistant_pseudo_tool_text_history: Callable[..., Any]


def normalize_provider_request(provider: str, pcfg: dict[str, Any], body: dict[str, Any],
    *,
    services: ProviderRequestServices,
) -> dict[str, Any]:
    """Normalize a Claude Code /v1/messages request for the active provider wire profile."""

    is_kimi_k3_model_id = services.is_kimi_k3_model_id
    normalize_anthropic_system_role_messages = services.normalize_anthropic_system_role_messages
    normalize_anthropic_tool_turns_for_provider = services.normalize_anthropic_tool_turns_for_provider
    normalize_thinking_for_non_anthropic_provider = services.normalize_thinking_for_non_anthropic_provider
    normalize_tool_choice_for_provider = services.normalize_tool_choice_for_provider
    provider_wire_profile = services.provider_wire_profile
    sanitize_assistant_pseudo_tool_text_history = services.sanitize_assistant_pseudo_tool_text_history
    profile = provider_wire_profile(provider, pcfg, body)
    out = normalize_thinking_for_non_anthropic_provider(provider, pcfg, body)
    requested_model = str(out.get("model") or pcfg.get("current_model") or "")
    if provider == "kimi" and is_kimi_k3_model_id(requested_model):
        thinking = out.get("thinking")
        if isinstance(thinking, dict) and str(thinking.get("type") or "").lower() != "disabled":
            normalized_thinking = dict(thinking)
            normalized_thinking["effort"] = "max"
            out = dict(out)
            out["thinking"] = normalized_thinking
    out = normalize_tool_choice_for_provider(provider, pcfg, out)
    out = sanitize_assistant_pseudo_tool_text_history(out)
    out = normalize_anthropic_tool_turns_for_provider(provider, pcfg, out)
    if profile.get("upstream_format") == "anthropic-messages":
        out = normalize_anthropic_system_role_messages(out)
    return out


__all__ = ["ProviderRequestServices", "ProviderWireServices", "normalize_provider_request", "resolve_provider_wire_profile"]
