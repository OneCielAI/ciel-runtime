"""Windows command-line materialization for native and batch executables."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence


def command_line_for_create_process(argv: Sequence[str]) -> str:
    """Build a CreateProcess command line without double-escaping batch arguments.

    ``subprocess.list2cmdline`` implements the Microsoft C runtime quoting rules.
    Applying it once to the batch command and then a second time to the complete
    ``cmd /c`` argv turns embedded TOML quotes into literal backslashes.  npm
    launchers such as ``codex.cmd`` then pass those fragments to Codex as stray
    positional arguments.  ``cmd /s /c`` instead needs one outer command-string
    boundary around the already materialized batch invocation.
    """

    values = [str(value) for value in argv]
    if not values:
        raise ValueError("command is empty")
    if values[0].lower().endswith((".cmd", ".bat")):
        comspec = os.environ.get("COMSPEC") or "cmd.exe"
        shell = subprocess.list2cmdline([comspec])
        inner = subprocess.list2cmdline(values)
        return f'{shell} /d /s /c "{inner}"'
    return subprocess.list2cmdline(values)


__all__ = ["command_line_for_create_process"]
