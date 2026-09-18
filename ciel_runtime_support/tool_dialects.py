"""Runtime-specific tool-name dialects and their registry."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

from .architecture import ToolDialect
from .registry import AdapterRegistry


def mcp_server_normalized_key(name: str) -> tuple[str, str] | None:
    match = re.fullmatch(r"mcp__(.+)__(.+)", str(name or ""))
    if not match:
        return None
    server, tool = match.groups()
    return re.sub(r"[-_]", "", server).lower(), tool.lower()


def match_available_tool_name(name: str, available: set[str]) -> str | None:
    if not available:
        return None
    if name in available:
        return name
    lowered = str(name or "").lower()
    case_matches = [candidate for candidate in available if candidate.lower() == lowered]
    if len(case_matches) == 1:
        return case_matches[0]
    if not name.startswith("mcp__"):
        normalized = re.sub(r"[^a-z0-9]+", "", lowered)
        matches = [
            candidate
            for candidate in available
            if not candidate.startswith("mcp__")
            and re.sub(r"[^a-z0-9]+", "", candidate.lower()) == normalized
        ]
        if len(matches) == 1:
            return matches[0]
    requested_mcp = mcp_server_normalized_key(name)
    if requested_mcp is not None:
        matches = [candidate for candidate in available if mcp_server_normalized_key(candidate) == requested_mcp]
        if len(matches) == 1:
            return matches[0]
    substring_matches = [
        candidate for candidate in available if lowered and (lowered in candidate.lower() or candidate.lower() in lowered)
    ]
    return sorted(substring_matches)[0] if substring_matches else None


# The opencode adapter declares lower-case bash/read for the zen gateway's
# free-tier check. When a client names its equivalent differently (Codex:
# exec/shell), a call to the declared name belongs to that tool
# (probed 2026-09-18).
GATE_TOOL_CLIENT_EQUIVALENTS = {
    "bash": ("exec", "shell", "execute", "run_command"),
    "read": ("read_file", "view_file", "view", "Read"),
}


def match_gate_tool_equivalent(raw_name: str, available: set[str]) -> str | None:
    """Return the client tool a gate alias stands in for, if the client has one."""

    equivalents = GATE_TOOL_CLIENT_EQUIVALENTS.get(str(raw_name or "").lower(), ())
    for candidate in equivalents:
        if candidate in available:
            return candidate
    for candidate in equivalents:
        for name in available:
            if name.rsplit("__", 1)[-1] == candidate:
                return name
    return None


class ClaudeToolDialect(ToolDialect):
    name = "claude"

    def __init__(
        self,
        *,
        available_tools: set[str] | None = None,
        repair: Callable[[str, dict[str, Any]], Mapping[str, Any]] | None = None,
        blocked: set[str] | None = None,
    ) -> None:
        self._available_tools = available_tools or set()
        self._repair = repair
        self._blocked = frozenset(blocked or ())

    def normalize_tool_name(self, name: str) -> str:
        return match_available_tool_name(name, self._available_tools) or str(name)

    def repair_tool_input(self, tool_name: str, value: Mapping[str, Any]) -> Mapping[str, Any]:
        payload = dict(value)
        return self._repair(tool_name, payload) if self._repair else payload

    def blocked_tools(self) -> frozenset[str]:
        return self._blocked


TOOL_DIALECTS: AdapterRegistry[ToolDialect] = AdapterRegistry()
TOOL_DIALECTS.register("claude", lambda **kwargs: ClaudeToolDialect(**kwargs), aliases=("claude-code",))


__all__ = [
    "TOOL_DIALECTS",
    "ClaudeToolDialect",
    "match_available_tool_name",
    "mcp_server_normalized_key",
]
