"""Prefer a sandbox's own profile over the system user profile.

A sandbox (for example an AAP Windows sandbox under
``C:\\ProgramData\\aap-win\\sandboxes\\u19\\home\\robert-ai``) can keep its own
Codex home (``.codex``) and Ciel configuration (``.config/ciel-runtime``)
inside the sandbox home while the Windows account's profile stays elsewhere.
Without explicit ``CODEX_HOME``/``CIEL_RUNTIME_CONFIG_DIR`` both Ciel and Codex
fall back to the account profile and find none of the sandbox's sessions
(robert, 2026-10-03: ``npm install -g`` replaced the wrapper that set them).

The sandbox home is the launch directory or its nearest parent below the
user profile that holds a Codex state database or a Ciel configuration file.
Explicit environment values always win; the result is written into the
environment so every child (router, Codex) resolves the same directories.
"""

from __future__ import annotations

from collections.abc import MutableMapping
import os
from pathlib import Path

CODEX_STATE_GLOB = "state_*.sqlite"


def _codex_home(directory: Path) -> Path | None:
    home = directory / ".codex"
    try:
        return home if any(home.glob(CODEX_STATE_GLOB)) else None
    except OSError:
        return None


def _ciel_config_dir(directory: Path) -> Path | None:
    config = directory / ".config" / "ciel-runtime"
    return config if (config / "config.json").is_file() else None


def _same_path(left: Path, right: Path) -> bool:
    try:
        return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))
    except OSError:
        return os.path.normcase(str(left)) == os.path.normcase(str(right))


def find_sandbox_home(start: Path, user_home: Path) -> Path | None:
    """Return the nearest directory from ``start`` up that holds a profile.

    The walk stops at the user profile, whose ``.codex`` is the ordinary
    default, and at the filesystem root.
    """

    boundaries = (user_home, Path.home())
    try:
        current = start.resolve()
    except OSError:
        current = start
    for directory in (current, *current.parents):
        if any(_same_path(directory, boundary) for boundary in boundaries):
            return None
        if _codex_home(directory) or _ciel_config_dir(directory):
            return directory
    return None


def apply_sandbox_profile(
    environ: MutableMapping[str, str],
    *,
    cwd: Path,
    user_home: Path,
) -> dict[str, str]:
    """Fill unset ``CODEX_HOME``/``CIEL_RUNTIME_CONFIG_DIR`` from a sandbox home."""

    if str(environ.get("CIEL_RUNTIME_SANDBOX_PROFILE") or "").strip().lower() in {"0", "false", "no", "off"}:
        return {}
    if environ.get("CODEX_HOME") and environ.get("CIEL_RUNTIME_CONFIG_DIR"):
        return {}
    start = Path(environ.get("CIEL_RUNTIME_LAUNCH_CWD") or cwd)
    home = find_sandbox_home(start, user_home)
    if home is None:
        return {}
    applied: dict[str, str] = {}
    codex_home = _codex_home(home)
    if codex_home is not None and not environ.get("CODEX_HOME"):
        applied["CODEX_HOME"] = str(codex_home)
    config_dir = _ciel_config_dir(home)
    if config_dir is not None and not environ.get("CIEL_RUNTIME_CONFIG_DIR"):
        applied["CIEL_RUNTIME_CONFIG_DIR"] = str(config_dir)
    environ.update(applied)
    return applied
