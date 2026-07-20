"""Provider-neutral runtime model metadata discovery service."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ProviderRuntimeInfoPorts:
    strategy: Callable[[str], str]
    lm_studio_info: Callable[..., dict[str, Any] | None]
    request_base: Callable[[str, dict[str, Any]], str]
    current_model: Callable[[str, dict[str, Any]], str]
    http_json: Callable[..., Any]
    join_url: Callable[[str, str], str]
    model_headers: Callable[[str, dict[str, Any]], dict[str, str]]
    positive_int: Callable[[Any], int | None]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class ProviderRuntimeInfoService:
    ports: ProviderRuntimeInfoPorts

    @staticmethod
    def model_context(item: dict[str, Any]) -> int | None:
        keys = (
            "max_model_len",
            "max_context_length",
            "context_length",
            "contextLength",
            "max_context_tokens",
            "max_position_embeddings",
            "trainingContextLength",
        )
        for key in keys:
            value = ProviderRuntimeInfoService._positive_int(item.get(key))
            if value:
                return value
        for key, value in item.items():
            if isinstance(key, str) and key.rsplit(".", 1)[-1] in keys:
                fixed = ProviderRuntimeInfoService._positive_int(value)
                if fixed:
                    return fixed
        details = item.get("details")
        if isinstance(details, dict):
            for key in keys:
                value = ProviderRuntimeInfoService._positive_int(details.get(key))
                if value:
                    return value
        return None

    @staticmethod
    def _positive_int(value: Any) -> int | None:
        try:
            fixed = int(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return fixed if fixed > 0 else None

    def discover(
        self,
        provider: str,
        provider_config: dict[str, Any],
        timeout: float = 3.0,
    ) -> dict[str, Any] | None:
        strategy = self.ports.strategy(provider)
        if not strategy:
            return None
        if strategy == "lm_studio":
            info = self.ports.lm_studio_info(provider_config, timeout=timeout)
            if info:
                return info
        base = self.ports.request_base(provider, provider_config)
        if not base:
            return None
        current = self.ports.current_model(provider, provider_config)
        models_url = self.ports.join_url(base, "/v1/models")
        try:
            data = self.ports.http_json(
                models_url,
                headers=self.ports.model_headers(provider, provider_config),
                timeout=timeout,
            )
        except Exception as exc:
            self.ports.log(
                "WARN",
                f"provider_runtime_info_failed provider={provider} error={type(exc).__name__}: {exc}",
            )
            return None
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return None
        candidates = [item for item in items if isinstance(item, dict)]
        selected = next((item for item in candidates if str(item.get("id") or "") == current), None)
        selected = selected or (candidates[0] if candidates else None)
        if not selected:
            return None
        return {
            "models_url": models_url,
            "requested_model": current,
            "runtime_model": str(selected.get("id") or ""),
            "max_model_len": self.model_context(selected),
            "owned_by": selected.get("owned_by"),
            "root": selected.get("root"),
        }

    def context_limit(self, provider: str, provider_config: dict[str, Any], timeout: float = 3.0) -> int | None:
        info = self.discover(provider, provider_config, timeout)
        return self.ports.positive_int(info.get("max_model_len")) if info else None


__all__ = ["ProviderRuntimeInfoPorts", "ProviderRuntimeInfoService"]
