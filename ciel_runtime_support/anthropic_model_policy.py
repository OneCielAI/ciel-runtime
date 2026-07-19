"""Anthropic model metadata and Claude Code capability policy."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable


SUPPORTED_CAPABILITIES = (
    "effort",
    "xhigh_effort",
    "max_effort",
    "thinking",
    "adaptive_thinking",
    "interleaved_thinking",
)


def model_family(model_id: str) -> str:
    model = (model_id or "").strip().lower()
    for family in ("fable", "mythos", "opus", "sonnet", "haiku"):
        if re.search(rf"(?:^|-)claude-(?:\d+(?:-\d+){{0,2}}-)?{family}(?:-|$)", model) or f"-{family}-" in model:
            return family
    return "claude"


def limit_hints(model_id: str) -> dict[str, Any]:
    model = (model_id or "").strip().lower()
    family = model_family(model)
    if family in ("fable", "mythos") or (
        family == "opus" and re.search(r"(?:^|-)opus-4-[678](?:-|$)", model)
    ):
        return {
            "context_window": 1048576,
            "max_output_tokens": 128000,
            "source": "anthropic-models-overview-current-table",
        }
    if family == "sonnet" and re.search(r"(?:^|-)sonnet-4-6(?:-|$)", model):
        return {
            "context_window": 1048576,
            "max_output_tokens": 64000,
            "source": "anthropic-models-overview-current-table",
        }
    if family == "haiku" and re.search(r"(?:^|-)haiku-4-5(?:-|$)", model):
        return {
            "context_window": 200000,
            "max_output_tokens": 64000,
            "source": "anthropic-models-overview-current-table",
        }
    return {"context_window": 200000, "source": "anthropic-default-compatibility"}


def runtime_hints(model_id: str) -> dict[str, Any]:
    model = (model_id or "").strip().lower()
    if model_family(model) in ("fable", "mythos"):
        return {
            "claude_code_default_effort": "high",
            "claude_code_max_effort": "xhigh",
            "thinking_mode": "adaptive",
            "extended_thinking": False,
            "adaptive_thinking_always_on": True,
            "unsupported_sampling_parameters": ["temperature", "top_p", "top_k"],
            "source": "anthropic-models-overview-current-table",
        }
    if re.search(r"(?:^|-)opus-4-8(?:-|$)", model):
        return {
            "claude_code_default_effort": "high",
            "claude_code_max_effort": "xhigh",
            "thinking_mode": "adaptive",
            "fast_mode": {"available": True, "preview": True},
            "unsupported_sampling_parameters": ["temperature", "top_p", "top_k"],
            "source": "anthropic-opus-4-8-launch-notes",
        }
    return {}


def normalize_capabilities(value: Any) -> list[str]:
    if value is None or value is False:
        return []
    if isinstance(value, str):
        raw_items = re.split(r"[,;\s]+", value.strip())
    elif isinstance(value, (list, tuple, set)):
        raw_items = [str(item) for item in value]
    else:
        raw_items = [str(value)]
    allowed = set(SUPPORTED_CAPABILITIES)
    result: list[str] = []
    for raw in raw_items:
        item = str(raw or "").strip().lower().replace("-", "_")
        if item and item in allowed and item not in result:
            result.append(item)
    return result


def infer_capabilities(model_id: str, strip_context_suffix: Callable[[str], str]) -> list[str]:
    model = strip_context_suffix(model_id).strip().lower()
    if re.search(r"(?:^|-)claude-(?:fable|mythos)(?:-|$)", model):
        return ["effort", "xhigh_effort", "max_effort", "thinking", "adaptive_thinking"]
    if re.search(r"(?:^|-)opus-4-[78](?:-|$)", model):
        return [
            "effort", "xhigh_effort", "max_effort", "thinking",
            "adaptive_thinking", "interleaved_thinking",
        ]
    if re.search(r"(?:^|-)(?:opus-4-6|sonnet-4-6)(?:-|$)", model):
        return ["effort", "max_effort", "thinking", "adaptive_thinking", "interleaved_thinking"]
    return []


def recommended_preset(model_id: str) -> str:
    return "fast" if model_family(model_id) == "haiku" else "balanced"


@dataclass(frozen=True, slots=True)
class AnthropicModelRecommendations:
    unique_ids: Callable[[str, list[str]], list[str]]
    preset_timeout_ms: Callable[[str], int]
    idle_timeout_ms: Callable[[int], int]

    def build(self, provider: str, models: list[str]) -> dict[str, Any]:
        if provider != "anthropic":
            return {}
        recommendations: dict[str, Any] = {}
        for model_id in self.unique_ids(provider, models):
            preset = recommended_preset(model_id)
            timeout = self.preset_timeout_ms(preset)
            recommendations[model_id] = {
                "schema": 1,
                "model_family": model_family(model_id),
                "recommended_preset": preset,
                "parameters": {
                    "max_output_tokens": 2048 if preset == "fast" else 4096,
                    "request_timeout_ms": timeout,
                    "stream_idle_timeout_ms": self.idle_timeout_ms(timeout),
                },
                "limits": limit_hints(model_id),
                "runtime": runtime_hints(model_id),
                "notes": [
                    "Native Claude Code manages the real context window; ciel-runtime stores context limits as model metadata only.",
                    "Recommended max_output_tokens is intentionally lower than the provider hard limit for interactive CLI use.",
                ],
            }
        return recommendations
