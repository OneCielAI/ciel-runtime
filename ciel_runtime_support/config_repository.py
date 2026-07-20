"""Configuration persistence port with atomic JSON file storage."""

from __future__ import annotations

import copy
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any


def build_default_config(provider_defaults: dict[str, Any]) -> dict[str, Any]:
    return {
        "current_provider": "nvidia-hosted",
        "language": "en",
        "migrations": {},
        "router_debug_external_access": False,
        "router_debug_external_access_confirmed": False,
        "router_debug_message_preview_chars": 0,
        "claude_code": {
            "compat_prompt_for_non_anthropic": True,
            "channels": [],
            "development_channels": False,
            "channel_delivery": "llm",
        },
        "cleanup": {"managed_services_on_launch": True},
        "web_search": {
            "auto_for_non_native": True,
            "provider": "duckduckgo",
            "package": "ddg-mcp-search",
            "fetch_enabled": True,
            "fetch_package": "mcp-server-fetch",
            "fetch_ignore_robots_txt": False,
            "fetch_user_agent": "",
        },
        "providers": provider_defaults,
    }


def deep_merge(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = json.loads(json.dumps(defaults))
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def normalize_loaded_config(
    config: dict[str, Any],
    normalize_model_id: Callable[[str, str], str],
) -> None:
    providers = config["providers"]
    cloud = providers.get("ollama-cloud", {})
    local_key = providers.get("ollama", {}).get("api_key", "")
    if not cloud.get("api_key") and local_key and local_key not in {"ollama", "dummy", "not-used"}:
        cloud["api_key"] = local_key
    for provider_name, provider_config in providers.items():
        if not isinstance(provider_config, dict):
            continue
        if provider_config.get("current_model"):
            provider_config["current_model"] = normalize_model_id(
                provider_name, str(provider_config["current_model"])
            )
        custom_models = provider_config.get("custom_models")
        if isinstance(custom_models, list):
            provider_config["custom_models"] = [
                normalize_model_id(provider_name, str(model_id))
                for model_id in custom_models
                if str(model_id).strip()
            ]


class JsonConfigRepository:
    """Own configuration caching, persistence, migration, and normalization."""

    def __init__(
        self,
        *,
        path: Path,
        defaults: dict[str, Any],
        merge: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
        migrate: Callable[[dict[str, Any]], None],
        normalize: Callable[[dict[str, Any]], None],
    ) -> None:
        self._path = path
        self._defaults = defaults
        self._merge = merge
        self._migrate = migrate
        self._normalize = normalize
        self._cache: dict[str, Any] | None = None
        self._cache_mtime = 0.0

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, Any]:
        try:
            mtime = self._path.stat().st_mtime
        except OSError:
            mtime = 0.0
        if self._cache is not None and mtime == self._cache_mtime:
            return copy.deepcopy(self._cache)
        try:
            data = json.loads(self._path.read_text(encoding="utf-8")) if self._path.exists() else {}
        except (OSError, ValueError, TypeError):
            data = {}
        config = self._merge(self._defaults, data if isinstance(data, dict) else {})
        self._migrate(config)
        self._normalize(config)
        self._cache = config
        self._cache_mtime = mtime
        return copy.deepcopy(config)

    def save(self, config: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f"{self._path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        temporary.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self._path)
        self._cache = copy.deepcopy(config)
        try:
            self._cache_mtime = self._path.stat().st_mtime
        except OSError:
            self._cache_mtime = 0.0

    def invalidate(self) -> None:
        self._cache = None
        self._cache_mtime = 0.0


class ConfigRepositoryProvider:
    """Path-aware repository factory that owns the mutable cache instance."""

    def __init__(self) -> None:
        self._repository: JsonConfigRepository | None = None

    def get(
        self,
        *,
        path: Path,
        defaults: dict[str, Any],
        merge: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
        migrate: Callable[[dict[str, Any]], None],
        normalize: Callable[[dict[str, Any]], None],
    ) -> JsonConfigRepository:
        if self._repository is None or self._repository.path != path:
            self._repository = JsonConfigRepository(
                path=path,
                defaults=defaults,
                merge=merge,
                migrate=migrate,
                normalize=normalize,
            )
        return self._repository


__all__ = [
    "ConfigRepositoryProvider",
    "JsonConfigRepository",
    "build_default_config",
    "deep_merge",
    "normalize_loaded_config",
]
