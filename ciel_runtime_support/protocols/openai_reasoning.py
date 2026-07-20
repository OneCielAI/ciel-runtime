"""OpenAI reasoning/tool-choice projection and provider policy orchestration."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Callable

from ..architecture import ProviderAdapter, ProviderConfig


def anthropic_tool_choice_to_openai(tool_choice: Any) -> Any:
    if not isinstance(tool_choice, dict):
        return tool_choice
    choice_type = tool_choice.get("type")
    if choice_type == "tool" and tool_choice.get("name"):
        return {"type": "function", "function": {"name": str(tool_choice["name"])}}
    if choice_type == "any":
        return "required"
    if choice_type == "auto":
        return "auto"
    return tool_choice


def openai_reasoning_to_anthropic_thinking_block(
    reasoning_content: Any,
) -> dict[str, Any] | None:
    reasoning = str(reasoning_content or "")
    if not reasoning:
        return None
    digest = hashlib.sha256(reasoning.encode("utf-8", errors="replace")).hexdigest()[:24]
    return {
        "type": "thinking",
        "thinking": reasoning,
        "signature": f"ciel-runtime-openai-reasoning-{digest}",
    }


@dataclass(frozen=True, slots=True)
class OpenAiReasoningPolicy:
    """Delegate provider-specific reasoning support to its registered adapter."""

    adapter_for: Callable[[str, dict[str, Any]], ProviderAdapter]
    config_for: Callable[[str, dict[str, Any]], ProviderConfig]

    def passback_enabled(
        self, provider: str, model: str | None, config: dict[str, Any]
    ) -> bool:
        adapter = self.adapter_for(provider, config)
        return adapter.openai_reasoning_passback_enabled(
            self.config_for(provider, config), model
        )

    def passback_enabled_for_body(
        self, provider: str, config: dict[str, Any], body: dict[str, Any]
    ) -> bool:
        return self.passback_enabled(provider, str(body.get("model") or ""), config)

    def should_omit_tool_choice(
        self,
        provider: str,
        model: str,
        body: dict[str, Any],
        config: dict[str, Any],
    ) -> bool:
        if body.get("tool_choice") is None:
            return False
        return self.passback_enabled(provider, model, config)


__all__ = [
    "OpenAiReasoningPolicy",
    "anthropic_tool_choice_to_openai",
    "openai_reasoning_to_anthropic_thinking_block",
]
