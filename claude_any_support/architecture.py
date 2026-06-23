"""Runtime/provider architecture contracts for claude-any.

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
MessageProtocol = Literal["anthropic_messages", "openai_chat", "openai_responses"]


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
