"""GitHub Copilot provider backed by GitHub Device OAuth."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Mapping

from ..architecture import (
    MessageProtocol,
    ProviderCapabilities,
    ProviderConfig,
    ProviderContextPolicy,
    ProviderModelCatalogPolicy,
    ProviderOptionPresentationPolicy,
    ProviderRequestPolicy,
    ProviderStatusPolicy,
)
from ..github_copilot_oauth import (
    COPILOT_API_VERSION,
    COPILOT_CHAT_VERSION,
    COPILOT_USER_AGENT,
    COPILOT_VSCODE_VERSION,
)
from ..remote_bridge import PUBLIC_MODEL_ID_METADATA_KEY
from .base import HttpBearerProviderAdapter, provider_configuration


GITHUB_COPILOT_BASE_URL = "https://api.githubcopilot.com"
GITHUB_COPILOT_MODELS = (
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.3-codex",
    "gpt-5-mini",
    "claude-fable-5",
    "claude-sonnet-5",
    "claude-opus-5",
    "claude-opus-4.8",
    "claude-opus-4.8-fast",
    "claude-opus-4.7",
    "claude-sonnet-4.6",
    "claude-opus-4.6",
    "claude-sonnet-4.5",
    "claude-opus-4.5",
    "claude-haiku-4.5",
    "gemini-3.6-flash",
    "gemini-3.7-flash",
    "gemini-3.5-flash",
    "gemini-3.1-pro-preview",
    "kimi-k2.7-code",
    "kimi-k3",
    "mai-code-1.1-flash",
    "mai-code-1-flash",
    "grok-4.6",
    "grok-4.5",
)

# Fallback for a cold model cache. The Copilot /models response is
# authoritative when its supported_endpoints metadata is available.
GITHUB_COPILOT_RESPONSES_ONLY_MODELS = frozenset(
    {
        "gpt-5.3-codex",
        "gpt-5.4-mini",
        "gpt-5.5",
        "gpt-5.6-luna",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "grok-4.5",
        "grok-4.6",
        "mai-code-1-flash",
        "mai-code-1.1-flash",
    }
)
GITHUB_COPILOT_PUBLIC_MODEL_IDS = {
    "mai-code-1-flash-picker": "mai-code-1-flash",
}


@dataclass(frozen=True)
class GitHubCopilotOAuthProviderAdapter(HttpBearerProviderAdapter):
    name: str = "github-copilot-oauth"
    base_url: str = GITHUB_COPILOT_BASE_URL
    configuration_defaults_value: dict = field(
        default_factory=lambda: provider_configuration(
            GITHUB_COPILOT_MODELS[0],
            custom_models=GITHUB_COPILOT_MODELS,
            native_compat=True,
            context_window=128_000,
            max_output_tokens=16_384,
            context_reserve_tokens=16_384,
            request_timeout_ms=300_000,
            stream_enabled=True,
            stream_word_chunking=False,
        )
    )
    include_x_api_key: bool = False
    require_api_key: bool = True
    api_key_display_name_value: str = "GitHub Copilot OAuth"
    api_key_launch_error_value: str = (
        "Launch blocked: GitHub Copilot OAuth login is required. "
        "Run: ciel-runtimectl copilot-oauth login"
    )
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="openai_chat",
            supports_thinking=True,
            preserves_anthropic_thinking=True,
            requires_api_key=True,
        )
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(
            chat_path="/chat/completions",
            models_path="/models",
        )
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(
            kind="openai",
            fallback_models=GITHUB_COPILOT_MODELS,
            allow_configured_fallback=True,
            authoritative_upstream_catalog=True,
        )
    )

    def build_headers(
        self,
        config: ProviderConfig,
        api_key: str | None,
    ) -> Mapping[str, str]:
        headers = dict(super().build_headers(config, api_key))
        headers.update(
            {
                "copilot-integration-id": "vscode-chat",
                "editor-version": f"vscode/{COPILOT_VSCODE_VERSION}",
                "editor-plugin-version": (
                    f"copilot-chat/{COPILOT_CHAT_VERSION}"
                ),
                "user-agent": COPILOT_USER_AGENT,
                "openai-intent": "conversation-panel",
                "x-github-api-version": COPILOT_API_VERSION,
                "x-vscode-user-agent-library-version": "electron-fetch",
                "X-Initiator": "user",
                "Accept": "application/json",
            }
        )
        return headers

    def build_model_headers(
        self,
        config: ProviderConfig,
        api_key: str | None,
    ) -> Mapping[str, str]:
        return self.build_headers(config, api_key)

    def resolve_endpoint(self, operation: str, config: ProviderConfig) -> str:
        if operation in {"chat", "openai_chat"}:
            return self.request_policy(config).chat_path
        if operation == "openai_responses":
            return "/responses"
        return super().resolve_endpoint(operation, config)

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(
            capacity_strategy="configured_first",
            settings_strategy="standard",
            hosted_timeout=True,
        )

    def supported_protocols(
        self,
        config: ProviderConfig,
        model: str | None = None,
    ) -> frozenset[MessageProtocol]:
        del config, model
        return frozenset(
            {"openai_chat", "openai_responses", "anthropic_messages"}
        )

    def select_protocol(
        self,
        operation: MessageProtocol,
        config: ProviderConfig,
        model: str | None = None,
    ) -> MessageProtocol:
        normalized = str(model or "").lower()
        metadata = config.options.get("_ciel_model_metadata")
        endpoints = {
            str(endpoint).strip()
            for endpoint in (
                metadata.get("supported_endpoints", [])
                if isinstance(metadata, Mapping)
                else []
            )
            if str(endpoint).strip()
        }
        if endpoints:
            if operation == "openai_chat":
                if "/chat/completions" in endpoints:
                    return "openai_chat"
                if "/responses" in endpoints:
                    return "openai_responses"
                if "/v1/messages" in endpoints:
                    return "anthropic_messages"
            if operation == "anthropic_messages":
                if "/v1/messages" in endpoints:
                    return "anthropic_messages"
                if "/chat/completions" in endpoints:
                    return "openai_chat"
                if "/responses" in endpoints:
                    return "openai_responses"
            if "/responses" in endpoints:
                return "openai_responses"
            if "/v1/messages" in endpoints:
                return "anthropic_messages"
            if "/chat/completions" in endpoints:
                return "openai_chat"
        if normalized in GITHUB_COPILOT_RESPONSES_ONLY_MODELS:
            return "openai_responses"
        if "claude" in normalized:
            return "anthropic_messages"
        if operation == "openai_responses" and not any(
            family in normalized for family in ("gemini", "claude")
        ):
            return "openai_responses"
        return "openai_chat"

    def project_model_metadata(
        self, raw: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        metadata = dict(super().project_model_metadata(raw))
        for key in (
            "capabilities",
            "model_picker_category",
            "model_picker_enabled",
            "name",
            "policy",
            "preview",
            "vendor",
            "version",
        ):
            if raw.get(key) is not None:
                metadata[key] = deepcopy(raw[key])
        endpoints = raw.get("supported_endpoints")
        if isinstance(endpoints, list):
            metadata["supported_endpoints"] = [
                str(endpoint).strip()
                for endpoint in endpoints
                if str(endpoint).strip()
            ]
        capabilities = raw.get("capabilities")
        limits = (
            capabilities.get("limits")
            if isinstance(capabilities, Mapping)
            and isinstance(capabilities.get("limits"), Mapping)
            else {}
        )
        if limits.get("max_context_window_tokens") is not None:
            metadata["max_model_len"] = limits["max_context_window_tokens"]
        if limits.get("max_output_tokens") is not None:
            metadata["max_output_tokens"] = limits["max_output_tokens"]
        raw_model_id = str(raw.get("id") or "").strip()
        if public_model_id := GITHUB_COPILOT_PUBLIC_MODEL_IDS.get(raw_model_id):
            metadata[PUBLIC_MODEL_ID_METADATA_KEY] = public_model_id
        return metadata

    def router_native_anthropic_enabled(
        self,
        config: ProviderConfig,
        model: str | None = None,
    ) -> bool:
        del config
        return "claude" in str(model or "").lower()

    def normalize_request_options(
        self,
        config: ProviderConfig,
        request: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        del config
        normalized = dict(request)
        if normalized.get("reasoning_effort") == "none":
            normalized.pop("reasoning_effort", None)
        model = str(normalized.get("model") or "").lower()
        if (
            ("gpt-5" in model or model.startswith(("o1", "o3", "o4")))
            and "max_tokens" in normalized
        ):
            normalized["max_completion_tokens"] = normalized.pop("max_tokens")
        return normalized

    def option_presentation_policy(
        self, config: ProviderConfig
    ) -> ProviderOptionPresentationPolicy:
        del config
        return ProviderOptionPresentationPolicy(
            show_native=True,
            show_tool_choice=True,
            show_stream=True,
            show_rate_limit_controls=True,
            show_sampling_controls=False,
            show_ip_family_control=True,
        )

    def status_policy(self, config: ProviderConfig) -> ProviderStatusPolicy:
        del config
        return ProviderStatusPolicy(
            kind="catalog",
            label="GitHub Copilot OAuth",
            catalog_path="/models",
        )


__all__ = [
    "GITHUB_COPILOT_BASE_URL",
    "GITHUB_COPILOT_MODELS",
    "GITHUB_COPILOT_PUBLIC_MODEL_IDS",
    "GITHUB_COPILOT_RESPONSES_ONLY_MODELS",
    "GitHubCopilotOAuthProviderAdapter",
]
