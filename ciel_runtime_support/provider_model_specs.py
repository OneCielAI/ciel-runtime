"""Current-model specification lookup, projection, and refresh service."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .architecture import ProviderContextPolicy


@dataclass(frozen=True, slots=True)
class ModelSpecLookupPorts:
    read_cache: Callable[[str, dict[str, Any]], Mapping[str, dict[str, Any]]]
    normalize_model: Callable[[str, str], str]
    upstream_model: Callable[[str, dict[str, Any]], str]
    strip_context_suffix: Callable[[str], str]


@dataclass(frozen=True, slots=True)
class ModelSpecMutationPorts:
    positive_int: Callable[[Any], int | None]
    apply_model_profile: Callable[[str, dict[str, Any]], list[str]]
    context_policy: Callable[[str, dict[str, Any]], ProviderContextPolicy]
    ollama_model_matches: Callable[[str, str | None], bool]
    preserve_ollama_cap: Callable[[dict[str, Any]], bool]
    format_context: Callable[[int | None], str]


@dataclass(frozen=True, slots=True)
class ModelSpecRefreshPorts:
    refresh_models: Callable[..., Sequence[str]]


class ProviderModelSpecService:
    def __init__(
        self,
        lookup: ModelSpecLookupPorts,
        mutation: ModelSpecMutationPorts,
        refresh: ModelSpecRefreshPorts,
    ) -> None:
        self.lookup = lookup
        self.mutation = mutation
        self.refresh_ports = refresh

    def current_info(
        self,
        provider: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        info = self.lookup.read_cache(provider, config)
        if not info:
            return {}
        upstream = self.lookup.upstream_model(provider, config)
        configured = str(config.get("current_model") or "")
        normalized_upstream = self.lookup.normalize_model(provider, upstream)
        normalized_configured = self.lookup.normalize_model(provider, configured)
        candidates = (
            normalized_upstream,
            normalized_configured,
            self.lookup.strip_context_suffix(normalized_configured),
        )
        for model_id in candidates:
            if model_id and model_id in info:
                return info[model_id]
        folded_current = normalized_upstream.casefold()
        for model_id, model_info in info.items():
            if (
                self.lookup.normalize_model(provider, model_id).casefold()
                == folded_current
            ):
                return model_info
        return {}

    def apply(self, provider: str, config: dict[str, Any]) -> list[str]:
        messages = self.mutation.apply_model_profile(provider, config)
        info = self.current_info(provider, config)
        max_context = (
            self.mutation.positive_int(info.get("max_model_len")) if info else None
        )
        if not max_context:
            return messages
        model = self.lookup.normalize_model(
            provider,
            self.lookup.upstream_model(provider, config),
        )
        strategy = self.mutation.context_policy(
            provider,
            config,
        ).settings_strategy
        if strategy == "ollama":
            cached_model = str(config.get("model_context_model") or "")
            cached_context = self.mutation.positive_int(config.get("model_context_max"))
            if (
                not self.mutation.ollama_model_matches(model, cached_model)
                or cached_context != max_context
            ):
                config["model_context_max"] = max_context
                config["model_context_model"] = model
                messages.append(self._context_notice(max_context))
            current_max = self.mutation.positive_int(config.get("num_ctx_max"))
            if not (
                current_max
                and current_max <= max_context
                and self.mutation.preserve_ollama_cap(config)
            ):
                config["num_ctx_max"] = (
                    min(current_max, max_context)
                    if current_max and current_max > max_context
                    else max_context
                )
        elif strategy == "standard":
            if self.mutation.positive_int(config.get("max_model_len")) != max_context:
                config["max_model_len"] = max_context
                messages.append(self._context_notice(max_context))
        return messages

    def refresh(self, provider: str, config: dict[str, Any]) -> list[str]:
        messages: list[str] = []
        try:
            models = self.refresh_ports.refresh_models(
                provider,
                config,
                force_refresh=True,
            )
            if models:
                messages.append(
                    f"Model specs refreshed from provider: {len(models)} model(s)."
                )
        except Exception as exc:
            messages.append(
                f"Model specs refresh failed: {type(exc).__name__}: {exc}"
            )
        messages.extend(self.apply(provider, config))
        return messages

    def _context_notice(self, max_context: int) -> str:
        return (
            "Model context size from provider specs: "
            f"{self.mutation.format_context(max_context)} "
            f"({max_context:,} tokens)."
        )
