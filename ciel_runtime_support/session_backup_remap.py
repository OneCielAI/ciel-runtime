"""Path remapping when a snapshot is restored to another folder, user or machine.

What follows the work folder: Claude's ``projects/<cwd key>`` folder name and the
``projects`` entry in ``~/.claude.json``; Codex's ``threads`` rows (``cwd``,
``rollout_path``) and the ``[projects."<cwd>"]`` trust table in config.toml; Ciel's
``launch-state.json`` entry.  Conversation records themselves are not rewritten:
they keep the paths they were written with, as history.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from ciel_runtime_support.session_backup_collect import claude_project_key, plain_path, same_path

_same = same_path


def _in_style_of(original: str, path: str) -> str:
    """``path`` written the way ``original`` was (Codex keeps the extended-length prefix)."""

    if original.startswith("\\\\?\\") and not path.startswith("\\\\"):
        return "\\\\?\\" + path
    return path


def remapped_entry_path(entry: Mapping[str, Any], old_cwd: str, new_cwd: str) -> str:
    """The snapshot path of an entry at its new place (Claude project folder renamed)."""

    path = str(entry["path"])
    if entry["root"] != "claude" or _same(old_cwd, new_cwd):
        return path
    old_key, new_key = claude_project_key(old_cwd), claude_project_key(new_cwd)
    prefix = f"projects/{old_key}/"
    return f"projects/{new_key}/{path[len(prefix):]}" if path.startswith(prefix) else path


def _replace_prefix(value: str, old: str, new: str) -> str:
    if not value:
        return value
    if os.path.normcase(value).startswith(os.path.normcase(old.rstrip("\\/"))):
        rest = value[len(old.rstrip("\\/")):]
        return new.rstrip("\\/") + rest
    return value


def remap_codex_threads(database: Path, old_cwd: str, new_cwd: str, old_codex: str, new_codex: str) -> int:
    if not database.is_file():
        return 0
    connection = sqlite3.connect(database)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(threads)")}
        if not {"id", "cwd"} <= columns:
            return 0
        changed = 0
        fields = ["id", "cwd"] + (["rollout_path"] if "rollout_path" in columns else [])
        for row in connection.execute(f"SELECT {', '.join(fields)} FROM threads").fetchall():
            values = dict(zip(fields, row))
            updates: dict[str, str] = {}
            if _same(values["cwd"] or "", old_cwd) and not _same(old_cwd, new_cwd):
                updates["cwd"] = _in_style_of(str(values["cwd"]), new_cwd)
            if values.get("rollout_path"):
                moved = _in_style_of(values["rollout_path"], _replace_prefix(plain_path(values["rollout_path"]), old_codex, new_codex))
                if moved != values["rollout_path"]:
                    updates["rollout_path"] = moved
            if updates:
                assignments = ", ".join(f"{name} = ?" for name in updates)
                connection.execute(f"UPDATE threads SET {assignments} WHERE id = ?", (*updates.values(), values["id"]))
                changed += 1
        connection.commit()
        return changed
    finally:
        connection.close()


def _toml_key(path: str) -> str:
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def remap_codex_config(config: Path, old_cwd: str, new_cwd: str) -> bool:
    """Carry the old folder's ``[projects."<cwd>"]`` table (trust level) over to the new folder."""

    if not config.is_file() or _same(old_cwd, new_cwd):
        return False
    with config.open(encoding="utf-8", newline="") as stream:
        text = stream.read()
    old_header = re.compile(r"^\[projects\.(\"(?:[^\"\\]|\\.)*\"|'[^']*')\]", re.MULTILINE)
    changed = False

    def replace(match: re.Match[str]) -> str:
        nonlocal changed
        raw = match.group(1)
        key = raw[1:-1].replace("\\\\", "\\").replace('\\"', '"') if raw.startswith('"') else raw[1:-1]
        if not _same(key, old_cwd):
            return match.group(0)
        changed = True
        return f"[projects.{_toml_key(new_cwd)}]"

    updated = old_header.sub(replace, text)
    if changed:
        with config.open("w", encoding="utf-8", newline="") as stream:
            stream.write(updated)
    return changed


def remap_json_key(path: Path, container: tuple[str, ...], old_cwd: str, new_cwd: str, *, forward_slashes: bool = False) -> bool:
    """Move the entry keyed by the old cwd inside ``container`` to the new cwd."""

    if not path.is_file() or _same(old_cwd, new_cwd):
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError:
        return False
    node = data
    for name in container:
        node = node.get(name) if isinstance(node, dict) else None
    if not isinstance(node, dict):
        return False
    new_key = new_cwd.replace("\\", "/") if forward_slashes else new_cwd
    moved = False
    for key in list(node):
        if _same(key, old_cwd):
            value = node.pop(key)
            if isinstance(value, dict) and "cwd" in value:
                value["cwd"] = new_cwd
            node[new_key] = value
            moved = True
    if moved:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return moved


def remap_after_restore(manifest: Mapping[str, Any], dest: Mapping[str, Path]) -> dict[str, Any]:
    roots = manifest.get("roots") or {}
    old_cwd, new_cwd = str(roots.get("cwd") or ""), str(dest["cwd"])
    old_codex, new_codex = str(roots.get("codex") or ""), str(dest["codex"])
    report: dict[str, Any] = {}
    codex = Path(new_codex)
    report["codex_threads"] = sum(
        remap_codex_threads(database, old_cwd, new_cwd, old_codex, new_codex) for database in sorted(codex.glob("state_*.sqlite"))
    )
    report["codex_trust"] = remap_codex_config(codex / "config.toml", old_cwd, new_cwd)
    launch_state = Path(dest["ciel"]) / "launch-state.json"
    report["launch_state"] = remap_json_key(launch_state, ("by_cwd",), old_cwd, new_cwd)
    if report["launch_state"]:
        data = json.loads(launch_state.read_text(encoding="utf-8"))
        if isinstance(data.get("last"), dict) and _same(data["last"].get("cwd") or "", old_cwd):
            data["last"]["cwd"] = new_cwd
            launch_state.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    report["claude_json"] = remap_json_key(Path(dest["home"]) / ".claude.json", ("projects",), old_cwd, new_cwd, forward_slashes=True)
    return report


__all__ = [
    "remap_after_restore",
    "remap_codex_config",
    "remap_codex_threads",
    "remap_json_key",
    "remapped_entry_path",
]
