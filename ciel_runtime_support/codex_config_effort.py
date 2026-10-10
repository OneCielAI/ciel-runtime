"""Fill an unset top-level reasoning effort in a Codex config.toml before launch.

With no ``model_reasoning_effort`` Codex falls back to the model catalog's
default, which is ``low`` for gpt-6.1-sol.  Ciel writes the default into the
file it launches Codex with, once, so the TUI, the app-server and the desktop
app all start from the same explicit value; an existing value is never changed.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ciel_runtime_support.codex_config import (
    REASONING_EFFORT_KEY,
    codex_config_sets_reasoning_effort,
    toml_string,
)

DEFAULT_REASONING_EFFORT = "medium"
_BOM = "﻿"


def with_default_reasoning_effort(
    text: str, effort: str = DEFAULT_REASONING_EFFORT
) -> str | None:
    """The config with the effort added, or None when the top level already has one.

    The line goes first: a TOML top-level key is valid only before the first
    table, and the start of the file is always before it.
    """

    if codex_config_sets_reasoning_effort(text, []):
        return None
    bom = _BOM if text.startswith(_BOM) else ""
    newline = "\r\n" if "\r\n" in text else "\n"
    return f"{bom}{REASONING_EFFORT_KEY} = {toml_string(effort)}{newline}{text[len(bom):]}"


def ensure_default_reasoning_effort(
    path: Path, effort: str = DEFAULT_REASONING_EFFORT
) -> bool:
    """Write the default into ``path`` when its top level has no effort; True if written."""

    path = Path(path)
    text = ""
    if path.exists():
        # newline="" keeps CRLF files CRLF.
        with path.open(encoding="utf-8", newline="") as stream:
            text = stream.read()
    updated = with_default_reasoning_effort(text, effort)
    if updated is None:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".config.toml.", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(updated)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return True


def codex_home_config_path(environ: Mapping[str, str], asset_home: Path) -> Path:
    """CODEX_HOME, else ``<asset_home>/.codex`` -- the home Ciel installs Codex prompts into."""

    configured = str(environ.get("CODEX_HOME") or "").strip()
    home = Path(configured).expanduser() if configured else Path(asset_home) / ".codex"
    return home / "config.toml"


def ensure_logged(path: Path, log: Callable[[str, str], Any]) -> bool:
    """Fill the default; a failure is logged and the launch goes on."""

    try:
        written = ensure_default_reasoning_effort(path)
    except (OSError, UnicodeError) as error:
        log("WARN", f"codex_config_effort_default_failed path={path} error={type(error).__name__}: {error}")
        return False
    if written:
        log("INFO", f"codex_config_effort_default_written path={path} effort={DEFAULT_REASONING_EFFORT}")
    return written


@dataclass(frozen=True, slots=True)
class CodexLaunchModelSettings:
    """Model-settings args for a Codex launch, after the launch config has an effort.

    Every Codex launch (TUI, app-server, remote TUI, desktop) builds its
    command through these args right before starting Codex, so this is the
    one point where the config file is prepared first.
    """

    model_catalog_args: Callable[..., list[str]]
    environ: Callable[[], Mapping[str, str]]
    asset_home: Path
    log: Callable[[str, str], Any]

    def __call__(self, codex: str, cfg: dict[str, Any], passthrough: list[str] | None = None) -> list[str]:
        ensure_logged(codex_home_config_path(self.environ(), self.asset_home), self.log)
        return self.model_catalog_args(codex, cfg, passthrough)
