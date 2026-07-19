"""Application service for synchronizing Ollama model context capacity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class OllamaContextSources:
    fetch_api_specs: Callable[[str, dict[str, Any], str], dict[str, Any]]
    load_catalog: Callable[[], dict[str, Any]]
    catalog_is_stale: Callable[[dict[str, Any]], bool]
    refresh_catalog: Callable[..., dict[str, Any]]
    catalog_context: Callable[[str], tuple[int | None, str | None, str | None]]
    fetch_library_context: Callable[[str], tuple[int | None, str | None, str | None]]
    update_catalog_context: Callable[[str, int, str | None, str | None], None]


@dataclass(frozen=True, slots=True)
class OllamaContextPolicy:
    positive_int: Callable[[Any], int | None]
    normalize_model_id: Callable[[str, str], str]
    model_context_hint: Callable[[str], int | None]
    context_model_matches: Callable[[str, str | None], bool]
    preserve_configured_cap: Callable[[dict[str, Any]], bool]
    log: Callable[[str, str], None]


def sync_ollama_context_limit(
    provider: str,
    config: dict[str, Any],
    model_id: str,
    sources: OllamaContextSources,
    policy: OllamaContextPolicy,
) -> list[str]:
    if provider not in ("ollama", "ollama-cloud"):
        return []
    try:
        api_specs = sources.fetch_api_specs(provider, config, model_id)
    except Exception as error:
        policy.log(
            "DEBUG",
            f"{provider} /api/show model specs unavailable for {model_id}: "
            f"{type(error).__name__}: {error}",
        )
        api_specs = {}
    limit = policy.positive_int(api_specs.get("max_model_len"))
    matched_model = policy.normalize_model_id(provider, model_id) if limit else ""
    source_url = "/api/show" if limit else ""
    if not limit:
        catalog = sources.load_catalog()
        if sources.catalog_is_stale(catalog):
            try:
                sources.refresh_catalog(include_contexts=False)
            except Exception as error:
                policy.log("WARN", f"ollama catalog: api refresh failed: {error}")
        limit, matched_model, source_url = sources.catalog_context(model_id)
    if not limit:
        limit, matched_model, source_url = sources.fetch_library_context(model_id)
        if limit:
            sources.update_catalog_context(model_id, limit, matched_model, source_url)
    if not limit:
        limit = policy.model_context_hint(model_id)
        if limit:
            matched_model = policy.normalize_model_id(provider, model_id)
        else:
            if not policy.context_model_matches(model_id, str(config.get("model_context_model") or "")):
                config.pop("model_context_max", None)
                config.pop("model_context_model", None)
            return []
    old_max = policy.positive_int(config.get("num_ctx_max"))
    config["model_context_max"] = limit
    config["model_context_model"] = matched_model
    if not (old_max and old_max <= limit and policy.preserve_configured_cap(config)):
        config["num_ctx_max"] = min(old_max, limit) if old_max and old_max > limit else limit
    minimum = policy.positive_int(config.get("num_ctx_min"))
    if minimum and minimum > limit:
        config["num_ctx_min"] = limit
    fixed_context = policy.positive_int(config.get("num_ctx"))
    if fixed_context and fixed_context > limit:
        config["num_ctx"] = limit
    label = f"{limit:,}"
    if old_max and old_max == limit:
        return [f"Ollama library context verified: {matched_model} -> {label} tokens."]
    detail = f" from {source_url}" if source_url else ""
    return [f"Ollama library context detected: {matched_model} -> {label} tokens{detail}."]
