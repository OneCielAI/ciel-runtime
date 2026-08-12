"""Codex configuration discovery and TOML projection policies."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path


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
