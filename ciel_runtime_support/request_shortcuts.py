"""Parse local router control requests embedded in runtime conversations."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Any, Callable


PLACEHOLDER_VALUES = frozenset(
    {"", "$0", "${0}", "$1", "${1}", "$2", "${2}", "$ARGUMENTS", "${ARGUMENTS}"}
)


@dataclass(frozen=True, slots=True)
class ShortcutTextServices:
    latest_user_text: Callable[[dict[str, Any]], str]


def has_marker(
    body: dict[str, Any], markers: tuple[str, ...], services: ShortcutTextServices
) -> bool:
    text = services.latest_user_text(body)
    return any(marker in text for marker in markers)


def marker_tail(
    body: dict[str, Any], markers: tuple[str, ...], services: ShortcutTextServices
) -> str:
    text = services.latest_user_text(body)
    for marker in markers:
        if marker in text:
            return text.split(marker, 1)[1]
    return ""


def single_value(
    body: dict[str, Any],
    markers: tuple[str, ...],
    services: ShortcutTextServices,
    *,
    empty_default: str,
    blank_value_default: str,
) -> str:
    tail = marker_tail(body, markers, services)
    if not tail:
        return empty_default
    for line in tail.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("value:"):
            return stripped.split(":", 1)[1].strip() or blank_value_default
    return tail.strip() or blank_value_default


def live_option_value(
    body: dict[str, Any], markers: tuple[str, ...], services: ShortcutTextServices
) -> str:
    tail = marker_tail(body, markers, services)
    if not tail:
        return "status"
    fallback_values: list[str] = []
    structured = False
    for line in tail.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if lowered.startswith("value:"):
            structured = True
            value = stripped.split(":", 1)[1].strip()
            if value not in PLACEHOLDER_VALUES:
                return value
        elif lowered.startswith("arguments:"):
            structured = True
            value = stripped.split(":", 1)[1].strip()
            if value not in PLACEHOLDER_VALUES:
                fallback_values.append(value)
    if fallback_values:
        return fallback_values[0]
    if structured:
        return "status"
    fallback = tail.strip()
    return fallback if fallback not in PLACEHOLDER_VALUES else "status"


def live_api_keys_value(
    body: dict[str, Any], markers: tuple[str, ...], services: ShortcutTextServices
) -> str:
    tail = marker_tail(body, markers, services)
    if not tail:
        return "status"
    value_line = ""
    arguments: list[str] = []
    structured = False
    capture_arguments = False
    for line in tail.splitlines():
        stripped = line.strip()
        if capture_arguments:
            if stripped not in PLACEHOLDER_VALUES:
                arguments.append(stripped)
            continue
        lowered = stripped.lower()
        if lowered.startswith("value:"):
            structured = True
            value = stripped.split(":", 1)[1].strip()
            if value not in PLACEHOLDER_VALUES:
                value_line = value
        elif lowered.startswith("arguments:"):
            structured = True
            capture_arguments = True
            value = stripped.split(":", 1)[1].strip()
            if value not in PLACEHOLDER_VALUES:
                arguments.append(value)
    if arguments:
        return "\n".join(arguments)
    if value_line:
        return value_line
    if structured:
        return "status"
    fallback = tail.strip()
    return fallback if fallback not in PLACEHOLDER_VALUES else "status"


def parse_channel_bridge_args(raw: str) -> tuple[str, dict[str, str]]:
    text = str(raw or "").strip()
    if not text:
        return "status", {}
    try:
        parts = shlex.split(text)
    except Exception:
        parts = text.split()
    if not parts:
        return "status", {}
    command = parts[0].strip().lower()
    if command not in {"status", "poll", "wait", "send", "post", "sse"}:
        parts = ["status", *parts]
        command = "status"
    options: dict[str, str] = {}
    loose: list[str] = []
    for part in parts[1:]:
        if "=" in part:
            key, value = part.split("=", 1)
            options[key.strip().lower().replace("-", "_")] = value.strip()
        elif part:
            loose.append(part)
    if loose and "message" not in options and command in {"send", "post"}:
        options["message"] = " ".join(loose)
    return command, options


def format_channel_messages(messages: list[dict[str, Any]], after: int) -> str:
    if not messages:
        return f"No channel messages after id {after}."
    lines = [f"Channel bridge messages ({len(messages)}):"]
    for item in messages:
        recipients = item.get("recipients") or []
        recipient_text = (
            ",".join(str(value) for value in recipients) or "all"
            if isinstance(recipients, list)
            else str(recipients or "all")
        )
        text = re.sub(r"\s+", " ", str(item.get("message") or "")).strip()
        if len(text) > 500:
            text = text[:500].rstrip() + "..."
        lines.append(
            f"- #{item.get('id')} [{item.get('channel')}] {item.get('sender_id')} -> {recipient_text}: {text}"
        )
    lines.append(f"Last id: {messages[-1].get('id')}")
    return "\n".join(lines)


def _strip_wrapping_quotes(value: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def split_import_session_arguments(value: str, *, posix: bool) -> tuple[str, str]:
    raw = str(value or "").strip()
    if not raw:
        return "", ""
    try:
        parts = [_strip_wrapping_quotes(part) for part in shlex.split(raw, posix=posix)]
    except Exception:
        parts = raw.split()
    if not parts:
        return "", ""
    target = parts[0].strip()
    path_parts: list[str] = []
    index = 1
    while index < len(parts):
        part = str(parts[index]).strip()
        lowered = part.lower()
        if lowered in {"--path", "-p", "path", "file", "transcript"} and index + 1 < len(parts):
            path_parts = [str(parts[index + 1])]
            index += 2
            continue
        if lowered.startswith(("--path=", "path=", "file=", "transcript=")):
            path_parts = [part.split("=", 1)[1]]
        else:
            path_parts.append(part)
        index += 1
    return target, _strip_wrapping_quotes(" ".join(path_parts).strip())


def import_session_args(
    body: dict[str, Any],
    markers: tuple[str, ...],
    services: ShortcutTextServices,
    *,
    posix: bool,
) -> tuple[str, str]:
    tail = marker_tail(body, markers, services)
    if not tail:
        return "", ""
    target = ""
    path = ""
    arguments: list[str] = []
    capture = False
    field_prefixes = ("target:", "source:", "format:", "runtime:")
    path_prefixes = ("path:", "file:", "transcript:")
    for line in tail.splitlines():
        stripped = line.strip()
        if capture:
            if stripped not in PLACEHOLDER_VALUES:
                arguments.append(stripped)
            continue
        lowered = stripped.lower()
        if lowered.startswith(field_prefixes):
            value = stripped.split(":", 1)[1].strip()
            if value not in PLACEHOLDER_VALUES:
                target = value
        elif lowered.startswith(path_prefixes):
            value = stripped.split(":", 1)[1].strip()
            if value not in PLACEHOLDER_VALUES:
                path = _strip_wrapping_quotes(value)
        elif lowered.startswith("arguments:"):
            capture = True
            value = stripped.split(":", 1)[1].strip()
            if value not in PLACEHOLDER_VALUES:
                arguments.append(value)
    arg_target, arg_path = split_import_session_arguments("\n".join(arguments), posix=posix)
    target = arg_target or target
    path = arg_path or path
    if not target:
        fallback = "\n".join(
            line
            for line in tail.splitlines()
            if not line.strip().lower().startswith((*field_prefixes, *path_prefixes, "arguments:"))
        ).strip()
        target, fallback_path = split_import_session_arguments(fallback, posix=posix)
        path = path or fallback_path
    return target.strip(), _strip_wrapping_quotes(path)
