"""Codex MCP discovery, capability filtering, and proxy configuration."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class CodexMcpConfigPorts:
    discover: Callable[..., dict[str, dict[str, Any]]]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class CodexMcpArtifactPorts:
    config_path: Callable[[], Path]
    save_json: Callable[[Path, dict[str, Any], str], None]
    unlink: Callable[[Path], None]
    load_json: Callable[[Path], Any]


@dataclass(frozen=True, slots=True)
class CodexMcpCapabilityPorts:
    ensure_probe_cache: Callable[..., Any]
    read_servers: Callable[[Path, Path], list[dict[str, Any]]]
    cached_probe_servers: Callable[[], list[dict[str, Any]]]
    path_key: Callable[[Path], str]
    cwd: Callable[[], Path]


@dataclass(frozen=True, slots=True)
class CodexMcpProjectionPorts:
    dedupe_strings: Callable[[Iterable[str]], list[str]]
    public_name: Callable[[str], str]
    is_streamable_http: Callable[[dict[str, Any]], bool]
    split_proxy_url: Callable[[str], str]
    toml_string: Callable[[str], str]


@dataclass(frozen=True, slots=True)
class CodexMcpIntegrationService:
    config: CodexMcpConfigPorts
    artifact: CodexMcpArtifactPorts
    capability: CodexMcpCapabilityPorts
    projection: CodexMcpProjectionPorts
    native_channel_names: frozenset[str]

    def discovered_servers(
        self,
        passthrough: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> dict[str, dict[str, Any]]:
        return self.config.discover(passthrough, env, cwd, log=self.config.log)

    def write_discovery_config(
        self,
        passthrough: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> Path | None:
        servers = self.discovered_servers(passthrough or [], env=env, cwd=cwd)
        path = self.artifact.config_path()
        if not servers:
            try:
                self.artifact.unlink(path)
            except FileNotFoundError:
                pass
            except Exception as exc:
                self.config.log(
                    "WARN",
                    "codex_mcp_config_remove_failed "
                    f"error={type(exc).__name__}: {exc}",
                )
            return None
        self.artifact.save_json(path, {"mcpServers": servers}, "codex_mcp_config")
        self.config.log(
            "INFO", f"codex_mcp_config_written servers={','.join(sorted(servers))}"
        )
        return path

    @staticmethod
    def config_bare_key(name: str) -> str | None:
        text = str(name or "").strip()
        return text if re.fullmatch(r"[A-Za-z0-9_-]+", text) else None

    def channel_capable_server_names(
        self, cfg: dict[str, Any], config_path: Path | None
    ) -> list[str]:
        if not config_path or not config_path.exists() or not config_path.is_file():
            return []
        self.capability.ensure_probe_cache(
            cfg, [], extra_config_paths=[config_path]
        )
        candidate_names = [
            str(server.get("channel") or "").strip()
            for server in self.capability.read_servers(
                config_path, self.capability.cwd()
            )
            if str(server.get("channel") or "").strip()
        ]
        source_key = self.capability.path_key(config_path)
        capable = {
            str(record.get("name") or "").strip()
            for record in self.capability.cached_probe_servers()
            if record.get("capable")
            and str(record.get("name") or "").strip()
            and self.capability.path_key(
                Path(str(record.get("source_path") or ""))
            )
            == source_key
        }
        return self.projection.dedupe_strings(
            name
            for name in candidate_names
            if name in capable and name.casefold() not in self.native_channel_names
        )

    def streamable_http_servers(
        self, config_path: Path | None
    ) -> dict[str, dict[str, Any]]:
        if not config_path or not config_path.exists() or not config_path.is_file():
            return {}
        try:
            data = self.artifact.load_json(config_path)
        except Exception as exc:
            self.config.log(
                "WARN",
                "codex_mcp_compat_source_read_failed "
                f"error={type(exc).__name__}: {exc}",
            )
            return {}
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        if not isinstance(servers, dict):
            return {}
        return {
            name: raw_server
            for raw_name, raw_server in servers.items()
            if (name := str(raw_name).strip())
            and isinstance(raw_server, dict)
            and self.projection.is_streamable_http(raw_server)
        }

    def native_http_compat_args(
        self,
        config_path: Path | None,
        *,
        split_http_proxy: bool = False,
        channel_owned_server_names: Iterable[str] | None = None,
    ) -> list[str]:
        servers = self.streamable_http_servers(config_path)
        if not servers:
            return []
        args: list[str] = []
        active: list[str] = []
        channel_owned = {
            self.projection.public_name(str(name or "").strip())
            for name in channel_owned_server_names or []
            if str(name or "").strip()
        }
        for name in sorted(servers):
            key = self.config_bare_key(name)
            if not key:
                self.config.log(
                    "WARN", f"codex_mcp_compat_skipped_unsafe_name server={name}"
                )
                continue
            if split_http_proxy or self.projection.public_name(name) in channel_owned:
                url = self.projection.toml_string(
                    self.projection.split_proxy_url(name)
                )
                args.extend(["-c", f"mcp_servers.{key}.url={url}"])
            active.append(name)
        if active:
            self.config.log(
                "INFO",
                "codex_mcp_native_http_compat servers=%s split_http_proxy=%s"
                % (",".join(active), str(bool(split_http_proxy)).lower()),
            )
        return args
