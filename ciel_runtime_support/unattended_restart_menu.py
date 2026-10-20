"""Menu panel "Unattended restart": the reboot-script command for this folder.

The script runs `ciel-runtime --ca-resume-last` in the work folder
(session_resume_last).  Writing it is all the panel does; how the machine runs
it after a reboot (Task Scheduler, a Startup shortcut, a service wrapper) is
the operator's choice.
"""

from __future__ import annotations

import os
from pathlib import Path

from ciel_runtime_support.session_resume_last import RESUME_LAST_FLAG


def script_path(state_dir: Path) -> Path:
    return Path(state_dir) / ("resume-last.cmd" if os.name == "nt" else "resume-last.sh")


def command_line(cwd: Path) -> str:
    if os.name == "nt":
        return f'cd /d "{cwd}" && ciel-runtime {RESUME_LAST_FLAG}'
    return f"cd '{cwd}' && ciel-runtime {RESUME_LAST_FLAG}"


def script_text(cwd: Path) -> str:
    if os.name == "nt":
        return f'@echo off\r\ncd /d "{cwd}"\r\ncall ciel-runtime {RESUME_LAST_FLAG}\r\n'
    return f"#!/bin/sh\ncd '{cwd}' || exit 1\nexec ciel-runtime {RESUME_LAST_FLAG}\n"


def summary(state_dir: Path) -> str:
    path = script_path(state_dir)
    return f"script {path.name}" if path.is_file() else "no script"


def panel_rows(state_dir: Path, cwd: Path) -> tuple[list[str], list[str]]:
    path = script_path(state_dir)
    rows = [
        f"Command: {command_line(cwd)}",
        "  Last runtime and settings of this folder, no menu, last conversation (a new one if none),",
        "  startup questions answered, then '계속-세션재시작됨' is sent to the session.",
        f"Write restart script  [{path if path.is_file() else 'not written'}]",
        "Back",
    ]
    return rows, ["__info__", "__info__", "__info__", "write", "back"]


def apply(state_dir: Path, cwd: Path, value: str) -> list[str]:
    if value != "write":
        return []
    path = script_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(script_text(cwd).encode("utf-8"))
    if os.name != "nt":
        path.chmod(0o755)
    return [f"Restart script written: {path}", f"Runs: {command_line(cwd)}"]


__all__ = ["apply", "command_line", "panel_rows", "script_path", "script_text", "summary"]
