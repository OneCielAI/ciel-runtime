"""First-class xAI API provider adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..architecture import (
    MessageProtocol,
    ModelInfo,
    ProviderCapabilities,
    ProviderConfig,
    ProviderContextPolicy,
    ProviderModelCatalogPolicy,
    ProviderOptionPresentationPolicy,
    ProviderRequestPolicy,
    ProviderStatusPolicy,
)
from ..runtime_constants import DEFAULT_REQUEST_TIMEOUT_MS
from .base import HttpBearerProviderAdapter, provider_configuration


XAI_MODEL_FALLBACK_IDS: tuple[str, ...] = (
    "grok-4.6",
    "grok-build-0.1",
    "grok-4.5",
    "grok-4.3",
    "grok-4.20-multi-agent-0309",
    "grok-4.20-0309-reasoning",
    "grok-4.20-0309-non-reasoning",
)

XAI_MEDIA_MODEL_FALLBACK_IDS: tuple[str, ...] = (
    "grok-imagine-image-2.0",
    "grok-imagine-image",
    "grok-imagine-image-quality",
    "grok-imagine-video-1.5",
    "grok-imagine-video",
    "grok-voice-think-fast-2.0",
)

_MODEL_CONTEXT_WINDOWS: tuple[tuple[str, int], ...] = (
    ("grok-build-", 256_000),
    ("grok-4.6", 500_000),
    ("grok-4.5", 500_000),
    ("grok-4.20", 1_000_000),
    ("grok-4.3", 1_000_000),
)


def _context_window(model: str) -> int:
    normalized = str(model or "").strip().casefold()
    for prefix, context in _MODEL_CONTEXT_WINDOWS:
        if normalized.startswith(prefix):
            return context
    return 500_000


@dataclass(frozen=True)
class XaiProviderAdapter(HttpBearerProviderAdapter):
    """xAI text inference, including native Responses and compaction support."""

    name: str = "xai"
    base_url: str = "https://api.x.ai/v1"
    configuration_defaults_value: Mapping[str, Any] = field(
        default_factory=lambda: provider_configuration(
            "grok-4.6",
            custom_models=XAI_MODEL_FALLBACK_IDS,
            native_compat=False,
            context_window=500_000,
            max_model_len=500_000,
            max_output_tokens=32_768,
            context_reserve_tokens=32_768,
            request_timeout_ms=max(DEFAULT_REQUEST_TIMEOUT_MS, 600_000),
            stream_enabled=True,
            stream_word_chunking=False,
            effort_level="high",
            supports_tool_choice=True,
            supports_vision=True,
            responses_compaction=True,
        )
    )
    authorization_header: str = "Authorization"
    include_x_api_key: bool = False
    require_api_key: bool = True
    api_key_display_name_value: str = "xAI"
    api_key_launch_error_value: str = "Launch blocked: xAI requires an API key."
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="openai_responses",
            supports_thinking=True,
            reasoning_output_recovery="minimum",
            minimum_reasoning_effort="low",
            requires_api_key=True,
        )
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(
            chat_path="/v1/chat/completions",
            models_path="/v1/models",
            default_timeout_seconds=600.0,
        )
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(
            kind="openai",
            fallback_models=XAI_MODEL_FALLBACK_IDS,
            allow_configured_fallback=True,
            authoritative_upstream_catalog=True,
        )
    )

    def list_models(self, config: ProviderConfig) -> tuple[ModelInfo, ...]:
        discovered = list(super().list_models(config))
        if not discovered:
            discovered = [ModelInfo(id=model) for model in XAI_MODEL_FALLBACK_IDS]
        return tuple(
            ModelInfo(
                id=model.id,
                display_name=model.display_name,
                context_window=model.context_window or _context_window(model.id),
                max_output_tokens=model.max_output_tokens,
                supports_tools=True if model.supports_tools is None else model.supports_tools,
                supports_vision=(
                    model.id.casefold().startswith("grok-4")
                    if model.supports_vision is None
                    else model.supports_vision
                ),
                raw=model.raw,
            )
            for model in discovered
        )

    def build_headers(
        self, config: ProviderConfig, api_key: str | None
    ) -> Mapping[str, str]:
        headers = dict(super().build_headers(config, api_key))
        conversation_id = str(config.options.get("conversation_id") or "").strip()
        if conversation_id:
            headers["x-grok-conv-id"] = conversation_id
        return headers

    def supported_protocols(
        self, config: ProviderConfig, model: str | None = None
    ) -> frozenset[MessageProtocol]:
        del config, model
        return frozenset({"openai_chat", "openai_responses"})

    def select_protocol(
        self,
        operation: MessageProtocol,
        config: ProviderConfig,
        model: str | None = None,
    ) -> MessageProtocol:
        del config, model
        return "openai_responses" if operation == "openai_responses" else "openai_chat"

    def resolve_endpoint(self, operation: str, config: ProviderConfig) -> str:
        if operation == "openai_responses_compact":
            return "/v1/responses/compact"
        return super().resolve_endpoint(operation, config)

    def model_configuration_profile(
        self, config: ProviderConfig
    ) -> tuple[Mapping[str, Any], str | None]:
        context = _context_window(config.model)
        return (
            {
                "context_window": context,
                "max_model_len": context,
                "model_profile": f"xai-{config.model or 'grok'}-{context}",
            },
            f"xAI model profile applied: {context:,}-token context. Start a new session.",
        )

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(
            capacity_strategy="configured_first",
            settings_strategy="standard",
            hosted_timeout=True,
        )

    def option_presentation_policy(
        self, config: ProviderConfig
    ) -> ProviderOptionPresentationPolicy:
        del config
        return ProviderOptionPresentationPolicy(
            show_tool_choice=True,
            show_stream=True,
            show_rate_limit_controls=True,
            show_sampling_controls=True,
            show_ip_family_control=True,
        )

    def status_policy(self, config: ProviderConfig) -> ProviderStatusPolicy:
        del config
        return ProviderStatusPolicy(
            kind="catalog", label="xAI", catalog_path="/v1/models"
        )

    def openai_reasoning_effort(
        self,
        config: ProviderConfig,
        model: str,
        request: Mapping[str, Any],
    ) -> str | None:
        requested = request.get("reasoning_effort") or config.options.get("effort_level")
        value = str(requested or "high").strip().casefold()
        allowed = {"low", "medium", "high"}
        if str(model).casefold().startswith("grok-4.6"):
            allowed.add("xhigh")
        return value if value in allowed else "high"


__all__ = [
    "XAI_MEDIA_MODEL_FALLBACK_IDS",
    "XAI_MODEL_FALLBACK_IDS",
    "XaiProviderAdapter",
]
