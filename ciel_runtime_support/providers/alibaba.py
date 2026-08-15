"""Alibaba Model Studio adapter for Qwen's native API capabilities."""

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
    ProviderRequestPolicy,
)
from ..runtime_constants import DEFAULT_REQUEST_TIMEOUT_MS
from .base import HttpBearerProviderAdapter, provider_configuration


QWEN38_MAX_MODEL = "qwen3.8-max"
QWEN38_MAX_PREVIEW_MODEL = "qwen3.8-max-preview"
QWEN38_CONTEXT_WINDOW = 1_048_576
QWEN38_MAX_OUTPUT = 131_072
QWEN38_AUTO_COMPACT = 900_000
ALIBABA_TOKEN_PLAN_RESPONSES_MAX_BYTES = 10 * 1024 * 1024
QWEN38_CODEX_CATALOG = {
    "context_window": 983_616,
    "max_context_window": 983_616,
    "effective_context_window_percent": 95,
    "supports_parallel_tool_calls": False,
    "supports_image_detail_original": True,
    "input_modalities": ["text", "image"],
    "shell_type": "default",
    "support_verbosity": False,
    "supports_reasoning_summaries": False,
    "experimental_supported_tools": [],
    "truncation_policy": {"mode": "bytes", "limit": 10_000},
    "supported_reasoning_levels": [
        {"effort": "low", "description": "Fast responses with lighter reasoning"},
        {"effort": "medium", "description": "Greater reasoning depth for complex problems"},
        {"effort": "xhigh", "description": "Extra high reasoning depth for complex problems"},
    ],
}
QWEN37_MAX_MODEL = "qwen3.7-max"
QWEN37_CONTEXT_WINDOW = 1_000_000
QWEN37_MAX_OUTPUT = 65_536
ALIBABA_CODING_PLAN_MODELS = (
    "qwen3.7-plus",
    "qwen3.6-plus",
    "kimi-k2.5",
    "glm-5",
    "MiniMax-M2.5",
    "qwen3.5-plus",
    "qwen3-max-2026-01-23",
    "qwen3-coder-next",
    "qwen3-coder-plus",
    "glm-4.7",
)
ALIBABA_MODEL_STUDIO_MODELS = (
    QWEN37_MAX_MODEL,
    "qwen3.7-plus",
    "qwen3.6-plus",
    "qwen3.6-flash",
    "qwen3.5-plus",
    "qwen3-coder-plus",
    "qwen3-coder-flash",
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "glm-5.2",
    "kimi-k2.7-code",
    "MiniMax-M2.5",
)
ALIBABA_TOKEN_PLAN_MODELS = (
    QWEN38_MAX_MODEL,
    QWEN37_MAX_MODEL,
    "qwen3.7-plus",
    "qwen3.6-plus",
    "qwen3.6-flash",
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "deepseek-v3.2",
    "kimi-k2.7-code",
    "kimi-k2.6",
    "kimi-k2.5",
    "glm-5.2",
    "glm-5.1",
    "glm-5",
    "MiniMax-M2.5",
)
_RESPONSES_MODEL_PREFIXES = (
    "qwen3.8-max",
    "qwen3.7-max",
    "qwen3.7-plus",
    "qwen3.6-plus",
    "qwen3.6-flash",
    "qwen3.5-plus",
    "qwen3.5-flash",
    "qwen3-coder-plus",
    "qwen3-coder-flash",
)
_CHAT_SEARCH_MODEL_PREFIXES = (
    "qwen3.8-max",
    "qwen3.7-plus",
    "qwen3.6-plus",
    "qwen3.6-flash",
    "qwen3.5-plus",
    "qwen3.5-flash",
)
_WEB_SEARCH_NAMES = frozenset({"websearch", "web_search"})
_WEB_FETCH_NAMES = frozenset({"webfetch", "web_fetch"})
_RESPONSES_TOOL_ALIASES = {
    "web_search_preview": "web_search",
    "t2i_search": "web_search_image",
    "i2i_search": "image_search",
}
_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class AlibabaModelStudioProviderAdapter(HttpBearerProviderAdapter):
    """Preserve Qwen Responses features while supporting Claude via Chat."""

    name: str = "alims-intl"
    base_url: str = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    configuration_defaults_value: dict = field(
        default_factory=lambda: provider_configuration(
            QWEN37_MAX_MODEL,
            custom_models=ALIBABA_MODEL_STUDIO_MODELS,
            native_compat=True,
            supports_tool_choice=True,
            context_window=QWEN37_CONTEXT_WINDOW,
            max_model_len=QWEN37_CONTEXT_WINDOW,
            max_output_tokens=QWEN37_MAX_OUTPUT,
            context_reserve_tokens=8192,
            auto_compact_window=QWEN38_AUTO_COMPACT,
            codex_auto_compact_window=QWEN38_AUTO_COMPACT,
            request_timeout_ms=DEFAULT_REQUEST_TIMEOUT_MS,
            stream_enabled=True,
            stream_word_chunking=False,
            effort_level="high",
            explicit_cache=True,
            explicit_cache_markers=4,
            haiku_model="qwen3.6-flash",
            opus_model=QWEN37_MAX_MODEL,
            sonnet_model="qwen3.7-plus",
            subagent_model="qwen3.7-plus",
        )
    )
    authorization_header: str = "authorization"
    include_x_api_key: bool = False
    require_api_key: bool = True
    api_key_display_name_value: str = "Alibaba Model Studio International"
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="openai_responses",
            supports_thinking=True,
            requires_api_key=True,
        )
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(
            chat_path="/v1/chat/completions",
            models_path="/v1/models",
        )
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(
            kind="openai",
            fallback_models=ALIBABA_MODEL_STUDIO_MODELS,
            allow_configured_fallback=True,
            authoritative_upstream_catalog=True,
        )
    )

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(
            capacity_strategy="configured_first",
            settings_strategy="standard",
            hosted_timeout=True,
        )

    def supported_protocols(
        self, config: ProviderConfig, model: str | None = None
    ) -> frozenset[MessageProtocol]:
        protocols: set[MessageProtocol] = {"openai_chat"}
        if self._supports_responses(model):
            protocols.add("openai_responses")
        if self.router_native_anthropic_enabled(config, model):
            protocols.add("anthropic_messages")
        return frozenset(protocols)

    def select_protocol(
        self, operation: MessageProtocol, config: ProviderConfig, model: str | None = None
    ) -> MessageProtocol:
        if operation == "anthropic_messages" and self.router_native_anthropic_enabled(
            config, model
        ):
            return "anthropic_messages"
        return (
            "openai_responses"
            if operation == "openai_responses" and self._supports_responses(model)
            else "openai_chat"
        )

    def router_native_anthropic_enabled(
        self, config: ProviderConfig, model: str | None = None
    ) -> bool:
        del model
        return bool(config.options.get("native_compat", True))

    def anthropic_base_url(self, config: ProviderConfig) -> str:
        base = str(config.base_url or self.default_base_url()).rstrip("/")
        suffix = "/compatible-mode/v1"
        if base.endswith(suffix):
            return f"{base[:-len(suffix)]}/apps/anthropic"
        return base

    def supports_server_web_tools(self, config: ProviderConfig) -> bool:
        return self._supports_chat_search(config.model)

    def model_configuration_profile(
        self, config: ProviderConfig
    ) -> tuple[Mapping[str, Any], str | None]:
        if not self._is_qwen38(config.model):
            if QWEN37_MAX_MODEL not in self._clean_model(config.model):
                return {}, None
            return (
                {
                    "context_window": QWEN37_CONTEXT_WINDOW,
                    "max_model_len": QWEN37_CONTEXT_WINDOW,
                    "max_output_tokens": QWEN37_MAX_OUTPUT,
                    "auto_compact_window": QWEN38_AUTO_COMPACT,
                    "codex_auto_compact_window": QWEN38_AUTO_COMPACT,
                    "effort_level": "high",
                    "model_profile": "qwen3.7-max-1m",
                },
                "Qwen3.7-Max profile applied: 1M context, 65K output, high reasoning, and 900K compaction.",
            )
        return (
            {
                "context_window": QWEN38_CONTEXT_WINDOW,
                "max_model_len": QWEN38_CONTEXT_WINDOW,
                "max_output_tokens": QWEN38_MAX_OUTPUT,
                "auto_compact_window": QWEN38_AUTO_COMPACT,
                "codex_auto_compact_window": QWEN38_AUTO_COMPACT,
                "effort_level": "xhigh",
                "model_profile": "qwen3.8-max-1m",
                "codex_model_catalog": deepcopy(QWEN38_CODEX_CATALOG),
            },
            "Qwen3.8-Max profile applied: 1M context, 131K output, xhigh reasoning, and 900K compaction.",
        )

    def model_selection_config_updates(
        self, config: ProviderConfig, model_id: str
    ) -> Mapping[str, Any]:
        del config
        return {
            "haiku_model": model_id,
            "opus_model": model_id,
            "sonnet_model": model_id,
            "subagent_model": model_id,
        }

    def normalize_request_options(
        self, config: ProviderConfig, request: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        normalized = dict(request)
        if "messages" in normalized:
            normalized["messages"] = deepcopy(normalized["messages"])
        if "tools" in normalized:
            normalized["tools"] = deepcopy(normalized["tools"])
        model = str(normalized.get("model") or config.model)
        if (
            "input" in normalized
            and "messages" not in normalized
            and self._supports_responses(model)
        ):
            self._normalize_responses(normalized, model)
        elif "messages" in normalized:
            self._normalize_chat(config, normalized, model)
        return normalized

    def openai_reasoning_effort(
        self, config: ProviderConfig, model: str, request: Mapping[str, Any]
    ) -> str | None:
        if not self._supports_responses(model):
            return None
        value = str(
            request.get("reasoning_effort")
            or config.options.get("effort_level")
            or "xhigh"
        ).strip().lower()
        if self._is_qwen38(model):
            return self._normalize_qwen38_effort(value)
        return value if value in _EFFORTS else "xhigh"

    def openai_reasoning_passback_enabled(
        self, config: ProviderConfig, model: str | None = None
    ) -> bool:
        del config
        return self._is_qwen38(model)

    def allows_sampling_overrides(self, config: ProviderConfig) -> bool:
        del config
        return False

    @classmethod
    def _normalize_responses(cls, request: dict[str, Any], model: str) -> None:
        reasoning = request.get("reasoning")
        if isinstance(reasoning, Mapping):
            projected = dict(reasoning)
            effort = str(projected.get("effort") or "xhigh").strip().lower()
            projected["effort"] = (
                cls._normalize_qwen38_effort(effort)
                if cls._is_qwen38(model)
                else effort if effort in _EFFORTS else "xhigh"
            )
            request["reasoning"] = projected
            if cls._is_qwen38(model):
                request.pop("thinking_budget", None)
        elif request.get("enable_thinking") is False and cls._is_qwen38(model):
            request["reasoning"] = {"effort": "none"}
            request.pop("thinking_budget", None)
        request.pop("enable_thinking", None)

        tools = request.get("tools")
        if isinstance(tools, list):
            normalized_tools: list[Any] = []
            seen: set[str] = set()
            for tool in tools:
                if not isinstance(tool, Mapping):
                    normalized_tools.append(tool)
                    continue
                projected = dict(tool)
                tool_type = str(projected.get("type") or "").strip()
                projected["type"] = _RESPONSES_TOOL_ALIASES.get(tool_type, tool_type)
                identity = str(projected)
                if identity not in seen:
                    normalized_tools.append(projected)
                    seen.add(identity)
            request["tools"] = normalized_tools

    @classmethod
    def _normalize_chat(
        cls, config: ProviderConfig, request: dict[str, Any], model: str
    ) -> None:
        tools = request.get("tools")
        has_search = False
        has_fetch = False
        remaining: list[Any] = []
        supports_search = cls._supports_chat_search(model)
        if isinstance(tools, list):
            for tool in tools:
                function = tool.get("function") if isinstance(tool, Mapping) else None
                name = str(function.get("name") or "").strip().lower() if isinstance(function, Mapping) else ""
                if supports_search and name in _WEB_SEARCH_NAMES:
                    has_search = True
                elif supports_search and name in _WEB_FETCH_NAMES:
                    has_fetch = True
                else:
                    remaining.append(tool)
            if remaining:
                request["tools"] = remaining
            else:
                request.pop("tools", None)
                request.pop("tool_choice", None)
        if has_search or has_fetch:
            request["enable_search"] = True
            request["search_options"] = {
                "search_strategy": (
                    "max" if cls._is_qwen38(model)
                    else "agent_max" if has_fetch else "agent"
                )
            }
            if request.get("tool_choice") not in (None, "none"):
                request["tool_choice"] = "auto"

        if bool(config.options.get("explicit_cache", True)):
            messages = request.get("messages")
            if isinstance(messages, list):
                cls._apply_explicit_cache_markers(
                    messages,
                    config.options.get("explicit_cache_markers", 4),
                )

        if cls._is_qwen38(model):
            effort = request.get("reasoning_effort")
            if effort is not None:
                request["reasoning_effort"] = cls._normalize_qwen38_effort(effort)
                request.pop("thinking_budget", None)
            if "max_tokens" in request and "max_completion_tokens" not in request:
                request["max_completion_tokens"] = request.pop("max_tokens")

    @staticmethod
    def _normalize_qwen38_effort(value: Any) -> str:
        effort = str(value or "xhigh").strip().lower()
        if effort in {"max", "high", "xhigh"}:
            return "xhigh"
        if effort == "medium":
            return "medium"
        if effort in {"minimal", "low"}:
            return "low"
        if effort == "none":
            return "none"
        return "xhigh"

    @classmethod
    def _apply_explicit_cache_markers(
        cls, messages: list[Any], configured_limit: Any
    ) -> None:
        try:
            limit = max(1, min(4, int(configured_limit)))
        except (TypeError, ValueError):
            limit = 4
        for message in messages:
            if isinstance(message, dict):
                cls._clear_message_cache_control(message)
        cacheable = [
            index
            for index, message in enumerate(messages)
            if isinstance(message, dict) and cls._cacheable_content(message.get("content"))
        ]
        if not cacheable:
            return
        system = next(
            (
                index
                for index in cacheable
                if str(messages[index].get("role") or "").lower() == "system"
            ),
            None,
        )
        conversation = [index for index in cacheable if index != system]
        selected: list[int] = [system] if system is not None else []
        thresholds = iter((1, 9, 17))
        threshold = next(thresholds, None)
        blocks_from_tail = 0
        for index in reversed(conversation):
            blocks_from_tail += cls._content_block_count(messages[index].get("content"))
            while threshold is not None and blocks_from_tail >= threshold:
                if index not in selected and len(selected) < limit:
                    selected.append(index)
                threshold = next(thresholds, None)
            if threshold is None or len(selected) >= limit:
                break
        for index in selected[:limit]:
            cls._mark_message_cache_control(messages[index])

    @staticmethod
    def _cacheable_content(content: Any) -> bool:
        if isinstance(content, str):
            return bool(content)
        return isinstance(content, list) and bool(content)

    @staticmethod
    def _content_block_count(content: Any) -> int:
        return max(1, len(content)) if isinstance(content, list) else 1

    @staticmethod
    def _mark_message_cache_control(message: dict[str, Any]) -> None:
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = [
                {
                    "type": "text",
                    "text": content,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
            return
        if not isinstance(content, list):
            return
        for index in range(len(content) - 1, -1, -1):
            block = content[index]
            if isinstance(block, Mapping):
                projected = dict(block)
                projected["cache_control"] = {"type": "ephemeral"}
                content[index] = projected
                return
            if isinstance(block, str) and block:
                content[index] = {
                    "type": "text",
                    "text": block,
                    "cache_control": {"type": "ephemeral"},
                }
                return

    @staticmethod
    def _clear_message_cache_control(message: dict[str, Any]) -> None:
        content = message.get("content")
        if not isinstance(content, list):
            return
        for index, block in enumerate(content):
            if not isinstance(block, Mapping) or "cache_control" not in block:
                continue
            projected = dict(block)
            projected.pop("cache_control", None)
            content[index] = projected

    @classmethod
    def _is_qwen38(cls, model: str) -> bool:
        return cls._clean_model(model) == QWEN38_MAX_MODEL

    @classmethod
    def _supports_responses(cls, model: str | None) -> bool:
        clean = cls._clean_model(str(model or ""))
        return any(prefix in clean for prefix in _RESPONSES_MODEL_PREFIXES)

    @classmethod
    def _supports_chat_search(cls, model: str | None) -> bool:
        clean = cls._clean_model(str(model or ""))
        return any(prefix in clean for prefix in _CHAT_SEARCH_MODEL_PREFIXES)

    def supports_tool_choice_for_request(
        self,
        config: ProviderConfig,
        model: str | None,
        request: Mapping[str, Any],
    ) -> bool:
        if self._thinking_enabled(config, request):
            return False
        return self.supports_tool_choice(config, model)

    @staticmethod
    def _thinking_enabled(
        config: ProviderConfig, request: Mapping[str, Any]
    ) -> bool:
        thinking = request.get("thinking")
        if isinstance(thinking, Mapping):
            state = str(thinking.get("type") or "").strip().lower()
            if state in {"enabled", "adaptive"}:
                return True
            if state == "disabled":
                return False
            try:
                if int(thinking.get("budget_tokens") or 0) > 0:
                    return True
            except (TypeError, ValueError):
                pass
        if request.get("enable_thinking") is True:
            return True
        if request.get("enable_thinking") is False:
            return False
        reasoning = request.get("reasoning")
        if isinstance(reasoning, Mapping):
            effort = str(reasoning.get("effort") or "").strip().lower()
            return effort not in {"", "none", "minimal"}
        effort = str(config.options.get("effort_level") or "").strip().lower()
        return effort not in {"", "none", "minimal"}

    @staticmethod
    def _clean_model(model: str) -> str:
        value = str(model or "").strip().lower()
        return QWEN38_MAX_MODEL if QWEN38_MAX_MODEL in value else value


@dataclass(frozen=True)
class AlibabaTokenPlanProviderAdapter(AlibabaModelStudioProviderAdapter):
    """Singapore Token Plan with native Claude and Responses-based Codex routes."""

    name: str = "alitoken"
    base_url: str = (
        "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
    )
    configuration_defaults_value: dict = field(
        default_factory=lambda: provider_configuration(
            QWEN38_MAX_MODEL,
            custom_models=ALIBABA_TOKEN_PLAN_MODELS,
            native_compat=True,
            supports_tool_choice=True,
            context_window=QWEN38_CONTEXT_WINDOW,
            max_model_len=QWEN38_CONTEXT_WINDOW,
            max_output_tokens=QWEN38_MAX_OUTPUT,
            context_reserve_tokens=8192,
            auto_compact_window=QWEN38_AUTO_COMPACT,
            codex_auto_compact_window=QWEN38_AUTO_COMPACT,
            request_timeout_ms=DEFAULT_REQUEST_TIMEOUT_MS,
            responses_stream_truncation_retries=1,
            stream_enabled=True,
            stream_word_chunking=False,
            effort_level="xhigh",
            explicit_cache=True,
            explicit_cache_markers=4,
            haiku_model="qwen3.6-flash",
            opus_model=QWEN38_MAX_MODEL,
            sonnet_model="qwen3.7-plus",
            subagent_model="qwen3.7-plus",
            region="ap-southeast-1",
        )
    )
    api_key_display_name_value: str = "Alibaba Model Studio Token Plan (Singapore)"
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(
            kind="openai",
            fallback_models=ALIBABA_TOKEN_PLAN_MODELS,
            allow_configured_fallback=True,
            authoritative_upstream_catalog=True,
            supplemental_model_aliases=((QWEN38_MAX_MODEL, QWEN38_MAX_PREVIEW_MODEL),),
        )
    )

    def responses_request_max_bytes(self, config: ProviderConfig) -> int | None:
        del config
        return ALIBABA_TOKEN_PLAN_RESPONSES_MAX_BYTES

@dataclass(frozen=True)
class AlibabaIndividualTokenPlanProviderAdapter(AlibabaTokenPlanProviderAdapter):
    """Individual Token Plan using its separately billed coding endpoint."""

    name: str = "alitoken-individual"
    base_url: str = "https://coding.dashscope.aliyuncs.com/v1"
    api_key_display_name_value: str = "Alibaba Token Plan Individual"
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(
            kind="openai",
            fallback_models=ALIBABA_TOKEN_PLAN_MODELS,
            allow_configured_fallback=True,
            authoritative_upstream_catalog=True,
        )
    )

    def responses_request_max_bytes(self, config: ProviderConfig) -> int | None:
        del config
        return None

    def anthropic_base_url(self, config: ProviderConfig) -> str:
        del config
        return "https://coding.dashscope.aliyuncs.com/apps/anthropic"


__all__ = [
    "ALIBABA_TOKEN_PLAN_RESPONSES_MAX_BYTES",
    "ALIBABA_CODING_PLAN_MODELS",
    "ALIBABA_MODEL_STUDIO_MODELS",
    "ALIBABA_TOKEN_PLAN_MODELS",
    "AlibabaModelStudioProviderAdapter",
    "AlibabaIndividualTokenPlanProviderAdapter",
    "AlibabaTokenPlanProviderAdapter",
    "QWEN38_AUTO_COMPACT",
    "QWEN38_CONTEXT_WINDOW",
    "QWEN38_MAX_MODEL",
    "QWEN38_MAX_OUTPUT",
]
