from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterable, TypeVar


T = TypeVar("T")


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
