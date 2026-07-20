"""Persistent provider model registry and short-lived model-list cache."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .runtime_constants import MODEL_CACHE_TTL_SECONDS


@dataclass(frozen=True, slots=True)
class ModelRegistryPaths:
    config_dir: Path
    registry: Path
    list_cache: Path


@dataclass(frozen=True, slots=True)
class ModelRegistryPolicy:
    cache_key: Callable[[str, dict[str, Any]], str]
    unique_ids: Callable[[str, list[str]], list[str]]
    normalize_id: Callable[[str, str], str]
    positive_int: Callable[[Any], int | None]
    recommendations: Callable[[str, list[str]], dict[str, Any]]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class ModelRegistryRepository:
    paths: ModelRegistryPaths
    policy: ModelRegistryPolicy
    ttl_seconds: float

    def read_registry(
        self,
        provider: str,
        config: dict[str, Any],
        max_age_seconds: float | None = None,
    ) -> dict[str, Any] | None:
        try:
            data = json.loads(self.paths.registry.read_text(encoding="utf-8"))
        except Exception:
            return None
        providers = data.get("providers") if isinstance(data, dict) else None
        entry = providers.get(provider) if isinstance(providers, dict) else None
        if not isinstance(entry, dict) or not self._key_matches(provider, config, entry):
            return None
        max_age = self.ttl_seconds if max_age_seconds is None else max_age_seconds
        try:
            if max_age > 0 and time.time() - float(entry.get("time", 0)) > max_age:
                return None
        except Exception:
            return None
        return entry if isinstance(entry.get("models"), list) else None

    def _key_matches(
        self,
        provider: str,
        config: dict[str, Any],
        entry: dict[str, Any],
    ) -> bool:
        current = self.policy.cache_key(provider, config)
        if entry.get("key") == current:
            return True
        if provider != "anthropic" or entry.get("source") != "anthropic-docs":
            return False
        try:
            saved_key = json.loads(str(entry.get("key") or "{}"))
            current_key = json.loads(current)
            saved_key.pop("api", None)
            current_key.pop("api", None)
            return saved_key == current_key
        except Exception:
            return False

    def read_registry_models(
        self,
        provider: str,
        config: dict[str, Any],
        max_age_seconds: float | None = None,
    ) -> list[str] | None:
        entry = self.read_registry(provider, config, max_age_seconds)
        models = entry.get("models") if entry else None
        if not isinstance(models, list):
            return None
        return self.policy.unique_ids(provider, [str(item) for item in models if str(item).strip()])

    def read_registry_info(
        self,
        provider: str,
        config: dict[str, Any],
        max_age_seconds: float | None = None,
    ) -> dict[str, dict[str, Any]]:
        entry = self.read_registry(provider, config, max_age_seconds)
        metadata = entry.get("metadata") if entry else None
        return self._normalize_info(provider, metadata)

    def write_registry(
        self,
        provider: str,
        config: dict[str, Any],
        models: list[str],
        source: str = "provider",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.paths.config_dir.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(self.paths.registry.read_text(encoding="utf-8")) if self.paths.registry.exists() else {}
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        providers = data.get("providers")
        if not isinstance(providers, dict):
            providers = {}
        providers[provider] = {
            "time": time.time(),
            "key": self.policy.cache_key(provider, config),
            "source": source,
            "models": self.policy.unique_ids(provider, models),
            "recommendations": self.policy.recommendations(provider, models),
            "metadata": metadata or {},
        }
        data.update(schema=1, providers=providers)
        try:
            self.paths.registry.write_text(
                json.dumps(data, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.chmod(self.paths.registry, 0o600)
        except Exception as error:
            self.policy.log(
                "WARN",
                f"model_registry_write_failed provider={provider} error={type(error).__name__}: {error}",
            )

    def read_list_cache(self, provider: str, config: dict[str, Any]) -> list[str] | None:
        try:
            data = json.loads(self.paths.list_cache.read_text())
        except Exception:
            return self.read_registry_models(provider, config, 0)
        if not isinstance(data, dict) or data.get("key") != self.policy.cache_key(provider, config):
            return self.read_registry_models(provider, config, 0)
        if time.time() - float(data.get("time", 0)) > self.ttl_seconds:
            if registry_models := self.read_registry_models(provider, config, 0):
                return registry_models
        models = data.get("models")
        if not isinstance(models, list):
            return None
        return self.policy.unique_ids(provider, [str(item) for item in models if str(item).strip()])

    def read_info_cache(self, provider: str, config: dict[str, Any]) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self.paths.list_cache.read_text())
        except Exception:
            return self.read_registry_info(provider, config, 0)
        if not isinstance(data, dict) or data.get("key") != self.policy.cache_key(provider, config):
            return self.read_registry_info(provider, config, 0)
        info = self._normalize_info(provider, data.get("metadata"))
        return info or self.read_registry_info(provider, config, 0)

    def _normalize_info(self, provider: str, metadata: Any) -> dict[str, dict[str, Any]]:
        raw_info = metadata.get("model_info") if isinstance(metadata, dict) else None
        if not isinstance(raw_info, dict):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for raw_id, raw_value in raw_info.items():
            model_id = self.policy.normalize_id(provider, str(raw_id))
            if not model_id or not isinstance(raw_value, dict):
                continue
            info = dict(raw_value)
            if context := self.policy.positive_int(info.get("max_model_len")):
                info["max_model_len"] = context
            result[model_id] = info
        return result

    def write_list_cache(
        self,
        provider: str,
        config: dict[str, Any],
        models: list[str],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.paths.config_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "time": time.time(),
            "key": self.policy.cache_key(provider, config),
            "models": models,
            "metadata": metadata or {},
        }
        try:
            self.paths.list_cache.write_text(json.dumps(data, indent=2) + "\n")
            os.chmod(self.paths.list_cache, 0o600)
        except Exception as error:
            self.policy.log(
                "WARN",
                f"model_list_cache_write_failed provider={provider} error={type(error).__name__}: {error}",
            )
        self.write_registry(provider, config, models, "provider", metadata)


@dataclass(frozen=True, slots=True)
class ModelRegistryApi:
    """Explicit public adapter for late-bound model registry repositories."""

    repository_factory: Callable[[], ModelRegistryRepository]

    def read_registry(self, provider: str, pcfg: dict[str, Any], max_age_seconds: float = MODEL_CACHE_TTL_SECONDS) -> dict[str, Any] | None:
        return self.repository_factory().read_registry(provider, pcfg, max_age_seconds)

    def read_registry_models(self, provider: str, pcfg: dict[str, Any], max_age_seconds: float = MODEL_CACHE_TTL_SECONDS) -> list[str] | None:
        return self.repository_factory().read_registry_models(provider, pcfg, max_age_seconds)

    def read_registry_info(self, provider: str, pcfg: dict[str, Any], max_age_seconds: float = MODEL_CACHE_TTL_SECONDS) -> dict[str, dict[str, Any]]:
        return self.repository_factory().read_registry_info(provider, pcfg, max_age_seconds)

    def write_registry(self, provider: str, pcfg: dict[str, Any], models: list[str], source: str = "provider", metadata: dict[str, Any] | None = None) -> None:
        self.repository_factory().write_registry(provider, pcfg, models, source, metadata)

    def read_list_cache(self, provider: str, pcfg: dict[str, Any]) -> list[str] | None:
        return self.repository_factory().read_list_cache(provider, pcfg)

    def read_info_cache(self, provider: str, pcfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return self.repository_factory().read_info_cache(provider, pcfg)

    def write_list_cache(self, provider: str, pcfg: dict[str, Any], models: list[str], metadata: dict[str, Any] | None = None) -> None:
        self.repository_factory().write_list_cache(provider, pcfg, models, metadata)
