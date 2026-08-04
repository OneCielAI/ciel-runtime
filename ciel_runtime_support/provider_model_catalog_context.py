"""Provider model registry and cache lifecycle bounded context."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .model_cache_lifecycle import (
    ModelCacheLifecyclePorts,
    ModelCacheLifecycleService,
)
from .model_registry_repository import (
    ModelRegistryPaths,
    ModelRegistryPolicy,
    ModelRegistryRepository,
)


@dataclass(frozen=True, slots=True)
class ProviderModelRegistryConfig:
    config_dir: Path
    registry_path: Path
    list_cache_path: Path
    gateway_cache_path: Path
    ttl_seconds: float


@dataclass(frozen=True, slots=True)
class ProviderModelRegistryPorts:
    cache_key: Callable[..., str]
    unique_ids: Callable[..., list[str]]
    normalize_id: Callable[..., str]
    positive_int: Callable[..., int]
    recommendations: Callable[..., dict[str, Any]]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class ProviderModelCachePorts:
    invalidate_config: Callable[[], None]
    upstream_model_ids: Callable[..., list[str]]
    catalog_model_ids: Callable[..., list[str]]
    sorted_model_ids: Callable[..., list[str]]


@dataclass(frozen=True, slots=True)
class ProviderModelCatalogContext:
    config: ProviderModelRegistryConfig
    registry: ProviderModelRegistryPorts
    cache: ProviderModelCachePorts

    def registry_repository(self) -> ModelRegistryRepository:
        return ModelRegistryRepository(
            paths=ModelRegistryPaths(
                self.config.config_dir,
                self.config.registry_path,
                self.config.list_cache_path,
            ),
            policy=ModelRegistryPolicy(
                cache_key=self.registry.cache_key,
                unique_ids=self.registry.unique_ids,
                normalize_id=self.registry.normalize_id,
                positive_int=self.registry.positive_int,
                recommendations=self.registry.recommendations,
                log=self.registry.log,
            ),
            ttl_seconds=self.config.ttl_seconds,
        )

    def lifecycle_service(self) -> ModelCacheLifecycleService:
        repository = self.registry_repository()
        return ModelCacheLifecycleService(
            ModelCacheLifecyclePorts(
                invalidate_config=self.cache.invalidate_config,
                artifact_paths=lambda: (
                    self.config.gateway_cache_path,
                    self.config.list_cache_path,
                    self.config.registry_path,
                ),
                read_list_cache=repository.read_list_cache,
                read_registry_models=repository.read_registry_models,
                upstream_model_ids=self.cache.upstream_model_ids,
                catalog_model_ids=self.cache.catalog_model_ids,
                normalize_model_id=self.registry.normalize_id,
                unique_model_ids=self.registry.unique_ids,
                sorted_model_ids=self.cache.sorted_model_ids,
                log=self.registry.log,
            )
        )

    def clear(self) -> None:
        self.lifecycle_service().clear()

    def cached_or_configured_ids(
        self, provider: str, pcfg: dict[str, Any]
    ) -> list[str]:
        return self.lifecycle_service().cached_or_configured_ids(provider, pcfg)

    def ensure_for_launch(self, provider: str, pcfg: dict[str, Any]) -> None:
        self.lifecycle_service().ensure_for_launch(provider, pcfg)


@dataclass(frozen=True, slots=True)
class ProviderModelCatalogCompatibilityApi:
    context: Callable[[], ProviderModelCatalogContext]

    def registry_repository(self) -> ModelRegistryRepository:
        return self.context().registry_repository()

    def lifecycle_service(self) -> ModelCacheLifecycleService:
        return self.context().lifecycle_service()

    def clear(self) -> None:
        self.context().clear()

    def cached_or_configured_ids(self, *args: Any, **kwargs: Any) -> list[str]:
        return self.context().cached_or_configured_ids(*args, **kwargs)

    def ensure_for_launch(self, *args: Any, **kwargs: Any) -> None:
        self.context().ensure_for_launch(*args, **kwargs)


__all__ = [
    "ProviderModelCachePorts",
    "ProviderModelCatalogCompatibilityApi",
    "ProviderModelCatalogContext",
    "ProviderModelRegistryConfig",
    "ProviderModelRegistryPorts",
]
