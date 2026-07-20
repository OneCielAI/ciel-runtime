"""Provider request output-token budgeting policy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class OpenAIContextBudgetPolicy:
    """Resolve context capacity for providers using OpenAI-compatible payloads."""

    positive_int: Callable[[Any], int | None]
    runtime_capacity: Callable[[str, dict[str, Any]], int]
    nvidia_default: Callable[[str], int]

    def context_limit(self, provider: str, config: dict[str, Any]) -> int:
        configured = self.positive_int(config.get("context_window")) or self.positive_int(
            config.get("max_model_len")
        )
        if provider in ("vllm", "self-hosted-nim"):
            runtime = self.runtime_capacity(provider, config)
            if configured and str(config.get("llm_preset") or "").strip():
                return min(configured, runtime) if runtime else configured
            if runtime:
                return runtime
        if configured:
            return configured
        if provider == "nvidia-hosted":
            return self.nvidia_default(str(config.get("current_model") or ""))
        return 65536


@dataclass(frozen=True, slots=True)
class OutputBudgetPolicy:
    positive_int: Callable[[Any], int | None]
    estimate_tokens: Callable[[Any, dict[int, int] | None], int]
    provider_options: Callable[[dict[str, Any]], dict[str, Any]]

    def configured_tokens(
        self,
        config: dict[str, Any],
        body: dict[str, Any],
        option_key: str | None = None,
    ) -> int | None:
        configured = self.positive_int(config.get("max_output_tokens"))
        if option_key:
            configured = (
                self.positive_int(self.provider_options(config).get(option_key))
                or configured
            )
        requested = self.positive_int(body.get("max_tokens"))
        if configured and requested:
            return min(configured, requested)
        return configured or requested

    def reserve_tokens(
        self, config: dict[str, Any], context_limit: int | None
    ) -> int:
        configured = self.positive_int(config.get("context_reserve_tokens"))
        if configured:
            return configured
        if not context_limit:
            return 1024
        return max(1024, min(32768, int(context_limit) // 32))

    def cap_tokens_for_context(
        self,
        config: dict[str, Any],
        body: dict[str, Any],
        payload: Any,
        context_limit: int | None,
        configured: int | None,
        _token_cache: dict[int, int] | None = None,
    ) -> int | None:
        del body
        if not configured:
            return None
        if not context_limit:
            return configured
        reserve = self.reserve_tokens(config, context_limit)
        available = context_limit - self.estimate_tokens(payload, _token_cache) - reserve
        if available <= 0:
            return min(configured, 256)
        return max(1, min(configured, available))


__all__ = ["OpenAIContextBudgetPolicy", "OutputBudgetPolicy"]
