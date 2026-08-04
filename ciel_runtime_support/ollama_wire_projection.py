"""Project provider-owned Ollama request settings onto wire payloads.

The application uses several Ollama request paths (interactive forwarding,
Advisor, and optional LLM compaction).  This policy is their single boundary
for optional model/runtime settings.  It deliberately distinguishes required
protocol fields from explicit operator overrides so persisted adapter defaults
do not silently become upstream request parameters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


@dataclass(frozen=True, slots=True)
class OllamaWireProjectionPorts:
    think_value: Callable[
        [str, str | None, dict[str, Any], Mapping[str, Any]], bool | str | None
    ]
    positive_int: Callable[[Any], int | None]


@dataclass(frozen=True, slots=True)
class OllamaWireProjection:
    ports: OllamaWireProjectionPorts
    legacy_keep_alive_default: str = "5m"

    @staticmethod
    def _marked_keys(config: Mapping[str, Any], name: str) -> set[str]:
        raw = config.get(name)
        if not isinstance(raw, (list, tuple, set)):
            return set()
        return {str(item) for item in raw if str(item).strip()}

    def explicit_options(self, config: Mapping[str, Any]) -> dict[str, Any]:
        raw = config.get("ollama_options")
        if not isinstance(raw, Mapping):
            return {}
        allowed = self._marked_keys(
            config, "ollama_explicit_options"
        ) | self._marked_keys(config, "ollama_transient_options")
        return {
            str(key): value
            for key, value in raw.items()
            if str(key) in allowed and value is not None
        }

    def keep_alive(self, config: Mapping[str, Any]) -> str | None:
        value = config.get("keep_alive")
        if value is None or str(value).strip() == "":
            return None
        if config.get("keep_alive_explicit"):
            return str(value)
        # Before provenance markers existed, any non-default persisted value
        # could only have come from an operator edit.  Preserve that intent.
        if str(value) != self.legacy_keep_alive_default:
            return str(value)
        return None

    def options(
        self,
        config: Mapping[str, Any],
        *,
        output_limit: int | None = None,
    ) -> dict[str, Any]:
        options = self.explicit_options(config)
        raw_num_ctx = config.get("num_ctx", "auto")
        if not (
            isinstance(raw_num_ctx, str)
            and raw_num_ctx.strip().lower() in {"", "auto", "dynamic"}
        ):
            num_ctx = self.ports.positive_int(raw_num_ctx)
            if num_ctx:
                options["num_ctx"] = num_ctx

        output_is_explicit = bool(config.get("output_tokens_explicit")) or (
            "num_predict"
            in self._marked_keys(config, "ollama_transient_options")
        )
        if output_is_explicit:
            configured = self.ports.positive_int(
                options.get("num_predict")
                or config.get("max_output_tokens")
            )
            limit = self.ports.positive_int(output_limit)
            if configured and limit:
                options["num_predict"] = min(configured, limit)
            elif configured:
                options["num_predict"] = configured
            elif limit:
                options["num_predict"] = limit
        else:
            options.pop("num_predict", None)
        return options

    def apply(
        self,
        request: dict[str, Any],
        provider: str,
        model: str | None,
        config: dict[str, Any],
        source_request: Mapping[str, Any] | None = None,
        *,
        output_limit: int | None = None,
    ) -> dict[str, Any]:
        projected = dict(request)
        think = self.ports.think_value(
            provider, model, config, source_request or {}
        )
        if think is not None:
            projected["think"] = think
        keep_alive = self.keep_alive(config)
        if keep_alive is not None:
            projected["keep_alive"] = keep_alive
        options = self.options(config, output_limit=output_limit)
        if options:
            projected["options"] = options
        return projected


@dataclass(frozen=True, slots=True)
class OllamaWireCompatibilityApi:
    """Stable facade adapter backed by typed projection dependencies."""

    think_value: Callable[
        [str, str | None, dict[str, Any], Mapping[str, Any]], bool | str | None
    ]
    positive_int: Callable[[Any], int | None]

    def apply(
        self,
        request: dict[str, Any],
        provider: str,
        model: str | None,
        config: dict[str, Any],
        source_request: Mapping[str, Any] | None = None,
        *,
        output_limit: int | None = None,
    ) -> dict[str, Any]:
        return OllamaWireProjection(
            OllamaWireProjectionPorts(self.think_value, self.positive_int)
        ).apply(
            request,
            provider,
            model,
            config,
            source_request,
            output_limit=output_limit,
        )


__all__ = [
    "OllamaWireCompatibilityApi",
    "OllamaWireProjection",
    "OllamaWireProjectionPorts",
]
