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
import re
from typing import Any, Literal, Mapping, Sequence


LaunchMode = Literal["native", "routed", "router"]
MessageProtocol = Literal[
    "anthropic_messages",
    "openai_chat",
    "openai_responses",
    "ollama_chat",
    "google_generative",
]


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
    supports_tool_choice: bool = True
    supports_thinking: bool = False
    preserves_anthropic_thinking: bool = False
    requires_api_key: bool = False
    local: bool = False

    @property
    def supported_protocols(self) -> frozenset[MessageProtocol]:
        return frozenset({self.upstream_protocol})


@dataclass(frozen=True)
class ProviderRequestPolicy:
    """Provider-owned endpoint and transport defaults."""

    chat_path: str
    models_path: str
    model_info_path: str | None = None
    default_timeout_seconds: float = 60.0
    model_alias_strategy: Literal["identity", "ncp"] = "identity"
    stream_required: bool = False
    normalize_historical_tool_turns: bool = True


@dataclass(frozen=True)
class ProviderModelCatalogPolicy:
    """Select a reusable model-catalog strategy without provider-name branching."""

    kind: Literal["configured", "anthropic", "openai", "ollama", "lm_studio", "nvidia", "fireworks"] = "openai"
    fallback_models: tuple[str, ...] = ()
    allow_configured_fallback: bool = False
    allow_public_without_auth: bool = False
    use_bundled_catalog_fallback: bool = False


@dataclass(frozen=True)
class ProviderConfigurationPolicy:
    """Provider-owned configuration mutation capabilities."""

    mutation_strategy: Literal["common", "ollama"] = "common"
    supports_route_through_router: bool = False
    supports_model_endpoint_overrides: bool = False
    native_compat_error: str | None = None
    text_option_aliases: Mapping[str, str] = field(default_factory=dict)
    strip_trailing_slash_fields: frozenset[str] = frozenset()
    status_fields: tuple[str, ...] = ()
    uses_ollama_status: bool = False
    runtime_owns_model: bool = False
    restricts_runtime_options: bool = False


@dataclass(frozen=True)
class ProviderStatusPolicy:
    """Provider-owned base URL status projection strategy."""

    kind: Literal["generic", "native_codex", "native_agy", "nvidia", "configured", "catalog"] = "generic"
    label: str = ""
    configured_description: str = ""
    catalog_path: str = ""
    catalog_count_key: Literal["data", "models"] = "data"
    catalog_scope: Literal["configured", "fireworks_management"] = "configured"
    catalog_count_label: str = "models"
    unreachable_hint: str = "Set a reachable Base URL before launching Claude Code."
    readiness_validation: Literal["none", "lm_studio"] = "none"


@dataclass(frozen=True)
class ProviderContextPolicy:
    """Provider-owned context capacity and configuration strategy."""

    capacity_strategy: Literal[
        "managed", "nvidia", "remote_first", "hint_first", "configured_first", "ollama", "hint_configured", "anthropic_hint"
    ] = "managed"
    settings_strategy: Literal["managed", "ollama", "standard"] = "managed"
    hosted_timeout: bool = False
    timeout_weight: float = 1.0
    uses_catalog_timeout: bool = False
    managed_preset_inference: bool = False
    context_family_before_size_markers: bool = False
    preset_context_profile: Literal["default", "ollama", "nvidia"] = "default"
    status_capacity_strategy: Literal[
        "configured", "ollama_budget", "openai_budget", "provider"
    ] = "configured"


@dataclass(frozen=True)
class ProviderOptionPresentationPolicy:
    """Provider-owned option status capabilities."""

    show_rate_limit: bool = False
    show_native: bool = False
    show_route: bool = False
    show_tool_choice: bool = False
    show_sampling: bool = False
    show_stream: bool = False
    show_ip_family: bool = False
    show_rate_limit_controls: bool = False
    show_sampling_controls: bool = False
    show_ip_family_control: bool = False


@dataclass(frozen=True)
class ProviderUiPolicy:
    """Provider-owned labels used by shared menus."""

    menu_label: str = ""
    routed_menu_label: str = ""
    native_choice: str = ""
    routed_choice: str = ""
    model_placeholder: str = ""
    advisor_placeholder: str = ""


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

    def default_configuration(self) -> Mapping[str, Any]:
        """Return the minimal persisted configuration for a newly registered provider."""

        return {
            "base_url": self.default_base_url(),
            "api_key": "",
            "current_model": "",
            "advisor_model": "",
            "custom_models": [],
        }

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

    def supported_protocols(self, config: ProviderConfig, model: str | None = None) -> frozenset[MessageProtocol]:
        """Return protocols this provider can use for the selected model."""

        del model
        return self.capabilities(config).supported_protocols

    def select_protocol(
        self,
        operation: MessageProtocol,
        config: ProviderConfig,
        model: str | None = None,
    ) -> MessageProtocol:
        """Choose the upstream protocol for an inbound protocol operation."""

        supported = self.supported_protocols(config, model)
        if operation in supported:
            return operation
        return self.capabilities(config).upstream_protocol

    def supports_tool_choice(self, config: ProviderConfig, model: str | None = None) -> bool:
        configured = config.options.get("supports_tool_choice")
        if configured is not None:
            return bool(configured)
        del model
        return self.capabilities(config).supports_tool_choice

    def preserves_anthropic_thinking(self, config: ProviderConfig) -> bool:
        configured = config.options.get("preserve_anthropic_thinking")
        if configured is not None:
            return bool(configured)
        return self.capabilities(config).preserves_anthropic_thinking

    def model_catalog_policy(self, config: ProviderConfig) -> ProviderModelCatalogPolicy:
        del config
        return ProviderModelCatalogPolicy()

    def project_model_metadata(self, raw: Mapping[str, Any]) -> Mapping[str, Any]:
        """Project provider response fields into the shared model metadata shape."""

        return {
            key: raw[key]
            for key in ("owned_by", "root", "object")
            if raw.get(key) is not None
        }

    def normalize_model_id(self, model_id: str) -> str:
        """Normalize a configured/catalog model id for shared runtime use."""

        text = str(model_id or "").strip()
        return re.sub(r"\[(?:1m)\]\s*$", "", text, flags=re.IGNORECASE).strip()

    def upstream_api_model_id(self, model_id: str) -> str:
        """Return the provider's wire-level model id."""

        return str(model_id or "").strip()

    def preserves_claude_model_alias(self, model_id: str) -> bool:
        """Whether an already Claude-facing model id must remain unchanged."""

        del model_id
        return False

    def launch_model_strategy(self, config: ProviderConfig) -> str:
        """Return the provider-owned launch alias strategy."""

        del config
        return "alias"

    def requires_catalog_model_selection(self, config: ProviderConfig) -> bool:
        """Whether placeholder model ids must be replaced from provider discovery."""

        del config
        return False

    def placeholder_model_ids(self) -> frozenset[str]:
        """Return non-routable placeholder model ids accepted in configuration."""

        return frozenset({"", "model"})

    def routing_mode_update(self, enabled: bool) -> tuple[str, ...]:
        """Describe a persisted route-through-router mode change."""

        state = "routed through ciel-runtime router" if enabled else "direct provider mode"
        return ("Provider routing mode updated.", f"mode: {state}")

    def selection_config_updates(self, config: ProviderConfig) -> Mapping[str, Any]:
        """Return provider-owned defaults applied when this provider is selected."""

        del config
        return {}

    def model_selection_config_updates(
        self, config: ProviderConfig, model_id: str
    ) -> Mapping[str, Any]:
        """Return provider-owned config updates for an explicit model choice."""

        del config, model_id
        return {}

    def selection_status_lines(self, config: ProviderConfig) -> tuple[str, ...]:
        """Return provider-owned status details after selection."""

        del config
        return ()

    def configuration_policy(self, config: ProviderConfig) -> ProviderConfigurationPolicy:
        """Return provider-owned option mutation behavior."""

        del config
        return ProviderConfigurationPolicy()

    def status_policy(self, config: ProviderConfig) -> ProviderStatusPolicy:
        """Return provider-owned base URL status behavior."""

        request = self.request_policy(config)
        count_key = "models" if request.models_path == "/api/tags" else "data"
        return ProviderStatusPolicy(catalog_path=request.models_path, catalog_count_key=count_key)

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        """Return provider-owned context capacity and mutation behavior."""

        del config
        return ProviderContextPolicy()

    def option_presentation_policy(self, config: ProviderConfig) -> ProviderOptionPresentationPolicy:
        del config
        return ProviderOptionPresentationPolicy()

    def ui_policy(self, config: ProviderConfig) -> ProviderUiPolicy:
        """Return provider-owned presentation and runtime compatibility metadata."""

        del config
        return ProviderUiPolicy(menu_label=self.name)

    def shows_claude_workflow_options(self, config: ProviderConfig) -> bool:
        del config
        return True

    def option_timeout_default(self) -> str:
        return "default"

    def model_configuration_profile(
        self, config: ProviderConfig
    ) -> tuple[Mapping[str, Any], str | None]:
        """Return provider/model-specific configuration updates and an optional notice."""

        del config
        return {}, None

    def propagates_inbound_beta_query(self, config: ProviderConfig) -> bool:
        """Whether an inbound Claude beta query should be forwarded upstream."""

        del config
        return False

    def api_key_display_name(self) -> str:
        """Return the provider-owned name used in API-key status text."""

        return self.name

    def api_key_status(self, config: ProviderConfig, *, key_count: int, primary_detail: str) -> str:
        """Format API-key readiness without central provider-name branching."""

        scope = self.api_key_display_name()
        round_robin = f"{key_count} keys, round-robin"
        if key_count > 1:
            detail = f"{scope}{primary_detail}" if scope else primary_detail.lstrip("; ")
            return f"API keys: {round_robin} ({detail})"
        if key_count:
            detail = f"{scope}{primary_detail}" if scope else primary_detail.lstrip("; ")
            return f"API key: set ({detail})"
        if self.capabilities(config).requires_api_key:
            return f"API key: missing ({scope} required)"
        if self.capabilities(config).local:
            return f"API key: not required for {scope}"
        return "API key: optional or not configured"

    def launch_api_key_error(self, config: ProviderConfig) -> str | None:
        """Return a launch blocker when this provider requires an absent key."""

        if self.capabilities(config).requires_api_key and not config.api_keys:
            return f"Launch blocked: {self.api_key_display_name()} requires an API key."
        return None

    def model_panel_badge(self, config: ProviderConfig, model: str) -> str:
        """Return an optional provider-owned model label for selection UIs."""

        del config, model
        return ""

    def supports_server_advisor_tool(self, config: ProviderConfig) -> bool:
        """Whether the upstream executes Claude's server-side advisor tool."""

        del config
        return False

    def context_compaction_available(self, config: ProviderConfig) -> bool:
        """Whether this configured provider can run an auxiliary summary request."""

        del config
        return True

    def router_native_anthropic_enabled(self, config: ProviderConfig, model: str | None = None) -> bool:
        """Whether the generic router may send Anthropic Messages directly upstream."""

        del config, model
        return False

    def advisor_panel_notice(self, config: ProviderConfig) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
        """Return a provider-owned advisor panel replacement, when applicable."""

        del config
        return None

    def advisor_model_badge(self, config: ProviderConfig, model: str) -> str:
        """Return an optional provider-owned advisor model annotation."""

        del config, model
        return ""

    def normalize_request_options(self, config: ProviderConfig, request: Mapping[str, Any]) -> Mapping[str, Any]:
        del config
        return request

    def normalize_tool_choice(self, config: ProviderConfig, model: str, tool_choice: Any) -> Any:
        del config, model
        return tool_choice


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
    "ProviderUiPolicy",
    "RateLimitState",
    "RuntimeAdapter",
    "RuntimeCommand",
    "RuntimeConfig",
    "ToolDialect",
]
