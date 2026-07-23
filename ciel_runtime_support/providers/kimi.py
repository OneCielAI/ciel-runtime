"""Kimi provider adapter."""

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
from .base import HttpBearerProviderAdapter, provider_configuration
from .constants import PROVIDER_DEFAULT_BASE_URLS
from ..runtime_constants import KIMI_MODEL_FALLBACK_IDS


@dataclass(frozen=True)
class KimiProviderAdapter(HttpBearerProviderAdapter):
    name: str = "kimi"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["kimi"]
    configuration_defaults_value: dict = field(
        default_factory=lambda: provider_configuration(
            "kimi-for-coding",
            custom_models=KIMI_MODEL_FALLBACK_IDS,
            native_compat=True,
            preserve_anthropic_thinking=True,
            normalize_anthropic_tool_use=True,
            supports_tool_choice=True,
            claude_code_supported_capabilities=["effort", "thinking"],
            context_window=262144,
            max_output_tokens=32768,
            context_reserve_tokens=32768,
            request_timeout_ms=600000,
            stream_enabled=True,
            stream_word_chunking=False,
            effort_level="high",
            haiku_model="kimi-for-coding",
            subagent_model="kimi-for-coding",
        )
    )
    send_placeholder_key: bool = True
    api_key_display_name_value: str = "Kimi.com"
    api_key_launch_error_value: str = (
        "Launch blocked: Kimi.com requires a Kimi API key."
    )
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="anthropic_messages",
            supports_thinking=True,
            requires_api_key=True,
        )
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(
            chat_path="/v1/messages", models_path="/v1/models"
        )
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(
            kind="openai", allow_configured_fallback=True
        )
    )

    def normalize_model_id(self, model_id: str) -> str:
        raw = str(model_id or "").strip()
        wants_one_million = raw.lower().endswith("[1m]")
        normalized = super().normalize_model_id(raw)
        lowered = normalized.lower().replace("_", "-").strip()
        if lowered in ("k3", "kimi-k3", "kimi/k3", "kimi-code/k3"):
            return "k3[1m]" if wants_one_million else "k3"
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
        if lowered in (
            "kimi-for-coding-highspeed",
            "kimi-code/kimi-for-coding-highspeed",
            "kimi/kimi-for-coding-highspeed",
        ):
            return "kimi-for-coding-highspeed"
        return normalized

    def upstream_api_model_id(self, model_id: str) -> str:
        normalized = self.normalize_model_id(model_id)
        return "k3" if normalized == "k3[1m]" else normalized

    def model_configuration_profile(
        self, config: ProviderConfig
    ) -> tuple[Mapping[str, Any], str | None]:
        model = self.normalize_model_id(config.model)
        if model == "kimi-for-coding-highspeed":
            return (
                {
                    "context_window": 262144,
                    "max_model_len": 262144,
                    "model_profile": "kimi-k2.7-highspeed-256k",
                },
                "Kimi K2.7 Code HighSpeed profile applied: 256K context; "
                "requires Allegretto or above and uses about 3x quota. Start a new session.",
            )
        if model == "kimi-for-coding":
            return (
                {
                    "context_window": 262144,
                    "max_model_len": 262144,
                    "model_profile": "kimi-k2.7-256k",
                },
                "Kimi K2.7 Code profile applied: 256K context with Thinking enabled. "
                "Start a new session after changing models.",
            )
        if model not in {"k3", "k3[1m]"}:
            return {}, None
        context = 1048576
        return (
            {
                "context_window": context,
                "max_model_len": context,
                "effort_level": "high",
                "model_profile": "kimi-k3-1m",
            },
            "Kimi K3 profile applied: 1M context and high reasoning effort. "
            "Start a new session after changing model, context, or reasoning effort.",
        )

    def model_selection_config_updates(
        self, config: ProviderConfig, model_id: str
    ) -> dict[str, str]:
        del config
        return {
            "haiku_model": model_id,
            "opus_model": model_id,
            "sonnet_model": model_id,
            "subagent_model": model_id,
        }

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(
            capacity_strategy="hint_first",
            settings_strategy="standard",
            hosted_timeout=True,
        )

    def router_native_anthropic_enabled(
        self, config: ProviderConfig, model: str | None = None
    ) -> bool:
        del model
        return bool(config.options.get("native_compat", True))

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

    def supported_protocols(
        self, config: ProviderConfig, model: str | None = None
    ) -> frozenset[MessageProtocol]:
        del config, model
        return frozenset({"anthropic_messages", "openai_chat"})

    def status_policy(self, config: ProviderConfig) -> ProviderStatusPolicy:
        del config
        return ProviderStatusPolicy(
            kind="catalog", label="Kimi.com", catalog_path="/v1/models"
        )

    def select_protocol(
        self,
        operation: MessageProtocol,
        config: ProviderConfig,
        model: str | None = None,
    ) -> MessageProtocol:
        del config, model
        return (
            "openai_chat"
            if operation in {"openai_chat", "openai_responses"}
            else "anthropic_messages"
        )

    def normalize_request_options(
        self, config: ProviderConfig, request: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        del config
        model = (
            str(request.get("model") or "")
            .split("[", 1)[0]
            .strip()
            .lower()
            .replace("_", "-")
        )
        if model.startswith("ciel-runtime-kimi-"):
            model = model[len("ciel-runtime-kimi-") :]
        normalized = dict(request)
        for key in ("temperature", "top_p", "top_k", "n"):
            normalized.pop(key, None)
        thinking = request.get("thinking")
        if not isinstance(thinking, Mapping):
            return normalized
        if str(thinking.get("type") or "").strip().lower() == "disabled":
            thinking = {**thinking, "type": "enabled"}
        if model in {"k3", "k3-1m", "kimi-k3", "kimi/k3", "kimi-code/k3"}:
            normalized["thinking"] = {
                **thinking,
                "effort": self._reasoning_effort(thinking.get("effort")),
            }
        else:
            normalized["thinking"] = thinking
        return normalized

    def openai_reasoning_effort(
        self, config: ProviderConfig, model: str, request: Mapping[str, Any]
    ) -> str | None:
        if self.normalize_model_id(model) not in {"k3", "k3[1m]"}:
            return None
        thinking = request.get("thinking")
        requested = thinking.get("effort") if isinstance(thinking, Mapping) else None
        if requested is None:
            requested = config.options.get("effort_level")
        return self._reasoning_effort(requested)

    def allows_sampling_overrides(self, config: ProviderConfig) -> bool:
        del config
        return False

    @staticmethod
    def _reasoning_effort(value: Any) -> str:
        effort = str(value or "high").strip().lower()
        if effort in {"ultra", "max", "xhigh"}:
            return "max"
        if effort in {"low", "minimum", "light"}:
            return "low"
        return "high"

    def normalize_tool_choice(
        self, config: ProviderConfig, model: str, tool_choice: Any
    ) -> Any:
        del model
        if config.options.get("supports_tool_choice") is False or not isinstance(
            tool_choice, Mapping
        ):
            return tool_choice
        if str(tool_choice.get("type") or "").strip().lower() in {"any", "tool"}:
            return {"type": "auto"}
        return tool_choice


__all__ = ["KimiProviderAdapter"]
