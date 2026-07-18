"""Provider-registry-backed model panel projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ModelPanelCatalog:
    alias_for: Callable[..., Any]
    cached_or_configured_model_ids: Callable[..., Any]
    read_model_info_cache: Callable[..., Any]
    read_model_list_cache: Callable[..., Any]
    unique_model_ids: Callable[..., Any]
    upstream_model_ids: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ModelPanelPresentation:
    advisor_model_badge: Callable[..., Any]
    advisor_panel_notice: Callable[..., Any]
    format_context_tokens: Callable[..., Any]
    format_parameter_count: Callable[..., Any]
    model_panel_badge: Callable[..., Any]
    normalize_model_id: Callable[..., Any]
    positive_int: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ModelPanelServices:
    catalog: ModelPanelCatalog
    presentation: ModelPanelPresentation


def model_panel_rows(
    provider: str,
    pcfg: dict[str, Any],
    fetch: bool = True,
    force_refresh: bool = False,
    *,
    services: ModelPanelServices,
) -> tuple[list[str], list[str]]:
    catalog = services.catalog
    presentation = services.presentation
    alias_for = catalog.alias_for
    cached_or_configured_model_ids = catalog.cached_or_configured_model_ids
    format_context_tokens = presentation.format_context_tokens
    format_parameter_count = presentation.format_parameter_count
    model_panel_badge = presentation.model_panel_badge
    normalize_model_id = presentation.normalize_model_id
    positive_int = presentation.positive_int
    read_model_info_cache = catalog.read_model_info_cache
    read_model_list_cache = catalog.read_model_list_cache
    unique_model_ids = catalog.unique_model_ids
    upstream_model_ids = catalog.upstream_model_ids
    values = unique_model_ids(
        provider,
        upstream_model_ids(provider, pcfg, force_refresh=force_refresh)
        if fetch else cached_or_configured_model_ids(provider, pcfg),
    )
    rows: list[str] = []
    current = pcfg.get("current_model")
    seen_aliases: set[str] = set()
    deduped_values: list[str] = []
    cache = read_model_list_cache(provider, pcfg)
    cached_info = read_model_info_cache(provider, pcfg)
    rows.append("Refresh provider model list..." if cache is None else "Refresh provider model list")
    deduped_values.append("__refresh_models__")
    for mid in values:
        alias = alias_for(provider, mid)
        suffix = ""
        info = cached_info.get(normalize_model_id(provider, mid), {})
        max_context = positive_int(info.get("max_model_len"))
        if max_context:
            suffix += f"  [ctx {format_context_tokens(max_context)}]"
        parameter_count = format_parameter_count(info.get("parameter_count"))
        if parameter_count:
            suffix += f"  [{parameter_count} params]"
        badge = model_panel_badge(provider, pcfg, mid)
        if badge:
            suffix += f"  [{badge}]"
        alias_key = alias.casefold()
        if alias_key in seen_aliases:
            continue
        seen_aliases.add(alias_key)
        deduped_values.append(mid)
        mark = "*" if mid == current else " "
        rows.append(f"{mark} {mid}  {alias}{suffix}")
    rows.append("+ Custom model id...")
    deduped_values.append("__custom__")
    rows.append("Back")
    deduped_values.append("back")
    return rows, deduped_values


def advisor_model_panel_rows(
    provider: str,
    pcfg: dict[str, Any],
    fetch: bool = True,
    force_refresh: bool = False,
    *,
    services: ModelPanelServices,
) -> tuple[list[str], list[str]]:
    catalog = services.catalog
    presentation = services.presentation
    advisor_model_badge = presentation.advisor_model_badge
    advisor_panel_notice = presentation.advisor_panel_notice
    cached_or_configured_model_ids = catalog.cached_or_configured_model_ids
    normalize_model_id = presentation.normalize_model_id
    read_model_list_cache = catalog.read_model_list_cache
    unique_model_ids = catalog.unique_model_ids
    upstream_model_ids = catalog.upstream_model_ids
    notice = advisor_panel_notice(provider, pcfg)
    if notice:
        return list(notice[0]), list(notice[1])
    current = normalize_model_id(provider, pcfg.get("advisor_model", ""))
    values = unique_model_ids(
        provider,
        (
            upstream_model_ids(provider, pcfg, force_refresh=force_refresh)
            if fetch else cached_or_configured_model_ids(provider, pcfg)
        )
        + ([current] if current else []),
    )
    rows: list[str] = []
    rows.append(("* Disable Advisor Model" if not current else "  Disable Advisor Model"))
    deduped_values = [""]
    cache = read_model_list_cache(provider, pcfg)
    rows.append("Refresh provider model list..." if cache is None else "Refresh provider model list")
    deduped_values.append("__refresh_models__")
    seen: set[str] = set()
    for mid in values:
        if not mid or mid in seen:
            continue
        seen.add(mid)
        mark = "*" if mid == current else " "
        badge = advisor_model_badge(provider, pcfg, mid)
        suffix = f"  {badge}" if badge else ""
        rows.append(f"{mark} {mid}{suffix}")
        deduped_values.append(mid)
    rows.append("+ Custom advisor model id...")
    deduped_values.append("__custom__")
    rows.append("Back")
    deduped_values.append("back")
    return rows, deduped_values

