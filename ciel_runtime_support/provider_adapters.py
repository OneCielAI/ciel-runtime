"""Production provider adapters for common HTTP authentication dialects."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import re
from typing import Any, Mapping, Sequence
from urllib.parse import quote

from .architecture import (
    ModelInfo,
    MessageProtocol,
    ProviderAdapter,
    ProviderCapabilities,
    ProviderConfigurationPolicy,
    ProviderConfig,
    ProviderContextPolicy,
    ProviderModelCatalogPolicy,
    ProviderOptionPresentationPolicy,
    ProviderRequestPolicy,
    ProviderStatusPolicy,
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

ZAI_MODEL_FALLBACK_IDS: tuple[str, ...] = (
    "glm-5.2[1m]",
    "glm-5.2",
    "glm-5.1",
    "glm-5",
    "glm-5-turbo",
    "glm-4.7",
    "glm-4.7-flashx",
    "glm-4.7-flash",
    "glm-4.6",
    "glm-4.5",
    "glm-4.5-x",
    "glm-4.5-airx",
    "glm-4.5-air",
    "glm-4.5-flash",
    "glm-4-32b-0414-128k",
)

ROUTER_STATUS_FIELDS: tuple[str, ...] = (
    "context_window",
    "context_reserve_tokens",
    "max_output_tokens",
    "request_timeout_ms",
    "stream_idle_timeout_ms",
)


def _configuration_policy(**values: Any) -> ProviderConfigurationPolicy:
    status_fields = values.pop("status_fields", ROUTER_STATUS_FIELDS)
    return ProviderConfigurationPolicy(status_fields=status_fields, **values)


@dataclass(frozen=True)
class HttpBearerProviderAdapter(ProviderAdapter):
    """Provider adapter for the bearer/x-api-key variants used by compatible APIs."""

    name: str
    base_url: str = ""
    authorization_header: str = "authorization"
    include_x_api_key: bool = True
    require_api_key: bool = False
    send_placeholder_key: bool = False
    api_key_display_name_value: str = ""
    api_key_launch_error_value: str = ""
    capabilities_value: ProviderCapabilities = field(default_factory=ProviderCapabilities)
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(
            chat_path="/v1/chat/completions",
            models_path="/v1/models",
        )
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(default_factory=ProviderModelCatalogPolicy)

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

    def model_catalog_policy(self, config: ProviderConfig) -> ProviderModelCatalogPolicy:
        del config
        return self.model_catalog_policy_value

    def configuration_policy(self, config: ProviderConfig) -> ProviderConfigurationPolicy:
        del config
        return _configuration_policy()

    def api_key_display_name(self) -> str:
        return self.api_key_display_name_value

    def launch_api_key_error(self, config: ProviderConfig) -> str | None:
        if self.capabilities(config).requires_api_key and not config.api_keys:
            return self.api_key_launch_error_value or super().launch_api_key_error(config)
        return None

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
        default_factory=lambda: ProviderRequestPolicy(
            chat_path="/v1/messages",
            models_path="/v1/models",
            normalize_historical_tool_turns=False,
        )
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(kind="anthropic")
    )

    def advisor_panel_notice(self, config: ProviderConfig) -> tuple[tuple[str, ...], tuple[str, ...]]:
        del config
        return (
            (
                "Claude native and Anthropic routed sessions use Claude Code's",
                "built-in /advisor (run /advisor in the session to pick its model).",
                "Back",
            ),
            ("back", "back", "back"),
        )

    def configuration_policy(self, config: ProviderConfig) -> ProviderConfigurationPolicy:
        del config
        return ProviderConfigurationPolicy(
            supports_route_through_router=True, restricts_runtime_options=True
        )

    def supports_server_advisor_tool(self, config: ProviderConfig) -> bool:
        del config
        return True

    def context_compaction_available(self, config: ProviderConfig) -> bool:
        return bool(config.api_keys)

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(hosted_timeout=True, managed_preset_inference=True)

    def routing_mode_update(self, enabled: bool) -> tuple[str, ...]:
        mode = "routed through ciel-runtime router" if enabled else "direct Claude Native"
        return ("Anthropic routing mode updated.", f"mode: {mode}")

    def option_presentation_policy(self, config: ProviderConfig) -> ProviderOptionPresentationPolicy:
        del config
        return ProviderOptionPresentationPolicy(show_route=True)

    def api_key_status(self, config: ProviderConfig, *, key_count: int, primary_detail: str) -> str:
        routed = bool(config.options.get("route_through_router"))
        scope = "Anthropic routed" if routed else "Anthropic"
        if key_count > 1:
            return f"API keys: {key_count} keys, round-robin ({scope}{primary_detail})"
        if key_count:
            return f"API key: set ({scope}{primary_detail})"
        return (
            "API key: not set (uses Claude Code OAuth/API auth headers)"
            if routed
            else "API key: not set (use API key or Claude login)"
        )

    def build_model_headers(self, config: ProviderConfig, api_key: str | None) -> Mapping[str, str]:
        del config
        key = str(api_key or "").strip()
        return {"anthropic-version": "2023-06-01", "x-api-key": key} if key else {"anthropic-version": "2023-06-01"}


@dataclass(frozen=True)
class OpenAICompatibleProviderAdapter(HttpBearerProviderAdapter):
    """Base for providers that implement the OpenAI Chat/Models wire surface."""

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(capacity_strategy="hint_configured", settings_strategy="standard")

    def router_native_anthropic_enabled(self, config: ProviderConfig, model: str | None = None) -> bool:
        del model
        return bool(config.options.get("native_compat", True))

    def option_presentation_policy(self, config: ProviderConfig) -> ProviderOptionPresentationPolicy:
        del config
        return ProviderOptionPresentationPolicy(
            show_native=True, show_tool_choice=True, show_sampling=True, show_stream=True
        )

    def supported_protocols(self, config: ProviderConfig, model: str | None = None) -> frozenset[MessageProtocol]:
        del model
        protocols: set[MessageProtocol] = {"openai_chat"}
        native = config.options.get("native_compat")
        if native is True or str(native).strip().lower() in {"1", "true", "yes", "on"}:
            protocols.add("anthropic_messages")
        return frozenset(protocols)

    def select_protocol(
        self,
        operation: MessageProtocol,
        config: ProviderConfig,
        model: str | None = None,
    ) -> MessageProtocol:
        supported = self.supported_protocols(config, model)
        if operation == "anthropic_messages" and "anthropic_messages" in supported:
            return "anthropic_messages"
        return "openai_chat"


@dataclass(frozen=True)
class OllamaProviderAdapter(HttpBearerProviderAdapter):
    name: str = "ollama"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["ollama"]
    send_placeholder_key: bool = True
    api_key_display_name_value: str = "Ollama"
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
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(kind="ollama")
    )

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(
            capacity_strategy="ollama", settings_strategy="ollama", uses_catalog_timeout=True
        )

    def option_presentation_policy(self, config: ProviderConfig) -> ProviderOptionPresentationPolicy:
        del config
        return ProviderOptionPresentationPolicy(
            show_rate_limit=True, show_tool_choice=True, show_stream=True
        )

    def model_paths(self, config: ProviderConfig) -> tuple[str, ...]:
        del config
        return ("/api/tags", "/v1/models")

    def configuration_policy(self, config: ProviderConfig) -> ProviderConfigurationPolicy:
        del config
        return ProviderConfigurationPolicy(mutation_strategy="ollama", uses_ollama_status=True)

    def status_policy(self, config: ProviderConfig) -> ProviderStatusPolicy:
        policy = super().status_policy(config)
        return replace(
            policy,
            unreachable_hint="Start Ollama or set a reachable Base URL before launching Claude Code.",
        )


@dataclass(frozen=True)
class OllamaCloudProviderAdapter(OllamaProviderAdapter):
    name: str = "ollama-cloud"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["ollama-cloud"]
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="ollama_chat", supports_thinking=True, requires_api_key=True
        )
    )
    api_key_display_name_value: str = "Ollama Cloud"
    api_key_launch_error_value: str = "Launch blocked: Ollama Cloud requires an API key."
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(kind="ollama", use_bundled_catalog_fallback=True)
    )

    def normalize_model_id(self, model_id: str) -> str:
        normalized = super().normalize_model_id(model_id)
        return normalized[:-6] if normalized.endswith(":cloud") else normalized

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(
            capacity_strategy="ollama",
            settings_strategy="ollama",
            hosted_timeout=True,
            timeout_weight=1.2,
            uses_catalog_timeout=True,
        )


@dataclass(frozen=True)
class OpenRouterProviderAdapter(OpenAICompatibleProviderAdapter):
    name: str = "OpenRouter"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["openrouter"]
    authorization_header: str = "Authorization"
    require_api_key: bool = True
    api_key_display_name_value: str = "OpenRouter"
    api_key_launch_error_value: str = "Launch blocked: OpenRouter requires an OpenRouter API key."

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(
            capacity_strategy="configured_first", settings_strategy="standard", hosted_timeout=True
        )

    def router_native_anthropic_enabled(self, config: ProviderConfig, model: str | None = None) -> bool:
        del config, model
        return False
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(upstream_protocol="openai_chat", requires_api_key=True)
    )


@dataclass(frozen=True)
class LMStudioProviderAdapter(OpenAICompatibleProviderAdapter):
    name: str = "lm-studio"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["lm-studio"]

    def requires_catalog_model_selection(self, config: ProviderConfig) -> bool:
        del config
        return True

    def placeholder_model_ids(self) -> frozenset[str]:
        return super().placeholder_model_ids() | {"local-model"}

    def option_presentation_policy(self, config: ProviderConfig) -> ProviderOptionPresentationPolicy:
        return replace(super().option_presentation_policy(config), show_rate_limit=True)
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(upstream_protocol="openai_chat", local=True)
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(kind="lm_studio")
    )

    def model_paths(self, config: ProviderConfig) -> tuple[str, ...]:
        del config
        return ("/api/v0/models", "/api/v1/models", "/v1/models", "/models")

    def status_policy(self, config: ProviderConfig) -> ProviderStatusPolicy:
        policy = super().status_policy(config)
        return replace(
            policy,
            unreachable_hint="Start LM Studio's Local Server or set a reachable Anthropic-compatible Base URL before launching Claude Code.",
            readiness_validation="lm_studio",
        )


@dataclass(frozen=True)
class VllmProviderAdapter(OpenAICompatibleProviderAdapter):
    name: str = "vllm"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["vllm"]

    def requires_catalog_model_selection(self, config: ProviderConfig) -> bool:
        del config
        return True

    def placeholder_model_ids(self) -> frozenset[str]:
        return super().placeholder_model_ids() | {"my-model"}

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(capacity_strategy="remote_first", settings_strategy="standard")
    send_placeholder_key: bool = True
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="openai_chat",
            supports_tool_choice=False,
            local=True,
        )
    )

    def status_policy(self, config: ProviderConfig) -> ProviderStatusPolicy:
        policy = super().status_policy(config)
        return replace(
            policy,
            unreachable_hint="vLLM must be reachable from this machine and expose Anthropic-compatible /v1/messages.",
        )


@dataclass(frozen=True)
class NvidiaHostedProviderAdapter(OpenAICompatibleProviderAdapter):
    name: str = "nvidia-hosted"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["nvidia-hosted"]
    api_key_display_name_value: str = "NVIDIA"
    api_key_launch_error_value: str = "Launch blocked: NVIDIA hosted requires an NVIDIA API key."
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(upstream_protocol="openai_chat", requires_api_key=True)
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(kind="nvidia")
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(
            chat_path="/v1/chat/completions",
            models_path="/v1/models",
            model_alias_strategy="ncp",
            stream_required=True,
        )
    )

    def configuration_policy(self, config: ProviderConfig) -> ProviderConfigurationPolicy:
        del config
        return _configuration_policy(
            native_compat_error=(
                "nvidia-hosted does not expose Anthropic /v1/messages; use router mode. "
                "Use self-hosted-nim or vLLM for native /v1/messages."
            ),
            status_fields=(
                "context_window",
                "max_output_tokens",
                "request_timeout_ms",
                "stream_idle_timeout_ms",
            ),
        )

    def status_policy(self, config: ProviderConfig) -> ProviderStatusPolicy:
        del config
        return ProviderStatusPolicy(kind="nvidia", label="NVIDIA hosted")

    def preserves_claude_model_alias(self, model_id: str) -> bool:
        return str(model_id).startswith("claude-")

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(
            capacity_strategy="nvidia", settings_strategy="standard", hosted_timeout=True
        )

    def router_native_anthropic_enabled(self, config: ProviderConfig, model: str | None = None) -> bool:
        del config, model
        return False

    def option_presentation_policy(self, config: ProviderConfig) -> ProviderOptionPresentationPolicy:
        return replace(
            super().option_presentation_policy(config), show_rate_limit=True, show_native=False
        )


@dataclass(frozen=True)
class SelfHostedNimProviderAdapter(OpenAICompatibleProviderAdapter):
    name: str = "self-hosted-nim"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["self-hosted-nim"]
    send_placeholder_key: bool = True
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(upstream_protocol="openai_chat", local=True)
    )

    def requires_catalog_model_selection(self, config: ProviderConfig) -> bool:
        del config
        return True

    def option_presentation_policy(self, config: ProviderConfig) -> ProviderOptionPresentationPolicy:
        return replace(super().option_presentation_policy(config), show_rate_limit=True)

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(capacity_strategy="remote_first", settings_strategy="standard")

    def status_policy(self, config: ProviderConfig) -> ProviderStatusPolicy:
        policy = super().status_policy(config)
        return replace(
            policy,
            unreachable_hint="Start NIM or set a reachable Anthropic-compatible Base URL before launching Claude Code.",
        )


@dataclass(frozen=True)
class DeepSeekProviderAdapter(HttpBearerProviderAdapter):
    name: str = "deepseek"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["deepseek"]
    send_placeholder_key: bool = True
    api_key_display_name_value: str = "DeepSeek"
    api_key_launch_error_value: str = "Launch blocked: DeepSeek.com requires a DeepSeek API key."
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="anthropic_messages", supports_thinking=True, requires_api_key=True
        )
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(chat_path="/v1/messages", models_path="/v1/models")
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(
            kind="configured",
            fallback_models=("deepseek-v4-pro[1m]", "deepseek-v4-flash"),
        )
    )

    def advisor_model_badge(self, config: ProviderConfig, model: str) -> str:
        del config
        return "recommended for long context" if model == "deepseek-v4-pro" else ""

    def normalize_model_id(self, model_id: str) -> str:
        return str(model_id or "").strip()

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(
            capacity_strategy="configured_first", settings_strategy="standard", hosted_timeout=True
        )

    def router_native_anthropic_enabled(self, config: ProviderConfig, model: str | None = None) -> bool:
        del model
        return bool(config.options.get("native_compat", True))

    def option_presentation_policy(self, config: ProviderConfig) -> ProviderOptionPresentationPolicy:
        del config
        return ProviderOptionPresentationPolicy(
            show_native=True, show_tool_choice=True, show_stream=True
        )

    def status_policy(self, config: ProviderConfig) -> ProviderStatusPolicy:
        del config
        return ProviderStatusPolicy(kind="configured", configured_description="DeepSeek Anthropic API configured")

    def supports_tool_choice(self, config: ProviderConfig, model: str | None = None) -> bool:
        configured = config.options.get("supports_tool_choice")
        if configured is not None:
            return bool(configured)
        return "deepseek-v4" not in str(model or config.model or "").split("[", 1)[0].lower()


@dataclass(frozen=True)
class KimiProviderAdapter(HttpBearerProviderAdapter):
    name: str = "kimi"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["kimi"]
    send_placeholder_key: bool = True
    api_key_display_name_value: str = "Kimi.com"
    api_key_launch_error_value: str = "Launch blocked: Kimi.com requires a Kimi API key."
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="anthropic_messages", supports_thinking=True, requires_api_key=True
        )
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(chat_path="/v1/messages", models_path="/v1/models")
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(kind="openai", allow_configured_fallback=True)
    )

    def normalize_model_id(self, model_id: str) -> str:
        normalized = super().normalize_model_id(model_id)
        lowered = normalized.lower().replace("_", "-").strip()
        if lowered in ("k3", "kimi-k3", "kimi/k3", "kimi-code/k3"):
            return "k3"
        if lowered in (
            "kimi-code/kimi-for-coding",
            "kimi/kimi-for-coding",
            "moonshot/kimi-for-coding",
            "kimi-k2.7-code",
            "kimi-k2.7-coding",
            "k2.7-code",
            "k2.7-coding",
        ):
            return "kimi-for-coding"
        return normalized

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(
            capacity_strategy="hint_first", settings_strategy="standard", hosted_timeout=True
        )

    def router_native_anthropic_enabled(self, config: ProviderConfig, model: str | None = None) -> bool:
        del model
        return bool(config.options.get("native_compat", True))

    def option_presentation_policy(self, config: ProviderConfig) -> ProviderOptionPresentationPolicy:
        del config
        return ProviderOptionPresentationPolicy(
            show_native=True, show_tool_choice=True, show_stream=True
        )

    def supported_protocols(self, config: ProviderConfig, model: str | None = None) -> frozenset[MessageProtocol]:
        del config, model
        return frozenset({"anthropic_messages", "openai_chat"})

    def status_policy(self, config: ProviderConfig) -> ProviderStatusPolicy:
        del config
        return ProviderStatusPolicy(kind="catalog", label="Kimi.com", catalog_path="/v1/models")

    def select_protocol(self, operation: MessageProtocol, config: ProviderConfig, model: str | None = None) -> MessageProtocol:
        del config, model
        return "openai_chat" if operation in {"openai_chat", "openai_responses"} else "anthropic_messages"

    def normalize_request_options(self, config: ProviderConfig, request: Mapping[str, Any]) -> Mapping[str, Any]:
        del config
        model = str(request.get("model") or "").split("[", 1)[0].strip().lower().replace("_", "-")
        if model.startswith("ciel-runtime-kimi-"):
            model = model[len("ciel-runtime-kimi-"):]
        thinking = request.get("thinking")
        if model not in {"k3", "kimi-k3", "kimi/k3", "kimi-code/k3"} or not isinstance(thinking, Mapping):
            return request
        if str(thinking.get("type") or "").lower() == "disabled":
            return request
        normalized = dict(request)
        normalized["thinking"] = {**thinking, "effort": "max"}
        return normalized

    def normalize_tool_choice(self, config: ProviderConfig, model: str, tool_choice: Any) -> Any:
        del model
        if config.options.get("supports_tool_choice") is False or not isinstance(tool_choice, Mapping):
            return tool_choice
        if str(tool_choice.get("type") or "").strip().lower() in {"any", "tool"}:
            return {"type": "auto"}
        return tool_choice


@dataclass(frozen=True)
class ZaiProviderAdapter(HttpBearerProviderAdapter):
    name: str = "zai"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["zai"]
    send_placeholder_key: bool = True
    api_key_display_name_value: str = "Z.AI GLM"
    api_key_launch_error_value: str = "Launch blocked: Z.AI GLM requires a Z.AI API key."
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="anthropic_messages", supports_thinking=True, requires_api_key=True
        )
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(chat_path="/v1/messages", models_path="/v1/models")
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(kind="openai", fallback_models=ZAI_MODEL_FALLBACK_IDS)
    )

    def normalize_model_id(self, model_id: str) -> str:
        return str(model_id or "").strip()

    def upstream_api_model_id(self, model_id: str) -> str:
        return super().normalize_model_id(model_id)

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(
            capacity_strategy="hint_first", settings_strategy="standard", hosted_timeout=True
        )

    def router_native_anthropic_enabled(self, config: ProviderConfig, model: str | None = None) -> bool:
        del model
        return bool(config.options.get("native_compat", True))

    def option_presentation_policy(self, config: ProviderConfig) -> ProviderOptionPresentationPolicy:
        del config
        return ProviderOptionPresentationPolicy(
            show_native=True, show_tool_choice=True, show_stream=True
        )

    def status_policy(self, config: ProviderConfig) -> ProviderStatusPolicy:
        del config
        return ProviderStatusPolicy(kind="configured", configured_description="Z.AI Anthropic API configured")


@dataclass(frozen=True)
class FireworksProviderAdapter(OpenAICompatibleProviderAdapter):
    name: str = "fireworks"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["fireworks"]
    send_placeholder_key: bool = True
    api_key_display_name_value: str = "Fireworks.ai"
    api_key_launch_error_value: str = "Launch blocked: Fireworks.ai requires a Fireworks API key."
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(upstream_protocol="openai_chat", requires_api_key=True)
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(kind="fireworks")
    )

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(
            capacity_strategy="configured_first", settings_strategy="standard", hosted_timeout=True
        )

    def option_presentation_policy(self, config: ProviderConfig) -> ProviderOptionPresentationPolicy:
        return replace(super().option_presentation_policy(config), show_sampling=False)

    def supported_protocols(self, config: ProviderConfig, model: str | None = None) -> frozenset[MessageProtocol]:
        del config, model
        return frozenset({"anthropic_messages", "openai_chat"})

    def status_policy(self, config: ProviderConfig) -> ProviderStatusPolicy:
        account_id = str(config.options.get("account_id") or "").strip()
        if not account_id:
            match = re.match(r"^accounts/([^/]+)/models/[^/]+$", config.model)
            account_id = match.group(1) if match else "fireworks"
        return ProviderStatusPolicy(
            kind="catalog",
            label="Fireworks.ai",
            catalog_path=f"/v1/accounts/{quote(account_id, safe='')}/models?pageSize=1",
            catalog_scope="fireworks_management",
            catalog_count_label="sampled",
        )

    def select_protocol(self, operation: MessageProtocol, config: ProviderConfig, model: str | None = None) -> MessageProtocol:
        del config, model
        return "openai_chat" if operation in {"openai_chat", "openai_responses"} else "anthropic_messages"

    def configuration_policy(self, config: ProviderConfig) -> ProviderConfigurationPolicy:
        del config
        return _configuration_policy(
            text_option_aliases={
                "account": "account_id",
                "account_id": "account_id",
                "management_base_url": "model_api_base_url",
                "model_api_base_url": "model_api_base_url",
                "model_base_url": "model_api_base_url",
                "models_base_url": "model_api_base_url",
            },
            strip_trailing_slash_fields=frozenset({"model_api_base_url"}),
        )

    def project_model_metadata(self, raw: Mapping[str, Any]) -> Mapping[str, Any]:
        metadata = dict(super().project_model_metadata(raw))
        for source_key, target_key in (
            ("displayName", "display_name"),
            ("description", "description"),
            ("kind", "kind"),
            ("importedFrom", "imported_from"),
        ):
            value = raw.get(source_key)
            if value is not None:
                metadata[target_key] = value
        for source_key, target_key in (
            ("supportsTools", "supports_tool_call"),
            ("supportsImageInput", "supports_vision"),
            ("public", "public"),
            ("supportsServerless", "supports_serverless"),
        ):
            value = raw.get(source_key)
            if isinstance(value, bool):
                metadata[target_key] = value
        details = raw.get("baseModelDetails")
        if isinstance(details, Mapping):
            if details.get("parameterCount") is not None:
                metadata["parameter_count"] = str(details["parameterCount"])
            for source_key, target_key in (
                ("worldSize", "world_size"),
                ("checkpointFormat", "checkpoint_format"),
                ("modelType", "model_type"),
                ("defaultPrecision", "default_precision"),
            ):
                value = details.get(source_key)
                if value is not None:
                    metadata[target_key] = value
        return metadata


@dataclass(frozen=True)
class OpenCodeProviderAdapter(HttpBearerProviderAdapter):
    name: str = "opencode"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["opencode"]
    send_placeholder_key: bool = True
    api_key_display_name_value: str = "OpenCode Zen"
    api_key_launch_error_value: str = "Launch blocked: OpenCode Zen requires a OpenCode Zen API key."
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="anthropic_messages", supports_thinking=True, requires_api_key=True
        )
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(chat_path="/messages", models_path="/v1/models")
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(
            kind="openai",
            allow_configured_fallback=True,
            allow_public_without_auth=True,
        )
    )

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(
            capacity_strategy="configured_first", settings_strategy="standard", hosted_timeout=True
        )

    def router_native_anthropic_enabled(self, config: ProviderConfig, model: str | None = None) -> bool:
        return bool(config.options.get("native_compat", True)) and (
            self.select_protocol("anthropic_messages", config, model) == "anthropic_messages"
        )

    def option_presentation_policy(self, config: ProviderConfig) -> ProviderOptionPresentationPolicy:
        del config
        return ProviderOptionPresentationPolicy(
            show_native=True,
            show_tool_choice=True,
            show_stream=True,
            show_ip_family=True,
        )

    def select_protocol(self, operation: MessageProtocol, config: ProviderConfig, model: str | None = None) -> MessageProtocol:
        del operation
        raw_model = str(model or config.model or "").strip()
        overrides = config.options.get("model_endpoints")
        if isinstance(overrides, Mapping):
            raw = overrides.get(raw_model)
            key = str(raw or "").strip().lower().replace("_", "-")
            mapped = {
                "anthropic": "anthropic_messages",
                "anthropic-messages": "anthropic_messages",
                "messages": "anthropic_messages",
                "openai": "openai_chat",
                "openai-chat": "openai_chat",
                "chat": "openai_chat",
                "openai-responses": "openai_responses",
                "responses": "openai_responses",
                "google-generative": "google_generative",
                "gemini": "google_generative",
            }.get(key)
            if mapped is not None:
                return mapped
        normalized = raw_model.split("[", 1)[0].lower()
        for prefix in ("ciel-runtime-opencode-go-", "ciel-runtime-opencode-"):
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix):]
                break
        if self.name == "opencode-go":
            if normalized.startswith(("glm-", "kimi-", "deepseek-", "mimo-", "hy3-")):
                return "openai_chat"
            return "anthropic_messages"
        if normalized.startswith("gpt-"):
            return "openai_responses"
        if normalized.startswith("gemini-"):
            return "google_generative"
        if normalized.startswith(("minimax-", "glm-", "kimi-", "grok-", "big-pickle", "deepseek-", "mimo-", "nemotron-", "north-")):
            return "openai_chat"
        return "anthropic_messages"

    def supported_protocols(self, config: ProviderConfig, model: str | None = None) -> frozenset[MessageProtocol]:
        return frozenset({self.select_protocol("anthropic_messages", config, model)})

    def status_policy(self, config: ProviderConfig) -> ProviderStatusPolicy:
        del config
        return ProviderStatusPolicy(kind="catalog", label=self.api_key_display_name_value, catalog_path="/v1/models")

    def model_panel_badge(self, config: ProviderConfig, model: str) -> str:
        protocol = self.select_protocol("anthropic_messages", config, model)
        label = {
            "anthropic_messages": "messages",
            "openai_chat": "chat",
            "openai_responses": "responses unsupported",
            "google_generative": "gemini unsupported",
        }.get(protocol, str(protocol))
        overrides = config.options.get("model_endpoints")
        if isinstance(overrides, Mapping) and model in overrides:
            label += " override"
        return label

    def configuration_policy(self, config: ProviderConfig) -> ProviderConfigurationPolicy:
        del config
        return _configuration_policy(supports_model_endpoint_overrides=True)


@dataclass(frozen=True)
class OpenCodeGoProviderAdapter(OpenCodeProviderAdapter):
    name: str = "opencode-go"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["opencode-go"]
    api_key_display_name_value: str = "OpenCode Go"
    api_key_launch_error_value: str = "Launch blocked: OpenCode Go requires a OpenCode Go API key."


@dataclass(frozen=True)
class CodexProviderAdapter(NoAuthProviderAdapter):
    name: str = "codex"

    def routing_mode_update(self, enabled: bool) -> tuple[str, ...]:
        return ("Codex routing mode updated.", f"mode: {'codex-routed' if enabled else 'codex-native'}")

    def option_presentation_policy(self, config: ProviderConfig) -> ProviderOptionPresentationPolicy:
        del config
        return ProviderOptionPresentationPolicy(show_route=True)
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["codex"]
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(upstream_protocol="openai_responses")
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(chat_path="/v1/responses", models_path="/v1/models")
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(kind="configured")
    )

    def api_key_status(self, config: ProviderConfig, *, key_count: int, primary_detail: str) -> str:
        routed = bool(config.options.get("route_through_router"))
        if routed:
            if key_count > 1:
                return (
                    f"API keys: {key_count} keys, round-robin "
                    f"(stored; Codex routed uses native login/auth headers{primary_detail})"
                )
            return (
                f"API key: set (stored; Codex routed uses native login/auth headers{primary_detail})"
                if key_count
                else "API key: not set (uses native Codex login/auth headers)"
            )
        if key_count > 1:
            return f"API keys: {key_count} keys, round-robin (Codex fallback{primary_detail})"
        return (
            f"API key: set (Codex fallback{primary_detail})"
            if key_count
            else "API key: not set (uses native Codex login/config)"
        )

    def status_policy(self, config: ProviderConfig) -> ProviderStatusPolicy:
        del config
        return ProviderStatusPolicy(kind="native_codex")

    def configuration_policy(self, config: ProviderConfig) -> ProviderConfigurationPolicy:
        del config
        return ProviderConfigurationPolicy(runtime_owns_model=True, restricts_runtime_options=True)


@dataclass(frozen=True)
class AgyProviderAdapter(NoAuthProviderAdapter):
    name: str = "agy"

    def routing_mode_update(self, enabled: bool) -> tuple[str, ...]:
        return ("AGY routing mode updated.", f"mode: {'agy-routed' if enabled else 'agy-native'}")

    def option_presentation_policy(self, config: ProviderConfig) -> ProviderOptionPresentationPolicy:
        del config
        return ProviderOptionPresentationPolicy(show_route=True)
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["agy"]
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(upstream_protocol="openai_responses")
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(kind="configured")
    )

    def api_key_status(self, config: ProviderConfig, *, key_count: int, primary_detail: str) -> str:
        del key_count, primary_detail
        return (
            "API key: not set (uses native AGY Google sign-in/keyring)"
            if config.options.get("route_through_router")
            else "API key: not set (uses native AGY Google sign-in/config)"
        )

    def status_policy(self, config: ProviderConfig) -> ProviderStatusPolicy:
        del config
        return ProviderStatusPolicy(kind="native_agy")

    def configuration_policy(self, config: ProviderConfig) -> ProviderConfigurationPolicy:
        del config
        return ProviderConfigurationPolicy(runtime_owns_model=True)


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
    "ZAI_MODEL_FALLBACK_IDS",
]
