"""Launch-mode policy for Ciel-owned tools, never user MCP definitions."""
from collections.abc import Mapping
from typing import Any


def should_inject_tool(*, native: bool, mode: str = "always") -> bool:
    if mode not in {"always", "native", "non_native"}:
        raise ValueError(f"Invalid managed tool injection mode: {mode}")
    return mode == "always" or (mode == "native") == native


def select_managed_tools(
    servers: Mapping[str, Any], *, native: bool,
) -> dict[str, Any]:
    selected = {}
    for name, definition in servers.items():
        if not isinstance(definition, dict):
            continue
        item = dict(definition)
        mode = item.pop("injection_mode", "always")
        if should_inject_tool(native=native, mode=mode):
            selected[name] = item
    return selected
