"""Resume requests for Codex sessions that run on an app-server Ciel Runtime owns.

``codex app-server`` takes no ``--continue``/``resume`` (codex 0.160.0: "error:
unexpected argument '--continue' found"), so the launcher takes them out of the
server arguments and the session opens the conversation itself: the channel
client resumes it before anything else, and a ``--remote`` TUI attaches with
``resume <id>``.  ``--continue``/``--resume`` map the way the Codex TUI launch
maps them (``resume --last`` / ``resume <id>``); ``last`` and the picker choose
among the current folder's conversations, like the plain codex launch.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from ciel_runtime_support.codex_cli import (
    CODEX_OPTIONS_WITH_VALUE,
    codex_passthrough_args_for_launch,
    codex_passthrough_first_non_option_index,
)

# Permissions are the server's; a session sets them over the protocol.
PERMISSION_FLAGS = frozenset({"--yolo", "--dangerously-bypass-approvals-and-sandbox", "--full-auto"})
PICKER_FLAGS = ("--all", "--include-non-interactive")


@dataclass(frozen=True, slots=True)
class AppServerResume:
    """The conversation a launch asked for: a new one by default."""

    mode: str = ""  # "", "last", "pick" or "id"
    session_id: str = ""
    picker_args: tuple[str, ...] = ()


def split_app_server_passthrough(passthrough: Iterable[str]) -> tuple[list[str], AppServerResume]:
    """Server arguments, and the resume request that belongs to the session."""

    args, _notes = codex_passthrough_args_for_launch([str(item) for item in passthrough])
    index = codex_passthrough_first_non_option_index(args)
    if index < 0 or args[index] != "resume":
        return [arg for arg in args if arg not in PERMISSION_FLAGS], AppServerResume()
    server = [arg for arg in args[:index] if arg not in PERMISSION_FLAGS]
    # `--continue -c k=v` maps to `resume --last -c k=v`: options after the
    # resume words still belong to the server.
    last = False
    picker: list[str] = []
    session_id = ""
    rest = args[index + 1 :]
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg == "--last":
            last = True
        elif arg in PICKER_FLAGS:
            picker.append(arg)
        elif arg in CODEX_OPTIONS_WITH_VALUE and i + 1 < len(rest):
            server.extend(rest[i : i + 2])
            i += 2
            continue
        elif arg.startswith("-"):
            if arg not in PERMISSION_FLAGS:
                server.append(arg)
        elif not session_id:
            session_id = arg
        i += 1
    if last:
        return server, AppServerResume("last", picker_args=tuple(picker))
    if session_id:
        return server, AppServerResume("id", session_id)
    return server, AppServerResume("pick", picker_args=tuple(picker))


def resolve_resume_thread(
    select: Callable[..., str | None] | None,
    resume: AppServerResume,
    env: dict[str, str],
    cwd: Path,
) -> str | None:
    """The conversation to open ("" for a new one), None when none was chosen.

    ``select`` is select_codex_resume_session(env, include_non_interactive=,
    passthrough=, cwd=, select_latest=); it prints why nothing was found.
    """

    if resume.mode in ("", "id"):
        return resume.session_id
    if select is None:
        print("Ciel Runtime cannot list saved Codex sessions here; pass resume <session id>.", flush=True)
        return None
    selected = select(
        env,
        include_non_interactive="--include-non-interactive" in resume.picker_args,
        passthrough=["resume", *resume.picker_args],
        cwd=cwd,
        select_latest=resume.mode == "last",
    )
    return str(selected or "").strip() or None


__all__ = [
    "AppServerResume",
    "PERMISSION_FLAGS",
    "PICKER_FLAGS",
    "resolve_resume_thread",
    "split_app_server_passthrough",
]
