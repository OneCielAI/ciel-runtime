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


__all__ = [
    "ProviderContextServices",
    "cap_context_settings",
    "resolve_context_capacity",
]
