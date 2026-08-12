"""Stable durable state for one workspace, independent of router ports."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


WORKSPACE_FILES = (
    "chat-messages.jsonl",
    "runtime-inputs.jsonl",
    "channel-llm-cursor.json",
    "channel-llm-clear-floor.json",
    "channel-llm-launch-guard.json",
    "channel-stdin-wake-claims.json",
    "external-event-secrets.vault.json",
    "external-event-secrets.vault.key",
    "external-event-sse-cursors.json",
)
WORKSPACE_DIRECTORIES = ("chat-files", "plan-artifacts")


def legacy_workspace_directories(config_dir: Path, workspace_digest: str) -> list[Path]:
    root = config_dir / "router-instances"
    try:
        candidates = [
            path
            for path in root.iterdir()
            if path.is_dir() and path.name.endswith(f"-{workspace_digest}")
        ]
    except OSError:
        return []
    return sorted(candidates, key=_activity_score, reverse=True)


def migrate_workspace_state(
    config_dir: Path,
    workspace_digest: str,
    target_dir: Path,
) -> str:
    """Copy the newest legacy workspace state once and return its instance id."""

    marker = target_dir / ".migrated-from-router-instance"
    if marker.is_file():
        return ""
    candidates = legacy_workspace_directories(config_dir, workspace_digest)
    if not candidates:
        return ""
    source = candidates[0]
    target_dir.mkdir(parents=True, exist_ok=True)
    for name in WORKSPACE_FILES:
        source_path = source / name
        if source_path.is_file() and not (target_dir / name).exists():
            _atomic_copy(source_path, target_dir / name)
    for name in WORKSPACE_DIRECTORIES:
        source_path = source / name
        target_path = target_dir / name
        if source_path.is_dir() and not target_path.exists():
            shutil.copytree(source_path, target_path)
    marker.write_text(source.name + "\n", encoding="utf-8")
    return source.name


def _activity_score(path: Path) -> tuple[float, int, str]:
    values: list[float] = []
    durable_count = 0
    for name in WORKSPACE_FILES:
        try:
            values.append((path / name).stat().st_mtime)
            durable_count += 1
        except OSError:
            pass
    try:
        values.append(path.stat().st_mtime)
    except OSError:
        pass
    return max(values, default=0.0), durable_count, path.name


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    shutil.copy2(source, temporary)
    os.replace(temporary, target)


__all__ = [
    "WORKSPACE_DIRECTORIES",
    "WORKSPACE_FILES",
    "legacy_workspace_directories",
    "migrate_workspace_state",
]
