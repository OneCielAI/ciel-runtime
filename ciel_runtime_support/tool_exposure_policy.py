"""Policy for removing tools that must not be exposed to an upstream provider."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ToolExposurePorts:
    blocked_tools: Callable[[str, dict[str, Any]], set[str]]
    ultracode_workflow_preferred: Callable[[dict[str, Any]], bool]
    plan_mode_active: Callable[[dict[str, Any]], bool]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class ToolExposurePolicy:
    ports: ToolExposurePorts

    def filter(self, provider: str, provider_config: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        blocked = set(self.ports.blocked_tools(provider, provider_config))
        dynamic_blocked: set[str] = set()
        if provider != "anthropic" and self.ports.ultracode_workflow_preferred(body) and not self.ports.plan_mode_active(body):
            dynamic_blocked.add("EnterPlanMode")
        blocked.update(dynamic_blocked)
        if not blocked:
            return body
        tools = body.get("tools")
        tool_choice = body.get("tool_choice") if isinstance(body.get("tool_choice"), dict) else None
        tool_choice_name = tool_choice.get("name") if tool_choice else None
        drop_choice = isinstance(tool_choice_name, str) and tool_choice_name in blocked
        if not isinstance(tools, list) or not tools:
            return self._without_blocked_choice(body, provider, tool_choice_name) if drop_choice else body
        kept: list[Any] = []
        dropped: list[str] = []
        for tool in tools:
            name = tool.get("name") if isinstance(tool, dict) else None
            if isinstance(name, str) and name in blocked:
                dropped.append(name)
            else:
                kept.append(tool)
        if not dropped:
            return self._without_blocked_choice(body, provider, tool_choice_name) if drop_choice else body
        reason = " ultracode_workflow_preferred=true" if dynamic_blocked.intersection(dropped) else ""
        self.ports.log("INFO", f"filtered upstream tools for {provider}: {', '.join(sorted(set(dropped)))}{reason}")
        filtered = dict(body)
        filtered["tools"] = kept
        if drop_choice:
            filtered.pop("tool_choice", None)
            self._log_removed_choice(provider, tool_choice_name)
        return filtered

    def _without_blocked_choice(self, body: dict[str, Any], provider: str, tool_choice_name: Any) -> dict[str, Any]:
        filtered = dict(body)
        filtered.pop("tool_choice", None)
        self._log_removed_choice(provider, tool_choice_name)
        return filtered

    def _log_removed_choice(self, provider: str, tool_choice_name: Any) -> None:
        self.ports.log("WARN", f"removed blocked tool_choice for {provider}: {tool_choice_name}")


__all__ = ["ToolExposurePolicy", "ToolExposurePorts"]
