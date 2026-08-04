"""Ollama catalog persistence and context-metadata bounded context."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import ollama_catalog as policy
from .ollama_catalog_repository import OllamaCatalogRepository


@dataclass(frozen=True, slots=True)
class OllamaCatalogRepositoryPorts:
    path: Path
    log: Callable[[str, str], None]
    with_user_agent: Callable[[dict[str, str] | None], dict[str, str]]


@dataclass(frozen=True, slots=True)
class OllamaCatalogProjectionPorts:
    normalize_model: Callable[[str, str], str]
    unique_models: Callable[[list[str]], list[str]]
    sorted_models: Callable[[list[str]], list[str]]
    model_lookup_ids: Callable[[str], list[str]]
    positive_int: Callable[[Any], int | None]
    catalog_url: str
    default_ttl_seconds: int


@dataclass(frozen=True, slots=True)
class OllamaCatalogWorkflowPorts:
    load: Callable[[], dict[str, Any]]
    save: Callable[[dict[str, Any]], None]
    fetch_json: Callable[[str, float], Any]
    fetch_context_map: Callable[[str, float], tuple[dict[str, int], str | None]]


@dataclass(frozen=True, slots=True)
class OllamaCatalogContext:
    repository_ports: OllamaCatalogRepositoryPorts
    projection: OllamaCatalogProjectionPorts
    workflow: OllamaCatalogWorkflowPorts

    def repository(self) -> OllamaCatalogRepository:
        return OllamaCatalogRepository(
            self.repository_ports.path,
            self.repository_ports.log,
            self.repository_ports.with_user_agent,
        )

    def load(self) -> dict[str, Any]:
        return self.repository().load()

    def save(self, catalog: dict[str, Any]) -> None:
        self.repository().save(catalog)

    def model_ids(
        self,
        provider: str = "ollama-cloud",
        catalog: dict[str, Any] | None = None,
    ) -> list[str]:
        source = catalog if isinstance(catalog, dict) else self.workflow.load()
        return policy.catalog_model_ids(
            source,
            provider,
            normalize_model_id=self.projection.normalize_model,
            unique_model_ids=self.projection.unique_models,
            sorted_model_ids=self.projection.sorted_models,
        )

    def is_stale(
        self, catalog: dict[str, Any], ttl_seconds: int | None = None
    ) -> bool:
        ttl = (
            self.projection.default_ttl_seconds
            if ttl_seconds is None
            else ttl_seconds
        )
        return policy.catalog_is_stale(catalog, ttl)

    def fetch_json(self, url: str, timeout: float = 12.0) -> Any:
        return self.repository().fetch_json(url, timeout)

    def fetch_context_map(
        self, base_model: str, timeout: float = 10.0
    ) -> tuple[dict[str, int], str | None]:
        return self.repository().fetch_library_context_map(base_model, timeout)

    def refresh(
        self, include_contexts: bool = True, timeout: float = 10.0
    ) -> dict[str, Any]:
        return policy.refresh_model_catalog(
            policy.OllamaCatalogRefreshServices(
                load_catalog=self.workflow.load,
                fetch_catalog=self.workflow.fetch_json,
                fetch_context_map=self.workflow.fetch_context_map,
                save_catalog=self.workflow.save,
                positive_int=self.projection.positive_int,
            ),
            include_contexts=include_contexts,
            timeout=timeout,
            catalog_url=self.projection.catalog_url,
        )

    def context_for_model(
        self, model_id: str
    ) -> tuple[int | None, str | None, str | None]:
        return policy.catalog_context_for_model(
            self.workflow.load(), model_id, self.projection.model_lookup_ids
        )

    def timeout_for_model(self, model_id: str) -> int | None:
        return policy.catalog_timeout_for_model(
            self.workflow.load(), model_id, self.projection.model_lookup_ids
        )

    def update_context(
        self,
        model_id: str,
        limit: int,
        matched_model: str | None,
        source_url: str | None,
    ) -> None:
        self.workflow.save(
            policy.with_updated_context(
                self.workflow.load(),
                model_id,
                limit,
                matched_model,
                source_url,
            )
        )

    def fetch_context_limit(
        self, model_id: str, timeout: float = 6.0
    ) -> tuple[int | None, str | None, str | None]:
        return policy.fetch_library_context_limit(
            model_id,
            timeout=timeout,
            fetch_context_map=self.workflow.fetch_context_map,
            positive_int=self.projection.positive_int,
        )


@dataclass(frozen=True, slots=True)
class OllamaCatalogCompatibilityApi:
    context: Callable[[], OllamaCatalogContext]

    def repository(self) -> OllamaCatalogRepository:
        return self.context().repository()

    def load(self) -> dict[str, Any]:
        return self.context().load()

    def save(self, catalog: dict[str, Any]) -> None:
        self.context().save(catalog)

    def model_ids(
        self,
        provider: str = "ollama-cloud",
        catalog: dict[str, Any] | None = None,
    ) -> list[str]:
        return self.context().model_ids(provider, catalog)

    def is_stale(
        self, catalog: dict[str, Any], ttl_seconds: int | None = None
    ) -> bool:
        return self.context().is_stale(catalog, ttl_seconds)

    def fetch_json(self, url: str, timeout: float = 12.0) -> Any:
        return self.context().fetch_json(url, timeout)

    def fetch_context_map(
        self, base_model: str, timeout: float = 10.0
    ) -> tuple[dict[str, int], str | None]:
        return self.context().fetch_context_map(base_model, timeout)

    def refresh(
        self, include_contexts: bool = True, timeout: float = 10.0
    ) -> dict[str, Any]:
        return self.context().refresh(include_contexts, timeout)

    def context_for_model(
        self, model_id: str
    ) -> tuple[int | None, str | None, str | None]:
        return self.context().context_for_model(model_id)

    def timeout_for_model(self, model_id: str) -> int | None:
        return self.context().timeout_for_model(model_id)

    def update_context(
        self,
        model_id: str,
        limit: int,
        matched_model: str | None,
        source_url: str | None,
    ) -> None:
        self.context().update_context(model_id, limit, matched_model, source_url)

    def fetch_context_limit(
        self, model_id: str, timeout: float = 6.0
    ) -> tuple[int | None, str | None, str | None]:
        return self.context().fetch_context_limit(model_id, timeout)


__all__ = [
    "OllamaCatalogCompatibilityApi",
    "OllamaCatalogContext",
    "OllamaCatalogProjectionPorts",
    "OllamaCatalogRepositoryPorts",
    "OllamaCatalogWorkflowPorts",
]
