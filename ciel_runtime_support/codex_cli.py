from __future__ import annotations


CODEX_COMMAND_NAMES = {
    "app",
    "app-server",
    "apply",
    "archive",
    "cloud",
    "completion",
    "debug",
    "delete",
    "doctor",
    "exec",
    "exec-server",
    "features",
    "fork",
    "help",
    "login",
    "logout",
    "mcp",
    "mcp-server",
    "plugin",
    "remote-control",
    "resume",
    "review",
    "sandbox",
    "unarchive",
    "update",
}


CODEX_OPTIONS_WITH_VALUE = {
    "-a",
    "--add-dir",
    "--ask-for-approval",
    "-C",
    "--cd",
    "-c",
    "--color",
    "--config",
    "--disable",
    "--enable",
    "-i",
    "--image",
    "--local-provider",
    "-m",
    "--model",
    "-o",
    "--output-last-message",
    "--output-schema",
    "-p",
    "--profile",
    "--remote",
    "--remote-auth-token-env",
    "-s",
    "--sandbox",
}


CODEX_CLAUDE_ONLY_VALUE_FLAGS = {
    "--allowedTools",
    "--append-system-prompt",
    "--disallowedTools",
    "--fallback-model",
    "--input-format",
    "--output-format",
    "--permission-prompt-tool",
    "--settings",
    "--system-prompt",
}


def codex_passthrough_first_non_option_arg(passthrough: list[str]) -> str:
    index = codex_passthrough_first_non_option_index(passthrough)
    return str(passthrough[index]) if index >= 0 else ""


def codex_passthrough_first_non_option_index(passthrough: list[str]) -> int:
    i = 0
    while i < len(passthrough):
        arg = str(passthrough[i])
        if arg == "--":
            return i + 1 if i + 1 < len(passthrough) else -1
        if arg.startswith("--") and "=" in arg:
            i += 1
            continue
        if arg in CODEX_OPTIONS_WITH_VALUE:
            i += 2 if i + 1 < len(passthrough) else 1
            continue
        if arg.startswith("-") and arg != "-":
            i += 1
            continue
        return i
    return -1


def codex_passthrough_has_command(passthrough: list[str]) -> bool:
    return codex_passthrough_first_non_option_arg(passthrough) in CODEX_COMMAND_NAMES


def _codex_consume_optional_value(passthrough: list[str], index: int) -> tuple[str, int]:
    if index + 1 < len(passthrough):
        value = str(passthrough[index + 1])
        if value != "--" and not value.startswith("-"):
            return value, index + 2
    return "", index + 1


def _is_channel_spec_tagged(spec: str) -> bool:
    return spec.startswith("plugin:") or spec.startswith("server:")


def _codex_drop_passthrough_channel_args(passthrough: list[str], index: int) -> int:
    arg = str(passthrough[index])
    if arg.startswith("--channels=") or arg.startswith("--dangerously-load-development-channels="):
        return index + 1
    i = index + 1
    while i < len(passthrough) and _is_channel_spec_tagged(str(passthrough[i])):
        i += 1
    return i


def _codex_drop_greedy_passthrough_values(passthrough: list[str], index: int) -> int:
    i = index + 1
    while i < len(passthrough) and not str(passthrough[i]).startswith("-"):
        i += 1
    return i


def _codex_session_id_after_index(passthrough: list[str], index: int) -> str:
    i = index + 1
    while i < len(passthrough):
        arg = str(passthrough[i])
        if arg.startswith("--session-id="):
            return arg.split("=", 1)[1]
        if arg == "--session-id" and i + 1 < len(passthrough):
            value = str(passthrough[i + 1])
            return value if value != "--" and not value.startswith("-") else ""
        i += 1
    return ""


def codex_resume_picker_needs_all(passthrough: list[str]) -> bool:
    command_index = codex_passthrough_first_non_option_index(passthrough)
    if command_index < 0 or str(passthrough[command_index]) != "resume":
        return False
    i = command_index + 1
    while i < len(passthrough):
        arg = str(passthrough[i])
        if arg == "--":
            return False
        if arg in ("--all", "--last"):
            return False
        if arg.startswith("--all=") or arg.startswith("--last="):
            return False
        if arg.startswith("--") and "=" in arg:
            i += 1
            continue
        if arg in CODEX_OPTIONS_WITH_VALUE:
            i += 2 if i + 1 < len(passthrough) else 1
            continue
        if arg.startswith("-") and arg != "-":
            i += 1
            continue
        return False
    return True


def codex_resume_with_all_sessions(passthrough: list[str]) -> tuple[list[str], bool]:
    if not codex_resume_picker_needs_all(passthrough):
        return passthrough, False
    command_index = codex_passthrough_first_non_option_index(passthrough)
    out = list(passthrough)
    out.insert(command_index + 1, "--all")
    return out, True


def codex_passthrough_args_for_launch(passthrough: list[str]) -> tuple[list[str], list[str]]:
    """Translate Claude-oriented passthrough flags before launching Codex.

    The prelaunch menu is shared with Claude Code, so users can arrive here
    with Claude-only session flags such as --continue. Codex should receive
    native Codex commands where the intent is clear, and should not receive
    flags it cannot parse.
    """
    out: list[str] = []
    notes: list[str] = []
    existing_codex_command = codex_passthrough_has_command(passthrough)
    mapped_command: list[str] = []
    mapped_permission_bypass = False
    i = 0
    while i < len(passthrough):
        arg = str(passthrough[i])

        if arg == "--continue":
            if not existing_codex_command and not mapped_command:
                mapped_command = ["resume", "--last"]
                notes.append("--continue -> resume --last")
            i += 1
            continue
        if arg.startswith("--continue="):
            value = arg.split("=", 1)[1]
            if not existing_codex_command and not mapped_command:
                mapped_command = ["resume", "--last"]
                if value:
                    out.append(value)
                notes.append("--continue -> resume --last")
            i += 1
            continue

        if arg == "-c":
            next_value = str(passthrough[i + 1]) if i + 1 < len(passthrough) else ""
            if next_value and "=" in next_value:
                out.extend([arg, next_value])
                i += 2
                continue
            if not existing_codex_command and not mapped_command:
                mapped_command = ["resume", "--last"]
                notes.append("-c -> resume --last")
            i += 1
            continue

        if arg in ("--resume", "-r"):
            session_id, i = _codex_consume_optional_value(passthrough, i)
            if not existing_codex_command and not mapped_command:
                mapped_command = ["resume"]
                if session_id:
                    mapped_command.append(session_id)
                    notes.append(f"{arg} <session> -> resume <session>")
                else:
                    notes.append(f"{arg} -> resume")
            continue
        if arg.startswith("--resume="):
            session_id = arg.split("=", 1)[1]
            if not existing_codex_command and not mapped_command:
                mapped_command = ["resume"]
                if session_id:
                    mapped_command.append(session_id)
                notes.append("--resume=<session> -> resume <session>")
            i += 1
            continue

        if arg == "--session-id":
            session_id, i = _codex_consume_optional_value(passthrough, i)
            if session_id and not existing_codex_command and not mapped_command:
                mapped_command = ["resume", session_id]
                notes.append("--session-id <session> -> resume <session>")
            continue
        if arg.startswith("--session-id="):
            session_id = arg.split("=", 1)[1]
            if session_id and not existing_codex_command and not mapped_command:
                mapped_command = ["resume", session_id]
                notes.append("--session-id=<session> -> resume <session>")
            i += 1
            continue

        if arg == "--fork-session":
            session_id = _codex_session_id_after_index(passthrough, i)
            if not existing_codex_command and not mapped_command:
                mapped_command = ["fork"]
                if session_id:
                    mapped_command.append(session_id)
                    notes.append("--fork-session --session-id=<session> -> fork <session>")
                else:
                    mapped_command.append("--last")
                    notes.append("--fork-session -> fork --last")
            i += 1
            continue

        if arg == "--print":
            if not existing_codex_command and not mapped_command:
                mapped_command = ["exec"]
                notes.append("--print -> exec")
            i += 1
            continue
        if arg.startswith("--print="):
            prompt = arg.split("=", 1)[1]
            if not existing_codex_command and not mapped_command:
                mapped_command = ["exec"]
                if prompt:
                    out.append(prompt)
                notes.append("--print -> exec")
            i += 1
            continue

        if arg == "--dangerously-skip-permissions":
            if not mapped_permission_bypass:
                out.append("--dangerously-bypass-approvals-and-sandbox")
                mapped_permission_bypass = True
                notes.append("--dangerously-skip-permissions -> --dangerously-bypass-approvals-and-sandbox")
            i += 1
            continue

        if arg == "--permission-mode" or arg.startswith("--permission-mode="):
            if arg == "--permission-mode":
                value, i = _codex_consume_optional_value(passthrough, i)
            else:
                value = arg.split("=", 1)[1]
                i += 1
            if value == "bypassPermissions" and not mapped_permission_bypass:
                out.append("--dangerously-bypass-approvals-and-sandbox")
                mapped_permission_bypass = True
                notes.append("--permission-mode bypassPermissions -> --dangerously-bypass-approvals-and-sandbox")
            else:
                notes.append("--permission-mode ignored for Codex")
            continue

        if arg in ("--channels", "--dangerously-load-development-channels") or arg.startswith(
            ("--channels=", "--dangerously-load-development-channels=")
        ):
            i = _codex_drop_passthrough_channel_args(passthrough, i)
            notes.append(f"{arg.split('=', 1)[0]} ignored for Codex launch")
            continue

        if arg in ("--mcp-config",):
            i = _codex_drop_greedy_passthrough_values(passthrough, i)
            notes.append("--mcp-config ignored for Codex launch")
            continue

        if arg in CODEX_CLAUDE_ONLY_VALUE_FLAGS:
            _, i = _codex_consume_optional_value(passthrough, i)
            notes.append(f"{arg} ignored for Codex launch")
            continue
        if any(arg.startswith(flag + "=") for flag in CODEX_CLAUDE_ONLY_VALUE_FLAGS):
            notes.append(f"{arg.split('=', 1)[0]} ignored for Codex launch")
            i += 1
            continue

        if arg == "--from-pr" or arg.startswith("--from-pr="):
            if arg == "--from-pr":
                _, i = _codex_consume_optional_value(passthrough, i)
            else:
                i += 1
            notes.append("--from-pr ignored for Codex launch")
            continue

        out.append(arg)
        i += 1

    result = [*mapped_command, *out] if mapped_command and not existing_codex_command else out
    result, added_all = codex_resume_with_all_sessions(result)
    if added_all:
        notes.append("resume -> resume --all (show sessions from every cwd)")
    return result, notes
