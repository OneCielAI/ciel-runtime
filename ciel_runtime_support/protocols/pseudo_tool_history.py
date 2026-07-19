from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class PseudoToolHistoryServices:
    tool_names: Callable[[dict[str, Any]], set[str]]
    match_tool_name: Callable[[str, set[str]], str | None]
    resolve_emitted_name: Callable[[str, dict[str, Any]], str]
    normalize_arguments: Callable[[str, Any], dict[str, Any]]
    log: Callable[[str, str], None]


_INVOKE_RE = re.compile(
    r"(?is)(?:^|\n)[ \t]*(?:court[ \t]*(?:\r?\n)+)?<invoke\s+name=[\"'][^\"']+[\"'][\s\S]*?</invoke>[ \t]*(?=\n|$)"
)
_INVOKE_CALL_RE = re.compile(
    r"(?is)(?:^|\n)[ \t]*(?:court[ \t]*(?:\r?\n)+)?"
    r"<invoke\s+name=[\"'](?P<name>[^\"']+)[\"'][^>]*>(?P<body>[\s\S]*?)</invoke>[ \t]*(?=\n|$)"
)
_INVOKE_OPEN_RE = re.compile(
    r"(?is)(?:^|\n)[ \t]*(?:court[ \t]*(?:\r?\n)+)?<invoke\s+name=[\"'](?P<name>[^\"']+)[\"'][^>]*>"
)
_XML_RE = re.compile(
    r"(?is)(?:^|\n)[ \t]*(?:court[ \t]*(?:\r?\n)+)?<(?P<name>[A-Za-z_][\w.-]{0,96})\b[^>]*>[\s\S]*?</(?P=name)>[ \t]*(?=\n|$)"
)
_XML_CALL_RE = re.compile(
    r"(?is)(?:^|\n)[ \t]*(?:court[ \t]*(?:\r?\n)+)?"
    r"<(?P<name>[A-Za-z_][\w.-]{0,96})\b[^>]*>(?P<body>[\s\S]*?)</(?P=name)>[ \t]*(?=\n|$)"
)
_XML_OPEN_RE = re.compile(
    r"(?is)(?:^|\n)[ \t]*(?:court[ \t]*(?:\r?\n)+)?<(?P<name>[A-Za-z_][\w.-]{0,96})\b[^>]*>"
)
_PARAMETER_RE = re.compile(
    r"(?is)<parameter\s+name=[\"'](?P<name>[^\"']+)[\"'][^>]*>(?P<value>[\s\S]*?)</parameter>"
)
_CHILD_ARG_RE = re.compile(
    r"(?is)<(?P<name>[A-Za-z_][\w.-]{0,96})\b[^>]*>(?P<value>[\s\S]*?)</(?P=name)>"
)


def request_tool_name_aliases(body: dict[str, Any]) -> set[str]:
    aliases: set[str] = set()
    tools = body.get("tools")
    if not isinstance(tools, list):
        return aliases
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip()
        if not name:
            continue
        lowered = name.lower()
        aliases.add(lowered)
        aliases.add(lowered.replace("-", "_"))
        if "__" in lowered:
            short = lowered.rsplit("__", 1)[-1]
            aliases.add(short)
            aliases.add(short.replace("-", "_"))
    return aliases


def resolve_pseudo_xml_tool_name(
    raw_name: str,
    source_body: dict[str, Any] | None,
    services: PseudoToolHistoryServices,
) -> str | None:
    if not isinstance(source_body, dict):
        return None
    raw = str(raw_name or "").strip()
    if not raw:
        return None
    available = services.tool_names(source_body)
    if not available:
        return None
    matched = services.match_tool_name(raw, available)
    if matched:
        return matched
    aliases = request_tool_name_aliases(source_body)
    lowered = raw.lower()
    if lowered not in aliases and lowered.replace("-", "_") not in aliases:
        return None
    return services.resolve_emitted_name(raw, source_body)


def parse_pseudo_xml_tool_args(
    tool_name: str,
    body: str,
    services: PseudoToolHistoryServices,
) -> dict[str, Any]:
    inner = str(body or "").strip()
    args: dict[str, Any] = {}
    for match in _PARAMETER_RE.finditer(inner):
        key = str(match.group("name") or "").strip()
        if key:
            args[key] = html.unescape(str(match.group("value") or "")).strip()
    if args:
        return args
    for match in _CHILD_ARG_RE.finditer(inner):
        key = str(match.group("name") or "").strip()
        if key and key.lower() not in {"invoke", tool_name.lower()}:
            args[key] = html.unescape(str(match.group("value") or "")).strip()
    if args:
        return args
    try:
        parsed = json.loads(inner)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        return parsed
    return services.normalize_arguments(tool_name, html.unescape(inner).strip())


def find_pseudo_xml_tool_start(
    text: str,
    source_body: dict[str, Any] | None,
    services: PseudoToolHistoryServices,
) -> int:
    if not isinstance(source_body, dict) or "<" not in text:
        return -1
    matches = list(_INVOKE_CALL_RE.finditer(text)) + list(_XML_CALL_RE.finditer(text))
    matches += list(_INVOKE_OPEN_RE.finditer(text)) + list(_XML_OPEN_RE.finditer(text))
    for match in sorted(matches, key=lambda item: item.start()):
        raw_name = str(match.group("name") or "")
        if raw_name.lower() == "invoke":
            continue
        if resolve_pseudo_xml_tool_name(raw_name, source_body, services):
            return match.start()
    return -1


def parse_xml_pseudo_tool_calls(
    text: str,
    source_body: dict[str, Any] | None,
    services: PseudoToolHistoryServices,
) -> tuple[str, list[dict[str, Any]]]:
    if not isinstance(source_body, dict) or "<" not in text:
        return text, []
    matches: list[tuple[int, int, str, str]] = []
    for match in _INVOKE_CALL_RE.finditer(text):
        matches.append(
            (match.start(), match.end(), str(match.group("name") or ""), str(match.group("body") or ""))
        )
    for match in _XML_CALL_RE.finditer(text):
        raw_name = str(match.group("name") or "")
        if raw_name.lower() != "invoke":
            matches.append(
                (match.start(), match.end(), raw_name, str(match.group("body") or ""))
            )
    if not matches:
        return text, []
    visible_parts: list[str] = []
    calls: list[dict[str, Any]] = []
    position = 0
    for start, end, raw_name, body in sorted(matches, key=lambda item: item[0]):
        if start < position:
            continue
        matched_name = resolve_pseudo_xml_tool_name(raw_name, source_body, services)
        if not matched_name:
            continue
        visible_parts.append(text[position:start])
        args = parse_pseudo_xml_tool_args(matched_name, body, services)
        calls.append(
            {"function": {"name": matched_name, "arguments": args}, "id": f"xml:{raw_name}:{start}"}
        )
        position = end
    if not calls:
        return text, []
    visible_parts.append(text[position:])
    return "".join(visible_parts), calls


def sanitize_assistant_pseudo_tool_history(
    body: dict[str, Any],
    services: PseudoToolHistoryServices,
) -> dict[str, Any]:
    messages = body.get("messages")
    if not isinstance(messages, list):
        return body
    aliases = request_tool_name_aliases(body)
    sanitized: list[Any] = []
    removed_blocks = 0
    for message in messages:
        next_message, removed = _sanitize_assistant_message(message, aliases)
        sanitized.append(next_message)
        removed_blocks += removed
    if not removed_blocks:
        return body
    output = dict(body)
    output["messages"] = sanitized
    services.log("INFO", f"sanitized assistant pseudo tool-call text blocks={removed_blocks}")
    return output


def _sanitize_assistant_message(message: Any, aliases: set[str]) -> tuple[Any, int]:
    if not isinstance(message, dict) or str(message.get("role") or "") != "assistant":
        return message, 0
    content = message.get("content")
    if isinstance(content, str):
        text, count = _sanitize_text(content, aliases)
        if not count:
            return message, 0
        output = dict(message)
        output["content"] = text.strip()
        return output, count
    if not isinstance(content, list):
        return message, 0
    output_blocks: list[Any] = []
    removed = 0
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            output_blocks.append(block)
            continue
        text, count = _sanitize_text(str(block.get("text") or ""), aliases)
        if count:
            next_block = dict(block)
            next_block["text"] = text.strip()
            output_blocks.append(next_block)
            removed += count
        else:
            output_blocks.append(block)
    if not removed:
        return message, 0
    output = dict(message)
    output["content"] = output_blocks
    return output, removed


def _sanitize_text(text: str, aliases: set[str]) -> tuple[str, int]:
    output, count = _INVOKE_RE.subn("\n", text)
    if not aliases:
        return output, count
    removed = 0

    def replace_xml(match: re.Match[str]) -> str:
        nonlocal removed
        name = str(match.group("name") or "").strip().lower()
        if name not in aliases and name.replace("-", "_") not in aliases:
            return match.group(0)
        removed += 1
        return "\n"

    return _XML_RE.sub(replace_xml, output), count + removed
