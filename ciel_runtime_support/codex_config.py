"""Codex configuration discovery and TOML projection policies."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import time
from pathlib import Path
from typing import Any, Callable, Iterable

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 remains a supported launcher runtime.
    tomllib = None  # type: ignore[assignment]


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
    configured_home = str(env.get("CODEX_HOME") or "").strip()
    default_home = Path.home() / ".codex"
    home = Path(configured_home or default_home).expanduser()
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
        # An explicit CODEX_HOME is an isolation boundary.  When cwd happens
        # to live below the OS home directory, parent discovery must not pull
        # the default ~/.codex/config.toml back into that isolated config.
        if configured_home and path.resolve() == (default_home / "config.toml").resolve():
            continue
        if path not in paths:
            paths.append(path)
    return paths


_MCP_PARENT_TABLE = re.compile(
    r'^\s*\[mcp_servers\.(?P<name>"(?:[^"\\]|\\.)+"|[A-Za-z0-9_-]+)\]\s*(?:#.*)?$'
)
_MCP_HEADER_TABLE = re.compile(
    r'^\s*\[mcp_servers\.(?P<name>"(?:[^"\\]|\\.)+"|[A-Za-z0-9_-]+)\.http_headers\]\s*(?:#.*)?$'
)


def _toml_table_blocks(lines: list[str], pattern: re.Pattern[str]) -> list[tuple[int, int, str, dict[str, Any]]]:
    blocks: list[tuple[int, int, str, dict[str, Any]]] = []
    if tomllib is None:
        return blocks
    for start, line in enumerate(lines):
        if pattern.fullmatch(line.rstrip("\r\n")) is None:
            continue
        end = next((index for index in range(start + 1, len(lines)) if lines[index].lstrip().startswith("[")), len(lines))
        try:
            parsed = tomllib.loads("".join(lines[start:end]))
            servers = parsed.get("mcp_servers", {})
            name, server = next(iter(servers.items()))
        except (ValueError, TypeError, AttributeError, StopIteration):
            continue
        if isinstance(server, dict):
            blocks.append((start, end, str(name), server))
    return blocks


def repair_codex_mcp_header_collisions(
    paths: Iterable[Path],
    *,
    report: Callable[[str], None] | None = None,
) -> list[Path]:
    """Repair only identical legacy-table/managed-inline MCP header collisions."""

    repaired: list[Path] = []
    if tomllib is None:
        return repaired
    for path in paths:
        if not path.is_file() or path.is_symlink():
            continue
        try:
            text = path.read_text(encoding="utf-8")
            tomllib.loads(text)
            continue
        except (OSError, UnicodeError):
            continue
        except tomllib.TOMLDecodeError:
            pass
        lines = text.splitlines(keepends=True)
        parents = {
            name: server.get("http_headers")
            for _start, _end, name, server in _toml_table_blocks(lines, _MCP_PARENT_TABLE)
            if isinstance(server.get("http_headers"), dict)
        }
        removals: list[tuple[int, int, str]] = []
        for start, end, name, server in _toml_table_blocks(lines, _MCP_HEADER_TABLE):
            old_headers = server.get("http_headers")
            if isinstance(old_headers, dict) and old_headers == parents.get(name):
                removals.append((start, end, name))
        if not removals:
            continue
        removed_lines = {index for start, end, _name in removals for index in range(start, end)}
        candidate = "".join(line for index, line in enumerate(lines) if index not in removed_lines)
        try:
            tomllib.loads(candidate)
        except tomllib.TOMLDecodeError:
            continue
        backup = path.with_name(f"{path.name}.ciel-mcp-repair-{time.time_ns()}.bak")
        temporary = path.with_name(f".{path.name}.ciel-mcp-repair-{os.getpid()}.tmp")
        try:
            shutil.copy2(path, backup)
            temporary.write_text(candidate, encoding="utf-8")
            os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
            os.replace(temporary, path)
        except OSError:
            temporary.unlink(missing_ok=True)
            continue
        repaired.append(path)
        if report is not None:
            names = ", ".join(sorted({name for _start, _end, name in removals}))
            report(f"Repaired duplicate Codex MCP headers in {path} ({names}); backup: {backup}")
    return repaired
