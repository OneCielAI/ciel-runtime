"""Concrete provider adapters and their shared transport bases."""

from .anthropic import AnthropicProviderAdapter
from .base import HttpBearerProviderAdapter, NoAuthProviderAdapter, OpenAICompatibleProviderAdapter
from .constants import PROVIDER_DEFAULT_BASE_URLS, ZAI_MODEL_FALLBACK_IDS
from .native import AgyProviderAdapter, CodexProviderAdapter
from .ollama import OllamaCloudProviderAdapter, OllamaProviderAdapter
from .openrouter import OpenRouterProviderAdapter
from .lm_studio import LMStudioProviderAdapter
from .nim import SelfHostedNimProviderAdapter
from .nvidia import NvidiaHostedProviderAdapter
from .vllm import VllmProviderAdapter
from .deepseek import DeepSeekProviderAdapter
from .zai import ZaiProviderAdapter

__all__ = [
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
    "ZaiProviderAdapter",
]
