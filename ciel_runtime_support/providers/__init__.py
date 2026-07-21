"""Concrete provider adapters and their shared transport bases."""

from .anthropic import AnthropicProviderAdapter
from .base import (
    HttpBearerProviderAdapter,
    NoAuthProviderAdapter,
    OpenAICompatibleProviderAdapter,
)
from .constants import PROVIDER_DEFAULT_BASE_URLS, ZAI_MODEL_FALLBACK_IDS
from .native import AgyProviderAdapter, CodexProviderAdapter
from .ollama import OllamaCloudProviderAdapter, OllamaProviderAdapter
from .openrouter import OpenRouterProviderAdapter
from .lm_studio import LMStudioProviderAdapter
from .nim import SelfHostedNimProviderAdapter
from .nvidia import NvidiaHostedProviderAdapter
from .vllm import VllmProviderAdapter
from .deepseek import DeepSeekProviderAdapter
from .fireworks import FireworksProviderAdapter
from .zai import ZaiProviderAdapter
from .kimi import KimiProviderAdapter
from .opencode import OpenCodeProviderAdapter
from .opencode_go import OpenCodeGoProviderAdapter
from .catalog import (
    COMPATIBLE_PROVIDER_SPECS,
    CatalogOpenAIProviderAdapter,
    CompatibleProviderSpec,
)
from .anthropic_catalog import (
    ANTHROPIC_COMPATIBLE_PROVIDER_SPECS,
    AnthropicCompatibleProviderSpec,
    CatalogAnthropicProviderAdapter,
)
from .cloud import AzureOpenAIProviderAdapter, CodeBuddyCnProviderAdapter

__all__ = [
    "ANTHROPIC_COMPATIBLE_PROVIDER_SPECS",
    "AnthropicCompatibleProviderSpec",
    "CatalogAnthropicProviderAdapter",
    "AzureOpenAIProviderAdapter",
    "CodeBuddyCnProviderAdapter",
    "HttpBearerProviderAdapter",
    "AnthropicProviderAdapter",
    "NoAuthProviderAdapter",
    "OpenAICompatibleProviderAdapter",
    "PROVIDER_DEFAULT_BASE_URLS",
    "ZAI_MODEL_FALLBACK_IDS",
    "AgyProviderAdapter",
    "CodexProviderAdapter",
    "OllamaCloudProviderAdapter",
    "OllamaProviderAdapter",
    "OpenRouterProviderAdapter",
    "LMStudioProviderAdapter",
    "NvidiaHostedProviderAdapter",
    "SelfHostedNimProviderAdapter",
    "VllmProviderAdapter",
    "DeepSeekProviderAdapter",
    "FireworksProviderAdapter",
    "ZaiProviderAdapter",
    "KimiProviderAdapter",
    "OpenCodeProviderAdapter",
    "OpenCodeGoProviderAdapter",
    "COMPATIBLE_PROVIDER_SPECS",
    "CatalogOpenAIProviderAdapter",
    "CompatibleProviderSpec",
]
