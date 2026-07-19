"""Context-aware provider request timeout calculation and application policy."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .architecture import ProviderContextPolicy


@dataclass(frozen=True, slots=True)
class ProviderTimeoutSettings:
    default_ms: int
    minimum_ms: int
    maximum_ms: int
    round_ms: int
    idle_max_ms: int
    preset_timeouts: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class ProviderTimeoutPorts:
    positive_int: Callable[[Any], int | None]
    context_policy: Callable[[str, dict[str, Any]], ProviderContextPolicy]
    context_capacity: Callable[[str, dict[str, Any]], int | None]
    output_token_cap: Callable[[str, dict[str, Any], int | None], int | None]
    ollama_options: Callable[[dict[str, Any]], dict[str, Any]]
    catalog_timeout: Callable[[str], int | None]
    model_preset: Callable[[str], Mapping[str, Any]]
    timeout_for_context: Callable[[int | None], int]
    format_context: Callable[[int | None], str]


class ProviderTimeoutPolicy:
    def __init__(
        self,
        settings: ProviderTimeoutSettings,
        ports: ProviderTimeoutPorts,
    ) -> None:
        self.settings = settings
        self.ports = ports

    def configured_context(
        self,
        provider: str,
        config: dict[str, Any],
    ) -> int | None:
        strategy = self.ports.context_policy(provider, config).settings_strategy
        positive_int = self.ports.positive_int
        if strategy == "ollama":
            fixed = positive_int(config.get("num_ctx"))
            if fixed:
                return fixed
            return self.ports.context_capacity(provider, config) or positive_int(
                config.get("num_ctx_max")
            )
        if strategy == "standard":
            return positive_int(
                config.get("context_window")
            ) or self.ports.context_capacity(provider, config)
        return self.ports.context_capacity(provider, config)

    def configured_output(
        self,
        provider: str,
        config: dict[str, Any],
    ) -> int | None:
        positive_int = self.ports.positive_int
        if self.ports.context_policy(provider, config).settings_strategy == "ollama":
            options = self.ports.ollama_options(config)
            configured = positive_int(options.get("num_predict")) or positive_int(
                config.get("max_output_tokens")
            )
        else:
            configured = positive_int(
                config.get("max_output_tokens")
            ) or positive_int(config.get("num_predict"))
        return self.ports.output_token_cap(provider, config, configured)

    def clamp(self, milliseconds: int | float | None) -> int:
        value = self.ports.positive_int(milliseconds) or self.settings.default_ms
        value = max(self.settings.minimum_ms, min(self.settings.maximum_ms, value))
        return int(
            math.ceil(value / self.settings.round_ms) * self.settings.round_ms
        )

    def calculated(
        self,
        provider: str,
        config: dict[str, Any],
        timeout_candidates: list[int] | None = None,
    ) -> int:
        context_policy = self.ports.context_policy(provider, config)
        context_tokens = self.ports.positive_int(
            self.configured_context(provider, config)
        )
        output_tokens = self.ports.positive_int(
            self.configured_output(provider, config)
        )
        timeout_ms = self.settings.minimum_ms
        if context_tokens:
            context_score = max(
                0.0,
                min(
                    1.0,
                    math.log2(max(context_tokens, 65536) / 65536) / 4.0,
                ),
            )
            timeout_ms += int(240000 * context_score)
        if output_tokens:
            output_score = max(0.0, min(1.0, (output_tokens - 2048) / 6144))
            timeout_ms += int(120000 * output_score)
        if context_policy.hosted_timeout:
            timeout_ms += 60000
        for candidate in timeout_candidates or []:
            fixed = self.ports.positive_int(candidate)
            if fixed:
                timeout_ms = max(timeout_ms, fixed)
        timeout_ms *= context_policy.timeout_weight
        return self.clamp(timeout_ms)

    def recommended(
        self,
        provider: str,
        config: dict[str, Any],
        *,
        use_context_fallback: bool = True,
    ) -> int:
        model = str(config.get("current_model") or "")
        candidates: list[int] = []
        context_policy = self.ports.context_policy(provider, config)
        preset_id = str(config.get("llm_preset") or "").strip()
        preset_timeout = self.ports.positive_int(
            self.settings.preset_timeouts.get(preset_id)
        )
        if preset_timeout:
            candidates.append(preset_timeout)
        if context_policy.uses_catalog_timeout:
            catalog_timeout = self.ports.catalog_timeout(model)
            if catalog_timeout:
                candidates.append(catalog_timeout)
        model_timeout = self.ports.positive_int(
            self.ports.model_preset(model).get("recommended_timeout_ms")
        )
        if model_timeout:
            candidates.append(model_timeout)
        if candidates:
            return self.calculated(provider, config, candidates)
        if not use_context_fallback:
            return self.settings.default_ms
        context_timeout = self.ports.timeout_for_context(
            self.configured_context(provider, config)
        )
        return self.calculated(provider, config, [context_timeout])

    def apply(
        self,
        provider: str,
        config: dict[str, Any],
        *,
        use_context_fallback: bool = True,
    ) -> list[str]:
        timeout_ms = self.recommended(
            provider,
            config,
            use_context_fallback=use_context_fallback,
        )
        idle_ms = min(timeout_ms, self.settings.idle_max_ms)
        positive_int = self.ports.positive_int
        changed = (
            positive_int(config.get("request_timeout_ms")) != timeout_ms
            or positive_int(config.get("stream_idle_timeout_ms")) != idle_ms
        )
        config["request_timeout_ms"] = timeout_ms
        config["stream_idle_timeout_ms"] = idle_ms
        if not changed:
            return []
        context = self.configured_context(provider, config)
        return [
            f"Auto timeout: {timeout_ms}ms for context {self.ports.format_context(context)}.",
            f"stream_idle_timeout_ms: {idle_ms}",
        ]
