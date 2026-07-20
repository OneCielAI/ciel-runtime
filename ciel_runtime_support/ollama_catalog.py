"""Pure Ollama catalog parsing and update policies.

Network and filesystem effects stay in the composition root.  This module is
the functional core used by those imperative-shell wrappers.
"""

from __future__ import annotations

import html
import math
import re
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Iterable


OLLAMA_MODEL_CATALOG_URL = "https://ollama.com/api/tags"


@dataclass(frozen=True)
class OllamaCatalogRefreshServices:
    load_catalog: Callable[[], dict[str, Any]]
    fetch_catalog: Callable[..., dict[str, Any]]
    fetch_context_map: Callable[..., tuple[dict[str, int], str | None]]
    save_catalog: Callable[[dict[str, Any]], None]
    positive_int: Callable[[Any], int | None]
    now: Callable[[], float] = time.time


def refresh_model_catalog(
    services: OllamaCatalogRefreshServices,
    *,
    include_contexts: bool = True,
    timeout: float = 10.0,
    catalog_url: str = OLLAMA_MODEL_CATALOG_URL,
) -> dict[str, Any]:
    old_catalog = services.load_catalog()
    old_models = old_catalog.get("models") if isinstance(old_catalog.get("models"), dict) else {}
    data = services.fetch_catalog(catalog_url, timeout=timeout)
    raw_models = data.get("models") if isinstance(data, dict) else None
    if raw_models is None and isinstance(data, dict):
        raw_models = data.get("data")
    if not isinstance(raw_models, list):
        raw_models = []
    models: dict[str, dict[str, Any]] = {}
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("model") or item.get("id") or "").strip()
        parts = model_catalog_key(name)
        if not parts:
            continue
        key, base, tag = parts
        entry = models.setdefault(
            key,
            {
                "id": base,
                "models": [],
                "tags": [],
                "raw": [],
                "context_windows": {},
                "context_source": None,
            },
        )
        if name not in entry["models"]:
            entry["models"].append(name)
        if tag not in entry["tags"]:
            entry["tags"].append(tag)
        entry["raw"].append(item)
    if not include_contexts and isinstance(old_models, dict):
        _restore_cached_contexts(models, old_models, services.positive_int)
    if include_contexts:
        _refresh_context_maps(models, timeout, services)
    catalog = {
        "schema": 1,
        "source": catalog_url,
        "updated_at": services.now(),
        "model_count": len(raw_models),
        "base_model_count": len(models),
        "models": models,
    }
    services.save_catalog(catalog)
    return catalog


def _restore_cached_contexts(
    models: dict[str, dict[str, Any]],
    old_models: dict[str, Any],
    positive_int: Callable[[Any], int | None],
) -> None:
    for key, entry in models.items():
        old_entry = old_models.get(key)
        if not isinstance(old_entry, dict):
            continue
        old_windows = old_entry.get("context_windows")
        if not isinstance(old_windows, dict) or not old_windows:
            continue
        entry["context_windows"] = old_windows
        entry["context_window"] = positive_int(old_entry.get("context_window")) or max(
            positive_int(value) or 0 for value in old_windows.values()
        )
        if isinstance(old_entry.get("recommended_timeout_ms_by_tag"), dict):
            entry["recommended_timeout_ms_by_tag"] = old_entry["recommended_timeout_ms_by_tag"]
        if positive_int(old_entry.get("recommended_timeout_ms")):
            entry["recommended_timeout_ms"] = positive_int(old_entry.get("recommended_timeout_ms"))
        entry["context_source"] = old_entry.get("context_source")


def _refresh_context_maps(
    models: dict[str, dict[str, Any]],
    timeout: float,
    services: OllamaCatalogRefreshServices,
) -> None:
    for key in sorted(models):
        entry = models[key]
        context_map, source = services.fetch_context_map(str(entry["id"]), timeout=timeout)
        if not context_map:
            continue
        entry["context_windows"] = context_map
        entry["context_window"] = max(context_map.values())
        entry["recommended_timeout_ms_by_tag"] = {
            tag: recommended_timeout_ms(tokens)
            for tag, tokens in context_map.items()
            if services.positive_int(tokens)
        }
        entry["recommended_timeout_ms"] = recommended_timeout_ms(entry["context_window"])
        entry["context_source"] = source


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None


def library_model_parts(model_id: str) -> tuple[str, str] | None:
    model = str(model_id or "").strip()
    if not model:
        return None
    base, tag = (model.split(":", 1) + ["latest"])[:2] if ":" in model else (model, "latest")
    base = base.strip()
    tag = tag.strip() or "latest"
    if "/" in base or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", base):
        return None
    return base, tag


def context_label_to_tokens(number: str, unit: str | None) -> int | None:
    try:
        value = float(number)
    except Exception:
        return None
    if value <= 0 or not math.isfinite(value):
        return None
    multiplier = {"k": 1024, "m": 1024**2, "g": 1024**3}.get(str(unit or "").strip().lower(), 1)
    return int(round(value * multiplier))


def recommended_timeout_ms(context_tokens: int | None, default_timeout_ms: int = 120_000) -> int:
    tokens = _positive_int(context_tokens)
    if not tokens:
        return default_timeout_ms
    if tokens >= 1024**2:
        return 300_000
    if tokens >= 512 * 1024:
        return 180_000
    return 120_000


def model_catalog_key(model_id: str) -> tuple[str, str, str] | None:
    parts = library_model_parts(model_id)
    if not parts:
        return None
    base, tag = parts
    return base.lower(), base, tag.lower()


def model_lookup_ids(model_id: str) -> list[str]:
    raw = str(model_id or "").strip()
    if not raw:
        return []
    candidates = [raw]
    normalized = raw.casefold().replace("_", "-")
    compact = re.sub(r"[^a-z0-9]+", "", normalized)

    def add(value: str) -> None:
        if value and value not in candidates:
            candidates.append(value)

    if ("qwen3.6" in normalized or "qwen36" in compact) and "27b" in normalized:
        add("qwen3.6:27b")
    if ("qwen3.6" in normalized or "qwen36" in compact) and "35b" in normalized:
        add("qwen3.6:35b-a3b")
        add("qwen3.6:35b")
    return candidates


def catalog_model_ids(
    catalog: dict[str, Any],
    provider: str,
    *,
    normalize_model_id: Callable[[str, str], str],
    unique_model_ids: Callable[[str, Iterable[str]], list[str]],
    sorted_model_ids: Callable[[Iterable[str]], list[str]],
) -> list[str]:
    models = catalog.get("models") if isinstance(catalog, dict) else None
    if not isinstance(models, dict):
        return []
    ids: list[str] = []
    for entry in models.values():
        if not isinstance(entry, dict):
            continue
        raw_models = entry.get("models")
        if isinstance(raw_models, list):
            ids.extend(normalize_model_id(provider, str(item)) for item in raw_models)
        base = str(entry.get("id") or "").strip()
        if not base:
            continue
        ids.append(normalize_model_id(provider, base))
        tags = entry.get("tags")
        if isinstance(tags, list):
            ids.extend(
                normalize_model_id(provider, f"{base}:{tag}")
                for tag in (str(item or "").strip() for item in tags)
                if tag and tag.lower() != "latest"
            )
    return sorted_model_ids(unique_model_ids(provider, [item for item in ids if item]))


def catalog_is_stale(catalog: dict[str, Any], ttl_seconds: int, now: float | None = None) -> bool:
    if not isinstance(catalog, dict) or not isinstance(catalog.get("models"), dict):
        return True
    try:
        updated_at = float(catalog.get("updated_at") or 0)
    except Exception:
        updated_at = 0.0
    current = time.time() if now is None else now
    return updated_at <= 0 or current - updated_at > ttl_seconds


def context_tokens_from_snippet(snippet: str, table_fallback: bool = True) -> int | None:
    match = re.search(r"\b(\d+(?:\.\d+)?)\s*([KMG])\s+context\s+window\b", snippet, re.IGNORECASE)
    if match:
        return context_label_to_tokens(match.group(1), match.group(2))
    if table_fallback:
        match = re.search(r">\s*(\d+(?:\.\d+)?)\s*([KM])\s*</p>", snippet, re.IGNORECASE)
        if match:
            return context_label_to_tokens(match.group(1), match.group(2))
    return None


def parse_library_context_map(page_html: str, base_model: str) -> dict[str, int]:
    text = html.unescape(page_html or "")
    base = str(base_model or "").strip()
    if not text or not base:
        return {}
    contexts: dict[str, int] = {}
    pattern = re.compile(
        r"(?<![A-Za-z0-9._/-])" + re.escape(base) + r":([A-Za-z0-9][A-Za-z0-9._-]*)",
        re.IGNORECASE,
    )
    for match in pattern.finditer(text):
        tag = match.group(1).strip().lower()
        # A tags page contains several rows. Prefer content following the
        # matched tag so a previous row's context label cannot leak forward.
        following = text[match.end(): match.end() + 3000]
        tokens = context_tokens_from_snippet(following)
        if not tokens:
            surrounding = text[max(0, match.start() - 700): match.end() + 3000]
            tokens = context_tokens_from_snippet(surrounding)
        if tag and tokens:
            contexts[tag] = max(contexts.get(tag, 0), tokens)
    if not contexts:
        match = re.search(r"\b(\d+(?:\.\d+)?)\s*([KMG])\s+context\s+window\b", text, re.IGNORECASE)
        tokens = context_label_to_tokens(match.group(1), match.group(2)) if match else None
        if tokens:
            contexts["latest"] = tokens
    return contexts


def catalog_context_for_model(
    catalog: dict[str, Any],
    model_id: str,
    model_lookup_ids: Callable[[str], Iterable[str]],
) -> tuple[int | None, str | None, str | None]:
    models = catalog.get("models", {}) if isinstance(catalog, dict) else {}
    if not isinstance(models, dict):
        return None, None, None
    for candidate_model in model_lookup_ids(model_id):
        parts = model_catalog_key(candidate_model)
        if not parts:
            continue
        key, base, tag = parts
        entry = models.get(key)
        if not isinstance(entry, dict):
            continue
        source = str(entry.get("context_source") or catalog.get("source") or "")
        windows = entry.get("context_windows")
        if isinstance(windows, dict):
            candidates = [tag, "cloud", "latest"]
            for candidate in dict.fromkeys(candidates):
                value = _positive_int(windows.get(candidate))
                if value:
                    return value, f"{base}:{candidate}", source or None
        value = _positive_int(entry.get("context_window"))
        if value:
            return value, base, source or None
    return None, None, None


def catalog_timeout_for_model(
    catalog: dict[str, Any],
    model_id: str,
    model_lookup_ids: Callable[[str], Iterable[str]],
) -> int | None:
    models = catalog.get("models", {}) if isinstance(catalog, dict) else {}
    if not isinstance(models, dict):
        return None
    for candidate_model in model_lookup_ids(model_id):
        parts = model_catalog_key(candidate_model)
        if not parts:
            continue
        key, _, tag = parts
        entry = models.get(key)
        if not isinstance(entry, dict):
            continue
        per_tag = entry.get("recommended_timeout_ms_by_tag")
        if isinstance(per_tag, dict):
            for candidate in dict.fromkeys([tag, "cloud", "latest"]):
                value = _positive_int(per_tag.get(candidate))
                if value:
                    return value
        value = _positive_int(entry.get("recommended_timeout_ms"))
        if value:
            return value
    return None


def with_updated_context(
    catalog: dict[str, Any],
    model_id: str,
    limit: int,
    matched_model: str | None,
    source_url: str | None,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    parts = model_catalog_key(model_id)
    if not parts or not _positive_int(limit):
        return catalog
    result = deepcopy(catalog)
    if not isinstance(result.get("models"), dict):
        result = {
            "schema": 1,
            "source": OLLAMA_MODEL_CATALOG_URL,
            "updated_at": now or time.time(),
            "model_count": 0,
            "base_model_count": 0,
            "models": {},
        }
    key, base, tag = parts
    entry = result["models"].setdefault(
        key,
        {"id": base, "models": [], "tags": [], "raw": [], "context_windows": {}, "context_source": None},
    )
    if model_id not in entry["models"]:
        entry["models"].append(model_id)
    matched_parts = model_catalog_key(matched_model or "")
    stored_tag = matched_parts[2] if matched_parts else tag
    if stored_tag not in entry.setdefault("tags", []):
        entry["tags"].append(stored_tag)
    entry.setdefault("context_windows", {})[stored_tag] = int(limit)
    entry.setdefault("recommended_timeout_ms_by_tag", {})[stored_tag] = recommended_timeout_ms(limit)
    entry["context_window"] = max(
        [_positive_int(value) or 0 for value in entry["context_windows"].values()] + [int(limit)]
    )
    entry["recommended_timeout_ms"] = recommended_timeout_ms(entry["context_window"])
    entry["context_source"] = source_url or entry.get("context_source")
    result["updated_at"] = time.time() if now is None else now
    result["base_model_count"] = len(result["models"])
    return result


def parse_library_context_limit(tags_html: str, full_model_id: str) -> int | None:
    text = html.unescape(tags_html or "")
    target = str(full_model_id or "").strip()
    if not text or not target:
        return None
    lower = text.lower()
    target_lower = target.lower()
    start = 0
    while True:
        index = lower.find(target_lower, start)
        if index < 0:
            return None
        tokens = context_tokens_from_snippet(text[max(0, index - 500): index + 2500])
        if tokens:
            return tokens
        start = index + len(target_lower)


def context_model_matches(current_model: str, cached_model: str | None) -> bool:
    current = str(current_model or "").strip().lower()
    cached = str(cached_model or "").strip().lower()
    if not current or not cached:
        return False

    def aliases(value: str) -> set[str]:
        result = {value}
        if ":" not in value:
            result.update({f"{value}:latest", f"{value}:cloud"})
        if value.endswith((":latest", ":cloud")):
            result.add(value.rsplit(":", 1)[0])
        return result

    return bool(aliases(current) & aliases(cached))


__all__ = [
    "OLLAMA_MODEL_CATALOG_URL",
    "OllamaCatalogRefreshServices",
    "catalog_context_for_model",
    "catalog_is_stale",
    "catalog_model_ids",
    "catalog_timeout_for_model",
    "context_label_to_tokens",
    "context_model_matches",
    "context_tokens_from_snippet",
    "library_model_parts",
    "model_catalog_key",
    "parse_library_context_limit",
    "parse_library_context_map",
    "recommended_timeout_ms",
    "refresh_model_catalog",
    "with_updated_context",
]
