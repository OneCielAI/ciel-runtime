"""Meta Model API / Muse Spark provider adapter."""

from __future__ import annotations

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
from .constants import DEFAULT_REQUEST_TIMEOUT_MS, PROVIDER_DEFAULT_BASE_URLS


MUSE_SPARK_MODEL = "muse-spark-1.1"
MUSE_SPARK_CONTEXT_WINDOW = 1_048_576
MUSE_SPARK_AUTO_COMPACT_LIMIT = 900_000


@dataclass(frozen=True)
class MetaModelProviderAdapter(HttpBearerProviderAdapter):
    """Preserve Meta's native Responses and Anthropic Messages contracts."""

    name: str = "meta"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["meta"]
    configuration_defaults_value: dict = field(
        default_factory=lambda: provider_configuration(
            MUSE_SPARK_MODEL,
            custom_models=(MUSE_SPARK_MODEL,),
            native_compat=True,
            preserve_anthropic_thinking=True,
            normalize_anthropic_tool_use=True,
            supports_tool_choice=True,
            claude_code_supported_capabilities=["effort", "thinking"],
            context_window=MUSE_SPARK_CONTEXT_WINDOW,
            max_model_len=MUSE_SPARK_CONTEXT_WINDOW,
            auto_compact_window=MUSE_SPARK_AUTO_COMPACT_LIMIT,
            codex_auto_compact_window=MUSE_SPARK_AUTO_COMPACT_LIMIT,
            request_timeout_ms=DEFAULT_REQUEST_TIMEOUT_MS,
            stream_enabled=True,
            stream_word_chunking=False,
            effort_level="high",
            enable_tool_search=True,
            haiku_model=MUSE_SPARK_MODEL,
            opus_model=MUSE_SPARK_MODEL,
            sonnet_model=MUSE_SPARK_MODEL,
            subagent_model=MUSE_SPARK_MODEL,
        )
    )
    authorization_header: str = "authorization"
    include_x_api_key: bool = False
    require_api_key: bool = True
    api_key_display_name_value: str = "Meta Model API"
    api_key_launch_error_value: str = (
        "Launch blocked: Meta Model API requires a MODEL_API_KEY."
    )
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="anthropic_messages",
            supports_thinking=True,
            preserves_anthropic_thinking=True,
            requires_api_key=True,
        )
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(
            chat_path="/v1/messages",
            models_path="/v1/models",
        )
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(
            kind="openai",
            fallback_models=(MUSE_SPARK_MODEL,),
            allow_configured_fallback=True,
        )
    )

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(
            capacity_strategy="configured_first",
            settings_strategy="standard",
            hosted_timeout=True,
        )

    def router_native_anthropic_enabled(
        self, config: ProviderConfig, model: str | None = None
    ) -> bool:
        del model
        return bool(config.options.get("native_compat", True))

    def supported_protocols(
        self, config: ProviderConfig, model: str | None = None
    ) -> frozenset[MessageProtocol]:
        del config, model
        return frozenset({"anthropic_messages", "openai_responses"})

    def select_protocol(
        self,
        operation: MessageProtocol,
        config: ProviderConfig,
        model: str | None = None,
    ) -> MessageProtocol:
        del config, model
        return (
            "openai_responses"
            if operation == "openai_responses"
            else "anthropic_messages"
        )

    def model_configuration_profile(
        self, config: ProviderConfig
    ) -> tuple[Mapping[str, Any], str | None]:
        if self.normalize_model_id(config.model) != MUSE_SPARK_MODEL:
            return {}, None
        return (
            {
                "context_window": MUSE_SPARK_CONTEXT_WINDOW,
                "max_model_len": MUSE_SPARK_CONTEXT_WINDOW,
                "auto_compact_window": MUSE_SPARK_AUTO_COMPACT_LIMIT,
                "effort_level": "high",
                "model_profile": "muse-spark-1.1-1m",
            },
            "Muse Spark 1.1 profile applied: 1M context, high reasoning effort, "
            "and 900K automatic compaction. Start a new session after changing "
            "model, context, or reasoning effort.",
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
            label="Meta Model API",
            catalog_path="/v1/models",
        )

    def normalize_request_options(
        self, config: ProviderConfig, request: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        del config
        normalized = dict(request)
        if "input" in normalized and "messages" not in normalized:
            self._normalize_responses_request(normalized)
        else:
            self._normalize_messages_request(normalized)
        return normalized

    def normalize_tool_choice(
        self, config: ProviderConfig, model: str, tool_choice: Any
    ) -> Any:
        del config, model
        if not isinstance(tool_choice, Mapping):
            return tool_choice
        if str(tool_choice.get("type") or "").strip().lower() == "tool":
            return {"type": "auto"}
        return tool_choice

    def allows_sampling_overrides(self, config: ProviderConfig) -> bool:
        del config
        return False

    @classmethod
    def _normalize_responses_request(cls, request: dict[str, Any]) -> None:
        include = request.get("include")
        if request.get("previous_response_id"):
            if isinstance(include, list):
                values = [
                    value
                    for value in include
                    if value != "reasoning.encrypted_content"
                ]
                if values:
                    request["include"] = values
                else:
                    request.pop("include", None)
        else:
            values = list(include) if isinstance(include, list) else []
            if "reasoning.encrypted_content" not in values:
                values.append("reasoning.encrypted_content")
            request["include"] = values
        truncation = str(request.get("truncation") or "").strip().lower()
        if truncation == "auto":
            request["truncation"] = "disabled"
        reasoning = request.get("reasoning")
        if isinstance(reasoning, Mapping):
            projected = dict(reasoning)
            projected["effort"] = cls._effort(projected.get("effort"))
            request["reasoning"] = projected

    @classmethod
    def _normalize_messages_request(cls, request: dict[str, Any]) -> None:
        for key in ("stop_sequences", "top_k", "container", "inference_geo"):
            request.pop(key, None)
        if "temperature" in request and "top_p" in request:
            request.pop("top_p", None)
        thinking = request.get("thinking")
        if isinstance(thinking, Mapping):
            projected = dict(thinking)
            if str(projected.get("type") or "").strip().lower() == "disabled":
                projected = {"type": "adaptive"}
            request["thinking"] = projected
        output_config = request.get("output_config")
        if isinstance(output_config, Mapping) and output_config.get("effort") is not None:
            projected_output = dict(output_config)
            projected_output["effort"] = cls._effort(projected_output.get("effort"))
            request["output_config"] = projected_output

    @staticmethod
    def _effort(value: Any) -> str:
        effort = str(value or "high").strip().lower()
        if effort in {"none", "minimal"}:
            return "minimal"
        if effort in {"low", "medium", "high", "xhigh"}:
            return effort
        if effort in {"max", "ultra"}:
            return "xhigh"
        return "high"


__all__ = [
    "MUSE_SPARK_AUTO_COMPACT_LIMIT",
    "MUSE_SPARK_CONTEXT_WINDOW",
    "MUSE_SPARK_MODEL",
    "MetaModelProviderAdapter",
]
