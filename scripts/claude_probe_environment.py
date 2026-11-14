"""Environment policy for tracked probes that directly start Claude Code."""

from __future__ import annotations

import os
from collections.abc import Mapping

CLAUDE_BG_SHELL_PRESSURE_REAP_ENV = "CLAUDE_CODE_DISABLE_BG_SHELL_PRESSURE_REAP"


def claude_probe_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a detached environment with the required Claude startup-only flag."""

    environment = dict(os.environ if source is None else source)
    environment[CLAUDE_BG_SHELL_PRESSURE_REAP_ENV] = "1"
    return environment
