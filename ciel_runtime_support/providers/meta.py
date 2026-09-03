"""Meta Model API / Muse Spark provider adapter."""

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
from .base import HttpBearerProviderAdapter, provider_configuration
from .constants import DEFAULT_REQUEST_TIMEOUT_MS, PROVIDER_DEFAULT_BASE_URLS


MUSE_SPARK_MODEL = "muse-spark-1.3"
MUSE_SPARK_MODELS = (
    MUSE_SPARK_MODEL,
    "muse-spark-1.3-contributor",
    "muse-spark-1.2",
    "muse-spark-1.2-contributor",
    "muse-spark-1.1",
)
MUSE_SPARK_CONTEXT_WINDOW = 1_048_576
MUSE_SPARK_AUTO_COMPACT_LIMIT = 900_000
MUSE_SPARK_CODEX_CATALOG = {
    "context_window": MUSE_SPARK_CONTEXT_WINDOW,
    "max_context_window": MUSE_SPARK_CONTEXT_WINDOW,
    "input_modalities": ["text", "image"],
    "support_verbosity": False,
    "supports_reasoning_summaries": True,
    "supported_reasoning_levels": [
        {"effort": "minimal", "description": "Shortest reasoning pass"},
        {"effort": "low", "description": "Light reasoning"},
        {"effort": "medium", "description": "Moderate reasoning depth"},
        {"effort": "high", "description": "Deep reasoning"},
        {
            "effort": "xhigh",
            "description": "Accepted alias; currently the same strength as high",
        },
    ],
    "default_reasoning_level": "high",
}


@dataclass(frozen=True)
class MetaModelProviderAdapter(HttpBearerProviderAdapter):
    """Preserve Meta's native Responses and Anthropic Messages contracts."""

    name: str = "meta"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["meta"]
    configuration_defaults_value: dict = field(
        default_factory=lambda: provider_configuration(
            MUSE_SPARK_MODEL,
            custom_models=MUSE_SPARK_MODELS,
            native_compat=True,
            preserve_anthropic_thinking=True,
            normalize_anthropic_tool_use=True,
            supports_tool_choice=True,
            claude_code_supported_capabilities=[
                "effort",
                "xhigh_effort",
                "thinking",
                "adaptive_thinking",
            ],
            context_window=MUSE_SPARK_CONTEXT_WINDOW,
            max_model_len=MUSE_SPARK_CONTEXT_WINDOW,
            auto_compact_window=MUSE_SPARK_AUTO_COMPACT_LIMIT,
            codex_auto_compact_window=MUSE_SPARK_AUTO_COMPACT_LIMIT,
            codex_model_catalog=deepcopy(MUSE_SPARK_CODEX_CATALOG),
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
            fallback_models=MUSE_SPARK_MODELS,
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

    def supports_server_web_tools(self, config: ProviderConfig) -> bool:
        del config
        return True

    def supported_protocols(
        self, config: ProviderConfig, model: str | None = None
    ) -> frozenset[MessageProtocol]:
        del config, model
        return frozenset(
            {"anthropic_messages", "openai_chat", "openai_responses"}
        )

    def select_protocol(
        self,
        operation: MessageProtocol,
        config: ProviderConfig,
        model: str | None = None,
    ) -> MessageProtocol:
        if operation in self.supported_protocols(config, model):
            return operation
        return "anthropic_messages"

    def model_configuration_profile(
        self, config: ProviderConfig
    ) -> tuple[Mapping[str, Any], str | None]:
        model = self.normalize_model_id(config.model)
        if model not in MUSE_SPARK_MODELS:
            return {}, None
        contributor_notice = (
            " Contributor tier permits Meta to train on prompts and completions."
            if model.endswith("-contributor")
            else ""
        )
        return (
            {
                "context_window": MUSE_SPARK_CONTEXT_WINDOW,
                "max_model_len": MUSE_SPARK_CONTEXT_WINDOW,
                "auto_compact_window": MUSE_SPARK_AUTO_COMPACT_LIMIT,
                "effort_level": "high",
                "codex_model_catalog": deepcopy(MUSE_SPARK_CODEX_CATALOG),
                "model_profile": f"{model}-1m",
            },
            f"{model} profile applied: 1M context, high reasoning effort, "
            "and 900K automatic compaction. Start a new session after changing "
            f"model, context, or reasoning effort.{contributor_notice}",
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

    def normalize_request_options_for_protocol(
        self,
        config: ProviderConfig,
        request: Mapping[str, Any],
        protocol: MessageProtocol | None,
    ) -> Mapping[str, Any]:
        del config
        normalized = dict(request)
        if protocol == "openai_responses":
            self._normalize_responses_request(normalized)
        elif protocol == "openai_chat":
            self._normalize_chat_request(normalized)
        elif protocol == "anthropic_messages":
            self._normalize_messages_request(normalized)
        elif "input" in normalized and "messages" not in normalized:
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
            projected["effort"] = cls._responses_effort(projected.get("effort"))
            # Match the official OpenCode Meta path: keep a concise visible
            # reasoning summary while encrypted_content carries replay state.
            projected.setdefault("summary", "auto")
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
            projected_output["effort"] = cls._messages_effort(
                projected_output.get("effort")
            )
            request["output_config"] = projected_output

    @staticmethod
    def _normalize_chat_request(request: dict[str, Any]) -> None:
        # Meta Chat Completions documents only the automatic tool-selection
        # mode. OpenAI clients can send a named function choice object; retain
        # the tools but project that unsupported selector to Meta's auto mode.
        tool_choice = request.get("tool_choice")
        if isinstance(tool_choice, Mapping):
            request["tool_choice"] = "auto"

    @staticmethod
    def _responses_effort(value: Any) -> str:
        effort = str(value or "high").strip().lower()
        if effort in {"none", "minimal"}:
            return "minimal"
        if effort in {"low", "medium", "high", "xhigh"}:
            return effort
        if effort in {"max", "ultra"}:
            return "xhigh"
        return "high"

    @classmethod
    def _messages_effort(cls, value: Any) -> str:
        effort = cls._responses_effort(value)
        return "low" if effort == "minimal" else effort


__all__ = [
    "MUSE_SPARK_AUTO_COMPACT_LIMIT",
    "MUSE_SPARK_CODEX_CATALOG",
    "MUSE_SPARK_CONTEXT_WINDOW",
    "MUSE_SPARK_MODEL",
    "MUSE_SPARK_MODELS",
    "MetaModelProviderAdapter",
]
