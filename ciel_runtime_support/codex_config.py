"""Codex configuration discovery and TOML projection policies."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any


def toml_string(value: str) -> str:
    return json.dumps(str(value))


def codex_config_override_keys(passthrough: list[str]) -> set[str]:
    keys: set[str] = set()
    index = 0
    while index < len(passthrough):
        argument = str(passthrough[index])
        value = ""
        if argument in ("-c", "--config") and index + 1 < len(passthrough):
            value = str(passthrough[index + 1])
            index += 2
        elif argument.startswith("--config="):
            value = argument.split("=", 1)[1]
            index += 1
        else:
            index += 1
            continue
        if "=" in value:
            keys.add(value.split("=", 1)[0].strip())
    return keys


def toml_scalar_without_comment(raw: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    out: list[str] = []
    for character in raw:
        if escaped:
            out.append(character)
            escaped = False
            continue
        if character == "\\" and in_double:
            out.append(character)
            escaped = True
            continue
        if character == '"' and not in_single:
            in_double = not in_double
        elif character == "'" and not in_double:
            in_single = not in_single
        elif character == "#" and not in_single and not in_double:
            break
        out.append(character)
    return "".join(out).strip()


def unquote_toml_string(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1].strip()
    return value


def codex_alternate_screen_value_from_config_text(text: str) -> str | None:
    table = ""
    for line in text.splitlines():
        stripped = toml_scalar_without_comment(line)
        if not stripped:
            continue
        table_match = re.fullmatch(r"\[([A-Za-z0-9_.-]+)\]", stripped)
        if table_match:
            table = table_match.group(1).strip()
            continue
        match = re.match(r"alternate_screen\s*=\s*(.+)$", stripped) if table == "tui" else None
        if match is None:
            match = re.match(r"tui\.alternate_screen\s*=\s*(.+)$", stripped)
        if match is None:
            continue
        value = unquote_toml_string(match.group(1)).casefold()
        if value in ("false", "0", "off", "no", "disabled", "disable"):
            return "never"
        if value in ("true", "1", "on", "yes", "enabled", "enable"):
            return "always"
        if value in ("auto", "always", "never"):
            return None
        return "auto"
    return None


def codex_config_paths_for_launch(
    passthrough: list[str],
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> list[Path]:
    env = env or os.environ
    home = Path(env.get("CODEX_HOME") or (Path.home() / ".codex")).expanduser()
    paths = [home / "config.toml"]
    profiles: list[str] = []
    index = 0
    while index < len(passthrough):
        argument = str(passthrough[index])
        if argument in ("-p", "--profile") and index + 1 < len(passthrough):
            profiles.append(str(passthrough[index + 1]))
            index += 2
            continue
        if argument.startswith("--profile="):
            profiles.append(argument.split("=", 1)[1])
        index += 1
    for profile in profiles:
        if re.fullmatch(r"[A-Za-z0-9_-]+", profile or ""):
            paths.append(home / f"{profile}.config.toml")
    current = (cwd or Path.cwd()).resolve()
    for parent in (current, *current.parents):
        path = parent / ".codex" / "config.toml"
        if path not in paths:
            paths.append(path)
    return paths


def normalize_codex_mcp_server(
    raw_name: Any, raw_server: Any
) -> tuple[str, dict[str, Any]] | None:
    name = str(raw_name or "").strip()
    if not name or not isinstance(raw_server, dict):
        return None
    url = str(raw_server.get("url") or raw_server.get("endpoint") or "").strip()
    if not url.startswith(("http://", "https://")):
        return None
    explicit_type = raw_server.get("type") is not None or raw_server.get("transport") is not None
    server_type = str(raw_server.get("type") or raw_server.get("transport") or "http").strip().lower()
    if server_type in {"streamable-http", "streamable_http"}:
        server_type = "http"
    if server_type not in {"http", "sse"}:
        server_type = "http"
    out: dict[str, Any] = {"type": server_type, "url": url}
    if explicit_type:
        out["_ciel_runtime_explicit_type"] = True
    for key in (
        "headers",
        "env_http_headers",
        "bearer_token_env_var",
        "token_env_var",
        "bearer_token",
        "token",
        "streamable_requires_session",
        "require_session",
        "mcp_session_required",
        "mcp_protocol_version",
        "protocolVersion",
        "protocol_version",
        "mcp_timeout_seconds",
        "timeout",
    ):
        value = raw_server.get(key)
        if value is not None:
            out[key] = value
    return name, out


def codex_mcp_servers_from_toml_data(data: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(data, dict) or not isinstance(data.get("mcp_servers"), dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for raw_name, raw_server in data["mcp_servers"].items():
        normalized = normalize_codex_mcp_server(raw_name, raw_server)
        if normalized is not None:
            name, server = normalized
            out[name] = server
    return out


def toml_table_parts(raw: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quote = ""
    escaped = False
    for character in raw:
        if escaped:
            current.append(character)
            escaped = False
            continue
        if character == "\\" and quote == '"':
            current.append(character)
            escaped = True
            continue
        if character in {"'", '"'}:
            quote = "" if quote == character else character if not quote else quote
            current.append(character)
            continue
        if character == "." and not quote:
            part = unquote_toml_string("".join(current).strip())
            if part:
                parts.append(part)
            current = []
            continue
        current.append(character)
    part = unquote_toml_string("".join(current).strip())
    if part:
        parts.append(part)
    return parts


def parse_simple_toml_value(raw: str) -> Any:
    value = toml_scalar_without_comment(raw).strip()
    if not value:
        return ""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return unquote_toml_string(value)
    low = value.lower()
    if low in {"true", "false"}:
        return low == "true"
    if value.startswith("[") and value.endswith("]"):
        items: list[str] = []
        current: list[str] = []
        quote = ""
        escaped = False
        for character in value[1:-1]:
            if escaped:
                current.append(character)
                escaped = False
                continue
            if character == "\\" and quote == '"':
                current.append(character)
                escaped = True
                continue
            if character in {"'", '"'}:
                quote = "" if quote == character else character if not quote else quote
                current.append(character)
                continue
            if character == "," and not quote:
                item = unquote_toml_string("".join(current).strip())
                if item:
                    items.append(item)
                current = []
                continue
            current.append(character)
        item = unquote_toml_string("".join(current).strip())
        if item:
            items.append(item)
        return items
    try:
        return int(value)
    except ValueError:
        return value


def fallback_codex_mcp_servers_from_config_text(text: str) -> dict[str, dict[str, Any]]:
    raw_servers: dict[str, dict[str, Any]] = {}
    current_name = ""
    current_subtable = ""
    for line in text.splitlines():
        stripped = toml_scalar_without_comment(line)
        if not stripped:
            continue
        table_match = re.fullmatch(r"\[(.+)\]", stripped)
        if table_match:
            parts = toml_table_parts(table_match.group(1).strip())
            current_name = ""
            current_subtable = ""
            if len(parts) >= 2 and parts[0] == "mcp_servers":
                current_name = parts[1]
                current_subtable = parts[2] if len(parts) >= 3 else ""
                raw_servers.setdefault(current_name, {})
            continue
        if not current_name or "=" not in stripped:
            continue
        key, raw_value = stripped.split("=", 1)
        value = parse_simple_toml_value(raw_value)
        server = raw_servers.setdefault(current_name, {})
        if current_subtable:
            nested = server.setdefault(current_subtable, {})
            if isinstance(nested, dict):
                nested[key.strip()] = value
        else:
            server[key.strip()] = value
    return codex_mcp_servers_from_toml_data({"mcp_servers": raw_servers})


def codex_mcp_servers_from_config_text(text: str) -> dict[str, dict[str, Any]]:
    try:
        import tomllib

        parsed = codex_mcp_servers_from_toml_data(tomllib.loads(text))
        if parsed:
            return parsed
    except (ImportError, TypeError, ValueError):
        pass
    return fallback_codex_mcp_servers_from_config_text(text)


def discover_codex_mcp_servers(
    passthrough: list[str] | None = None,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    *,
    log: Callable[[str, str], None],
) -> dict[str, dict[str, Any]]:
    servers: dict[str, dict[str, Any]] = {}
    for path in codex_config_paths_for_launch(passthrough or [], env=env, cwd=cwd):
        if not path.is_file():
            continue
        try:
            parsed = codex_mcp_servers_from_config_text(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as exc:
            log(
                "WARN",
                f"codex_mcp_config_read_failed path={path} "
                f"error={type(exc).__name__}: {exc}",
            )
            continue
        servers.update(parsed)
    return servers
