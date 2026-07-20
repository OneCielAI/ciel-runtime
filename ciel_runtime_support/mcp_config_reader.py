from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterable, TypeVar


T = TypeVar("T")


class ClaudeMcpConfigPathPolicy:
    @staticmethod
    def passthrough_values(passthrough: list[str]) -> list[str]:
        values: list[str] = []
        index = 0
        while index < len(passthrough):
            argument = passthrough[index]
            if argument == "--mcp-config":
                index += 1
                while (
                    index < len(passthrough)
                    and not passthrough[index].startswith("-")
                ):
                    values.append(passthrough[index])
                    index += 1
                continue
            if argument.startswith("--mcp-config="):
                value = argument.split("=", 1)[1].strip()
                if value:
                    values.append(value)
            index += 1
        return values

    @classmethod
    def strip_passthrough(cls, passthrough: list[str]) -> list[str]:
        stripped: list[str] = []
        index = 0
        while index < len(passthrough):
            argument = passthrough[index]
            if argument == "--mcp-config":
                index += 1
                while (
                    index < len(passthrough)
                    and not passthrough[index].startswith("-")
                ):
                    index += 1
                continue
            if argument.startswith("--mcp-config="):
                index += 1
                continue
            stripped.append(argument)
            index += 1
        return stripped

    @classmethod
    def passthrough_paths(
        cls,
        passthrough: list[str],
    ) -> list[Path]:
        return [
            Path(value).expanduser()
            for value in cls.passthrough_values(passthrough)
        ]

    @classmethod
    def paths(
        cls,
        passthrough: list[str],
        cwd: Path,
        home: Path,
    ) -> list[Path]:
        paths = cls.passthrough_paths(passthrough)
        current = cwd
        visited: set[str] = set()
        while True:
            key = path_for_compare(current)
            if key in visited:
                break
            visited.add(key)
            paths.append(current / ".mcp.json")
            if current == current.parent:
                break
            current = current.parent
        paths.extend(
            (
                home / ".mcp.json",
                home / ".claude" / "settings.json",
                home / ".claude.json",
            )
        )
        out: list[Path] = []
        seen: set[str] = set()
        for path in paths:
            key = path_for_compare(path)
            if key in seen:
                continue
            seen.add(key)
            out.append(path)
        return out

    @classmethod
    def existing_paths(
        cls,
        passthrough: list[str],
        cwd: Path,
        home: Path,
    ) -> list[Path]:
        return [
            path
            for path in cls.paths(passthrough, cwd, home)
            if path.exists() and path.is_file()
        ]


def dedupe_strings(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def path_for_compare(path: Path | str) -> str:
    try:
        value = Path(path).expanduser().resolve()
    except (OSError, RuntimeError, TypeError, ValueError):
        value = path
    return str(value).replace("\\", "/").rstrip("/").casefold()


def project_key_matches_cwd(project_key: str, cwd: Path) -> bool:
    key = str(project_key or "").strip()
    if not key:
        return False
    try:
        project_path = Path(key).expanduser()
    except (OSError, RuntimeError, TypeError, ValueError):
        return False
    if not project_path.is_absolute():
        return False
    project = path_for_compare(project_path)
    current = path_for_compare(cwd)
    return current == project or current.startswith(project + "/")


def server_names_from_mapping(mapping: Any) -> list[str]:
    if not isinstance(mapping, dict):
        return []
    names: list[str] = []
    for key in ("mcpServers", "servers"):
        servers = mapping.get(key)
        if isinstance(servers, dict):
            names.extend(str(name).strip() for name in servers if str(name).strip())
    return dedupe_strings(names)


def servers_from_mapping(mapping: Any) -> list[tuple[str, dict[str, Any]]]:
    if not isinstance(mapping, dict):
        return []
    found: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for key in ("mcpServers", "servers"):
        servers = mapping.get(key)
        if not isinstance(servers, dict):
            continue
        for raw_name, raw_server in servers.items():
            name = str(raw_name or "").strip()
            if not name or name in seen or not isinstance(raw_server, dict):
                continue
            seen.add(name)
            found.append((name, dict(raw_server)))
    return found


def read_mcp_config_items(
    path: Path,
    cwd: Path,
    projector: Callable[[Any], list[T]],
    identity: Callable[[T], str],
    log: Callable[[str, str], None],
) -> list[T]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
        log(
            "WARN",
            f"mcp_config_read_failed path={path} error={type(exc).__name__}: {exc}",
        )
        return []
    if not isinstance(data, dict):
        log("WARN", f"mcp_config_read_failed path={path} error=invalid_payload")
        return []
    items = projector(data)
    if path.name == ".claude.json":
        projects = data.get("projects")
        if isinstance(projects, dict):
            for project_key, project_data in projects.items():
                if project_key_matches_cwd(str(project_key), cwd):
                    items.extend(projector(project_data))
    out: list[T] = []
    seen: set[str] = set()
    for item in items:
        key = identity(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
