"""Policies and controller for locally synthesized Claude Code tool uses."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class SyntheticTasklistPorts:
    provider_enabled: Callable[[str], bool]
    content_to_text: Callable[[Any], str]
    should_continue: Callable[..., bool]
    now_ms: Callable[[], int]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class SyntheticTasklistPolicy:
    ports: SyntheticTasklistPorts

    def append(
        self,
        message: dict[str, Any],
        model: str,
        source_body: dict[str, Any],
        reason: str,
        provider: str = "",
    ) -> dict[str, Any]:
        if not self.ports.provider_enabled(provider):
            return message
        content = message.get("content")
        if not isinstance(content, list):
            content = [{"type": "text", "text": self.ports.content_to_text(content)}] if content else []
        tool_calls = [block for block in content if isinstance(block, dict) and block.get("type") == "tool_use"]
        if not self.ports.should_continue(source_body, self.ports.content_to_text(content), tool_calls):
            return message
        projected = dict(message)
        projected["content"] = [
            *content,
            {
                "type": "tool_use",
                "id": f"toolu_ciel_runtime_TaskList_{reason}_{self.ports.now_ms()}",
                "name": "TaskList",
                "input": {},
            },
        ]
        projected["stop_reason"] = "tool_use"
        projected.setdefault("model", model or message.get("model") or "ciel-runtime-router")
        self.ports.log("WARN", f"auto-synthesized TaskList after clarification question ({reason})")
        return projected


@dataclass(frozen=True, slots=True)
class ForcedPlanModePorts:
    forced_choice_name: Callable[[dict[str, Any]], str]
    should_defer: Callable[..., bool]
    tool_names: Callable[[dict[str, Any]], set[str]]
    plan_mode_active: Callable[[dict[str, Any]], bool]
    synthetic_response: Callable[..., dict[str, Any]]
    write_json: Callable[..., Any]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class ForcedPlanModeController:
    ports: ForcedPlanModePorts

    def handle(
        self,
        handler: Any,
        provider: str,
        provider_config: dict[str, Any],
        body: dict[str, Any],
    ) -> bool:
        if provider == "anthropic":
            return False
        name = self.ports.forced_choice_name(body)
        if name != "EnterPlanMode":
            return False
        if self.ports.should_defer(provider, provider_config, body, name):
            self.ports.log(
                "INFO",
                f"deferred forced {name} tool_choice to native Anthropic-compatible upstream because thinking is enabled",
            )
            return False
        available = self.ports.tool_names(body)
        if available and name not in available:
            return False
        if self.ports.plan_mode_active(body):
            self.ports.log("WARN", f"ignored forced {name} tool_choice because plan mode is already active")
            return False
        self.ports.log("INFO", f"synthesized {name} tool_use for {provider} forced tool_choice")
        self.ports.write_json(
            handler,
            self.ports.synthetic_response(str(body.get("model") or ""), name, {}),
        )
        return True


__all__ = [
    "ForcedPlanModeController",
    "ForcedPlanModePorts",
    "SyntheticTasklistPolicy",
    "SyntheticTasklistPorts",
]
