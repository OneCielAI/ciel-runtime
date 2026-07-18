"""Production provider adapters for common HTTP authentication dialects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .architecture import ModelInfo, ProviderAdapter, ProviderConfig
from .registry import AdapterRegistry


PROVIDER_DEFAULT_BASE_URLS: dict[str, str] = {
    "anthropic": "https://api.anthropic.com",
    "agy": "https://antigravity.google",
    "codex": "https://api.openai.com",
    "ollama": "http://your-ollama:11434",
    "ollama-cloud": "https://ollama.com",
    "deepseek": "https://api.deepseek.com/anthropic",
    "opencode": "https://opencode.ai/zen",
    "opencode-go": "https://opencode.ai/zen/go",
    "kimi": "https://api.kimi.com/coding",
    "zai": "https://api.z.ai/api/anthropic",
    "vllm": "http://your-vllm:8000",
    "lm-studio": "http://127.0.0.1:1234/v1",
    "nvidia-hosted": "",
    "self-hosted-nim": "http://your-nim:8000",
    "openrouter": "https://openrouter.ai/api/v1",
    "fireworks": "https://api.fireworks.ai/inference",
}


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
        raw_models: Any = config.options.get("available_models") or config.options.get("models") or ()
        if isinstance(raw_models, str):
            raw_models = [raw_models]
        models: list[ModelInfo] = []
        if isinstance(raw_models, (list, tuple)):
            for item in raw_models:
                if isinstance(item, str) and item.strip():
                    models.append(ModelInfo(id=item.strip()))
                elif isinstance(item, dict) and str(item.get("id") or "").strip():
                    models.append(ModelInfo(id=str(item["id"]).strip(), raw=item))
        if config.model and all(model.id != config.model for model in models):
            models.insert(0, ModelInfo(id=config.model))
        return tuple(models)

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


@dataclass(frozen=True)
class NoAuthProviderAdapter(HttpBearerProviderAdapter):
    def build_headers(self, config: ProviderConfig, api_key: str | None) -> Mapping[str, str]:
        del config, api_key
        return {}


PROVIDER_ADAPTERS: AdapterRegistry[ProviderAdapter] = AdapterRegistry()


def _bearer_factory(
    *,
    provider_name: str,
    base_url: str = "",
    authorization_header: str = "authorization",
    require_api_key: bool = False,
    send_placeholder_key: bool = False,
) -> HttpBearerProviderAdapter:
    return HttpBearerProviderAdapter(
        name=provider_name,
        base_url=base_url or PROVIDER_DEFAULT_BASE_URLS.get(provider_name.lower(), ""),
        authorization_header=authorization_header,
        require_api_key=require_api_key,
        send_placeholder_key=send_placeholder_key,
    )


for _provider_name in (
    "ollama",
    "ollama-cloud",
    "vllm",
    "self-hosted-nim",
    "deepseek",
    "opencode",
    "opencode-go",
    "kimi",
    "zai",
    "fireworks",
):
    PROVIDER_ADAPTERS.register(
        _provider_name,
        lambda provider_name=_provider_name, **kwargs: _bearer_factory(
            provider_name=provider_name,
            send_placeholder_key=True,
            **kwargs,
        ),
    )

PROVIDER_ADAPTERS.register(
    "openrouter",
    lambda **kwargs: _bearer_factory(
        provider_name="OpenRouter",
        authorization_header="Authorization",
        require_api_key=True,
        **kwargs,
    ),
)
for _provider_name in ("lm-studio", "nvidia-hosted"):
    PROVIDER_ADAPTERS.register(
        _provider_name,
        lambda provider_name=_provider_name, **kwargs: _bearer_factory(provider_name=provider_name, **kwargs),
    )

for _provider_name in ("anthropic", "codex", "agy"):
    PROVIDER_ADAPTERS.register(
        _provider_name,
        lambda provider_name=_provider_name, **kwargs: NoAuthProviderAdapter(
            name=provider_name,
            base_url=str(kwargs.get("base_url") or PROVIDER_DEFAULT_BASE_URLS.get(provider_name, "")),
            include_x_api_key=False,
        ),
    )


__all__ = [
    "PROVIDER_ADAPTERS",
    "PROVIDER_DEFAULT_BASE_URLS",
    "HttpBearerProviderAdapter",
    "NoAuthProviderAdapter",
]
