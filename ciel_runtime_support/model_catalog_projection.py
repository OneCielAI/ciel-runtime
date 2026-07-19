from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class ModelCatalogProjectionServices:
    normalize_model_id: Callable[[str, str], str]
    model_context: Callable[[Mapping[str, Any]], int | None]
    positive_int: Callable[[Any], int | None]
    project_metadata: Callable[[Mapping[str, Any]], Mapping[str, Any]]


def project_model_info(
    provider: str,
    data: Any,
    services: ModelCatalogProjectionServices,
) -> dict[str, dict[str, Any]]:
    candidates = _model_candidates(data)
    if candidates is None:
        return {}
    output: dict[str, dict[str, Any]] = {}
    for item in candidates:
        if isinstance(item, str):
            model_id = item
            raw: Mapping[str, Any] = {}
        elif isinstance(item, Mapping):
            model_id = str(
                item.get("id")
                or item.get("key")
                or item.get("name")
                or item.get("model")
                or ""
            )
            raw = item
        else:
            continue
        normalized_id = services.normalize_model_id(provider, model_id.strip())
        if not normalized_id:
            continue
        info = dict(services.project_metadata(raw))
        max_context = services.positive_int(raw.get("max_context_length")) or services.model_context(raw)
        if max_context:
            info["max_model_len"] = max_context
        if info:
            output[normalized_id] = info
    return output


def _model_candidates(data: Any) -> list[Any] | None:
    candidates = data
    if isinstance(data, Mapping):
        candidates = data.get("data")
        if candidates is None:
            candidates = data.get("models")
        if candidates is None:
            candidates = data.get("model")
    if isinstance(candidates, Mapping) or isinstance(candidates, str):
        candidates = [candidates]
    return candidates if isinstance(candidates, list) else None
