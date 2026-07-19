"""Production provider adapters for common HTTP authentication dialects."""

from __future__ import annotations

from typing import Any

from .architecture import (
    ProviderAdapter,
)
from .registry import AdapterRegistry
from .providers.base import (
    HttpBearerProviderAdapter,
    NoAuthProviderAdapter,
)
from .providers.anthropic import AnthropicProviderAdapter
from .providers.constants import PROVIDER_DEFAULT_BASE_URLS, ZAI_MODEL_FALLBACK_IDS
from .providers.native import AgyProviderAdapter, CodexProviderAdapter
from .providers.ollama import OllamaCloudProviderAdapter, OllamaProviderAdapter
from .providers.openrouter import OpenRouterProviderAdapter
from .providers.lm_studio import LMStudioProviderAdapter
from .providers.nim import SelfHostedNimProviderAdapter
from .providers.nvidia import NvidiaHostedProviderAdapter
from .providers.vllm import VllmProviderAdapter
from .providers.deepseek import DeepSeekProviderAdapter
from .providers.zai import ZaiProviderAdapter
from .providers.kimi import KimiProviderAdapter
from .providers.fireworks import FireworksProviderAdapter
from .providers.opencode import OpenCodeProviderAdapter
from .providers.opencode_go import OpenCodeGoProviderAdapter


PROVIDER_ADAPTERS: AdapterRegistry[ProviderAdapter] = AdapterRegistry()


def _configured_factory(adapter_type: type[ProviderAdapter]):
    def create(**kwargs: Any) -> ProviderAdapter:
        base_url = str(kwargs.get("base_url") or "").strip()
        return adapter_type(**({"base_url": base_url} if base_url else {}))

    return create


_PROVIDER_DEFINITIONS: dict[str, tuple[type[ProviderAdapter], str]] = {
    "anthropic": (AnthropicProviderAdapter, "Claude Native"),
    "agy": (AgyProviderAdapter, "AGY"),
    "codex": (CodexProviderAdapter, "Codex Native"),
    "ollama": (OllamaProviderAdapter, "Ollama"),
    "ollama-cloud": (OllamaCloudProviderAdapter, "Ollama Cloud"),
    "deepseek": (DeepSeekProviderAdapter, "DeepSeek.com"),
    "opencode": (OpenCodeProviderAdapter, "OpenCode Zen"),
    "opencode-go": (OpenCodeGoProviderAdapter, "OpenCode Go"),
    "kimi": (KimiProviderAdapter, "Kimi.com"),
    "zai": (ZaiProviderAdapter, "Z.AI GLM"),
    "vllm": (VllmProviderAdapter, "vLLM"),
    "lm-studio": (LMStudioProviderAdapter, "LM Studio"),
    "nvidia-hosted": (NvidiaHostedProviderAdapter, "Nvidia Hosted"),
    "self-hosted-nim": (SelfHostedNimProviderAdapter, "Self Hosted NIM"),
    "openrouter": (OpenRouterProviderAdapter, "OpenRouter"),
    "fireworks": (FireworksProviderAdapter, "Fireworks.ai"),
}
PROVIDER_LABELS: dict[str, str] = {
    name: label for name, (_, label) in _PROVIDER_DEFINITIONS.items()
}

for _provider_name, (_adapter_type, _provider_label) in _PROVIDER_DEFINITIONS.items():
    PROVIDER_ADAPTERS.register(_provider_name, _configured_factory(_adapter_type))


def provider_default_configurations() -> dict[str, dict[str, Any]]:
    """Build fresh provider defaults from the registered concrete adapters."""

    return {
        name: dict(PROVIDER_ADAPTERS.create(name).default_configuration())
        for name in PROVIDER_ADAPTERS.names()
    }


__all__ = [
    "PROVIDER_ADAPTERS",
    "PROVIDER_DEFAULT_BASE_URLS",
    "PROVIDER_LABELS",
    "provider_default_configurations",
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
    "ZAI_MODEL_FALLBACK_IDS",
]
