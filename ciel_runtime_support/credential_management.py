"""Credential persistence transactions for provider API keys."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import os
from pathlib import Path
from threading import Lock, RLock
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class CredentialPersistencePorts:
    load_config: Callable[[], dict[str, Any]]
    save_config: Callable[[dict[str, Any]], None]
    clear_model_cache: Callable[[], None]
    parse_keys: Callable[[Any], list[str]]
    clear_requested: Callable[[Any], bool]
    rotation_name: Callable[[str, dict[str, Any]], str]


@dataclass(frozen=True, slots=True)
class ExternalCredentialPorts:
    enabled: Callable[[str], bool]
    store: Callable[[str], None]
    clear: Callable[[], None]
    has_key: Callable[[], bool]
    normalize_provider_config: Callable[[dict[str, Any]], bool]
    location: Any


@dataclass(frozen=True, slots=True)
class CredentialPresentationPorts:
    mask: Callable[[str | None], str]
    fingerprint: Callable[[str | None], str]


class CredentialRotationRepository:
    def __init__(self, cursor: dict[str, int], lock: Lock | RLock) -> None:
        self._cursor = cursor
        self._lock = lock

    def reset(self, name: str) -> None:
        with self._lock:
            self._cursor.pop(name, None)


@dataclass(frozen=True, slots=True)
class EnvCredentialRepository:
    """Persist one credential in a chmod-protected dotenv file."""

    path: Path
    key_name: str
    defaults: dict[str, str]
    read_env_file: Callable[[Path], dict[str, str]]
    parse_values: Callable[[Any], list[str]]

    def store(self, value: str) -> None:
        environment = self.read_env_file(self.path)
        environment[self.key_name] = value
        for name, default in self.defaults.items():
            environment.setdefault(name, default)
        self._write(environment)

    def clear(self) -> None:
        environment = self.read_env_file(self.path)
        if self.key_name not in environment:
            return
        environment.pop(self.key_name, None)
        self._write(environment)

    def has_key(self) -> bool:
        return bool(self.parse_values(self.read_env_file(self.path).get(self.key_name)))

    def _write(self, environment: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("".join(f"{key}={value}\n" for key, value in environment.items()))
        os.chmod(self.path, 0o600)


def nvidia_env_credential_repository(
    path: Path,
    read_env_file: Callable[[Path], dict[str, str]],
    parse_values: Callable[[Any], list[str]],
    base_url: str,
) -> EnvCredentialRepository:
    return EnvCredentialRepository(
        path=path,
        key_name="NVIDIA_API_KEY",
        defaults={
            "NVIDIA_BASE_URL": base_url,
            "PROXY_HOST": "127.0.0.1",
            "PROXY_PORT": "8788",
            "STORAGE_ENGINE": "sqlite",
        },
        read_env_file=read_env_file,
        parse_values=parse_values,
    )


@dataclass(frozen=True, slots=True)
class CredentialManagementService:
    persistence: CredentialPersistencePorts
    external: ExternalCredentialPorts
    presentation: CredentialPresentationPorts
    rotation: CredentialRotationRepository
    config_location: Any

    def store_one(self, provider: str, key: str) -> list[str]:
        if self.persistence.clear_requested(key):
            return self.clear(provider)
        config = self.persistence.load_config()
        provider_config = config["providers"][provider]
        if self.external.enabled(provider):
            self.external.store(key)
            provider_config.pop("api_keys", None)
            if self.external.normalize_provider_config(provider_config):
                self.persistence.save_config(config)
            location = self.external.location
        else:
            provider_config["api_key"] = key
            provider_config.pop("api_keys", None)
            self.persistence.save_config(config)
            location = self.config_location
        self.persistence.clear_model_cache()
        return [
            f"Stored API key for {provider}.",
            f"Saved: {self.presentation.mask(key)}; "
            f"fp {self.presentation.fingerprint(key)} in {location}",
        ]

    def clear(self, provider: str) -> list[str]:
        config = self.persistence.load_config()
        providers = config["providers"]
        other_key_fields = self._snapshot_other_provider_keys(providers, provider)
        provider_config = providers[provider]
        had_key = bool(
            self.persistence.parse_keys(provider_config.get("api_key"))
            or self.persistence.parse_keys(provider_config.get("api_keys"))
        )
        provider_config.pop("api_key", None)
        provider_config.pop("api_keys", None)
        if self.external.enabled(provider):
            had_key = had_key or self.external.has_key()
            self.external.clear()
            self.external.normalize_provider_config(provider_config)
        self._restore_other_provider_keys(providers, other_key_fields)
        self.persistence.save_config(config)
        self.persistence.clear_model_cache()
        self.rotation.reset(self.persistence.rotation_name(provider, provider_config))
        if had_key:
            return [f"Cleared stored API key(s) for {provider}. Other providers unchanged."]
        return [f"No stored API key(s) for {provider}; other providers unchanged."]

    def store_many(self, provider: str, keys: list[str]) -> list[str]:
        parsed = self.persistence.parse_keys(keys)
        if len(parsed) == 1 and self.persistence.clear_requested(parsed[0]):
            return self.clear(provider)
        if not parsed:
            raise SystemExit("No API keys provided; unchanged.")
        config = self.persistence.load_config()
        provider_config = config["providers"][provider]
        provider_config["api_key"] = parsed[0]
        if len(parsed) > 1:
            provider_config["api_keys"] = parsed
        else:
            provider_config.pop("api_keys", None)
        if self.external.enabled(provider):
            self.external.store(parsed[0])
            self.external.normalize_provider_config(provider_config)
        self.persistence.save_config(config)
        self.persistence.clear_model_cache()
        self.rotation.reset(self.persistence.rotation_name(provider, provider_config))
        return [
            f"Stored {len(parsed)} API key{'s' if len(parsed) != 1 else ''} for {provider}.",
            f"Round-robin: {'enabled' if len(parsed) > 1 else 'disabled'}",
            f"Primary: {self.presentation.mask(parsed[0])}; "
            f"fp {self.presentation.fingerprint(parsed[0])}",
        ]

    def store_input(self, provider: str, raw_value: str) -> list[str]:
        if self.persistence.clear_requested(raw_value):
            return self.clear(provider)
        keys = self.persistence.parse_keys(raw_value)
        if len(keys) > 1:
            return self.store_many(provider, keys)
        if len(keys) == 1:
            return self.store_one(provider, keys[0])
        raise SystemExit("No API key provided; unchanged.")

    @staticmethod
    def _snapshot_other_provider_keys(
        providers: dict[str, Any], target_provider: str
    ) -> dict[str, tuple[bool, Any, bool, Any]]:
        snapshots: dict[str, tuple[bool, Any, bool, Any]] = {}
        for name, provider_config in providers.items():
            if name == target_provider or not isinstance(provider_config, dict):
                continue
            snapshots[name] = (
                "api_key" in provider_config,
                deepcopy(provider_config.get("api_key")),
                "api_keys" in provider_config,
                deepcopy(provider_config.get("api_keys")),
            )
        return snapshots

    @staticmethod
    def _restore_other_provider_keys(
        providers: dict[str, Any], snapshots: dict[str, tuple[bool, Any, bool, Any]]
    ) -> None:
        for name, (has_key, key, has_keys, keys) in snapshots.items():
            provider_config = providers.get(name)
            if not isinstance(provider_config, dict):
                continue
            if has_key:
                provider_config["api_key"] = key
            else:
                provider_config.pop("api_key", None)
            if has_keys:
                provider_config["api_keys"] = keys
            else:
                provider_config.pop("api_keys", None)


__all__ = [
    "CredentialManagementService",
    "CredentialPersistencePorts",
    "CredentialPresentationPorts",
    "CredentialRotationRepository",
    "EnvCredentialRepository",
    "ExternalCredentialPorts",
    "nvidia_env_credential_repository",
]
