from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .architecture import ProviderContextPolicy


@dataclass(frozen=True)
class ProviderContextServices:
    positive_int: Callable[[Any], int | None]
    model_context_hint: Callable[[str], int | None]
    nvidia_context_default: Callable[[str], int | None]
    upstream_context_limit: Callable[..., int | None]
    ollama_context_limit: Callable[[dict[str, Any]], int | None]


@dataclass(frozen=True)
class ContextPresetServices:
    positive_int: Callable[[Any], int | None]
    ollama_options: Callable[[dict[str, Any]], dict[str, Any]]
    ollama_thinking_enabled: Callable[[str, dict[str, Any]], bool]


def resolve_context_capacity(
    provider: str,
    config: dict[str, Any],
    policy: ProviderContextPolicy,
    services: ProviderContextServices,
) -> int | None:
    strategy = policy.capacity_strategy
    model = str(config.get("current_model") or "")
    positive_int = services.positive_int

    def hint() -> int | None:
        return services.model_context_hint(model)

    def configured() -> int | None:
        return positive_int(config.get("context_window"))

    def maximum() -> int | None:
        return positive_int(config.get("max_model_len"))
    if strategy == "managed":
        return None
    if strategy == "nvidia":
        return services.nvidia_context_default(model)
    if strategy == "remote_first":
        return (
            services.upstream_context_limit(provider, config, timeout=1.0)
            or maximum()
            or hint()
            or configured()
        )
    if strategy == "hint_first":
        return hint() or maximum() or configured()
    if strategy == "configured_first":
        return maximum() or hint() or configured()
    if strategy == "ollama":
        return (
            services.ollama_context_limit(config)
            or hint()
            or positive_int(config.get("num_ctx_max"))
            or positive_int(config.get("num_ctx"))
        )
    return hint() or configured() or maximum()


def cap_context_settings(
    config: dict[str, Any],
    capacity: int | None,
    policy: ProviderContextPolicy,
    *,
    positive_int: Callable[[Any], int | None],
) -> list[str]:
    if not capacity:
        return []
    if policy.settings_strategy == "ollama":
        maximum = positive_int(config.get("num_ctx_max"))
        messages: list[str] = []
        if maximum and maximum > capacity:
            config["num_ctx_max"] = capacity
            messages.append(f"Context max capped to selected model limit: {capacity:,} tokens.")
        for key in ("num_ctx_min", "num_ctx"):
            value = positive_int(config.get(key))
            if value and value > capacity:
                config[key] = capacity
        return messages
    if policy.settings_strategy == "standard":
        context_window = positive_int(config.get("context_window"))
        if context_window and context_window > capacity:
            config["context_window"] = capacity
            return [f"Context window capped to selected model limit: {capacity:,} tokens."]
    return []


def infer_context_preset(
    config: dict[str, Any],
    policy: ProviderContextPolicy,
    services: ContextPresetServices,
) -> str | None:
    positive_int = services.positive_int
    if policy.settings_strategy == "managed" and not policy.managed_preset_inference:
        return None
    if policy.settings_strategy == "ollama":
        options = services.ollama_options(config)
        output = positive_int(options.get("num_predict")) or 0
        fixed_context = positive_int(config.get("num_ctx")) or 0
        minimum = positive_int(config.get("num_ctx_min")) or 0
        context = positive_int(config.get("num_ctx_max")) or 0
        if services.ollama_thinking_enabled(str(config.get("current_model") or ""), config):
            return "reasoning"
    else:
        output = positive_int(config.get("max_output_tokens")) or 0
        fixed_context = 0
        minimum = 0
        context = positive_int(config.get("context_window")) or 0
        if bool(config.get("think", False)):
            return "reasoning"
    if context >= 1048576:
        return "million-context-1m"
    if context >= 524288:
        return "long-context-512k"
    if context >= 307200:
        return "long-context-300k"
    if context >= 262144:
        return "long-context-256k"
    if context >= 131072 and output >= 8192:
        return "long-context-128k"
    if output >= 8192:
        return "large-output"
    if minimum >= 65536 or context >= 65536:
        return "long-context-65k"
    if fixed_context and fixed_context <= 32768 and output and output <= 2048:
        return "fast"
    if policy.settings_strategy != "ollama" and output and output <= 2048:
        return "fast"
    return None


def classify_model_family(
    config: dict[str, Any],
    policy: ProviderContextPolicy,
    capacity: int | None,
    services: ContextPresetServices,
) -> str:
    model = str(config.get("current_model") or "").lower()
    if any(marker in model for marker in ("coder", "codegemma", "starcoder", "devstral")):
        return "coding"
    if any(marker in model for marker in ("reason", "thinking", "r1", "qwq")):
        return "reasoning"
    if policy.context_family_before_size_markers:
        return _context_family(capacity)
    if any(marker in model for marker in ("1m", "v4-pro", "million")):
        return "million-context"
    if any(marker in model for marker in ("70b", "120b", "253b", "405b", "480b", "large", "ultra", "pro")):
        return "large"
    if policy.settings_strategy == "standard":
        return _context_family(capacity)
    if policy.settings_strategy == "ollama":
        context = (
            services.positive_int(config.get("num_ctx_max"))
            or services.positive_int(config.get("num_ctx"))
        )
        return _context_family(context)
    return "general"


def recommended_preset(family: str, capacity: int | None) -> str:
    if family in {"reasoning", "coding"}:
        return family
    if family == "million-context":
        return "million-context-1m"
    if family == "long-context":
        if capacity and capacity >= 524288:
            return "long-context-512k"
        if capacity and capacity >= 307200:
            return "long-context-300k"
        if capacity and capacity >= 262144:
            return "long-context-256k"
        if capacity and capacity >= 131072:
            return "long-context-128k"
        return "long-context-65k"
    return "balanced"


def required_context_for_preset(preset_id: str, policy: ProviderContextPolicy) -> int | None:
    profile = policy.preset_context_profile
    if preset_id == "million-context-1m":
        return 1048576
    if preset_id == "humanities-researcher":
        return 524288 if profile == "ollama" else 262144
    if preset_id in {"novelist", "mathematician", "product-architect"}:
        return 262144
    if preset_id == "teacher":
        return 131072
    if preset_id == "reasoning":
        return 262144 if profile == "nvidia" else 131072
    if preset_id == "large-output":
        if profile == "nvidia":
            return 262144
        return 131072 if profile == "ollama" else 65536
    return {
        "long-context-512k": 524288,
        "long-context-300k": 307200,
        "long-context-256k": 262144,
        "long-context-128k": 131072,
        "long-context-65k": 131072 if profile == "nvidia" else 65536,
    }.get(preset_id)


def _context_family(capacity: int | None) -> str:
    if capacity and capacity >= 1048576:
        return "million-context"
    if capacity and capacity >= 65536:
        return "long-context"
    return "general"


__all__ = [
    "ContextPresetServices",
    "ProviderContextServices",
    "cap_context_settings",
    "infer_context_preset",
    "classify_model_family",
    "recommended_preset",
    "required_context_for_preset",
    "resolve_context_capacity",
]
