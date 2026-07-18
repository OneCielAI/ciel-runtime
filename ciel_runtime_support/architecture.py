"""Runtime/provider architecture contracts for ciel-runtime.

This module is intentionally small and dependency-light.  It gives future
refactors a stable vocabulary without changing the current single-file
entrypoint behavior.

Ownership boundaries:
- Runtime adapters own the CLI product being launched, such as Claude Code.
- Provider adapters own upstream LLM APIs, keys, models, headers, and limits.
- Protocol adapters own request/response wire formats.
- Tool dialects own runtime-specific tool names and repairs.

Do not put provider-specific rate limit logic in runtime adapters.
Do not put runtime launch flags in provider adapters.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence


LaunchMode = Literal["native", "routed", "router"]
MessageProtocol = Literal["anthropic_messages", "openai_chat", "openai_responses", "ollama_chat"]


@dataclass(frozen=True)
class ProviderConfig:
    """Provider-facing configuration.

    This is about the upstream LLM service, not the local CLI runtime.
    """

    name: str
    base_url: str
    model: str
    api_keys: tuple[str, ...] = ()
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeConfig:
    """Runtime-facing configuration.

    This is about the client being launched, such as Claude Code.  Codex/Agy
    support should add new runtime adapters instead of extending provider
    branches.
    """

    name: str
    executable: str | None = None
    mcp_config_paths: tuple[Path, ...] = ()
    enable_channels: bool = False
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LaunchSpec:
    """Normalized launch request crossing runtime/provider boundaries."""

    runtime: RuntimeConfig
    provider: ProviderConfig
    mode: LaunchMode
    protocol: MessageProtocol
    passthrough: tuple[str, ...] = ()
    cwd: Path | None = None


@dataclass(frozen=True)
class RuntimeCommand:
    """A fully materialized runtime command."""

    argv: tuple[str, ...]
    env: Mapping[str, str] = field(default_factory=dict)
    cwd: Path | None = None


@dataclass(frozen=True)
class ModelInfo:
    """Provider-neutral model metadata used by menus and presets."""

    id: str
    display_name: str | None = None
    context_window: int | None = None
    max_output_tokens: int | None = None
    supports_tools: bool | None = None
    supports_vision: bool | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RateLimitState:
    """Provider-neutral rate-limit observation."""

    limited: bool
    retry_after_seconds: float | None = None
    scope: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class ProviderCapabilities:
    """Provider-owned features that affect routing without naming providers."""

    upstream_protocol: MessageProtocol = "openai_chat"
    supports_streaming: bool = True
    supports_tools: bool = True
    supports_thinking: bool = False
    preserves_anthropic_thinking: bool = False
    requires_api_key: bool = False
    local: bool = False


@dataclass(frozen=True)
class ProviderRequestPolicy:
    """Provider-owned endpoint and transport defaults."""

    chat_path: str
    models_path: str
    model_info_path: str | None = None
    default_timeout_seconds: float = 60.0


class RuntimeAdapter(ABC):
    """Adapter for a local/interactive coding runtime.

    Claude Code is the only implemented runtime today.  Codex/Agy should be
    added by implementing this interface, not by adding conditionals inside the
    Claude runtime.
    """

    name: str

    @abstractmethod
    def find_executable(self) -> Path | None:
        """Return the runtime executable, if available."""

    @abstractmethod
    def build_command(self, spec: LaunchSpec) -> RuntimeCommand:
        """Build argv/env/cwd for one runtime launch."""

    @abstractmethod
    def mcp_config_paths(self, spec: LaunchSpec) -> tuple[Path, ...]:
        """Return runtime-specific MCP config paths."""

    @abstractmethod
    def supports_channel_injection(self, spec: LaunchSpec) -> bool:
        """Whether this runtime can receive external channel events."""


class ProviderAdapter(ABC):
    """Adapter for an upstream LLM provider."""

    name: str

    @abstractmethod
    def default_base_url(self) -> str:
        """Return the provider default API base URL."""

    @abstractmethod
    def list_models(self, config: ProviderConfig) -> Sequence[ModelInfo]:
        """Return known models for this provider."""

    @abstractmethod
    def build_headers(self, config: ProviderConfig, api_key: str | None) -> Mapping[str, str]:
        """Build upstream HTTP headers for this provider."""

    def parse_rate_limit(self, response_or_error: Any) -> RateLimitState | None:
        """Return a rate-limit observation when the provider exposes one."""

        return None

    def capabilities(self, config: ProviderConfig) -> ProviderCapabilities:
        """Return routing capabilities for this configured provider."""

        del config
        return ProviderCapabilities()

    def request_policy(self, config: ProviderConfig) -> ProviderRequestPolicy:
        """Return endpoint and transport defaults for this provider."""

        del config
        return ProviderRequestPolicy(chat_path="/v1/chat/completions", models_path="/v1/models")

    def resolve_endpoint(self, operation: str, config: ProviderConfig) -> str:
        """Resolve a provider operation to a path without exposing provider conditionals."""

        policy = self.request_policy(config)
        paths = {
            "chat": policy.chat_path,
            "models": policy.models_path,
            "model_info": policy.model_info_path,
            "anthropic_messages": "/v1/messages",
            "openai_chat": "/v1/chat/completions",
            "openai_responses": "/v1/responses",
            "ollama_chat": "/api/chat",
        }
        path = paths.get(operation)
        if not path:
            raise KeyError(f"{self.name} does not support provider operation: {operation}")
        return path

    def build_model_headers(self, config: ProviderConfig, api_key: str | None) -> Mapping[str, str]:
        """Build headers for model discovery; providers may override request auth."""

        return self.build_headers(config, api_key)

    def model_paths(self, config: ProviderConfig) -> tuple[str, ...]:
        """Return model discovery paths in provider-preferred fallback order."""

        primary = self.request_policy(config).models_path
        return (primary,) if primary == "/models" else (primary, "/models")


class MessageProtocolAdapter(ABC):
    """Adapter for upstream request/response wire formats."""

    name: MessageProtocol

    @abstractmethod
    def normalize_request(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Convert a runtime request into this protocol's request shape."""

    @abstractmethod
    def normalize_response(self, response: Mapping[str, Any]) -> Mapping[str, Any]:
        """Convert this protocol's response into the runtime response shape."""


class ToolDialect(ABC):
    """Runtime-specific tool naming and schema repair contract."""

    name: str

    @abstractmethod
    def normalize_tool_name(self, name: str) -> str:
        """Return the runtime's canonical tool name."""

    @abstractmethod
    def repair_tool_input(self, tool_name: str, value: Mapping[str, Any]) -> Mapping[str, Any]:
        """Repair common model mistakes for one tool input."""

    def blocked_tools(self) -> frozenset[str]:
        """Tools that should not be exposed through a given path."""

        return frozenset()


__all__ = [
    "LaunchMode",
    "LaunchSpec",
    "MessageProtocol",
    "MessageProtocolAdapter",
    "ModelInfo",
    "ProviderAdapter",
    "ProviderConfig",
    "RateLimitState",
    "RuntimeAdapter",
    "RuntimeCommand",
    "RuntimeConfig",
    "ToolDialect",
]
