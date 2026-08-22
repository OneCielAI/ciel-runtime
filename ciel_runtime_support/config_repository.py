"""Configuration persistence port with atomic JSON file storage."""

from __future__ import annotations

import copy
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any


_SHARED_CREDENTIAL_NAMES = frozenset(
    {
        "access_token",
        "api_key",
        "api_keys",
        "authorization",
        "bearer_token",
        "client_secret",
        "cookie",
        "copilot_token",
        "copilot_token_expires_at",
        "github_access_token",
        "oauth_token_record",
        "password",
        "refresh_token",
        "speech_api_key",
        "tailscale_auth_key",
        "token",
        "webhook_secret",
        "x-api-key",
    }
)
_SHARED_CREDENTIAL_SUFFIXES = (
    "_access_token",
    "_api_key",
    "_api_keys",
    "_auth_key",
    "_bearer_token",
    "_client_secret",
    "_password",
    "_refresh_token",
    "_secret",
)


def is_shared_credential_field(name: Any) -> bool:
    normalized = str(name or "").strip().lower()
    return normalized in _SHARED_CREDENTIAL_NAMES or normalized.endswith(
        _SHARED_CREDENTIAL_SUFFIXES
    )


def without_shared_credentials(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: without_shared_credentials(item)
            for key, item in value.items()
            if not is_shared_credential_field(key)
        }
    if isinstance(value, list):
        return [without_shared_credentials(item) for item in value]
    return copy.deepcopy(value)


def shared_credentials(value: Any) -> Any:
    if not isinstance(value, dict):
        return {}
    extracted: dict[str, Any] = {}
    for key, item in value.items():
        if is_shared_credential_field(key):
            extracted[key] = copy.deepcopy(item)
        elif isinstance(item, dict):
            nested = shared_credentials(item)
            if nested:
                extracted[key] = nested
        elif isinstance(item, list):
            nested_items = [shared_credentials(entry) for entry in item]
            if any(nested_items):
                extracted[key] = nested_items
    return extracted


def overlay_config(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = overlay_config(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def build_default_config(provider_defaults: dict[str, Any]) -> dict[str, Any]:
    return {
        "current_provider": "nvidia-hosted",
        "last_launch_action": "",
        "language": "en",
        "migrations": {},
        "router_debug_external_access": False,
        "router_debug_external_access_confirmed": False,
        "router_debug_message_preview_chars": 0,
        "web_backend": {
            "enabled": False,
            "host": "127.0.0.1",
            "port": 0,
            "tailscale_https": False,
            "workspace": "",
        },
        "web_backends": {},
        "workspace_mcp": {"servers": {}},
        "external_event_receivers": {},
        "transcript_events": {
            "enabled": False,
            "url": "",
            "authorization": "",
            "timeout_seconds": 5,
            "poll_interval_ms": 1000,
            "max_batch_bytes": 1048576,
            "start_mode": "tail",
        },
        "remote_instructions": {
            "enabled": False,
            "claude_url": "",
            "codex_url": "",
            "agy_url": "",
            "kimi_url": "",
            "grok_url": "",
            "authorization": "",
            "timeout_seconds": 5,
            "max_bytes": 1048576,
        },
        "remote_memory": {
            "enabled": False,
            "manifest_url": "",
            "authorization": "",
            "directory": ".ciel/memory",
            "timeout_seconds": 5,
            "max_manifest_bytes": 1048576,
            "max_file_bytes": 4194304,
            "max_total_bytes": 33554432,
            "max_files": 256,
        },
        "speech": {
            "colab": {
                "enabled": True,
                "distribution": "Ubuntu-26.04",
                "auth": "adc",
                "profile": "default",
                "asr_session": "ciel-asr",
                "tts_session": "ciel-tts",
                "asr_model": "Qwen/Qwen3-ASR-0.6B",
                "asr_accelerator": "T4",
                "tts_accelerator": "T4",
                "tts_backend": "moss",
            },
            "asr": {
                "enabled": False,
                "base_url": "http://ciel-asr:8000",
                "endpoint": "/v1/audio/transcriptions",
                "model": "Qwen/Qwen3-ASR-0.6B",
                "language": "auto",
                "silence_ms": 900,
                "min_speech_ms": 300,
                "vad_threshold": 0.018,
                "api_key": "",
                "timeout_seconds": 300,
            },
            "tts": {
                "enabled": False,
                "base_url": "http://ciel-tts:8091",
                "endpoint": "/v1/audio/speech",
                "voices_endpoint": "/v1/audio/voices",
                "model": "OpenMOSS-Team/MOSS-TTS-Nano",
                "voice": "default",
                "language": "ko",
                "ref_audio": "",
                "ref_text": "",
                "response_format": "wav",
                "speed": 1.0,
                "auto_speak": False,
                "streaming": False,
                "sample_rate": 48000,
                "api_key": "",
                "timeout_seconds": 300,
            },
            "tailscale": {
                "enabled": True,
                "asr_hostname": "ciel-asr",
                "tts_hostname": "ciel-tts",
            },
        },
        "claude_code": {
            "compat_prompt_for_non_anthropic": True,
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
        fallback_path: Path | None = None,
        bootstrap: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._path = path
        self._defaults = defaults
        self._merge = merge
        self._migrate = migrate
        self._normalize = normalize
        self._fallback_path = fallback_path
        self._bootstrap = bootstrap
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
        primary_exists = self._path.exists()
        source_path = self._path
        if not primary_exists and self._fallback_path is not None and self._fallback_path.exists():
            source_path = self._fallback_path
        try:
            data = json.loads(source_path.read_text(encoding="utf-8")) if source_path.exists() else {}
        except (OSError, ValueError, TypeError):
            data = {}
        config = self._merge(self._defaults, data if isinstance(data, dict) else {})
        self._migrate(config)
        self._normalize(config)
        if not primary_exists:
            if self._bootstrap is not None:
                self._bootstrap(config)
            try:
                self.save(config)
                return copy.deepcopy(config)
            except OSError:
                pass
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
        fallback_path: Path | None = None,
        bootstrap: Callable[[dict[str, Any]], None] | None = None,
    ) -> JsonConfigRepository:
        if self._repository is None or self._repository.path != path:
            self._repository = JsonConfigRepository(
                path=path,
                defaults=defaults,
                merge=merge,
                migrate=migrate,
                normalize=normalize,
                fallback_path=fallback_path,
                bootstrap=bootstrap,
            )
        return self._repository


class WorkspaceConfigRepository:
    """Workspace selections layered with credentials from one shared repository."""

    def __init__(
        self,
        *,
        workspace: JsonConfigRepository,
        shared: JsonConfigRepository,
    ) -> None:
        self._workspace = workspace
        self._shared = shared

    @property
    def path(self) -> Path:
        return self._workspace.path

    def load(self) -> dict[str, Any]:
        workspace_config = self._workspace.load()
        sanitized = without_shared_credentials(workspace_config)
        if workspace_config != sanitized:
            self._workspace.save(sanitized)
        return overlay_config(sanitized, shared_credentials(self._shared.load()))

    def save(self, config: dict[str, Any]) -> None:
        shared_config = self._shared.load()
        updated_shared = overlay_config(
            without_shared_credentials(shared_config),
            shared_credentials(config),
        )
        self._shared.save(updated_shared)
        self._workspace.save(without_shared_credentials(config))

    def invalidate(self) -> None:
        self._workspace.invalidate()
        self._shared.invalidate()


__all__ = [
    "ConfigRepositoryProvider",
    "JsonConfigRepository",
    "WorkspaceConfigRepository",
    "build_default_config",
    "deep_merge",
    "is_shared_credential_field",
    "normalize_loaded_config",
    "overlay_config",
    "shared_credentials",
    "without_shared_credentials",
]
