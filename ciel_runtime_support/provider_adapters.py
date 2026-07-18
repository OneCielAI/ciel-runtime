"""Production provider adapters for common HTTP authentication dialects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .architecture import ModelInfo, ProviderAdapter, ProviderConfig


@dataclass(frozen=True)
class HttpBearerProviderAdapter(ProviderAdapter):
    """Provider adapter for the bearer/x-api-key variants used by compatible APIs."""

    name: str
    base_url: str = ""
    authorization_header: str = "authorization"
    include_x_api_key: bool = True
    require_api_key: bool = False
    send_placeholder_key: bool = False

    def default_base_url(self) -> str:
        return self.base_url

    def list_models(self, config: ProviderConfig) -> Sequence[ModelInfo]:
        return ()

    def build_headers(self, config: ProviderConfig, api_key: str | None) -> Mapping[str, str]:
        key = str(api_key or "").strip()
        if self.require_api_key and not key:
            raise RuntimeError(f"{self.name} requires a configured API key.")
        if not key and self.send_placeholder_key:
            key = "not-used"
        if not key:
            return {}
        headers = {self.authorization_header: f"Bearer {key}"}
        if self.include_x_api_key:
            headers["x-api-key"] = key
        return headers


__all__ = ["HttpBearerProviderAdapter"]
