"""`--ca-resume-last`: start the last session of this folder without anyone at the keyboard.

Meant for a script that runs after the machine rebooted: the runtime this
folder used last (launch-state.json), the provider and model in its config,
no prelaunch menu and no update checks, the most recent conversation continued
(a new one when there is none), the CLI's startup dialogs answered
(startup_prompt_answers) and "계속-세션재시작됨" sent once the session is up.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ciel_runtime_support.runtime_session_restart import runtime_session_control_present
from ciel_runtime_support.session_backup_collect import claude_project_key
from ciel_runtime_support.startup_prompt_answers import STARTUP_AUTO_ANSWER_ENV

RESUME_LAST_ENV = "CIEL_RUNTIME_RESUME_LAST"
RESUME_LAST_FLAG = "--ca-resume-last"


@dataclass(frozen=True, slots=True)
class ResumeLastRequest:
    """Shaped like a restart request for queue_restart_notice."""

    source: str = "resume-last"
    reason: str = "unattended start"
    resume: bool = True


def resume_last_requested(environ: Mapping[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    return str(source.get(RESUME_LAST_ENV) or "").strip() == "1"


def enable_resume_last(environ: dict[str, str] | None = None) -> None:
    """Mark this launcher process (and the children it starts) as an unattended start."""

    target = os.environ if environ is None else environ
    target[RESUME_LAST_ENV] = "1"
    target[STARTUP_AUTO_ANSWER_ENV] = "1"


def claude_has_conversation(cwd: Path, environ: Mapping[str, str], home: Path) -> bool:
    """Whether Claude Code has a saved conversation for this folder.

    `claude --continue` without one prints "No conversation found to continue"
    and exits (Claude Code 2.1.296), so a reboot script would end there.
    """

    config = Path(environ.get("CLAUDE_CONFIG_DIR") or home / ".claude")
    folder = config / "projects" / claude_project_key(str(cwd))
    try:
        return any(path.is_file() for path in folder.glob("*.jsonl"))
    except OSError:
        return False


def resume_last_passthrough(
    passthrough: list[str],
    runtime: str,
    *,
    cwd: Path,
    environ: Mapping[str, str],
    home: Path,
) -> list[str]:
    """The launch arguments with the conversation-continue argument added.

    Codex and codex-remote map ``--continue`` to ``resume --last`` and fall
    back to a new conversation when the folder has none (resume_last_requested).
    """

    mode = str(runtime or "").strip().lower()
    arguments = list(passthrough)
    if runtime_session_control_present(["cli", *arguments], "codex" if mode.startswith("codex") else mode):
        return arguments
    if mode == "claude" and not claude_has_conversation(cwd, environ, home):
        return arguments
    if mode in ("claude", "codex", "codex-remote", "codex-app-server"):
        return ["--continue", *arguments]
    return arguments


__all__ = [
    "RESUME_LAST_ENV",
    "RESUME_LAST_FLAG",
    "ResumeLastRequest",
    "claude_has_conversation",
    "enable_resume_last",
    "resume_last_passthrough",
    "resume_last_requested",
]
