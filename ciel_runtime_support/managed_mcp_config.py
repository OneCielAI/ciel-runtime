"""Managed MCP configuration generation for web, Z.AI, and channel servers."""
from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import os
from pathlib import Path
import sys
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ManagedMcpConfigPaths:
    web_tools: Path
    duckduckgo_compat: Path
    zai: Path
    channel: Path


@dataclass(frozen=True, slots=True)
class ManagedMcpConfigPolicy:
    router_base: str
    zai_servers: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class ManagedMcpConfigPorts:
    find_executable: Callable[[str], str | None]
    save_json: Callable[[Path, dict[str, Any], str], None]
    primary_api_key: Callable[[str, dict[str, Any]], str]
    meaningful_key: Callable[[str], bool]
    initialize_channel_cursor: Callable[[], Any]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class ManagedMcpConfigService:
    paths: ManagedMcpConfigPaths
    policy: ManagedMcpConfigPolicy
    ports: ManagedMcpConfigPorts

    def write_web_tools(self, config: dict[str, Any]) -> Path:
        web = config.get("web_search", {})
        package = web.get("package") or "ddg-mcp-search"
        npx = self.ports.find_executable("npx") or ("npx.cmd" if os.name == "nt" else "npx")
        servers: dict[str, Any] = {"duckduckgo": {"command": npx, "args": ["-y", package]}}
        if web.get("fetch_enabled", True):
            command, arguments = self._fetch_command(web)
            if command:
                servers["web_fetch"] = {
                    "command": command,
                    "args": arguments,
                    "ciel_runtime_stdio": "jsonl",
                }
            else:
                self.ports.log("WARN", "web_fetch_disabled_missing_runner install=uvx_or_uv")
        self.ports.save_json(self.paths.web_tools, {"mcpServers": servers}, "web_tools_mcp_config")
        return self.paths.web_tools

    def write_duckduckgo_compat(self, config: dict[str, Any]) -> Path:
        path = self.write_web_tools(config)
        try:
            self.paths.duckduckgo_compat.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            self.ports.log(
                "WARN",
                f"duckduckgo_mcp_compat_config_write_failed error={type(exc).__name__}: {exc}",
            )
        return path

    def write_zai(self, provider: str, provider_config: dict[str, Any]) -> Path | None:
        if provider != "zai" or not bool(provider_config.get("managed_mcp", True)):
            return None
        key = self.ports.primary_api_key(provider, provider_config)
        if not self.ports.meaningful_key(key):
            self.ports.log("WARN", "zai_mcp_config_skipped_missing_api_key")
            return None
        npx = self.ports.find_executable("npx") or ("npx.cmd" if os.name == "nt" else "npx")
        servers: dict[str, Any] = {
            "zai-mcp-server": {
                "type": "stdio",
                "command": npx,
                "args": ["-y", "@z_ai/mcp-server@latest"],
                "env": {"Z_AI_API_KEY": key, "Z_AI_MODE": "ZAI"},
            }
        }
        for name, url in self.policy.zai_servers:
            servers[name] = {
                "type": "http",
                "url": url,
                "headers": {"Authorization": f"Bearer {key}"},
            }
        self.ports.save_json(self.paths.zai, {"mcpServers": servers}, "zai_mcp_config")
        self.ports.log("INFO", f"zai_mcp_config_written servers={','.join(sorted(servers))}")
        return self.paths.zai

    def reset_zai_if_inactive(self, provider: str) -> None:
        if provider == "zai":
            return
        try:
            self.paths.zai.unlink()
            self.ports.log("INFO", "zai_mcp_config_removed inactive_provider")
        except FileNotFoundError:
            return
        except OSError as exc:
            self.ports.log("WARN", f"zai_mcp_config_remove_failed error={type(exc).__name__}: {exc}")

    def write_channel(self) -> Path:
        data = {
            "mcpServers": {
                "ciel-runtime-router": {
                    "type": "sse",
                    "url": f"{self.policy.router_base}/ca/mcp/sse",
                }
            }
        }
        self.ports.save_json(self.paths.channel, data, "channel_mcp_config")
        self.ports.initialize_channel_cursor()
        return self.paths.channel

    def _fetch_command(self, web: dict[str, Any]) -> tuple[str | None, list[Any]]:
        arguments: list[Any] = [web.get("fetch_package") or "mcp-server-fetch"]
        if web.get("fetch_user_agent"):
            arguments.extend(["--user-agent", str(web["fetch_user_agent"])])
        if web.get("fetch_ignore_robots_txt", False):
            arguments.append("--ignore-robots-txt")
        command = self.ports.find_executable("uvx")
        if command:
            return command, arguments
        uv = self.ports.find_executable("uv")
        if uv:
            return uv, ["tool", "run", *arguments]
        if importlib.util.find_spec("uv") is not None:
            return sys.executable, ["-m", "uv", "tool", "run", *arguments]
        pipx = self.ports.find_executable("pipx")
        return (pipx, ["run", *arguments]) if pipx else (None, arguments)


__all__ = [
    "ManagedMcpConfigPaths",
    "ManagedMcpConfigPolicy",
    "ManagedMcpConfigPorts",
    "ManagedMcpConfigService",
]
