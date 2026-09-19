"""Attach the Ciel Runtime router MCP server to a Muse Code session.

Claude and Codex pick up the router's MCP server from launch flags
(``--mcp-config``, ``-c mcp_servers…``). Muse Code has no such flag: it reads
``mcpServers`` from ``settings.json`` (``~/.config/muse/settings.json``), so a
launch has to merge one entry into that shared file instead - and on Windows the
file lives inside WSL, where Muse actually runs.

The entry is what makes a Muse session able to restart itself
(``restart_session``), read channel inputs (``submit_input``) and report
telemetry, exactly as a routed Claude/Codex launch does. A launch that has no
router behind it removes the entry again, so Muse never advertises a dead
server.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

MUSE_ROUTER_SERVER_NAME = "ciel-runtime-router"
MUSE_SETTINGS_RELATIVE = ".config/muse/settings.json"
MUSE_MCP_MODE = "optional"
MUSE_MCP_TYPE = "streamable-http"
# Launcher-side WSL calls must not hang on a wedged mount: `wsl.exe` translates
# an inherited workspace cwd before running anything, and that translation can
# stall indefinitely (live 2026-09-19: F: drvfs wedged, `wsl -e` from
# F:\aap.ezonebot never returned). Run from the user's home and under a
# deadline; a timeout surfaces as a failed sync, which the store already logs
# without breaking the launch.
WSL_SETTINGS_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True, slots=True)
class MuseRouterMcpDecision:
    attach: bool
    reason: str


def router_mcp_entry(
    base_url: str,
    token: str,
    *,
    name: str = MUSE_ROUTER_SERVER_NAME,
) -> dict[str, Any]:
    """The Muse ``mcpServers`` entry that points at the router's MCP endpoint."""

    return {
        "type": MUSE_MCP_TYPE,
        "url": f"{str(base_url).rstrip('/')}/ca/mcp",
        "headers": {"Authorization": f"Bearer {token}"},
        "mode": MUSE_MCP_MODE,
    }


def router_mcp_decision(
    *,
    enabled: bool,
    manage_router: bool,
    base_url: str,
    token: str,
    wsl: bool,
    loopback: bool,
) -> MuseRouterMcpDecision:
    """Whether this launch can attach the router MCP, and why not when it cannot."""

    if not enabled:
        return MuseRouterMcpDecision(False, "disabled by muse.router_mcp")
    if not manage_router or not base_url:
        return MuseRouterMcpDecision(False, "no router is managed for this launch")
    if wsl and loopback:
        return MuseRouterMcpDecision(
            False,
            "WSL cannot reach the Windows loopback; relaunch with "
            "`ciel-runtime muse --ca-web-address <windows-wsl-ip>`",
        )
    if not token:
        return MuseRouterMcpDecision(
            False,
            "the router is bound outside loopback but external access is off; "
            "enable `router_debug_external_access` for this workspace",
        )
    return MuseRouterMcpDecision(True, "attached")


def sync_settings_text(
    text: str | None,
    entry: Mapping[str, Any] | None,
    *,
    name: str = MUSE_ROUTER_SERVER_NAME,
) -> tuple[str, str]:
    """Merge or remove the router entry; return (new text, action)."""

    normalized = str(text or "").strip()
    settings: dict[str, Any] = {}
    if normalized:
        try:
            parsed = json.loads(normalized)
        except ValueError:
            return (str(text or ""), "unreadable")
        if not isinstance(parsed, dict):
            return (str(text or ""), "unreadable")
        settings = parsed
    servers = settings.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    if entry is None:
        if name not in servers:
            return (str(text or ""), "absent" if not normalized else "unchanged")
        remaining = {key: value for key, value in servers.items() if key != name}
        if remaining:
            settings["mcpServers"] = remaining
        else:
            settings.pop("mcpServers", None)
        return (json.dumps(settings, ensure_ascii=False, indent=2) + "\n", "removed")
    current = servers.get(name)
    if current == entry:
        return (str(text or ""), "unchanged")
    servers = {**servers, name: dict(entry)}
    settings["mcpServers"] = servers
    return (json.dumps(settings, ensure_ascii=False, indent=2) + "\n", "updated")


@dataclass(frozen=True, slots=True)
class MuseSettingsStore:
    """Read and write Muse's settings file through caller-provided IO."""

    read: Callable[[], str | None]
    write: Callable[[str], None]
    log: Callable[[str, str], Any] = lambda _level, _message: None

    def sync(
        self,
        entry: Mapping[str, Any] | None,
        *,
        name: str = MUSE_ROUTER_SERVER_NAME,
    ) -> str:
        try:
            current = self.read()
        except Exception as exc:  # noqa: BLE001 - settings IO must never break a launch
            self.log("WARN", f"muse_mcp_settings_read_failed error={type(exc).__name__}")
            return "failed"
        updated, action = sync_settings_text(current, entry, name=name)
        if action in {"updated", "removed"}:
            try:
                self.write(updated)
            except Exception as exc:  # noqa: BLE001
                self.log(
                    "WARN", f"muse_mcp_settings_write_failed error={type(exc).__name__}"
                )
                return "failed"
        self.log("INFO", f"muse_router_mcp_{action} server={name}")
        return action


def native_settings_store(
    log: Callable[[str, str], Any] = lambda _level, _message: None,
    *,
    home: Path | None = None,
) -> MuseSettingsStore:
    """Settings IO for a Muse binary running on this machine."""

    path = (home or Path.home()) / MUSE_SETTINGS_RELATIVE

    def read() -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def write(text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)

    return MuseSettingsStore(read=read, write=write, log=log)


def wsl_settings_store(
    run: Callable[..., Any],
    wsl_command: str = "wsl",
    log: Callable[[str, str], Any] = lambda _level, _message: None,
) -> MuseSettingsStore:
    """Settings IO for a Muse binary living inside WSL.

    The launcher runs on Windows while the file (and the Muse process) live in
    the distribution, so both directions go through ``wsl.exe``.
    """

    settings_path = f"$HOME/{MUSE_SETTINGS_RELATIVE}"

    def read() -> str | None:
        result = run(
            [wsl_command, "-e", "sh", "-lc", f"cat {settings_path} 2>/dev/null || true"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(Path.home()),
            timeout=WSL_SETTINGS_TIMEOUT_SECONDS,
        )
        return str(getattr(result, "stdout", "") or "") or None

    def write(text: str) -> None:
        run(
            [
                wsl_command,
                "-e",
                "sh",
                "-lc",
                f"mkdir -p \"$HOME/{os.path.dirname(MUSE_SETTINGS_RELATIVE)}\" && "
                f"cat > {settings_path}",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            input=text,
            cwd=str(Path.home()),
            timeout=WSL_SETTINGS_TIMEOUT_SECONDS,
        )

    return MuseSettingsStore(read=read, write=write, log=log)


def settings_store_for(
    *,
    wsl: bool,
    run: Callable[..., Any] | None = None,
    wsl_command: str = "wsl",
    log: Callable[[str, str], Any] = lambda _level, _message: None,
    home: Path | None = None,
) -> MuseSettingsStore:
    if wsl and run is not None:
        return wsl_settings_store(run, wsl_command, log)
    return native_settings_store(log, home=home)


__all__ = [
    "MUSE_MCP_MODE",
    "MUSE_MCP_TYPE",
    "MUSE_ROUTER_SERVER_NAME",
    "MUSE_SETTINGS_RELATIVE",
    "MuseRouterMcpDecision",
    "MuseSettingsStore",
    "native_settings_store",
    "router_mcp_decision",
    "router_mcp_entry",
    "settings_store_for",
    "sync_settings_text",
    "wsl_settings_store",
]
