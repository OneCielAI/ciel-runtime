"""Model cache invalidation and launch-time hydration orchestration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ModelCacheLifecyclePorts:
    invalidate_config: Callable[[], None]
    artifact_paths: Callable[[], tuple[Path, ...]]
    read_list_cache: Callable[[str, dict[str, Any]], list[str] | None]
    read_registry_models: Callable[[str, dict[str, Any], float], list[str] | None]
    upstream_model_ids: Callable[[str, dict[str, Any]], list[str]]
    catalog_model_ids: Callable[[str], list[str]]
    normalize_model_id: Callable[[str, str], str]
    unique_model_ids: Callable[[str, list[str]], list[str]]
    sorted_model_ids: Callable[[list[str]], list[str]]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class ModelCacheLifecycleService:
    ports: ModelCacheLifecyclePorts

    def clear(self) -> None:
        self.ports.invalidate_config()
        for path in self.ports.artifact_paths():
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def cached_or_configured_ids(
        self,
        provider: str,
        config: dict[str, Any],
    ) -> list[str]:
        model_ids = self.ports.read_list_cache(provider, config) or []
        if provider == "ollama-cloud":
            model_ids.extend(self.ports.catalog_model_ids(provider))
        for raw_model_id in config.get("custom_models", []) or []:
            model_id = self.ports.normalize_model_id(provider, raw_model_id)
            if model_id and model_id not in model_ids:
                model_ids.append(model_id)
        current = self.ports.normalize_model_id(provider, config.get("current_model") or "")
        if current and current not in model_ids and not current.startswith(
            f"ciel-runtime-{provider}-"
        ):
            model_ids.insert(0, current)
        model_ids = self.ports.unique_model_ids(provider, model_ids)
        return model_ids if provider == "anthropic" else self.ports.sorted_model_ids(model_ids)

    def ensure_for_launch(self, provider: str, config: dict[str, Any]) -> None:
        if self.ports.read_list_cache(provider, config):
            return
        if self.ports.read_registry_models(provider, config, 0):
            return
        try:
            model_ids = self.ports.upstream_model_ids(provider, config)
        except Exception as exc:
            self.ports.log(
                "WARN",
                "launch_model_cache_refresh_failed "
                f"provider={provider} error={type(exc).__name__}: {exc}",
            )
            return
        if model_ids:
            self.ports.log(
                "INFO",
                f"launch_model_cache_ready provider={provider} count={len(model_ids)}",
            )


__all__ = ["ModelCacheLifecyclePorts", "ModelCacheLifecycleService"]
