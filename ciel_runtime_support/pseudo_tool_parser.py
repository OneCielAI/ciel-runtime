"""Parser strategy for provider-emitted pseudo tool-call envelopes."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


PSEUDO_TOOL_START = "<|tool_calls_section_begin|>"
PSEUDO_TOOL_END = "<|tool_calls_section_end|>"
PSEUDO_CALL_BEGIN = "<|tool_call_begin|>"
PSEUDO_ARG_BEGIN = "<|tool_call_argument_begin|>"
PSEUDO_CALL_END = "<|tool_call_end|>"


def normalize_tool_arguments(tool_name: str, arguments: Any) -> dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        text = arguments.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except (TypeError, ValueError):
            pass
        if tool_name == "Bash":
            return {"command": text}
    return {}


def infer_tool_name_from_args(arguments: dict[str, Any]) -> str:
    keys = set(arguments)
    if "command" in keys:
        return "Bash"
    if {"file_path", "content"}.issubset(keys):
        return "Write"
    if {"file_path", "old_string", "new_string"}.issubset(keys):
        return "Edit"
    if "file_path" in keys:
        return "Read"
    if keys & {"taskId", "task_id", "addBlocks", "addBlockedBy"}:
        return "TaskUpdate"
    return "TaskList" if not arguments else "Write"


@dataclass(frozen=True, slots=True)
class PseudoToolParserServices:
    parse_xml: Callable[..., tuple[str, list[dict[str, Any]]]]
    fuzzy_tool_name: Callable[[str], str]


def parse_pseudo_tool_calls(
    text: str,
    source_body: dict[str, Any] | None,
    services: PseudoToolParserServices,
) -> tuple[str, list[dict[str, Any]]]:
    if PSEUDO_TOOL_START not in text:
        return services.parse_xml(text, source_body)
    visible_parts: list[str] = []
    calls: list[dict[str, Any]] = []
    position = 0
    while True:
        start = text.find(PSEUDO_TOOL_START, position)
        if start < 0:
            visible_parts.append(text[position:])
            break
        visible_parts.append(text[position:start])
        end = text.find(PSEUDO_TOOL_END, start)
        if end < 0:
            section = text[start + len(PSEUDO_TOOL_START) :]
            position = len(text)
        else:
            section = text[start + len(PSEUDO_TOOL_START) : end]
            position = end + len(PSEUDO_TOOL_END)
        pattern = (
            re.escape(PSEUDO_CALL_BEGIN)
            + r"(.*?)"
            + re.escape(PSEUDO_ARG_BEGIN)
            + r"(.*?)"
            + re.escape(PSEUDO_CALL_END)
        )
        for match in re.finditer(pattern, section, flags=re.DOTALL):
            raw_header = match.group(1).strip()
            try:
                arguments = json.loads(match.group(2).strip())
            except (TypeError, ValueError):
                continue
            if not isinstance(arguments, dict):
                continue
            name = next(
                (
                    candidate
                    for part in re.split(r"[\s:|,]+", raw_header)
                    if (candidate := services.fuzzy_tool_name(part))
                ),
                "",
            )
            if not name:
                name = infer_tool_name_from_args(arguments)
            calls.append(
                {
                    "function": {"name": name, "arguments": arguments},
                    "id": raw_header,
                }
            )
        if end < 0:
            break
    visible_text, xml_calls = services.parse_xml("".join(visible_parts), source_body)
    return visible_text, calls + xml_calls
