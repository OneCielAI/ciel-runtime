"""Production provider adapters for common HTTP authentication dialects."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .architecture import (
    ModelInfo,
    ProviderAdapter,
    ProviderCapabilities,
    ProviderConfig,
    ProviderRequestPolicy,
)
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
    capabilities_value: ProviderCapabilities = field(default_factory=ProviderCapabilities)
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(
            chat_path="/v1/chat/completions",
            models_path="/v1/models",
        )
    )

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

    def capabilities(self, config: ProviderConfig) -> ProviderCapabilities:
        del config
        return self.capabilities_value

    def request_policy(self, config: ProviderConfig) -> ProviderRequestPolicy:
        del config
        return self.request_policy_value

    def build_model_headers(self, config: ProviderConfig, api_key: str | None) -> Mapping[str, str]:
        del config
        key = str(api_key or "").strip()
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


@dataclass(frozen=True)
class AnthropicProviderAdapter(NoAuthProviderAdapter):
    name: str = "anthropic"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["anthropic"]
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="anthropic_messages",
            supports_thinking=True,
            preserves_anthropic_thinking=True,
        )
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(chat_path="/v1/messages", models_path="/v1/models")
    )

    def build_model_headers(self, config: ProviderConfig, api_key: str | None) -> Mapping[str, str]:
        del config
        key = str(api_key or "").strip()
        return {"anthropic-version": "2023-06-01", "x-api-key": key} if key else {"anthropic-version": "2023-06-01"}


@dataclass(frozen=True)
class OpenAICompatibleProviderAdapter(HttpBearerProviderAdapter):
    """Base for providers that implement the OpenAI Chat/Models wire surface."""


@dataclass(frozen=True)
class OllamaProviderAdapter(HttpBearerProviderAdapter):
    name: str = "ollama"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["ollama"]
    send_placeholder_key: bool = True
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="ollama_chat",
            supports_thinking=True,
            local=True,
        )
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(
            chat_path="/api/chat",
            models_path="/api/tags",
            model_info_path="/api/show",
            default_timeout_seconds=300.0,
        )
    )

    def model_paths(self, config: ProviderConfig) -> tuple[str, ...]:
        del config
        return ("/api/tags", "/v1/models")


@dataclass(frozen=True)
class OllamaCloudProviderAdapter(OllamaProviderAdapter):
    name: str = "ollama-cloud"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["ollama-cloud"]
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(upstream_protocol="ollama_chat", supports_thinking=True)
    )


@dataclass(frozen=True)
class OpenRouterProviderAdapter(OpenAICompatibleProviderAdapter):
    name: str = "OpenRouter"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["openrouter"]
    authorization_header: str = "Authorization"
    require_api_key: bool = True
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(upstream_protocol="openai_chat", requires_api_key=True)
    )


@dataclass(frozen=True)
class LMStudioProviderAdapter(OpenAICompatibleProviderAdapter):
    name: str = "lm-studio"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["lm-studio"]
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(upstream_protocol="openai_chat", local=True)
    )

    def model_paths(self, config: ProviderConfig) -> tuple[str, ...]:
        del config
        return ("/api/v0/models", "/api/v1/models", "/v1/models", "/models")


@dataclass(frozen=True)
class VllmProviderAdapter(OpenAICompatibleProviderAdapter):
    name: str = "vllm"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["vllm"]
    send_placeholder_key: bool = True
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(upstream_protocol="openai_chat", local=True)
    )


@dataclass(frozen=True)
class NvidiaHostedProviderAdapter(OpenAICompatibleProviderAdapter):
    name: str = "nvidia-hosted"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["nvidia-hosted"]


@dataclass(frozen=True)
class SelfHostedNimProviderAdapter(OpenAICompatibleProviderAdapter):
    name: str = "self-hosted-nim"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["self-hosted-nim"]
    send_placeholder_key: bool = True
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(upstream_protocol="openai_chat", local=True)
    )


@dataclass(frozen=True)
class DeepSeekProviderAdapter(HttpBearerProviderAdapter):
    name: str = "deepseek"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["deepseek"]
    send_placeholder_key: bool = True
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(upstream_protocol="anthropic_messages", supports_thinking=True)
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(chat_path="/v1/messages", models_path="/v1/models")
    )


@dataclass(frozen=True)
class KimiProviderAdapter(HttpBearerProviderAdapter):
    name: str = "kimi"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["kimi"]
    send_placeholder_key: bool = True
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(upstream_protocol="anthropic_messages", supports_thinking=True)
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(chat_path="/v1/messages", models_path="/v1/models")
    )


@dataclass(frozen=True)
class ZaiProviderAdapter(HttpBearerProviderAdapter):
    name: str = "zai"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["zai"]
    send_placeholder_key: bool = True
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(upstream_protocol="anthropic_messages", supports_thinking=True)
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(chat_path="/v1/messages", models_path="/v1/models")
    )


@dataclass(frozen=True)
class FireworksProviderAdapter(OpenAICompatibleProviderAdapter):
    name: str = "fireworks"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["fireworks"]
    send_placeholder_key: bool = True


@dataclass(frozen=True)
class OpenCodeProviderAdapter(HttpBearerProviderAdapter):
    name: str = "opencode"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["opencode"]
    send_placeholder_key: bool = True
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(upstream_protocol="anthropic_messages", supports_thinking=True)
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(chat_path="/messages", models_path="/v1/models")
    )


@dataclass(frozen=True)
class OpenCodeGoProviderAdapter(OpenCodeProviderAdapter):
    name: str = "opencode-go"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["opencode-go"]


@dataclass(frozen=True)
class CodexProviderAdapter(NoAuthProviderAdapter):
    name: str = "codex"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["codex"]
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(upstream_protocol="openai_responses")
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(chat_path="/v1/responses", models_path="/v1/models")
    )


@dataclass(frozen=True)
class AgyProviderAdapter(NoAuthProviderAdapter):
    name: str = "agy"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["agy"]
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(upstream_protocol="openai_responses")
    )


PROVIDER_ADAPTERS: AdapterRegistry[ProviderAdapter] = AdapterRegistry()


def _configured_factory(adapter_type: type[ProviderAdapter]):
    def create(**kwargs: Any) -> ProviderAdapter:
        base_url = str(kwargs.get("base_url") or "").strip()
        return adapter_type(**({"base_url": base_url} if base_url else {}))

    return create


for _provider_name, _adapter_type in {
    "agy": AgyProviderAdapter,
    "anthropic": AnthropicProviderAdapter,
    "codex": CodexProviderAdapter,
    "deepseek": DeepSeekProviderAdapter,
    "fireworks": FireworksProviderAdapter,
    "kimi": KimiProviderAdapter,
    "lm-studio": LMStudioProviderAdapter,
    "nvidia-hosted": NvidiaHostedProviderAdapter,
    "ollama": OllamaProviderAdapter,
    "ollama-cloud": OllamaCloudProviderAdapter,
    "opencode": OpenCodeProviderAdapter,
    "opencode-go": OpenCodeGoProviderAdapter,
    "openrouter": OpenRouterProviderAdapter,
    "self-hosted-nim": SelfHostedNimProviderAdapter,
    "vllm": VllmProviderAdapter,
    "zai": ZaiProviderAdapter,
}.items():
    PROVIDER_ADAPTERS.register(_provider_name, _configured_factory(_adapter_type))


__all__ = [
    "PROVIDER_ADAPTERS",
    "PROVIDER_DEFAULT_BASE_URLS",
    "HttpBearerProviderAdapter",
    "NoAuthProviderAdapter",
    "AgyProviderAdapter",
    "AnthropicProviderAdapter",
    "CodexProviderAdapter",
    "DeepSeekProviderAdapter",
    "FireworksProviderAdapter",
    "KimiProviderAdapter",
    "LMStudioProviderAdapter",
    "NvidiaHostedProviderAdapter",
    "OllamaCloudProviderAdapter",
    "OllamaProviderAdapter",
    "OpenCodeGoProviderAdapter",
    "OpenCodeProviderAdapter",
    "OpenRouterProviderAdapter",
    "SelfHostedNimProviderAdapter",
    "VllmProviderAdapter",
    "ZaiProviderAdapter",
]
