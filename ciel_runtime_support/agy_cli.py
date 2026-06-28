from __future__ import annotations


AGY_COMMAND_NAMES = {
    "changelog",
    "help",
    "install",
    "models",
    "plugin",
    "plugins",
    "update",
}


AGY_OPTIONS_WITH_VALUE = {
    "--add-dir",
    "--conversation",
    "--log-file",
    "--model",
    "--new-project",
    "--print-timeout",
    "--project",
    "--sandbox",
}


AGY_CLAUDE_ONLY_VALUE_FLAGS = {
    "--allowedTools",
    "--append-system-prompt",
    "--disallowedTools",
    "--fallback-model",
    "--input-format",
    "--mcp-config",
    "--output-format",
    "--permission-prompt-tool",
    "--settings",
    "--system-prompt",
}


def agy_passthrough_first_non_option_arg(passthrough: list[str]) -> str:
    i = 0
    while i < len(passthrough):
        arg = str(passthrough[i])
        if arg == "--":
            return str(passthrough[i + 1]) if i + 1 < len(passthrough) else ""
        if arg.startswith("--") and "=" in arg:
            i += 1
            continue
        if arg in AGY_OPTIONS_WITH_VALUE:
            i += 2 if i + 1 < len(passthrough) else 1
            continue
        if arg.startswith("-") and arg != "-":
            i += 1
            continue
        return arg
    return ""


def agy_passthrough_has_command(passthrough: list[str]) -> bool:
    return agy_passthrough_first_non_option_arg(passthrough) in AGY_COMMAND_NAMES


def _agy_consume_optional_value(passthrough: list[str], index: int) -> tuple[str, int]:
    if index + 1 < len(passthrough):
        value = str(passthrough[index + 1])
        if value != "--" and not value.startswith("-"):
            return value, index + 2
    return "", index + 1


def _is_channel_spec_tagged(spec: str) -> bool:
    return spec.startswith("plugin:") or spec.startswith("server:")


def _agy_drop_passthrough_channel_args(passthrough: list[str], index: int) -> int:
    arg = str(passthrough[index])
    if arg.startswith("--channels=") or arg.startswith("--dangerously-load-development-channels="):
        return index + 1
    i = index + 1
    while i < len(passthrough) and _is_channel_spec_tagged(str(passthrough[i])):
        i += 1
    return i


def _agy_drop_greedy_passthrough_values(passthrough: list[str], index: int) -> int:
    i = index + 1
    while i < len(passthrough) and not str(passthrough[i]).startswith("-"):
        i += 1
    return i


def agy_passthrough_args_for_launch(passthrough: list[str]) -> tuple[list[str], list[str]]:
    """Translate shared Claude/Codex-oriented flags before launching AGY.

    AGY exposes a smaller native CLI surface than Claude Code and Codex. Keep
    intent-preserving mappings and drop flags that AGY does not parse.
    """
    out: list[str] = []
    notes: list[str] = []
    existing_agy_command = agy_passthrough_has_command(passthrough)
    mapped_permission_bypass = False
    i = 0
    while i < len(passthrough):
        arg = str(passthrough[i])

        if arg == "resume" and i == 0 and not existing_agy_command:
            session_id = str(passthrough[i + 1]) if i + 1 < len(passthrough) else ""
            if session_id and not session_id.startswith("-"):
                out.extend(["--conversation", session_id])
                notes.append("resume <conversation> -> --conversation <conversation>")
                i += 2
            else:
                out.append("--continue")
                notes.append("resume -> --continue")
                i += 1
            continue

        if arg in ("--continue", "-c"):
            out.append(arg)
            i += 1
            continue
        if arg.startswith("--continue="):
            out.append("--continue")
            prompt = arg.split("=", 1)[1]
            if prompt:
                out.append(prompt)
            notes.append("--continue=<prompt> -> --continue <prompt>")
            i += 1
            continue

        if arg in ("--resume", "-r"):
            session_id, i = _agy_consume_optional_value(passthrough, i)
            if session_id:
                out.extend(["--conversation", session_id])
                notes.append(f"{arg} <session> -> --conversation <session>")
            else:
                out.append("--continue")
                notes.append(f"{arg} -> --continue")
            continue
        if arg.startswith("--resume="):
            session_id = arg.split("=", 1)[1]
            if session_id:
                out.extend(["--conversation", session_id])
                notes.append("--resume=<session> -> --conversation <session>")
            else:
                out.append("--continue")
                notes.append("--resume= -> --continue")
            i += 1
            continue

        if arg == "--session-id":
            session_id, i = _agy_consume_optional_value(passthrough, i)
            if session_id:
                out.extend(["--conversation", session_id])
                notes.append("--session-id <session> -> --conversation <session>")
            continue
        if arg.startswith("--session-id="):
            session_id = arg.split("=", 1)[1]
            if session_id:
                out.extend(["--conversation", session_id])
                notes.append("--session-id=<session> -> --conversation <session>")
            i += 1
            continue

        if arg in ("--print", "-p", "--prompt"):
            out.append("--print")
            i += 1
            continue
        if arg.startswith(("--print=", "--prompt=")):
            prompt = arg.split("=", 1)[1]
            out.append("--print")
            if prompt:
                out.append(prompt)
            notes.append(f"{arg.split('=', 1)[0]}=<prompt> -> --print <prompt>")
            i += 1
            continue

        if arg == "exec" and i == 0 and not existing_agy_command:
            out.append("--print")
            notes.append("exec -> --print")
            i += 1
            continue

        if arg in ("--yolo", "--dangerously-bypass-approvals-and-sandbox"):
            if not mapped_permission_bypass:
                out.append("--dangerously-skip-permissions")
                mapped_permission_bypass = True
                notes.append(f"{arg} -> --dangerously-skip-permissions")
            i += 1
            continue

        if arg == "--dangerously-skip-permissions":
            if not mapped_permission_bypass:
                out.append(arg)
                mapped_permission_bypass = True
            i += 1
            continue

        if arg == "--permission-mode" or arg.startswith("--permission-mode="):
            if arg == "--permission-mode":
                value, i = _agy_consume_optional_value(passthrough, i)
            else:
                value = arg.split("=", 1)[1]
                i += 1
            if value == "bypassPermissions" and not mapped_permission_bypass:
                out.append("--dangerously-skip-permissions")
                mapped_permission_bypass = True
                notes.append("--permission-mode bypassPermissions -> --dangerously-skip-permissions")
            else:
                notes.append("--permission-mode ignored for AGY")
            continue

        if arg in ("--channels", "--dangerously-load-development-channels") or arg.startswith(
            ("--channels=", "--dangerously-load-development-channels=")
        ):
            i = _agy_drop_passthrough_channel_args(passthrough, i)
            notes.append(f"{arg.split('=', 1)[0]} ignored for AGY launch")
            continue

        if arg in AGY_CLAUDE_ONLY_VALUE_FLAGS:
            if arg == "--mcp-config":
                i = _agy_drop_greedy_passthrough_values(passthrough, i)
            else:
                _, i = _agy_consume_optional_value(passthrough, i)
            notes.append(f"{arg} ignored for AGY launch")
            continue
        if any(arg.startswith(flag + "=") for flag in AGY_CLAUDE_ONLY_VALUE_FLAGS):
            notes.append(f"{arg.split('=', 1)[0]} ignored for AGY launch")
            i += 1
            continue

        if arg == "--fork-session" or arg.startswith("--fork-session="):
            if arg == "--fork-session":
                i += 1
            else:
                i += 1
            notes.append("--fork-session ignored for AGY launch")
            continue

        out.append(arg)
        i += 1

    return out, notes


def agy_dangerous_launch_args(passthrough: list[str]) -> list[str]:
    return [] if "--dangerously-skip-permissions" in passthrough else ["--dangerously-skip-permissions"]
