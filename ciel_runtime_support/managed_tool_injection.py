"""Launch-mode policy for Ciel-owned tools, never user MCP definitions."""
from collections.abc import Mapping
import json
import logging
from pathlib import Path
import subprocess
from subprocess import run as run_codex_config_probe
from typing import Any


def should_inject_tool(*, native: bool, mode: str = "always") -> bool:
    if mode not in {"always", "native", "non_native"}:
        raise ValueError(f"Invalid managed tool injection mode: {mode}")
    return mode == "always" or (mode == "native") == native


def codex_native_web_tool_overrides(
    *, native: bool, passthrough: list[str] | None = None,
    env: dict[str, str] | None = None, cwd: Path | None = None,
    codex: str = "codex",
) -> list[str]:
    """Disable inherited replacement web MCPs for this launch, not on disk."""
    if not native:
        return []
    # An enabled-only table is NOT a valid Codex MCP transport, even when
    # disabled. Never manufacture such a table on a fresh installation.
    # Ask the same executable to resolve its effective configuration. Merely
    # scanning project TOML is unsafe: Codex can ignore untrusted projects,
    # which would turn our enabled override into another transport-less table.
    # `mcp list` reads configuration; it does not connect to MCP servers.
    arguments = passthrough or []
    configuration_args = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            break
        if argument in ("-c", "--config", "-p", "--profile", "-C", "--cd") and index + 1 < len(arguments):
            configuration_args.extend([argument, arguments[index + 1]])
            index += 1
        elif argument.startswith(("--config=", "--profile=", "--cd=")):
            configuration_args.append(argument)
        index += 1
    try:
        completed = run_codex_config_probe(
            [codex, *configuration_args, "mcp", "list", "--json"],
            env=env, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=5, check=False,
        )
        if completed.returncode != 0:
            raise ValueError("configuration probe failed")
        entries = json.loads(completed.stdout)
        if not isinstance(entries, list):
            raise ValueError("unexpected MCP list shape")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        # Never print probe output: MCP configuration can contain credentials.
        logging.getLogger(__name__).warning(
            "Could not inspect Codex MCP configuration; skipping native web MCP overrides"
        )
        return []
    names = {entry.get("name") for entry in entries if isinstance(entry, dict)
             and isinstance(entry.get("name"), str)}
    result = []
    for name in ("duckduckgo", "web_fetch"):
        if name in names:
            result.extend(["-c", f"mcp_servers.{name}.enabled=false"])
    return result


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
