"""Ollama-specific context sizing, options, and context-error recovery policy."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Set
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class OllamaRequestContextPolicy:
    environ: Mapping[str, str]
    positive_int: Callable[[Any], int | None]
    estimate_tokens: Callable[[Any, dict[int, int] | None], int]
    model_matches: Callable[[str, str | None], bool]
    preset_names: Set[str]
    default_request_timeout_ms: int

    @staticmethod
    def context_bucket(target: int, minimum: int, maximum: int) -> int:
        target = max(minimum, min(maximum, target))
        for bucket in (4096, 8192, 16384, 32768, 65536, 131072, 262144):
            if bucket >= target:
                return min(bucket, maximum)
        return maximum

    def provider_context_limit(self, config: dict[str, Any]) -> int | None:
        current_model = str(config.get("current_model") or "")
        cached_model = str(config.get("model_context_model") or "")
        cached_limit = self.positive_int(config.get("model_context_max"))
        if not cached_limit:
            return None
        if cached_model and (
            not current_model or not self.model_matches(current_model, cached_model)
        ):
            return None
        return cached_limit

    def preserve_configured_context_cap(self, config: dict[str, Any]) -> bool:
        return str(config.get("llm_preset") or "").strip() in self.preset_names

    def effective_context_limit(self, config: dict[str, Any]) -> int | None:
        provider_limit = self.provider_context_limit(config)
        configured_max = self.positive_int(config.get("num_ctx_max"))
        if (
            provider_limit
            and configured_max
            and self.preserve_configured_context_cap(config)
        ):
            return min(provider_limit, configured_max)
        return provider_limit or configured_max

    def num_ctx_for_payload(
        self,
        config: dict[str, Any],
        payload: Any,
        _token_cache: dict[int, int] | None = None,
    ) -> int | None:
        override = self.environ.get("CIEL_RUNTIME_OLLAMA_NUM_CTX")
        if override:
            return self.positive_int(override)
        raw = config.get("num_ctx", "auto")
        if isinstance(raw, str) and raw.strip().lower() in {"", "auto", "dynamic"}:
            provider_limit = self.provider_context_limit(config)
            if provider_limit:
                return self.effective_context_limit(config) or provider_limit
            minimum = self.positive_int(config.get("num_ctx_min")) or 8192
            maximum = self.positive_int(config.get("num_ctx_max")) or 65536
            maximum = max(minimum, maximum)
            estimated = self.estimate_tokens(payload, _token_cache)
            target = int(estimated * 1.45) + 2048
            return self.context_bucket(target, minimum, maximum)
        return self.positive_int(raw)

    def num_ctx_status(self, config: dict[str, Any]) -> str:
        raw = config.get("num_ctx", "auto")
        if isinstance(raw, str) and raw.strip().lower() in {"", "auto", "dynamic"}:
            provider_limit = self.provider_context_limit(config)
            if provider_limit:
                effective_limit = self.effective_context_limit(config) or provider_limit
                if effective_limit < provider_limit:
                    return f"auto ({effective_limit:,}; model max {provider_limit:,})"
                return f"auto (provider {effective_limit:,})"
            minimum = self.positive_int(config.get("num_ctx_min")) or 8192
            maximum = self.positive_int(config.get("num_ctx_max")) or 65536
            return f"auto ({minimum}-{maximum})"
        return str(self.positive_int(raw) or raw)

    @staticmethod
    def extra_options(config: dict[str, Any]) -> dict[str, Any]:
        raw = config.get("ollama_options") or {}
        if not isinstance(raw, dict):
            return {}
        return {str(key): value for key, value in raw.items() if value is not None}

    def options_status(self, config: dict[str, Any]) -> str:
        options = self.extra_options(config)
        if not options:
            return "{}"
        return ", ".join(
            f"{key}={json.dumps(value, ensure_ascii=False)}"
            for key, value in sorted(options.items())
        )

    def request_timeout_seconds(self, config: dict[str, Any]) -> float:
        raw = config.get(
            "request_timeout_ms",
            config.get(
                "request_timeout",
                config.get("timeout_ms", self.default_request_timeout_ms),
            ),
        )
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return 120.0
        if value <= 0:
            return 120.0
        return max(1.0, value / 1000.0) if value > 10000 else value

    def context_error_limit(self, raw: str | None) -> int | None:
        text = str(raw or "")
        normalized = text.lower()
        if "context" not in normalized and "n_ctx" not in normalized:
            return None
        patterns = (
            r"available context size\s*\(\s*(\d+)\s+tokens?\s*\)",
            r'"n_ctx"\s*:\s*(\d+)',
            r"\bn_ctx\s*[=:]\s*(\d+)",
        )
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return self.positive_int(match.group(1))
        return None

    def context_retry_config(
        self, config: dict[str, Any], context_limit: int
    ) -> dict[str, Any]:
        retry_config = dict(config)
        context_limit = max(8192, int(context_limit))
        retry_config["num_ctx"] = context_limit
        retry_config["num_ctx_max"] = context_limit
        minimum = self.positive_int(retry_config.get("num_ctx_min"))
        if minimum and minimum > context_limit:
            retry_config["num_ctx_min"] = context_limit
        output_cap = max(256, min(2048, context_limit // 8))
        configured_output = self.positive_int(retry_config.get("max_output_tokens"))
        retry_config["max_output_tokens"] = (
            min(configured_output, output_cap) if configured_output else output_cap
        )
        options = dict(self.extra_options(retry_config))
        configured_num_predict = self.positive_int(options.get("num_predict"))
        if configured_num_predict:
            options["num_predict"] = min(configured_num_predict, output_cap)
        retry_config["ollama_options"] = options
        return retry_config

    def context_limit_for_budget(self, config: dict[str, Any]) -> int:
        raw = config.get("num_ctx", "auto")
        if isinstance(raw, str) and raw.strip().lower() in {"", "auto", "dynamic"}:
            return self.effective_context_limit(config) or 65536
        return (
            self.positive_int(raw)
            or self.positive_int(config.get("num_ctx_max"))
            or 65536
        )


__all__ = ["OllamaRequestContextPolicy"]
