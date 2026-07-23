"""Conservative restoration of missing native Codex MCP configuration."""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .mcp_inventory import McpInventoryService


_SCALAR_KEYS = (
    "command",
    "cwd",
    "url",
    "bearer_token_env_var",
    "enabled",
    "experimental_environment",
    "oauth_resource",
    "required",
    "startup_timeout_ms",
    "startup_timeout_sec",
    "tool_timeout_sec",
)
_LIST_KEYS = (
    "args",
    "env_vars",
    "enabled_tools",
    "disabled_tools",
    "scopes",
)
_TABLE_KEYS = ("env", "http_headers", "env_http_headers")


@dataclass(frozen=True, slots=True)
class CodexMcpRestorePorts:
    config_paths: Callable[..., list[Path]]
    discover_managed: Callable[[Path], dict[str, dict[str, Any]]]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class CodexMcpRestoreService:
    ports: CodexMcpRestorePorts

    def restore(
        self,
        passthrough: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> list[str]:
        working_directory = (cwd or Path.cwd()).resolve()
        paths = self.ports.config_paths(
            passthrough or [], env=env, cwd=working_directory
        )
        if not paths:
            return []
        config_path = paths[0]
        reserved: set[str] = set()
        for path in paths[1:]:
            if not path.is_file():
                continue
            try:
                reserved.update(
                    self._existing_names(path.read_text(encoding="utf-8"))
                )
            except (OSError, UnicodeError, ValueError, TypeError) as exc:
                self.ports.log(
                    "WARN",
                    "codex_mcp_restore_config_read_failed "
                    f"path={path} error={type(exc).__name__}: {exc}",
                )
                return []
        managed = self.ports.discover_managed(working_directory)
        return self._restore_missing(config_path, managed, reserved=reserved)

    def _restore_missing(
        self,
        config_path: Path,
        managed: dict[str, dict[str, Any]],
        *,
        reserved: set[str] | None = None,
    ) -> list[str]:
        try:
            before = self._signature(config_path)
            original = config_path.read_text(encoding="utf-8") if before else ""
            existing = self._existing_names(original)
        except (OSError, UnicodeError, ValueError, TypeError, RuntimeError) as exc:
            self.ports.log(
                "WARN",
                "codex_mcp_restore_config_read_failed "
                f"path={config_path} error={type(exc).__name__}: {exc}",
            )
            return []

        projected: dict[str, dict[str, Any]] = {}
        for raw_name, raw_server in managed.items():
            name = str(raw_name or "").strip()
            if not name or not isinstance(raw_server, dict):
                continue
            server = self._project_server(raw_server)
            if server is None:
                self.ports.log(
                    "WARN", f"codex_mcp_restore_skipped_unsupported server={name}"
                )
                continue
            projected[name] = server
        merged = McpInventoryService.merge(
            {name: {} for name in existing}, projected, reserved=reserved
        )
        additions = [(name, projected[name]) for name in merged.added]

        if not additions:
            return []

        suffix = "" if not original or original.endswith("\n") else "\n"
        blocks = "\n".join(
            self._server_toml(name, server) for name, server in additions
        )
        restored = original + suffix + ("\n" if original else "") + blocks
        if not restored.endswith("\n"):
            restored += "\n"

        try:
            self._validate(restored)
            self._atomic_replace(config_path, restored, before)
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            self.ports.log(
                "WARN",
                "codex_mcp_restore_write_failed "
                f"path={config_path} error={type(exc).__name__}: {exc}",
            )
            return []

        names = [name for name, _server in additions]
        self.ports.log(
            "INFO",
            "codex_mcp_config_restored "
            f"path={config_path} servers={','.join(sorted(names))}",
        )
        return names

    @staticmethod
    def _signature(path: Path) -> tuple[int, int, int] | None:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        return stat.st_mtime_ns, stat.st_size, getattr(stat, "st_ino", 0)

    @staticmethod
    def _validate(text: str) -> dict[str, Any]:
        import tomllib

        data = tomllib.loads(text)
        if not isinstance(data, dict):
            raise ValueError("Codex config must be a TOML table")
        return data

    @classmethod
    def _existing_names(cls, text: str) -> set[str]:
        if not text.strip():
            return set()
        data = cls._validate(text)
        servers = data.get("mcp_servers")
        if not isinstance(servers, dict):
            return set()
        return {str(name).strip() for name in servers if str(name).strip()}

    @classmethod
    def _project_server(cls, server: dict[str, Any]) -> dict[str, Any] | None:
        projected: dict[str, Any] = {}
        command = str(server.get("command") or "").strip()
        url = str(server.get("url") or server.get("endpoint") or "").strip()
        raw_args = server.get("args")
        arguments = [str(item) for item in raw_args] if isinstance(raw_args, list) else []
        if "mcp-proxy" in arguments and "--server-config" in arguments:
            return None
        transport = str(server.get("type") or server.get("transport") or "").strip().casefold()
        if command:
            projected["command"] = command
        elif url.startswith(("http://", "https://")) and transport != "sse":
            projected["url"] = url
        else:
            return None

        aliases = {
            "token_env_var": "bearer_token_env_var",
            "headers": "http_headers",
        }
        normalized = dict(server)
        for source, target in aliases.items():
            if target not in normalized and source in normalized:
                normalized[target] = normalized[source]
        for key in _SCALAR_KEYS:
            if key in {"command", "url"} or normalized.get(key) is None:
                continue
            value = normalized[key]
            if isinstance(value, (str, bool, int)) or (
                isinstance(value, float) and math.isfinite(value)
            ):
                projected[key] = value
        for key in _LIST_KEYS:
            value = normalized.get(key)
            if isinstance(value, list) and all(
                cls._supported_list_item(item) for item in value
            ):
                projected[key] = list(value)
        for key in _TABLE_KEYS:
            value = normalized.get(key)
            if isinstance(value, dict):
                projected[key] = {
                    str(item_key): str(item_value)
                    for item_key, item_value in value.items()
                    if str(item_key).strip()
                }
        return projected

    @staticmethod
    def _supported_list_item(value: Any) -> bool:
        if isinstance(value, (str, bool, int)):
            return True
        if isinstance(value, float):
            return math.isfinite(value)
        if isinstance(value, dict):
            return all(
                str(key).strip()
                and CodexMcpRestoreService._supported_list_item(item)
                and not isinstance(item, (list, dict))
                for key, item in value.items()
            )
        return False

    @staticmethod
    def _toml_value(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return repr(value)
        if isinstance(value, dict):
            entries = (
                f"{json.dumps(str(key), ensure_ascii=False)} = {CodexMcpRestoreService._toml_value(item)}"
                for key, item in value.items()
            )
            return "{ " + ", ".join(entries) + " }"
        if isinstance(value, list):
            return "[" + ", ".join(CodexMcpRestoreService._toml_value(item) for item in value) + "]"
        return json.dumps(str(value), ensure_ascii=False)

    @classmethod
    def _server_toml(cls, name: str, server: dict[str, Any]) -> str:
        table_name = json.dumps(name, ensure_ascii=False)
        lines = [
            "# Restored by Ciel Runtime from managed MCP state; existing Codex entries take precedence.",
            f"[mcp_servers.{table_name}]",
        ]
        for key in _SCALAR_KEYS:
            if key in server:
                lines.append(f"{key} = {cls._toml_value(server[key])}")
        for key in _LIST_KEYS:
            if key in server:
                lines.append(f"{key} = {cls._toml_value(server[key])}")
        for key in _TABLE_KEYS:
            values = server.get(key)
            if not isinstance(values, dict) or not values:
                continue
            lines.extend(("", f"[mcp_servers.{table_name}.{key}]"))
            for item_key, item_value in sorted(values.items()):
                encoded_key = json.dumps(str(item_key), ensure_ascii=False)
                lines.append(f"{encoded_key} = {cls._toml_value(item_value)}")
        return "\n".join(lines) + "\n"

    @classmethod
    def _atomic_replace(
        cls,
        path: Path,
        text: str,
        expected: tuple[int, int, int] | None,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if cls._signature(path) != expected:
            raise RuntimeError("Codex config changed while MCP restoration was running")
        temporary = path.with_name(
            f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
        )
        try:
            temporary.write_text(text, encoding="utf-8")
            os.chmod(temporary, 0o600)
            if cls._signature(path) != expected:
                raise RuntimeError("Codex config changed while MCP restoration was running")
            temporary.replace(path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


__all__ = ["CodexMcpRestorePorts", "CodexMcpRestoreService"]
