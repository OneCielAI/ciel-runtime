"""Provider-owned tool exposure and tool-choice application policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .protocols.anthropic_thinking_policy import (
    ToolChoicePorts,
    normalize_tool_choice,
)


ProviderConfig = dict[str, Any]


@dataclass(frozen=True, slots=True)
class ProviderToolPolicy:
    adapter_for: Callable[..., Any]
    contract_for: Callable[..., Any]
    current_model: Callable[[str, ProviderConfig], str]
    strip_context_suffix: Callable[[str], str]
    resolve_emitted_name: Callable[
        [str, dict[str, Any] | None],
        str,
    ]
    default_blocked_tools: frozenset[str]
    repair_tools: frozenset[str]
    log: Callable[[str, str], None]

    def blocked_tools(
        self,
        provider: str,
        config: ProviderConfig,
    ) -> set[str]:
        override = config.get("blocked_tools")
        if override is False:
            return set()
        if isinstance(override, list):
            return {
                str(name).strip()
                for name in override
                if str(name).strip()
            }
        adapter, contract = self._adapter(provider, config)
        if adapter.capabilities(contract).blocks_default_tools:
            return set(self.default_blocked_tools)
        return set()

    def normalize_anthropic_stream_tool_use(
        self,
        provider: str,
        config: ProviderConfig,
    ) -> bool:
        adapter, contract = self._adapter(provider, config)
        return adapter.normalizes_anthropic_tool_use(contract)

    def supports_tool_choice(
        self,
        provider: str,
        config: ProviderConfig,
        body: dict[str, Any],
    ) -> bool:
        raw_model = str(
            body.get("model") or config.get("current_model") or ""
        )
        model = self.strip_context_suffix(raw_model).lower()
        adapter, contract = self._adapter(provider, config)
        return adapter.supports_tool_choice(contract, model)

    def tool_choice_status(
        self,
        provider: str,
        config: ProviderConfig,
    ) -> str:
        configured = config.get("supports_tool_choice")
        if configured is not None:
            return "on" if bool(configured) else "off"
        model = self.current_model(provider, config)
        enabled = self.supports_tool_choice(
            provider,
            config,
            {"model": model},
        )
        return f"auto ({'on' if enabled else 'off'})"

    def normalize_tool_choice(
        self,
        provider: str,
        config: ProviderConfig,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        return normalize_tool_choice(
            provider,
            config,
            body,
            ToolChoicePorts(
                normalize=self._normalize_choice,
                supports=self.supports_tool_choice,
                log=self.log,
            ),
        )

    def should_repair_passthrough_input(
        self,
        provider: str,
        config: ProviderConfig,
        raw_name: str,
        source_body: dict[str, Any] | None,
    ) -> bool:
        adapter, contract = self._adapter(provider, config)
        if not adapter.capabilities(contract).repairs_anthropic_tool_input:
            return False
        return self.resolve_emitted_name(
            raw_name,
            source_body,
        ) in self.repair_tools

    def _adapter(
        self,
        provider: str,
        config: ProviderConfig,
    ) -> tuple[Any, Any]:
        return (
            self.adapter_for(provider, config),
            self.contract_for(provider, config),
        )

    def _normalize_choice(
        self,
        provider: str,
        config: ProviderConfig,
        request: dict[str, Any],
        choice: Any,
    ) -> Any:
        adapter, contract = self._adapter(provider, config)
        model = str(
            request.get("model")
            or config.get("current_model")
            or ""
        )
        return adapter.normalize_tool_choice(contract, model, choice)


__all__ = ["ProviderToolPolicy"]
