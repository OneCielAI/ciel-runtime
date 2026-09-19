"""DeepSeek provider adapter."""

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

from ..architecture import (
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


THINKING_PASSTHROUGH_PLACEHOLDER = (
    "(no reasoning content was recorded for this turn)"
)


@dataclass(frozen=True)
class DeepSeekProviderAdapter(HttpBearerProviderAdapter):
    name: str = "deepseek"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["deepseek"]
    configuration_defaults_value: dict = field(
        default_factory=lambda: provider_configuration(
            "deepseek-v4-pro[1m]",
            custom_models=("deepseek-v4-pro[1m]", "deepseek-v4-flash"),
            native_compat=True,
            claude_code_supported_capabilities=[
                "effort",
                "max_effort",
                "thinking",
                "interleaved_thinking",
            ],
            context_window=1048576,
            max_output_tokens=8192,
            context_reserve_tokens=8192,
            request_timeout_ms=DEFAULT_REQUEST_TIMEOUT_MS,
            stream_enabled=True,
            stream_word_chunking=False,
            effort_level="max",
            haiku_model="deepseek-v4-flash",
            subagent_model="deepseek-v4-flash",
        )
    )
    send_placeholder_key: bool = True
    api_key_display_name_value: str = "DeepSeek"
    api_key_launch_error_value: str = (
        "Launch blocked: DeepSeek.com requires a DeepSeek API key."
    )
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="anthropic_messages",
            supports_thinking=True,
            # DeepSeek's Anthropic-compatible endpoint refuses a thinking-mode
            # request whose assistant history lost its thinking blocks
            # ("The `content[].thinking` in the thinking mode must be passed
            # back to the API", live 2026-09-18), so they are forwarded as-is
            # instead of being stripped for a "non-Anthropic" provider.
            preserves_anthropic_thinking=True,
            reasoning_output_recovery="disable",
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
            capacity_strategy="configured_first",
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
            show_sampling_controls=True,
            show_ip_family_control=True,
        )

    def status_policy(self, config: ProviderConfig) -> ProviderStatusPolicy:
        del config
        return ProviderStatusPolicy(
            kind="configured",
            configured_description="DeepSeek Anthropic API configured",
        )

    def supports_tool_choice(
        self, config: ProviderConfig, model: str | None = None
    ) -> bool:
        configured = config.options.get("supports_tool_choice")
        if configured is not None:
            return bool(configured)
        return (
            "deepseek-v4"
            not in str(model or config.model or "").split("[", 1)[0].lower()
        )

    @staticmethod
    def _normalized_effort(value: Any) -> str:
        effort = str(value or "").strip().lower()
        if effort in {"max", "xhigh"}:
            return "max"
        return "high"

    @staticmethod
    def _with_thinking_passthrough(messages: Any) -> Any:
        """Restore the thinking blocks DeepSeek demands from replayed history.

        A thinking-mode request whose assistant history holds a message without
        a ``thinking`` block is refused with "The `content[].thinking` in the
        thinking mode must be passed back to the API", whichever model produced
        that turn. Two live sources of such turns: a replayed cross-provider
        transcript, and a turn this router itself retried with thinking
        disabled (``codex_reasoning_budget_recovery``). DeepSeek does not
        validate the signature -- a synthesized placeholder answers 200
        (verified against the live API, 2026-09-18) -- so the turn is kept and
        given one. The signature is derived from the message content so a
        replayed prefix stays byte-stable for provider-side prompt caching.
        """

        if not isinstance(messages, list):
            return messages
        projected: list[Any] = []
        changed = False
        for message in messages:
            if (
                not isinstance(message, Mapping)
                or str(message.get("role") or "") != "assistant"
            ):
                projected.append(message)
                continue
            content = message.get("content")
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            if not isinstance(content, list) or any(
                isinstance(block, Mapping) and block.get("type") == "thinking"
                for block in content
            ):
                projected.append(message)
                continue
            digest = hashlib.sha1(
                json.dumps(
                    message, ensure_ascii=False, sort_keys=True, default=str
                ).encode("utf-8", "replace")
            ).hexdigest()
            projected.append(
                {
                    **message,
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": THINKING_PASSTHROUGH_PLACEHOLDER,
                            "signature": str(
                                uuid.uuid5(
                                    uuid.NAMESPACE_URL,
                                    f"ciel-runtime:deepseek:{digest}",
                                )
                            ),
                        },
                        *content,
                    ],
                }
            )
            changed = True
        return projected if changed else messages

    def normalize_request_options(
        self, config: ProviderConfig, request: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Honor DeepSeek's native thinking contract on Anthropic requests."""

        del config
        normalized = dict(request)
        thinking = request.get("thinking")
        thinking_disabled = isinstance(thinking, Mapping) and str(
            thinking.get("type") or ""
        ).strip().lower() in {"disabled", "none", "off", "false"}
        if not thinking_disabled:
            for key in (
                "temperature",
                "top_p",
                "top_k",
                "presence_penalty",
                "frequency_penalty",
            ):
                normalized.pop(key, None)
        # Only an explicitly enabled thinking mode carries the passthrough
        # contract; a body without the field is not an Anthropic Messages
        # thinking request (an OpenAI chat body reaches this hook too).
        if isinstance(thinking, Mapping) and not thinking_disabled:
            normalized["messages"] = self._with_thinking_passthrough(
                normalized.get("messages")
            )
        output_config = request.get("output_config")
        metadata = request.get("metadata")
        hinted_effort = (
            metadata.get("ciel_runtime_reasoning_effort")
            if isinstance(metadata, Mapping)
            else None
        )
        if thinking_disabled:
            normalized.pop("output_config", None)
            output_config = None
        elif not isinstance(output_config, Mapping) and isinstance(thinking, Mapping):
            if thinking.get("effort") is not None:
                output_config = {"effort": thinking.get("effort")}
        if (
            not thinking_disabled
            and not isinstance(output_config, Mapping)
            and hinted_effort is not None
        ):
            output_config = {"effort": hinted_effort}
        if isinstance(output_config, Mapping) and output_config.get("effort") is not None:
            normalized["output_config"] = {
                **output_config,
                "effort": self._normalized_effort(output_config.get("effort")),
            }
        if isinstance(thinking, Mapping) and "effort" in thinking:
            normalized["thinking"] = {
                key: value for key, value in thinking.items() if key != "effort"
            }
        return normalized

    def openai_reasoning_passback_enabled(
        self, config: ProviderConfig, model: str | None = None
    ) -> bool:
        del config
        normalized = str(model or "").split("[", 1)[0].lower()
        return normalized.startswith(("deepseek-v4-", "deepseek-reasoner"))

    def openai_reasoning_effort(
        self, config: ProviderConfig, model: str, request: Mapping[str, Any]
    ) -> str | None:
        del model
        metadata = request.get("metadata")
        hinted = metadata.get("ciel_runtime_reasoning_effort") if isinstance(metadata, Mapping) else None
        return self._normalized_effort(hinted or config.options.get("effort_level") or "high")

    def allows_sampling_overrides(self, config: ProviderConfig) -> bool:
        # Thinking is enabled by default. Do not inject configured sampling
        # values that DeepSeek documents as ineffective in that mode.
        del config
        return False


__all__ = ["DeepSeekProviderAdapter"]
