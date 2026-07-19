from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ciel_runtime_support.settings_repository import JsonSettingsRepository


@dataclass(frozen=True)
class StatusLineServices:
    repository: JsonSettingsRepository
    warn: Callable[[str], None]
    chmod: Callable[[Path, int], None] = os.chmod


def install_statusline_settings(
    script_path: Path,
    script: str,
    python_executable: str,
    services: StatusLineServices,
) -> None:
    try:
        script_path.parent.mkdir(parents=True, exist_ok=True)
        if not script_path.exists() or script_path.read_text(encoding="utf-8") != script:
            script_path.write_text(script, encoding="utf-8")
        try:
            services.chmod(script_path, 0o700)
        except Exception as exc:
            services.warn(
                f"could not restrict status line script permissions ({type(exc).__name__}: {exc})."
            )
        settings = services.repository.load("statusline")
        if settings is None:
            services.warn(
                f"could not read {services.repository.path}; status line was not installed."
            )
            return
        command = f"{shlex.quote(python_executable)} {shlex.quote(str(script_path))}"
        current = settings.get("statusLine")
        if isinstance(current, dict) and current.get("command") == command:
            return
        settings["statusLine"] = {
            "type": "command",
            "command": command,
            "padding": 0,
            "refreshInterval": 1000,
        }
        services.repository.save(settings, "statusline")
    except Exception as exc:
        services.warn(f"could not install status line ({type(exc).__name__}: {exc}).")
