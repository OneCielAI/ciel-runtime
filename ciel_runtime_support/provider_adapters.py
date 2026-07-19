"""Production provider adapters for common HTTP authentication dialects."""

from __future__ import annotations

from typing import Any

from .architecture import (
    ProviderAdapter,
)
from .provider_descriptor import ProviderDescriptor, ProviderDescriptorRegistry
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


PROVIDER_DESCRIPTORS = ProviderDescriptorRegistry(
    (
        ProviderDescriptor("anthropic", "Claude Native", AnthropicProviderAdapter),
        ProviderDescriptor("agy", "AGY", AgyProviderAdapter),
        ProviderDescriptor("codex", "Codex Native", CodexProviderAdapter),
        ProviderDescriptor("ollama", "Ollama", OllamaProviderAdapter),
        ProviderDescriptor("ollama-cloud", "Ollama Cloud", OllamaCloudProviderAdapter),
        ProviderDescriptor("deepseek", "DeepSeek.com", DeepSeekProviderAdapter),
        ProviderDescriptor("opencode", "OpenCode Zen", OpenCodeProviderAdapter),
        ProviderDescriptor("opencode-go", "OpenCode Go", OpenCodeGoProviderAdapter),
        ProviderDescriptor("kimi", "Kimi.com", KimiProviderAdapter),
        ProviderDescriptor("zai", "Z.AI GLM", ZaiProviderAdapter),
        ProviderDescriptor("vllm", "vLLM", VllmProviderAdapter),
        ProviderDescriptor("lm-studio", "LM Studio", LMStudioProviderAdapter),
        ProviderDescriptor("nvidia-hosted", "Nvidia Hosted", NvidiaHostedProviderAdapter),
        ProviderDescriptor("self-hosted-nim", "Self Hosted NIM", SelfHostedNimProviderAdapter),
        ProviderDescriptor("openrouter", "OpenRouter", OpenRouterProviderAdapter),
        ProviderDescriptor("fireworks", "Fireworks.ai", FireworksProviderAdapter),
    )
)
PROVIDER_ADAPTERS: AdapterRegistry[ProviderAdapter] = AdapterRegistry()
PROVIDER_LABELS: dict[str, str] = {
    descriptor.normalized_name: descriptor.label
    for descriptor in PROVIDER_DESCRIPTORS.descriptors()
}

for _descriptor in PROVIDER_DESCRIPTORS.descriptors():
    PROVIDER_ADAPTERS.register(
        _descriptor.normalized_name,
        _descriptor.create,
        aliases=_descriptor.aliases,
    )


def provider_default_configurations() -> dict[str, dict[str, Any]]:
    """Build fresh provider defaults from the registered concrete adapters."""

    return {
        name: dict(PROVIDER_ADAPTERS.create(name).default_configuration())
        for name in PROVIDER_ADAPTERS.names()
    }


__all__ = [
    "PROVIDER_ADAPTERS",
    "PROVIDER_DEFAULT_BASE_URLS",
    "PROVIDER_DESCRIPTORS",
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
