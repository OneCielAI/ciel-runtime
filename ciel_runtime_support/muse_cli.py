"""Translate Claude-style session flags into Muse Code's own argv.

Muse Code resumes with a subcommand (``muse resume [--last] [<session-ref>]``)
and knows nothing about Claude's ``--continue``/``--resume`` flags, so a launch
that carries one has to be rewritten before Muse sees it - the same job
``codex_cli`` and ``agy_cli`` do for their runtimes.
"""

from __future__ import annotations

MUSE_COMMAND_NAMES = frozenset(
    {
        "auth",
        "config",
        "exec",
        "export",
        "init",
        "login",
        "logout",
        "mcp",
        "plugins",
        "resume",
        "sandbox",
        "schema",
        "serve",
        "session-message",
        "skills",
        "trace",
    }
)


def muse_passthrough_has_command(passthrough: list[str]) -> bool:
    """Whether the argv already names a Muse subcommand."""

    for value in passthrough:
        text = str(value)
        if text.startswith("-"):
            continue
        return text in MUSE_COMMAND_NAMES
    return False


def _muse_consume_optional_value(passthrough: list[str], index: int) -> tuple[str, int]:
    next_index = index + 1
    if next_index < len(passthrough):
        value = str(passthrough[next_index])
        if value and not value.startswith("-"):
            return value, next_index + 1
    return "", index + 1


def muse_passthrough_mapping(passthrough: list[str]) -> tuple[list[str], list[str]]:
    """Return (mapped argv, notes) with the session flags Muse understands.

    ``--continue``/``-c`` become ``resume --last`` (the most recent session in
    this workspace), and ``--resume``/``-r``/``--session-id`` carry their
    session reference into ``resume <session-ref>``. A launch that already
    names a subcommand keeps it: the session flag is dropped rather than
    risking two commands in one argv.
    """

    notes: list[str] = []
    out: list[str] = []
    existing_command = muse_passthrough_has_command(passthrough)
    mapped = False
    index = 0
    while index < len(passthrough):
        argument = str(passthrough[index])

        if argument == "--continue" or (
            argument == "-c"
            and "=" not in str(passthrough[index + 1] if index + 1 < len(passthrough) else "")
        ):
            if not existing_command and not mapped:
                out.extend(["resume", "--last"])
                mapped = True
                notes.append(f"{argument} -> resume --last")
            index += 1
            continue

        if argument in ("--resume", "-r"):
            session_ref, index = _muse_consume_optional_value(passthrough, index)
            if not existing_command and not mapped:
                if session_ref:
                    out.extend(["resume", session_ref])
                    notes.append(f"{argument} <session> -> resume <session>")
                else:
                    out.extend(["resume", "--last"])
                    notes.append(f"{argument} -> resume --last")
                mapped = True
            continue

        if argument == "--session-id":
            session_id, index = _muse_consume_optional_value(passthrough, index)
            if session_id and not existing_command and not mapped:
                out.extend(["resume", session_id])
                mapped = True
                notes.append("--session-id <session> -> resume <session>")
            continue

        out.append(argument)
        index += 1

    return out, notes


__all__ = [
    "MUSE_COMMAND_NAMES",
    "muse_passthrough_has_command",
    "muse_passthrough_mapping",
]
