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
import tomllib
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


@dataclass(frozen=True, slots=True)
class ResumeModel:
    """The model and provider a session's app-server was launched with.

    ``thread/resume`` without a model override applies the model and provider
    saved with the thread (codex rust-v0.160.0 app-server thread_processor.rs
    merge_persisted_resume_metadata).  A thread saved under another Ciel
    provider id (``ciel-runtime`` vs ``ciel-runtime-codex``) then fails with
    "Model provider `ciel-runtime` not found" (Edward, 2026-10-05).  The Codex
    TUI sends the current model and provider when it resumes; so does this.
    """

    model: str | None = None
    model_provider: str | None = None


def _config_value(setting: str, key: str) -> str | None:
    name, _, raw = setting.partition("=")
    if name.strip() != key or not raw.strip():
        return None
    try:
        value = tomllib.loads(f"v = {raw.strip()}").get("v")
    except tomllib.TOMLDecodeError:
        value = raw.strip().strip('"').strip("'")
    return value if isinstance(value, str) and value else None


def resume_model_from_command(cmd: Iterable[str]) -> ResumeModel:
    """The last ``model``/``model_provider`` the server command sets."""

    values = [str(item) for item in cmd]
    model: str | None = None
    provider: str | None = None
    i = 0
    while i < len(values):
        arg = values[i]
        setting = None
        if arg in ("-c", "--config") and i + 1 < len(values):
            setting = values[i + 1]
            i += 1
        elif arg.startswith("--config="):
            setting = arg.split("=", 1)[1]
        elif arg in ("-m", "--model") and i + 1 < len(values):
            model = values[i + 1] or model
            i += 1
        elif arg.startswith("--model="):
            model = arg.split("=", 1)[1] or model
        if setting is not None:
            model = _config_value(setting, "model") or model
            provider = _config_value(setting, "model_provider") or provider
        i += 1
    return ResumeModel(model, provider)


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
    "ResumeModel",
    "resolve_resume_thread",
    "resume_model_from_command",
    "split_app_server_passthrough",
]
