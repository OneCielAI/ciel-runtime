"""Presentation projection for provider and LLM option status."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


def format_context_tokens(value: int | None) -> str:
    if not value:
        return "unknown"
    if value >= 1024 * 1024 and value % (1024 * 1024) == 0:
        return f"{value // (1024 * 1024)}M"
    if value >= 1024 and value % 1024 == 0:
        return f"{value // 1024}K"
    return f"{value:,}"


def format_parameter_count(
    value: Any, positive_int: Callable[[Any], int | None]
) -> str:
    fixed = positive_int(value)
    if not fixed:
        return ""
    for suffix, scale in (
        ("T", 1_000_000_000_000),
        ("B", 1_000_000_000),
        ("M", 1_000_000),
    ):
        if fixed >= scale:
            scaled = fixed / scale
            text = f"{scaled:.1f}".rstrip("0").rstrip(".")
            return f"{text}{suffix}"
    return str(fixed)


@dataclass(frozen=True, slots=True)
class ProviderContextStatusPorts:
    capacity: Callable[[str, dict[str, Any]], int | None]
    context_policy: Callable[..., Any]
    ollama_num_ctx_status: Callable[[dict[str, Any]], str]
    positive_int: Callable[[Any], int | None]


@dataclass(frozen=True, slots=True)
class ProviderContextStatusProjection:
    ports: ProviderContextStatusPorts

    def status(self, provider: str, config: dict[str, Any]) -> str:
        capacity = self.ports.capacity(provider, config)
        cap_text = format_context_tokens(capacity)
        strategy = self.ports.context_policy(provider, config).settings_strategy
        if strategy == "ollama":
            return f"model max {cap_text}; {self.ports.ollama_num_ctx_status(config)}"
        if strategy == "standard":
            window = self.ports.positive_int(config.get("context_window"))
            reserve = self.ports.positive_int(config.get("context_reserve_tokens"))
            reserve_text = (
                f"; reserve {format_context_tokens(reserve)}" if reserve else ""
            )
            return (
                f"model max {cap_text}; window {format_context_tokens(window)}"
                f"{reserve_text}"
            )
        if strategy == "managed":
            return "managed by Claude Code"
        return f"model max {cap_text}"


@dataclass(frozen=True, slots=True)
class ProviderOptionStatusPorts:
    configured_adapter: Callable[..., Any]
    contract_config: Callable[..., Any]
    rate_usage: Callable[..., tuple[int, int | None]]
    ollama_num_ctx: Callable[[dict[str, Any]], str]
    ollama_options_status: Callable[[dict[str, Any]], str]
    ip_family: Callable[..., str]
    parse_bool: Callable[..., bool]
    tool_choice_status: Callable[..., str]
    ollama_extra_options: Callable[[dict[str, Any]], dict[str, Any]]
    anthropic_routed: Callable[..., bool]


class ProviderOptionStatusProjection:
    def __init__(
        self,
        sampling_options: tuple[str, ...],
        ports: ProviderOptionStatusPorts,
    ) -> None:
        self.sampling_options = sampling_options
        self.ports = ports

    def sampling(self, config: dict[str, Any]) -> list[str]:
        return [
            f"{key}={config.get(key, 'default')}" for key in self.sampling_options
        ]

    def provider(self, provider: str, config: dict[str, Any]) -> str:
        adapter = self.ports.configured_adapter(provider, config)
        contract = self.ports.contract_config(provider, config)
        presentation = adapter.option_presentation_policy(contract)
        context_strategy = adapter.context_policy(contract).settings_strategy
        timeout = config.get("request_timeout_ms", "default")
        timeout_text = f"{timeout}ms" if timeout != "default" else "default"
        parts = [
            f"max_output_tokens={config.get('max_output_tokens', 'default')}",
            f"timeout={timeout_text}",
        ]
        if config.get("stream_idle_timeout_ms") is not None:
            parts.append(f"stream_idle_timeout={config['stream_idle_timeout_ms']}ms")
        if presentation.show_rate_limit:
            parts.extend(self._rate_limit(provider, config))
        if context_strategy == "ollama":
            parts.insert(0, f"num_ctx={self.ports.ollama_num_ctx(config)}")
            parts.append(
                f"ollama_options={self.ports.ollama_options_status(config)}"
            )
        if context_strategy == "standard":
            parts.insert(0, f"context_window={config.get('context_window', 'default')}")
            parts.insert(
                1, f"reserve={config.get('context_reserve_tokens', 'default')}"
            )
        if presentation.show_native:
            parts.append(f"native={bool(config.get('native_compat', True))}")
        if presentation.show_ip_family:
            overrides = config.get("model_endpoints")
            parts.append(f"ip_family={self.ports.ip_family(provider, config)}")
            parts.append(
                f"endpoint_overrides={len(overrides) if isinstance(overrides, dict) else 0}"
            )
        if presentation.show_route:
            routed = self.ports.parse_bool(
                config.get("route_through_router"), default=False
            )
            parts.append(f"routed={'on' if routed else 'off'}")
        elif presentation.show_tool_choice:
            parts.append(
                f"tool_choice={self.ports.tool_choice_status(provider, config)}"
            )
        query = str(config.get("force_query_string") or "").strip()
        if query:
            parts.append(f"query={query}")
        if presentation.show_sampling:
            parts.extend(self.sampling(config))
        if presentation.show_stream:
            parts.append(
                f"stream={'on' if bool(config.get('stream_enabled', True)) else 'off'}"
            )
            if bool(config.get("stream_word_chunking", False)):
                parts.append("word_chunk=on")
        return ", ".join(parts)

    def _rate_limit(self, provider: str, config: dict[str, Any]) -> list[str]:
        parts = [f"rate_limit_rpm={config.get('rate_limit_rpm', 0)}"]
        if bool(config.get("rate_limit_status", False)):
            used, limit = self.ports.rate_usage(provider, config)
            if limit is not None:
                suffix = f"{used}/{limit}" if limit > 0 else f"{used}/min(unmanaged)"
                parts.append(f"rpm_used={suffix}")
        return parts

    def llm(self, provider: str, config: dict[str, Any]) -> str:
        adapter = self.ports.configured_adapter(provider, config)
        contract = self.ports.contract_config(provider, config)
        presentation = adapter.option_presentation_policy(contract)
        if adapter.context_policy(contract).settings_strategy == "ollama":
            options = self.ports.ollama_extra_options(config)
            pieces = [
                f"ctx {self.ports.ollama_num_ctx(config)}",
                f"keep {config.get('keep_alive', 'default')}",
                f"think {bool(config.get('think', False))}",
                f"timeout {config.get('request_timeout_ms', 'default')}ms",
            ]
            if config.get("stream_idle_timeout_ms") is not None:
                pieces.append(
                    f"stream_idle_timeout={config['stream_idle_timeout_ms']}ms"
                )
            for key in ("num_predict", "temperature", "top_p", "top_k"):
                if key in options:
                    pieces.append(f"{key}={options[key]}")
            return "; ".join(pieces)
        owns_model = adapter.configuration_policy(contract).runtime_owns_model
        if presentation.show_route and not owns_model:
            return (
                f"max_output_tokens={config.get('max_output_tokens', 'Claude Code default')}, "
                f"timeout={config.get('request_timeout_ms', 'Claude Code default')}ms, "
                f"routed={'on' if self.ports.anthropic_routed(provider, config) else 'off'}"
            )
        if presentation.show_tool_choice or presentation.show_route:
            return self.provider(provider, config)
        return "provider defaults"
