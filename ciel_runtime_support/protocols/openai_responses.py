"""Pure OpenAI Responses <-> Anthropic Messages conversions.

This module intentionally has no router, configuration, or network dependencies.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import time
import uuid
from typing import Any

from ..architecture import MessageProtocolAdapter
from ..responses_input_compatibility import router_synthesized_item_id


_REASONING_ENVELOPE_PREFIX = "ciel-responses-reasoning-v1:"
_COMMENTARY_ENVELOPE_PREFIX = "ciel-responses-commentary-v1:"
_ANTHROPIC_REASONING_ENVELOPE_PREFIX = "ciel-anthropic-reasoning-v1:"
_ANTHROPIC_IMAGE_MEDIA_TYPES = {
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}
_CODEX_0150_APPLY_PATCH_LARK = """start: begin_patch hunk+ end_patch
begin_patch: "*** Begin Patch" LF
end_patch: "*** End Patch" LF?

hunk: add_hunk | delete_hunk | update_hunk
add_hunk: "*** Add File: " filename LF add_line+
delete_hunk: "*** Delete File: " filename LF
update_hunk: "*** Update File: " filename LF change_move? change?

filename: /(.+)/
add_line: "+" /(.*)/ LF -> line

change_move: "*** Move to: " filename LF
change: (change_context | change_line)+ eof_line?
change_context: ("@@" | "@@ " /(.+)/) LF
change_line: ("+" | "-" | " ") /(.*)/ LF
eof_line: "*** End of File" LF

%import common.LF
"""
_CODEX_0150_APPLY_PATCH_ENVIRONMENT_LARK = _CODEX_0150_APPLY_PATCH_LARK.replace(
    "start: begin_patch hunk+ end_patch",
    "start: begin_patch environment_id? hunk+ end_patch\n"
    'environment_id: "*** Environment ID: " filename LF',
)
_CODEX_0150_CODE_MODE_EXEC_LARK = r"""
start: pragma_source | plain_source
pragma_source: PRAGMA_LINE NEWLINE SOURCE
plain_source: SOURCE

PRAGMA_LINE: /[ \t]*\/\/ @exec:[^\r\n]*/
NEWLINE: /\r?\n/
SOURCE: /[\s\S]+/
"""


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    return {}


def _required_json_object(value: Any, field: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"{field} must contain a JSON object") from exc
        if isinstance(parsed, dict):
            return parsed
    raise ValueError(f"{field} must contain a JSON object")


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        content_type = str(content.get("type") or "")
        if content_type in ("input_text", "output_text", "text"):
            return str(content.get("text") or "")
        if content_type == "refusal":
            return str(content.get("refusal") or "")
        return str(content.get("text") or content.get("output") or "")
    if isinstance(content, list):
        parts = [_content_text(item) for item in content]
        return "\n".join(part for part in parts if part)
    return ""


def _content_blocks(content: Any) -> list[dict[str, Any]]:
    text = _content_text(content)
    return [{"type": "text", "text": text}] if text else []


def _reasoning_summary_text(item: dict[str, Any]) -> str:
    summary = item.get("summary")
    if not isinstance(summary, list):
        return ""
    return "\n".join(
        str(part.get("text") or "")
        for part in summary
        if isinstance(part, dict) and part.get("type") == "summary_text"
    ).strip()


def _namespace_tool_alias(namespace: str, name: str) -> str:
    combined = (
        f"{namespace}{name}"
        if namespace.endswith("_") or name.startswith("_")
        else f"{namespace}__{name}"
    )
    if len(combined) <= 64 and all(
        character.isascii()
        and (character.isalnum() or character in {"_", "-"})
        for character in combined
    ):
        return combined
    sanitized = "".join(
        character
        if character.isascii()
        and (character.isalnum() or character in {"_", "-"})
        else "_"
        for character in combined
    )
    digest = hashlib.sha256(
        f"{namespace}\0{name}".encode("utf-8", errors="strict")
    ).hexdigest()[:12]
    return f"{sanitized[:51]}_{digest}"


def _namespace_tool_identity(
    tools: Any, emitted_name: str
) -> tuple[str, str, str] | None:
    if not isinstance(tools, list):
        return None
    matches: list[tuple[str, str, str]] = []
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("type") != "namespace":
            continue
        namespace = tool.get("name")
        members = tool.get("tools")
        if not isinstance(namespace, str) or not isinstance(members, list):
            continue
        for member in members:
            if not isinstance(member, dict):
                continue
            name = member.get("name")
            member_type = member.get("type")
            if (
                isinstance(name, str)
                and member_type in {"function", "custom"}
                and _namespace_tool_alias(namespace, name) == emitted_name
            ):
                matches.append((namespace, name, member_type))
    return matches[0] if len(matches) == 1 else None


def _responses_source_tools(body: dict[str, Any] | None) -> Any:
    """Return the tool declaration carried by a Responses request.

    Responses Lite moves the declaration from the top-level ``tools`` field to
    a leading ``additional_tools`` input item. Strict request validation is
    performed by the caller; response projection uses this helper only to
    restore tool identities from an already accepted source request.
    """

    if not isinstance(body, dict):
        return None
    top_level = body.get("tools")
    if top_level is not None:
        return top_level
    raw_input = body.get("input")
    if not isinstance(raw_input, list):
        return None
    declarations = [
        item.get("tools")
        for item in raw_input
        if isinstance(item, dict) and item.get("type") == "additional_tools"
    ]
    return declarations[0] if len(declarations) == 1 else None


def _tools_to_anthropic(
    tools: Any,
    *,
    strict_projection: bool = False,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if tools is None:
        return out
    if not isinstance(tools, list):
        if strict_projection:
            raise ValueError("Responses tools must be an array")
        return out
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict):
            if strict_projection:
                raise ValueError(f"Responses tools[{index}] must be an object")
            continue
        if strict_projection:
            tool_type = tool.get("type")
            if tool_type == "namespace":
                unknown_namespace_fields = sorted(
                    set(tool) - {"description", "name", "tools", "type"}
                )
                namespace = tool.get("name")
                description = tool.get("description")
                members = tool.get("tools")
                if unknown_namespace_fields:
                    raise ValueError(
                        f"Responses tools[{index}] namespace fields cannot be "
                        "projected: " + ", ".join(unknown_namespace_fields)
                    )
                if not isinstance(namespace, str) or not namespace.strip():
                    raise ValueError(
                        f"Responses tools[{index}] namespace name is required"
                    )
                if namespace != namespace.strip():
                    raise ValueError(
                        f"Responses tools[{index}] namespace name must not have "
                        "leading or trailing whitespace"
                    )
                if description is not None and not isinstance(description, str):
                    raise ValueError(
                        f"Responses tools[{index}] namespace description must "
                        "be a string"
                    )
                if not isinstance(members, list) or not members:
                    raise ValueError(
                        f"Responses tools[{index}] namespace tools must be a "
                        "non-empty array"
                    )
                for member_index, member in enumerate(members):
                    if not isinstance(member, dict):
                        raise ValueError(
                            f"Responses tools[{index}].tools[{member_index}] "
                            "must be an object"
                        )
                    member_name = member.get("name")
                    if not isinstance(member_name, str) or not member_name.strip():
                        raise ValueError(
                            f"Responses tools[{index}].tools[{member_index}].name "
                            "is required"
                        )
                    if member_name != member_name.strip():
                        raise ValueError(
                            f"Responses tools[{index}].tools[{member_index}].name "
                            "must not have leading or trailing whitespace"
                        )
                    projected_members = _tools_to_anthropic(
                        [
                            {
                                **member,
                                "name": _namespace_tool_alias(
                                    namespace.strip(), member_name.strip()
                                ),
                            }
                        ],
                        strict_projection=True,
                    )
                    projected_member = projected_members[0]
                    namespace_description = str(description or "").strip()
                    member_description = str(
                        projected_member.get("description") or ""
                    ).strip()
                    projected_member["description"] = "\n\n".join(
                        part
                        for part in (namespace_description, member_description)
                        if part
                    )
                    out.append(projected_member)
                continue
            if tool_type not in {"function", "custom"}:
                raise ValueError(
                    "Responses hosted tool cannot be projected to Anthropic: "
                    f"tools[{index}].type={tool_type!r}"
                )
            allowed_fields = {
                "allowed_callers",
                "defer_loading",
                "description",
                "format",
                "input_examples",
                "name",
                "parameters",
                "strict",
                "type",
            }
            unknown = sorted(set(tool) - allowed_fields)
            if unknown:
                raise ValueError(
                    f"Responses tools[{index}] fields cannot be projected: "
                    + ", ".join(unknown)
                )
            name_value = tool.get("name")
            if not isinstance(name_value, str) or not name_value.strip():
                raise ValueError(f"Responses tools[{index}].name is required")
            if name_value != name_value.strip():
                raise ValueError(
                    f"Responses tools[{index}].name must not have leading or "
                    "trailing whitespace"
                )
            description_value = tool.get("description")
            if description_value is not None and not isinstance(
                description_value, str
            ):
                raise ValueError(
                    f"Responses tools[{index}].description must be a string"
                )
            if tool_type == "function":
                parameters_value = tool.get("parameters")
                if not isinstance(parameters_value, dict):
                    raise ValueError(
                        f"Responses tools[{index}].parameters must be an object"
                    )
                projected_description = description_value or ""
            else:
                projected_description = _custom_tool_description_for_anthropic(
                    description_value or "",
                    tool.get("format"),
                    field=f"Responses tools[{index}].format",
                )
                parameters_value = {
                    "type": "object",
                    "properties": {
                        "input": {
                            "type": "string",
                            "description": (
                                "Complete raw input for the custom tool; it must "
                                "obey the format contract in the tool description."
                            ),
                        }
                    },
                    "required": ["input"],
                    "additionalProperties": False,
                }
            projected: dict[str, Any] = {
                "name": name_value,
                "description": projected_description,
                "input_schema": dict(parameters_value),
            }
            strict_value = tool.get("strict")
            if strict_value is not None:
                if not isinstance(strict_value, bool):
                    raise ValueError(
                        f"Responses tools[{index}].strict must be a boolean"
                    )
                projected["strict"] = strict_value
            allowed_callers = tool.get("allowed_callers")
            if allowed_callers is not None:
                if (
                    not isinstance(allowed_callers, list)
                    or not allowed_callers
                    or any(caller != "direct" for caller in allowed_callers)
                ):
                    raise ValueError(
                        f"Responses tools[{index}].allowed_callers cannot be "
                        "projected to Anthropic"
                    )
                projected["allowed_callers"] = list(allowed_callers)
            defer_loading = tool.get("defer_loading")
            if defer_loading is not None:
                if not isinstance(defer_loading, bool):
                    raise ValueError(
                        f"Responses tools[{index}].defer_loading must be a boolean"
                    )
                if defer_loading:
                    raise ValueError(
                        f"Responses tools[{index}].defer_loading=true requires "
                        "a hosted tool-search capability that cannot be projected"
                    )
                projected["defer_loading"] = False
            input_examples = tool.get("input_examples")
            if input_examples is not None:
                if not isinstance(input_examples, list) or any(
                    not isinstance(example, dict) for example in input_examples
                ):
                    raise ValueError(
                        f"Responses tools[{index}].input_examples must be an "
                        "array of objects"
                    )
                projected["input_examples"] = [
                    dict(example) for example in input_examples
                ]
            out.append(projected)
            continue
        name = tool.get("name")
        description = tool.get("description", "")
        parameters = tool.get("parameters")
        if not name and isinstance(tool.get("function"), dict):
            function = tool["function"]
            name = function.get("name")
            description = function.get("description", description)
            parameters = function.get("parameters", parameters)
        if tool.get("type") not in (None, "function") and not name:
            continue
        if not name:
            continue
        is_custom = str(tool.get("type") or "").strip().lower() == "custom"
        if is_custom:
            description = _custom_tool_description_for_anthropic(
                str(description or ""),
                tool.get("format"),
                field=f"Responses tools[{index}].format",
            )
            parameters = {
                "type": "object",
                "properties": {
                    "input": {
                        "type": "string",
                        "description": (
                            "Complete raw input for the custom tool; it must obey "
                            "the format contract in the tool description."
                        ),
                    }
                },
                "required": ["input"],
                "additionalProperties": False,
            }
        projected = {
            "name": str(name),
            "description": str(description or ""),
            "input_schema": parameters if isinstance(parameters, dict) else {"type": "object", "properties": {}},
        }
        strict = tool.get("strict")
        if strict is not None:
            if not isinstance(strict, bool):
                raise ValueError("Responses function tool strict must be a boolean")
            projected["strict"] = strict
        for key in ("allowed_callers", "defer_loading", "input_examples"):
            if tool.get(key) is not None:
                projected[key] = tool[key]
        out.append(projected)
    if strict_projection:
        projected_names = [str(tool.get("name") or "") for tool in out]
        if len(projected_names) != len(set(projected_names)):
            raise ValueError(
                "Responses tools contain names that collide after namespace "
                "projection"
            )
    return out


def _custom_tool_names(tools: Any) -> set[str]:
    if not isinstance(tools, list):
        return set()
    return {
        str(tool.get("name") or "")
        for tool in tools
        if isinstance(tool, dict)
        and str(tool.get("type") or "").strip().lower() == "custom"
        and str(tool.get("name") or "")
    }


def _custom_tool_source(tools: Any, emitted_name: str) -> dict[str, Any] | None:
    if not isinstance(tools, list):
        return None
    matches: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") == "custom" and tool.get("name") == emitted_name:
            matches.append(tool)
            continue
        if tool.get("type") != "namespace":
            continue
        namespace = tool.get("name")
        members = tool.get("tools")
        if not isinstance(namespace, str) or not isinstance(members, list):
            continue
        for member in members:
            if (
                isinstance(member, dict)
                and member.get("type") == "custom"
                and isinstance(member.get("name"), str)
                and _namespace_tool_alias(namespace, member["name"]) == emitted_name
            ):
                matches.append(member)
    return matches[0] if len(matches) == 1 else None


def _validated_custom_lark_definition(
    format_value: Any,
    *,
    field: str,
) -> str | None:
    if format_value is None:
        return None
    if not isinstance(format_value, dict):
        raise ValueError(f"{field} must be an object")
    unknown = sorted(set(format_value) - {"definition", "syntax", "type"})
    if unknown:
        raise ValueError(
            f"{field} fields cannot be projected: " + ", ".join(unknown)
        )
    if format_value.get("type") != "grammar":
        raise ValueError(f"{field}.type must be 'grammar'")
    if format_value.get("syntax") != "lark":
        raise ValueError(f"{field}.syntax must be 'lark'")
    definition = format_value.get("definition")
    if not isinstance(definition, str) or not definition.strip():
        raise ValueError(f"{field}.definition must be a non-empty string")
    normalized_definition = definition.replace("\r\n", "\n")
    supported_definitions = {
        _CODEX_0150_APPLY_PATCH_LARK,
        _CODEX_0150_APPLY_PATCH_ENVIRONMENT_LARK,
        _CODEX_0150_CODE_MODE_EXEC_LARK,
    }
    if "\r" in normalized_definition or normalized_definition not in supported_definitions:
        raise ValueError(
            f"{field}.definition is not the supported Codex 0.150.1 "
            "custom-tool grammar"
        )
    return definition


def _validate_codex_apply_patch_input(
    value: str,
    *,
    field: str,
    allow_environment_id: bool = False,
) -> None:
    def invalid(detail: str) -> None:
        raise ValueError(
            f"{field} does not satisfy the Codex 0.150.1 apply_patch grammar: "
            f"{detail}"
        )

    begin = "*** Begin Patch\n"
    end = "*** End Patch"
    if "\r" in value:
        invalid("only LF line endings are allowed")
    if not value.startswith(begin):
        invalid("missing begin marker")
    if value.endswith(end + "\n"):
        body = value[len(begin) : -len(end + "\n")]
    elif value.endswith(end):
        body = value[len(begin) : -len(end)]
    else:
        invalid("missing terminal end marker")
        return
    raw_lines = body.splitlines(keepends=True)
    if not raw_lines or any(not line.endswith("\n") for line in raw_lines):
        invalid("every hunk line must end with LF")
    lines = [line[:-1] for line in raw_lines]
    hunk_prefixes = (
        "*** Add File: ",
        "*** Delete File: ",
        "*** Update File: ",
    )

    def is_hunk_header(line: str) -> bool:
        return line.startswith(hunk_prefixes)

    index = 0
    if lines and lines[0].startswith("*** Environment ID: "):
        if not allow_environment_id:
            invalid("environment ID is not declared by this grammar")
        if not lines[0][len("*** Environment ID: ") :]:
            invalid("environment ID is empty")
        index = 1
    hunks = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith(hunk_prefixes[0]):
            if not line[len(hunk_prefixes[0]) :]:
                invalid("add hunk filename is empty")
            index += 1
            first_add_line = index
            while index < len(lines) and lines[index].startswith("+"):
                index += 1
            if index == first_add_line:
                invalid("add hunk requires at least one '+' line")
        elif line.startswith(hunk_prefixes[1]):
            if not line[len(hunk_prefixes[1]) :]:
                invalid("delete hunk filename is empty")
            index += 1
        elif line.startswith(hunk_prefixes[2]):
            if not line[len(hunk_prefixes[2]) :]:
                invalid("update hunk filename is empty")
            index += 1
            if index < len(lines) and lines[index].startswith("*** Move to: "):
                if not lines[index][len("*** Move to: ") :]:
                    invalid("move destination is empty")
                index += 1
            saw_change = False
            while index < len(lines) and not is_hunk_header(lines[index]):
                change = lines[index]
                if change == "*** End of File":
                    if not saw_change:
                        invalid("end-of-file marker requires a change")
                    index += 1
                    if index < len(lines) and not is_hunk_header(lines[index]):
                        invalid("end-of-file marker must finish the update hunk")
                    break
                if change == "@@" or (
                    change.startswith("@@ ") and bool(change[len("@@ ") :])
                ):
                    saw_change = True
                    index += 1
                    continue
                if change.startswith(("+", "-", " ")):
                    saw_change = True
                    index += 1
                    continue
                invalid("update hunk contains an invalid change line")
        else:
            invalid("expected an add, delete, or update hunk")
        hunks += 1
    if not hunks:
        invalid("at least one hunk is required")


def _validate_codex_custom_tool_input(
    value: str,
    definition: str,
    *,
    field: str,
) -> None:
    normalized_definition = definition.replace("\r\n", "\n")
    if normalized_definition in {
        _CODEX_0150_APPLY_PATCH_LARK,
        _CODEX_0150_APPLY_PATCH_ENVIRONMENT_LARK,
    }:
        _validate_codex_apply_patch_input(
            value,
            field=field,
            allow_environment_id=(
                normalized_definition
                == _CODEX_0150_APPLY_PATCH_ENVIRONMENT_LARK
            ),
        )
        return
    if normalized_definition == _CODEX_0150_CODE_MODE_EXEC_LARK:
        if not value:
            raise ValueError(
                f"{field} does not satisfy the Codex 0.150.1 code-mode exec "
                "grammar: SOURCE must be non-empty"
            )
        return
    raise ValueError(
        f"{field} has no validator for the declared Codex 0.150.1 "
        "custom-tool grammar"
    )


def _custom_tool_description_for_anthropic(
    description: str,
    format_value: Any,
    *,
    field: str,
) -> str:
    """Preserve a Responses freeform contract on the Anthropic tool wire."""
    contract = (
        "This is an OpenAI Responses custom/freeform tool. Call it with exactly "
        "one JSON field named `input`; that field's string value is the complete "
        "raw custom-tool payload."
    )
    definition = _validated_custom_lark_definition(format_value, field=field)
    if definition is None:
        return "\n\n".join(part for part in (description, contract) if part)
    exact_format = json.dumps(
        {
            "type": "grammar",
            "syntax": "lark",
            "definition": definition,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    grammar_contract = (
        "The raw `input` string MUST satisfy this exact OpenAI Responses "
        "custom-tool format. The format is JSON-encoded here so every grammar "
        f"character is preserved exactly:\n{exact_format}"
    )
    return "\n\n".join(
        part for part in (description, contract, grammar_contract) if part
    )


def _custom_tool_input(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return {"input": value}
    if value is None:
        return {"input": ""}
    return {"input": json.dumps(value, ensure_ascii=False, sort_keys=True)}


def _raw_custom_tool_input(value: Any, *, strict: bool = False) -> str:
    if strict:
        if (
            not isinstance(value, dict)
            or set(value) != {"input"}
            or not isinstance(value.get("input"), str)
        ):
            raise ValueError(
                "Anthropic custom tool_use input must contain exactly one "
                "string field named input"
            )
        return value["input"]
    if isinstance(value, dict):
        raw = value.get("input")
        if isinstance(raw, str):
            return raw
        if len(value) == 1:
            return str(next(iter(value.values())))
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value or "")


def _tool_choice_to_anthropic(
    tool_choice: Any,
    *,
    strict_projection: bool = False,
) -> Any:
    if tool_choice is None:
        return None
    if isinstance(tool_choice, str):
        lowered = tool_choice.strip().lower()
        if strict_projection and tool_choice != lowered:
            raise ValueError(
                "Responses tool_choice string must be an exact lowercase value"
            )
        if lowered == "required":
            return {"type": "any"}
        if lowered in ("auto", "none"):
            return {"type": lowered}
        if strict_projection:
            raise ValueError(f"unsupported Responses tool_choice: {tool_choice!r}")
        return tool_choice
    if not isinstance(tool_choice, dict):
        if strict_projection:
            raise ValueError("Responses tool_choice must be a string or object")
        return tool_choice
    if tool_choice.get("type") == "function":
        if strict_projection and set(tool_choice) != {"type", "name"}:
            raise ValueError("Responses function tool_choice has unsupported fields")
        name = tool_choice.get("name")
        if not name and isinstance(tool_choice.get("function"), dict):
            name = tool_choice["function"].get("name")
        if name:
            if strict_projection and (
                not isinstance(name, str)
                or not name.strip()
                or name != name.strip()
            ):
                raise ValueError(
                    "Responses function tool_choice.name must be a non-empty "
                    "string without leading or trailing whitespace"
                )
            return {"type": "tool", "name": str(name)}
    if strict_projection:
        raise ValueError(f"unsupported Responses tool_choice: {tool_choice!r}")
    return tool_choice


def _anthropic_tool_choice_to_responses(
    tool_choice: Any,
    *,
    strict: bool = False,
) -> Any:
    if tool_choice is None:
        return None
    if isinstance(tool_choice, str):
        lowered = tool_choice.strip().lower()
        if strict and tool_choice != lowered:
            raise ValueError(
                "Anthropic tool_choice string must be an exact lowercase value"
            )
        if lowered in {"auto", "none", "required"}:
            return lowered
        raise ValueError(f"unsupported Anthropic tool_choice: {tool_choice!r}")
    if not isinstance(tool_choice, dict):
        raise ValueError("Anthropic tool_choice must be an object")
    type_value = tool_choice.get("type")
    choice_type = str(type_value or "").strip().lower()
    if strict and type_value != choice_type:
        raise ValueError(
            "Anthropic tool_choice.type must be an exact lowercase discriminator"
        )
    allowed_keys = {"type", "disable_parallel_tool_use"}
    if choice_type == "tool":
        allowed_keys.add("name")
    unknown = sorted(set(tool_choice) - allowed_keys)
    if unknown:
        raise ValueError(
            "Anthropic tool_choice fields cannot be projected: "
            + ", ".join(unknown)
        )
    parallel = tool_choice.get("disable_parallel_tool_use")
    if parallel is not None and not isinstance(parallel, bool):
        raise ValueError("tool_choice.disable_parallel_tool_use must be a boolean")
    if choice_type == "any":
        return "required"
    if choice_type in {"auto", "none"}:
        return choice_type
    if choice_type == "tool" and tool_choice.get("name"):
        name_value = tool_choice["name"]
        if strict and (
            not isinstance(name_value, str)
            or not name_value.strip()
            or name_value != name_value.strip()
        ):
            raise ValueError(
                "Anthropic tool_choice.name must be a non-empty string without "
                "leading or trailing whitespace"
            )
        return {"type": "function", "name": str(name_value)}
    raise ValueError(f"unsupported Anthropic tool_choice: {tool_choice!r}")


def _anthropic_tools_to_responses(
    tools: Any,
    *,
    strict: bool = False,
) -> list[dict[str, Any]]:
    if tools is None:
        return []
    if not isinstance(tools, list):
        raise ValueError("Anthropic tools must be an array")
    projected: list[dict[str, Any]] = []
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict):
            raise ValueError(f"Anthropic tools[{index}] must be an object")
        supported_fields = {
            "_ciel_openai_strict",
            "allowed_callers",
            "cache_control",
            "defer_loading",
            "description",
            "eager_input_streaming",
            "input_examples",
            "input_schema",
            "name",
            "strict",
            "type",
        }
        unknown = sorted(set(tool) - supported_fields)
        if unknown:
            raise ValueError(
                f"Anthropic tools[{index}] fields cannot be projected: "
                + ", ".join(unknown)
            )
        type_value = tool.get("type")
        tool_type = str(type_value or "").strip().lower()
        if strict and type_value not in (None, "custom"):
            raise ValueError(
                "Anthropic client tool type must be omitted or the exact "
                f"'custom' discriminator: tools[{index}].type={type_value!r}"
            )
        if tool_type not in {"", "custom"}:
            raise ValueError(
                "Anthropic server tools cannot be projected to Responses client "
                f"functions: tools[{index}].type={tool_type!r}"
            )
        name_value = tool.get("name")
        name = str(name_value or "").strip()
        if not name:
            raise ValueError(f"Anthropic tools[{index}].name is required")
        if strict and (
            not isinstance(name_value, str) or name_value != name_value.strip()
        ):
            raise ValueError(
                f"Anthropic tools[{index}].name must not have leading or "
                "trailing whitespace"
            )
        schema = tool.get("input_schema")
        if not isinstance(schema, dict):
            raise ValueError(f"Anthropic tools[{index}].input_schema must be an object")
        item: dict[str, Any] = {
            "type": "function",
            "name": name,
            "parameters": dict(schema),
        }
        if tool.get("description") is not None:
            item["description"] = str(tool.get("description") or "")
        tool_strict = tool.get("strict", tool.get("_ciel_openai_strict", False))
        if not isinstance(tool_strict, bool):
            raise ValueError(f"Anthropic tools[{index}].strict must be a boolean")
        item["strict"] = tool_strict
        allowed_callers = tool.get("allowed_callers")
        if allowed_callers is not None:
            if not isinstance(allowed_callers, list) or any(
                str(caller) != "direct" for caller in allowed_callers
            ):
                raise ValueError(
                    f"Anthropic tools[{index}].allowed_callers cannot be projected"
                )
            item["allowed_callers"] = ["direct" for _caller in allowed_callers]
        if tool.get("defer_loading") is not None:
            if not isinstance(tool["defer_loading"], bool):
                raise ValueError(
                    f"Anthropic tools[{index}].defer_loading must be a boolean"
                )
            item["defer_loading"] = tool["defer_loading"]
        unsupported = [
            key
            for key in ("cache_control", "eager_input_streaming", "input_examples")
            if key in tool and tool.get(key) is not None
        ]
        if unsupported:
            raise ValueError(
                f"Anthropic tools[{index}] fields cannot be projected: "
                + ", ".join(unsupported)
            )
        projected.append(item)
    return projected


def _anthropic_input_content(block: Any, role: str, field: str) -> dict[str, Any]:
    text_type = "input_text"
    if isinstance(block, str):
        return {"type": text_type, "text": block}
    if not isinstance(block, dict):
        raise ValueError(f"{field} must be a string or content block object")
    block_type_value = block.get("type")
    if not isinstance(block_type_value, str) or not block_type_value.strip():
        raise ValueError(f"{field}.type is required")
    block_type = block_type_value.strip()
    if block_type == "text":
        unknown = sorted(set(block) - {"type", "text", "cache_control", "citations"})
        if unknown:
            raise ValueError(f"{field} fields cannot be projected: {', '.join(unknown)}")
        if any(key in block and block.get(key) is not None for key in ("cache_control", "citations")):
            raise ValueError(f"{field} text metadata cannot be projected")
        text = block.get("text", "")
        if not isinstance(text, str):
            raise ValueError(f"{field}.text must be a string")
        return {"type": text_type, "text": text}
    source_value = block.get("source")
    if not isinstance(source_value, dict):
        raise ValueError(f"{field}.source must be an object")
    source = source_value
    if block_type == "image":
        if role != "user":
            raise ValueError(f"{field} {role} image content cannot be projected")
        unknown = sorted(set(block) - {"type", "source", "cache_control", "transformations"})
        if unknown:
            raise ValueError(f"{field} fields cannot be projected: {', '.join(unknown)}")
        if any(key in block and block.get(key) is not None for key in ("cache_control", "transformations")):
            raise ValueError(f"{field} image metadata cannot be projected")
        source_type = str(source.get("type") or "")
        if source_type == "base64":
            expected_source_keys = {"type", "data", "media_type"}
        elif source_type == "url":
            expected_source_keys = {"type", "url"}
        elif source_type == "file":
            expected_source_keys = {"type", "file_id"}
        else:
            expected_source_keys = {"type"}
        extra_source = sorted(set(source) - expected_source_keys)
        if extra_source:
            raise ValueError(f"{field}.source fields cannot be projected: {', '.join(extra_source)}")
        if source_type == "base64":
            data = source.get("data")
            media_type = source.get("media_type")
            if not isinstance(data, str) or not data or not isinstance(media_type, str) or not media_type:
                raise ValueError(f"{field} image base64 source requires media_type and data")
            if media_type not in _ANTHROPIC_IMAGE_MEDIA_TYPES:
                raise ValueError(
                    f"{field} image media_type is unsupported: {media_type!r}"
                )
            image_url = f"data:{media_type};base64,{data}"
        elif source_type == "url":
            url = source.get("url")
            if not isinstance(url, str) or not url:
                raise ValueError(f"{field} image URL source requires url")
            image_url = url
        elif source_type == "file":
            file_id = source.get("file_id")
            if not isinstance(file_id, str) or not file_id:
                raise ValueError(f"{field} image file source requires file_id")
            return {"type": "input_image", "file_id": file_id, "detail": "auto"}
        else:
            raise ValueError(f"{field} image source cannot be projected")
        return {"type": "input_image", "image_url": image_url, "detail": "auto"}
    if block_type == "document":
        if role != "user":
            raise ValueError(f"{field} {role} document content cannot be projected")
        unknown = sorted(set(block) - {"type", "source", "cache_control", "citations", "context", "title"})
        if unknown:
            raise ValueError(f"{field} fields cannot be projected: {', '.join(unknown)}")
        if any(key in block and block.get(key) is not None for key in ("cache_control", "citations", "context")):
            raise ValueError(f"{field} document metadata cannot be projected")
        source_type = str(source.get("type") or "")
        if source_type == "base64":
            expected_source_keys = {"type", "data", "media_type"}
        elif source_type == "text":
            expected_source_keys = {"type", "data", "media_type"}
        elif source_type == "url":
            expected_source_keys = {"type", "url"}
        elif source_type == "file":
            expected_source_keys = {"type", "file_id"}
        else:
            expected_source_keys = {"type"}
        extra_source = sorted(set(source) - expected_source_keys)
        if extra_source:
            raise ValueError(f"{field}.source fields cannot be projected: {', '.join(extra_source)}")
        title = block.get("title")
        if title is not None and not isinstance(title, str):
            raise ValueError(f"{field}.title must be a string")
        if source_type == "base64":
            data = source.get("data")
            media_type = source.get("media_type")
            if not isinstance(data, str) or not data or not isinstance(media_type, str) or not media_type:
                raise ValueError(f"{field} document base64 source requires media_type and data")
            if media_type != "application/pdf":
                raise ValueError(
                    f"{field} base64 document media_type must be 'application/pdf'"
                )
            return {
                "type": "input_file",
                "file_data": f"data:{media_type};base64,{data}",
                "filename": title or "document",
            }
        if source_type == "text":
            data = source.get("data")
            media_type = source.get("media_type")
            if media_type != "text/plain" or not isinstance(data, str):
                raise ValueError(
                    f"{field} text document requires text/plain string data"
                )
            encoded = base64.b64encode(data.encode("utf-8")).decode("ascii")
            return {
                "type": "input_file",
                "file_data": f"data:text/plain;base64,{encoded}",
                "filename": title or "document.txt",
            }
        if source_type == "url":
            url = source.get("url")
            if not isinstance(url, str) or not url:
                raise ValueError(f"{field} document URL source requires url")
            projected = {"type": "input_file", "file_url": url}
            if title:
                projected["filename"] = title
            return projected
        if source_type == "file":
            file_id = source.get("file_id")
            if not isinstance(file_id, str) or not file_id:
                raise ValueError(f"{field} document file source requires file_id")
            projected = {"type": "input_file", "file_id": file_id}
            if title:
                projected["filename"] = title
            return projected
        raise ValueError(f"{field} document source cannot be projected")
    raise ValueError(f"{field} content type cannot be projected: {block_type!r}")


def _anthropic_result_output(
    block: dict[str, Any], *, strict: bool = False
) -> str | list[dict[str, Any]]:
    unknown = sorted(
        set(block)
        - {"type", "tool_use_id", "content", "is_error", "cache_control", "toolset_name"}
    )
    if unknown:
        raise ValueError(
            "tool_result fields cannot be projected: " + ", ".join(unknown)
        )
    if block.get("cache_control") is not None:
        raise ValueError("tool_result metadata cannot be projected")
    content = block.get("content")
    if isinstance(content, str):
        output: str | list[dict[str, Any]] = content
    elif isinstance(content, list):
        parts: list[dict[str, Any]] = []
        for index, item in enumerate(content):
            projected = _anthropic_input_content(
                item,
                "user",
                f"tool_result.content[{index}]",
            )
            parts.append(projected)
        output = parts
    elif content is None:
        output = ""
    else:
        raise ValueError("tool_result.content must be text or an array of content blocks")
    if block.get("is_error") not in (None, True, False):
        raise ValueError("tool_result.is_error must be a boolean")
    if block.get("is_error") is True:
        if strict:
            raise ValueError(
                "Anthropic tool_result.is_error cannot be projected to Responses"
            )
        if isinstance(output, str):
            return f"[tool_error]\n{output}" if output else "[tool_error]"
        return [{"type": "input_text", "text": "[tool_error]"}, *output]
    return output


def _anthropic_system_text(system: Any) -> str:
    if system is None:
        return ""
    if isinstance(system, str):
        return system
    if not isinstance(system, list):
        raise ValueError("Anthropic system must be text or an array of text blocks")
    parts: list[str] = []
    for index, block in enumerate(system):
        projected = _anthropic_input_content(block, "user", f"system[{index}]")
        if projected.get("type") != "input_text":
            raise ValueError("Anthropic system supports only text across Responses")
        text = str(projected.get("text") or "")
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def _reasoning_to_redacted_block(item: dict[str, Any]) -> dict[str, Any] | None:
    if not str(item.get("encrypted_content") or "").strip():
        return None
    payload = {
        key: item[key]
        for key in ("type", "id", "encrypted_content", "summary")
        if item.get(key) is not None
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    return {"type": "redacted_thinking", "data": _REASONING_ENVELOPE_PREFIX + encoded}


def _reasoning_from_redacted_block(block: dict[str, Any]) -> dict[str, Any] | None:
    data = str(block.get("data") or "")
    if not data.startswith(_REASONING_ENVELOPE_PREFIX):
        return None
    encoded = data[len(_REASONING_ENVELOPE_PREFIX) :]
    try:
        decoded = base64.urlsafe_b64decode(encoded.encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("type") != "reasoning":
        return None
    if not str(payload.get("encrypted_content") or "").strip():
        return None
    return {
        key: payload[key]
        for key in ("type", "id", "encrypted_content", "summary")
        if payload.get(key) is not None
    }


def _commentary_to_redacted_block(item: dict[str, Any]) -> dict[str, Any]:
    encoded = base64.urlsafe_b64encode(
        json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    return {
        "type": "redacted_thinking",
        "data": _COMMENTARY_ENVELOPE_PREFIX + encoded,
    }


def _commentary_from_redacted_block(
    block: dict[str, Any],
) -> dict[str, Any] | None:
    data = str(block.get("data") or "")
    if not data.startswith(_COMMENTARY_ENVELOPE_PREFIX):
        return None
    encoded = data[len(_COMMENTARY_ENVELOPE_PREFIX) :]
    try:
        payload = json.loads(
            base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8")
        )
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("type") != "message"
        or payload.get("role") != "assistant"
        or payload.get("phase") != "commentary"
        or not isinstance(payload.get("content"), list)
    ):
        return None
    return payload


def _commentary_visible_input_blocks(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the visible text companion carried beside a commentary envelope."""

    allowed_item_fields = {"type", "id", "role", "status", "content", "phase"}
    unknown_item_fields = sorted(set(item) - allowed_item_fields)
    if unknown_item_fields:
        raise ValueError(
            "Responses commentary envelope fields cannot be projected: "
            + ", ".join(unknown_item_fields)
        )
    item_id = item.get("id")
    if not isinstance(item_id, str) or not item_id.strip():
        raise ValueError("Responses commentary envelope requires a non-empty id")
    if item.get("status") != "completed":
        raise ValueError("Responses commentary envelope must be completed")
    content = item.get("content")
    if not isinstance(content, list):
        raise ValueError("Responses commentary envelope content must be an array")
    visible: list[dict[str, Any]] = []
    for index, block in enumerate(content):
        if not isinstance(block, dict) or block.get("type") != "output_text":
            raise ValueError(
                "Responses commentary envelope supports output_text content only"
            )
        if set(block) - {"type", "text", "annotations", "logprobs"}:
            raise ValueError(
                f"Responses commentary envelope content[{index}] has invalid fields"
            )
        if block.get("annotations") not in (None, []):
            raise ValueError(
                "Responses commentary annotations cannot be projected to Anthropic"
            )
        if block.get("logprobs") not in (None, []):
            raise ValueError(
                "Responses commentary logprobs cannot be projected to Anthropic"
            )
        text = block.get("text")
        if not isinstance(text, str):
            raise ValueError("Responses commentary text must be a string")
        if text:
            visible.append({"type": "input_text", "text": text})
    return visible


def _anthropic_reasoning_envelope(block: dict[str, Any]) -> str:
    encoded = base64.urlsafe_b64encode(
        json.dumps(block, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    return _ANTHROPIC_REASONING_ENVELOPE_PREFIX + encoded


def _anthropic_reasoning_from_envelope(item: dict[str, Any]) -> dict[str, Any] | None:
    value = item.get("encrypted_content")
    if not isinstance(value, str) or not value.startswith(
        _ANTHROPIC_REASONING_ENVELOPE_PREFIX
    ):
        return None
    encoded = value[len(_ANTHROPIC_REASONING_ENVELOPE_PREFIX) :]
    try:
        payload = json.loads(
            base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8")
        )
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    block_type = payload.get("type")
    if block_type == "thinking":
        if (
            set(payload) != {"type", "thinking", "signature"}
            or not isinstance(payload.get("thinking"), str)
            or not isinstance(payload.get("signature"), str)
            or not payload["signature"]
        ):
            return None
    elif block_type == "redacted_thinking":
        if (
            set(payload) != {"type", "data"}
            or not isinstance(payload.get("data"), str)
            or not payload["data"]
        ):
            return None
    else:
        return None
    return payload


def strip_openai_responses_reasoning_envelopes(
    body: dict[str, Any],
) -> dict[str, Any]:
    """Remove bridge-only reasoning blocks before switching wire families."""

    messages = body.get("messages")
    if not isinstance(messages, list):
        return body
    changed = False
    projected_messages: list[Any] = []
    for message in messages:
        if not isinstance(message, dict) or not isinstance(message.get("content"), list):
            projected_messages.append(message)
            continue
        content = []
        for block in message["content"]:
            if (
                isinstance(block, dict)
                and block.get("type") == "redacted_thinking"
                and str(block.get("data") or "").startswith(
                    (_REASONING_ENVELOPE_PREFIX, _COMMENTARY_ENVELOPE_PREFIX)
                )
            ):
                changed = True
                continue
            content.append(block)
        projected_messages.append({**message, "content": content})
    return {**body, "messages": projected_messages} if changed else body


def _anthropic_responses_controls(body: dict[str, Any]) -> dict[str, Any]:
    supported = {
        "cache_control",
        "container",
        "inference_geo",
        "max_completion_tokens",
        "max_output_tokens",
        "max_tokens",
        "messages",
        "metadata",
        "model",
        "output_config",
        "reasoning_effort",
        "service_tier",
        "stop_sequences",
        "stream",
        "system",
        "temperature",
        "thinking",
        "tool_choice",
        "tools",
        "top_k",
        "top_p",
    }
    unknown = sorted(str(key) for key in set(body) - supported)
    if unknown:
        raise ValueError(
            "Anthropic fields cannot be projected to Responses: "
            + ", ".join(unknown)
        )
    for key in ("cache_control", "container", "inference_geo", "top_k"):
        if body.get(key) is not None:
            raise ValueError(f"Anthropic {key} cannot be projected to Responses")

    controls: dict[str, Any] = {}
    output_config_value = body.get("output_config")
    if output_config_value is None:
        output_config: dict[str, Any] = {}
    elif isinstance(output_config_value, dict):
        output_config = output_config_value
    else:
        raise ValueError("Anthropic output_config must be an object")
    extra_output = sorted(set(output_config) - {"effort", "format"})
    if extra_output:
        raise ValueError(
            "Anthropic output_config fields cannot be projected: "
            + ", ".join(extra_output)
        )
    format_value = output_config.get("format")
    if format_value is not None:
        if (
            not isinstance(format_value, dict)
            or format_value.get("type") != "json_schema"
            or not isinstance(format_value.get("schema"), dict)
            or set(format_value) - {"type", "schema"}
        ):
            raise ValueError(
                "Anthropic output_config.format must be a json_schema object"
            )
        controls["text"] = {
            "format": {
                "type": "json_schema",
                "name": "anthropic_output",
                "schema": dict(format_value["schema"]),
                "strict": True,
            }
        }

    direct_effort = body.get("reasoning_effort")
    output_effort = output_config.get("effort")
    if direct_effort is not None and output_effort is not None:
        if direct_effort != output_effort:
            raise ValueError(
                "reasoning_effort conflicts with output_config.effort"
            )
    effort = direct_effort if direct_effort is not None else output_effort
    if effort is not None:
        if not isinstance(effort, str):
            raise ValueError("Anthropic effort must be a string")
        effort = effort.strip().lower()
        if effort not in {"none", "low", "medium", "high", "xhigh", "max"}:
            raise ValueError(f"unsupported Anthropic effort: {effort!r}")
    thinking_value = body.get("thinking")
    reasoning_summary: str | None = None
    if thinking_value is not None:
        if not isinstance(thinking_value, dict):
            raise ValueError("Anthropic thinking must be an object")
        extra_thinking = sorted(
            set(thinking_value) - {"budget_tokens", "display", "type"}
        )
        if extra_thinking:
            raise ValueError(
                "Anthropic thinking fields cannot be projected: "
                + ", ".join(extra_thinking)
            )
        thinking_type = str(thinking_value.get("type") or "").strip().lower()
        if thinking_type == "disabled":
            if set(thinking_value) != {"type"}:
                raise ValueError(
                    "disabled thinking supports only the type field across Responses"
                )
            if effort not in (None, "none"):
                raise ValueError("thinking disabled conflicts with requested effort")
            effort = "none"
        elif thinking_type == "adaptive":
            if set(thinking_value) - {"type", "display"}:
                raise ValueError(
                    "adaptive thinking supports only type/display across Responses"
                )
            display = thinking_value.get("display", "summarized")
            if display not in {"summarized", "omitted"}:
                raise ValueError(
                    "adaptive thinking display must be 'summarized' or 'omitted'"
                )
            if effort == "none":
                raise ValueError(
                    "adaptive thinking conflicts with effort='none'"
                )
            reasoning_summary = "auto" if display == "summarized" else None
        elif thinking_type == "enabled":
            raise ValueError(
                "token-budget thinking cannot be projected to Responses reasoning"
            )
        else:
            raise ValueError(f"unsupported Anthropic thinking.type: {thinking_type!r}")
    if effort is not None or reasoning_summary is not None:
        reasoning: dict[str, Any] = {}
        if effort is not None:
            reasoning["effort"] = effort
        if reasoning_summary is not None:
            reasoning["summary"] = reasoning_summary
        controls["reasoning"] = reasoning

    service_tier = body.get("service_tier")
    if service_tier is not None:
        if not isinstance(service_tier, str):
            raise ValueError("Anthropic service_tier must be a string")
        tiers = {"auto": "auto", "standard_only": "default"}
        normalized_tier = str(service_tier).strip().lower()
        if normalized_tier not in tiers:
            raise ValueError(f"unsupported Anthropic service_tier: {service_tier!r}")
        controls["service_tier"] = tiers[normalized_tier]

    metadata_value = body.get("metadata")
    if metadata_value is not None:
        if not isinstance(metadata_value, dict):
            raise ValueError("Anthropic metadata must be an object")
        extra_metadata = sorted(set(metadata_value) - {"user_id"})
        if extra_metadata:
            raise ValueError(
                "Anthropic metadata fields cannot be projected: "
                + ", ".join(extra_metadata)
            )
        user_id_value = metadata_value.get("user_id")
        if user_id_value is not None and not isinstance(user_id_value, str):
            raise ValueError("Anthropic metadata.user_id must be a string")
        user_id = user_id_value or ""
        if len(user_id) > 64:
            raise ValueError("Anthropic metadata.user_id exceeds Responses limit 64")
        if user_id:
            controls["safety_identifier"] = user_id
    return controls


def anthropic_messages_to_openai_responses(
    body: dict[str, Any],
    fallback_model: str = "model",
    *,
    strict: bool = False,
) -> dict[str, Any]:
    """Project one Anthropic Messages request onto the Responses wire."""

    if strict:
        max_tokens = body.get("max_tokens")
        if (
            isinstance(max_tokens, bool)
            or not isinstance(max_tokens, int)
            or max_tokens <= 0
        ):
            raise ValueError(
                "Anthropic max_tokens is required and must be a positive integer"
            )
    controls = _anthropic_responses_controls(body)
    if body.get("stop_sequences") not in (None, []):
        raise ValueError(
            "stop_sequences is not supported when the selected model uses "
            "the Responses API"
        )

    input_items: list[dict[str, Any]] = []
    messages = body.get("messages")
    if not isinstance(messages, list):
        raise ValueError("Anthropic messages must be an array")
    pending_tool_calls: dict[str, str | None] = {}
    seen_tool_calls: set[str] = set()
    for message_index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise ValueError(f"Anthropic messages[{message_index}] must be an object")
        unknown_message = sorted(set(message) - {"role", "content"})
        if unknown_message:
            raise ValueError(
                f"Anthropic messages[{message_index}] fields cannot be projected: "
                + ", ".join(unknown_message)
            )
        role_value = message.get("role")
        if not isinstance(role_value, str) or not role_value.strip():
            raise ValueError(f"Anthropic messages[{message_index}].role is required")
        role = role_value.strip().lower()
        if role not in {"user", "assistant", "system"}:
            raise ValueError(
                f"Anthropic messages[{message_index}].role is unsupported: {role!r}"
            )
        if "content" not in message:
            raise ValueError(f"Anthropic messages[{message_index}].content is required")
        content = message["content"]
        if not isinstance(content, (str, list)):
            raise ValueError(
                f"Anthropic messages[{message_index}].content must be text or an array"
            )
        blocks = content if isinstance(content, list) else [content]
        expected_results = set(pending_tool_calls)
        if expected_results:
            first_type = (
                str(blocks[0].get("type") or "text")
                if blocks and isinstance(blocks[0], dict)
                else "text"
            )
            if role != "user" or first_type != "tool_result":
                raise ValueError(
                    "Anthropic tool_use blocks require matching tool_result "
                    "blocks at the start of the immediately following user message"
                )
        result_prefix_open = bool(expected_results)
        pending_content: list[dict[str, Any]] = []

        def flush_message() -> None:
            if pending_content:
                input_items.append(
                    {"type": "message", "role": role, "content": list(pending_content)}
                )
                pending_content.clear()

        for block_index, block in enumerate(blocks):
            block_type = (
                str(block.get("type") or "text")
                if isinstance(block, dict)
                else "text"
            )
            if expected_results and block_type != "tool_result":
                result_prefix_open = False
            if block_type == "tool_use" and isinstance(block, dict):
                unknown_tool_use = sorted(
                    set(block)
                    - {
                        "type",
                        "id",
                        "name",
                        "input",
                        "caller",
                        "toolset_name",
                    }
                )
                if unknown_tool_use:
                    raise ValueError(
                        "Anthropic tool_use fields cannot be projected: "
                        + ", ".join(unknown_tool_use)
                    )
                if role != "assistant":
                    raise ValueError("Anthropic tool_use requires assistant role")
                call_id_value = block.get("id")
                name_value = block.get("name")
                call_id = str(call_id_value or "").strip()
                name = str(name_value or "").strip()
                if not call_id or not name or not isinstance(block.get("input"), dict):
                    raise ValueError(
                        "Anthropic tool_use requires non-empty id/name and object input"
                    )
                if strict and (
                    not isinstance(call_id_value, str)
                    or call_id_value != call_id
                    or not isinstance(name_value, str)
                    or name_value != name
                ):
                    raise ValueError(
                        "Anthropic tool_use id/name must not have leading or "
                        "trailing whitespace"
                    )
                if call_id in seen_tool_calls:
                    raise ValueError(f"Anthropic tool_use id is duplicated: {call_id}")
                seen_tool_calls.add(call_id)
                flush_message()
                function_call: dict[str, Any] = {
                    "type": "function_call",
                    "call_id": call_id,
                    "name": name,
                    "arguments": json.dumps(
                        block["input"],
                        ensure_ascii=False,
                    ),
                }
                caller = block.get("caller")
                if caller is not None:
                    if caller == {"type": "direct"}:
                        function_call["caller"] = {"type": "direct"}
                    elif (
                        isinstance(caller, dict)
                        and str(caller.get("type") or "").startswith(
                            "code_execution_"
                        )
                        and isinstance(caller.get("tool_id"), str)
                        and caller["tool_id"]
                        and set(caller) == {"type", "tool_id"}
                    ):
                        function_call["caller"] = {
                            "type": "program",
                            "caller_id": caller["tool_id"],
                        }
                    else:
                        raise ValueError(
                            "Anthropic tool_use caller cannot be projected"
                        )
                toolset_name = block.get("toolset_name")
                if toolset_name is not None:
                    if not isinstance(toolset_name, str) or not toolset_name:
                        raise ValueError(
                            "Anthropic tool_use toolset_name must be a "
                            "non-empty string"
                        )
                    if strict and toolset_name != toolset_name.strip():
                        raise ValueError(
                            "Anthropic tool_use toolset_name must not have "
                            "leading or trailing whitespace"
                        )
                    function_call["namespace"] = toolset_name
                pending_tool_calls[call_id] = toolset_name
                input_items.append(function_call)
                continue
            if block_type == "redacted_thinking" and isinstance(block, dict):
                if set(block) != {"type", "data"}:
                    raise ValueError(
                        "Anthropic redacted_thinking fields cannot be projected"
                    )
                if role != "assistant":
                    raise ValueError(
                        "Anthropic redacted_thinking requires assistant role"
                    )
                commentary = _commentary_from_redacted_block(block)
                if commentary is not None:
                    visible = _commentary_visible_input_blocks(commentary)
                    if len(pending_content) < len(visible) or (
                        visible and pending_content[-len(visible) :] != visible
                    ):
                        raise ValueError(
                            "Anthropic commentary envelope is missing its matching "
                            "visible text"
                        )
                    if visible:
                        del pending_content[-len(visible) :]
                    flush_message()
                    input_items.append(commentary)
                    continue
                flush_message()
                reasoning = _reasoning_from_redacted_block(block)
                if reasoning is None:
                    raise ValueError(
                        "Anthropic redacted_thinking is not a Ciel Responses envelope"
                    )
                input_items.append(reasoning)
                continue
            if block_type == "thinking":
                raise ValueError(
                    "Anthropic signed thinking history cannot be projected to Responses"
                )
            if block_type == "tool_result" and isinstance(block, dict):
                if role != "user":
                    raise ValueError("Anthropic tool_result requires user role")
                if expected_results and not result_prefix_open:
                    raise ValueError(
                        "Anthropic tool_result blocks must precede other user content"
                    )
                call_id_value = block.get("tool_use_id")
                call_id = str(call_id_value or "").strip()
                if not call_id:
                    raise ValueError("Anthropic tool_result requires tool_use_id")
                if strict and (
                    not isinstance(call_id_value, str) or call_id_value != call_id
                ):
                    raise ValueError(
                        "Anthropic tool_result tool_use_id must not have leading "
                        "or trailing whitespace"
                    )
                if call_id not in pending_tool_calls:
                    raise ValueError(
                        "Anthropic tool_result has no matching immediately preceding "
                        f"tool_use: {call_id}"
                    )
                expected_toolset_name = pending_tool_calls.pop(call_id)
                result_toolset_name = block.get("toolset_name")
                if result_toolset_name != expected_toolset_name:
                    raise ValueError(
                        "Anthropic tool_result toolset_name does not match its "
                        f"tool_use: {call_id}"
                    )
                flush_message()
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": _anthropic_result_output(block, strict=strict),
                    }
                )
                continue
            projected = _anthropic_input_content(
                block,
                role,
                f"messages[{message_index}].content[{block_index}]",
            )
            pending_content.append(projected)
        flush_message()
        unresolved_results = expected_results & set(pending_tool_calls)
        if unresolved_results:
            raise ValueError(
                "Anthropic tool_result blocks are missing for: "
                + ", ".join(sorted(unresolved_results))
            )

    if pending_tool_calls:
        raise ValueError(
            "Anthropic tool_use blocks require matching tool_result blocks: "
            + ", ".join(sorted(pending_tool_calls))
        )

    model_value = body.get("model")
    if model_value is not None and (
        not isinstance(model_value, str) or not model_value.strip()
    ):
        raise ValueError("Anthropic model must be a non-empty string")
    stream_value = body.get("stream", False)
    if not isinstance(stream_value, bool):
        raise ValueError("Anthropic stream must be a boolean")
    request: dict[str, Any] = {
        "model": model_value or fallback_model or "model",
        "input": input_items,
        "include": ["reasoning.encrypted_content"],
        "store": False,
        "stream": stream_value,
        **controls,
    }
    instructions = _anthropic_system_text(body.get("system"))
    if instructions:
        request["instructions"] = instructions
    tools = _anthropic_tools_to_responses(body.get("tools"), strict=strict)
    if tools:
        request["tools"] = tools
    tool_choice = _anthropic_tool_choice_to_responses(
        body.get("tool_choice"),
        strict=strict,
    )
    if tool_choice is not None:
        request["tool_choice"] = tool_choice
    if isinstance(body.get("tool_choice"), dict) and body["tool_choice"].get(
        "disable_parallel_tool_use"
    ) is not None:
        request["parallel_tool_calls"] = not bool(
            body["tool_choice"]["disable_parallel_tool_use"]
        )
    max_fields = [
        key
        for key in ("max_tokens", "max_output_tokens", "max_completion_tokens")
        if body.get(key) is not None
    ]
    max_values: list[int] = []
    for key in max_fields:
        value = body[key]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"Anthropic {key} must be a positive integer")
        max_values.append(value)
    if len(set(max_values)) > 1:
        raise ValueError("Anthropic max token fields conflict")
    max_tokens = max_values[0] if max_values else None
    if max_tokens:
        request["max_output_tokens"] = max_tokens
    for key in ("temperature", "top_p"):
        if body.get(key) is not None:
            value = body[key]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"Anthropic {key} must be a finite number")
            upper = 1.0
            if value < 0 or value > upper:
                raise ValueError(f"Anthropic {key} must be between 0 and {upper:g}")
            request[key] = value
    return request


def _responses_usage_to_anthropic(
    usage_value: Any, *, strict: bool = False
) -> dict[str, Any]:
    usage = usage_value if isinstance(usage_value, dict) else {}
    if strict:
        if not isinstance(usage_value, dict):
            raise ValueError("Responses upstream usage is required")
        required_usage = {
            "input_tokens",
            "input_tokens_details",
            "output_tokens",
            "output_tokens_details",
            "total_tokens",
        }
        if not required_usage.issubset(usage):
            missing = ", ".join(sorted(required_usage - set(usage)))
            raise ValueError(f"Responses upstream usage fields are missing: {missing}")
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            value = usage.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(
                    f"Responses upstream usage.{key} must be a non-negative integer"
                )
        if usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"]:
            raise ValueError("Responses upstream usage.total_tokens is inconsistent")
    input_tokens = _positive_int(usage.get("input_tokens")) or 0
    output_tokens = _positive_int(usage.get("output_tokens")) or 0
    input_details = (
        usage.get("input_tokens_details")
        if isinstance(usage.get("input_tokens_details"), dict)
        else {}
    )
    if strict and set(input_details) != {"cache_write_tokens", "cached_tokens"}:
        raise ValueError(
            "Responses upstream usage.input_tokens_details requires "
            "cached_tokens and cache_write_tokens"
        )
    cached_value = input_details.get("cached_tokens", 0)
    cache_write_value = input_details.get("cache_write_tokens", 0)
    if strict and (
        isinstance(cached_value, bool)
        or not isinstance(cached_value, int)
        or cached_value < 0
        or isinstance(cache_write_value, bool)
        or not isinstance(cache_write_value, int)
        or cache_write_value < 0
        or cached_value + cache_write_value > input_tokens
    ):
        raise ValueError(
            "Responses upstream usage.input_tokens_details token counts are invalid"
        )
    cached_tokens = _positive_int(input_details.get("cached_tokens")) or 0
    cache_write_tokens = (
        _positive_int(input_details.get("cache_write_tokens")) or 0
    )
    output_details = (
        usage.get("output_tokens_details")
        if isinstance(usage.get("output_tokens_details"), dict)
        else {}
    )
    if strict and set(output_details) != {"reasoning_tokens"}:
        raise ValueError(
            "Responses upstream usage.output_tokens_details requires reasoning_tokens"
        )
    reasoning_value = output_details.get("reasoning_tokens", 0)
    if strict and (
        isinstance(reasoning_value, bool)
        or not isinstance(reasoning_value, int)
        or reasoning_value < 0
        or reasoning_value > output_tokens
    ):
        raise ValueError(
            "Responses upstream usage.output_tokens_details.reasoning_tokens is invalid"
        )
    reasoning_tokens = _positive_int(reasoning_value) or 0
    projected = {
        "input_tokens": max(
            0, input_tokens - cached_tokens - cache_write_tokens
        ),
        "output_tokens": output_tokens,
        "output_tokens_details": {"thinking_tokens": reasoning_tokens},
    }
    if cached_tokens:
        projected["cache_read_input_tokens"] = cached_tokens
    if cache_write_tokens:
        projected["cache_creation_input_tokens"] = cache_write_tokens
    return projected


def openai_response_to_anthropic_message(
    response: dict[str, Any],
    fallback_model: str = "model",
    *,
    strict: bool = False,
) -> dict[str, Any]:
    """Project one completed Responses object onto an Anthropic message."""

    if isinstance(response.get("error"), dict):
        error = response["error"]
        raise ValueError(str(error.get("message") or error.get("type") or "upstream error"))
    if strict and response.get("error") is not None:
        raise ValueError("Responses upstream response.error must be null")
    if strict:
        allowed_response_fields = {
            "background",
            "completed_at",
            "conversation",
            "created_at",
            "error",
            "id",
            "incomplete_details",
            "instructions",
            "max_output_tokens",
            "max_tool_calls",
            "metadata",
            "model",
            "moderation",
            "object",
            "output",
            "parallel_tool_calls",
            "previous_response_id",
            "prompt",
            "prompt_cache_key",
            "prompt_cache_options",
            "prompt_cache_retention",
            "reasoning",
            "safety_identifier",
            "service_tier",
            "status",
            "temperature",
            "text",
            "tool_choice",
            "tools",
            "top_logprobs",
            "top_p",
            "truncation",
            "usage",
            "user",
        }
        unknown_response_fields = sorted(set(response) - allowed_response_fields)
        if unknown_response_fields:
            raise ValueError(
                "Responses upstream response fields cannot be projected: "
                + ", ".join(unknown_response_fields)
            )
        for field in ("conversation", "metadata", "moderation", "prompt"):
            if response.get(field) not in (None, {}):
                raise ValueError(
                    f"Responses upstream response.{field} cannot be projected"
                )
        if response.get("background") not in (None, False):
            raise ValueError(
                "Responses upstream background response cannot be projected"
            )
        if response.get("previous_response_id") is not None:
            raise ValueError(
                "Responses upstream previous_response_id cannot be projected"
            )
        response_id_value = response.get("id")
        if not isinstance(response_id_value, str) or not response_id_value.strip():
            raise ValueError("Responses upstream response.id is required")
        if response.get("object") != "response":
            raise ValueError("Responses upstream response.object must be 'response'")
        response_model = response.get("model")
        if not isinstance(response_model, str) or not response_model.strip():
            raise ValueError("Responses upstream response.model is required")
        projected_usage = _responses_usage_to_anthropic(
            response.get("usage"), strict=True
        )
    else:
        projected_usage = _responses_usage_to_anthropic(response.get("usage"))
    status = str(response.get("status") or "")
    if status not in {"completed", "incomplete"}:
        raise ValueError(f"Responses upstream returned non-terminal status: {status}")
    incomplete_value = response.get("incomplete_details")
    if status == "completed" and incomplete_value not in (None, {}):
        raise ValueError(
            "Responses completed status cannot include incomplete_details"
        )
    if status == "incomplete" and not isinstance(incomplete_value, dict):
        raise ValueError(
            "Responses incomplete status requires incomplete_details with a "
            "supported reason"
        )
    content: list[dict[str, Any]] = []
    saw_tool_use = False
    saw_refusal = False
    output_value = response.get("output")
    if not isinstance(output_value, list):
        raise ValueError("Responses upstream output must be an array")
    output = output_value
    for item_index, item in enumerate(output):
        if not isinstance(item, dict):
            raise ValueError(f"Responses upstream output[{item_index}] must be an object")
        item_type = str(item.get("type") or "")
        if item_type == "message":
            if strict:
                allowed_message_fields = {
                    "content",
                    "id",
                    "phase",
                    "role",
                    "status",
                    "type",
                }
                unknown_message_fields = sorted(
                    set(item) - allowed_message_fields
                )
                if unknown_message_fields:
                    raise ValueError(
                        "Responses upstream message fields cannot be projected: "
                        + ", ".join(unknown_message_fields)
                    )
                item_id = item.get("id")
                if not isinstance(item_id, str) or not item_id.strip():
                    raise ValueError("Responses upstream message.id is required")
            if item.get("role") != "assistant":
                raise ValueError("Responses upstream message role must be assistant")
            if strict and "status" not in item:
                raise ValueError("Responses upstream message.status is required")
            item_status = str(item.get("status") or ("" if strict else status))
            valid_item_status = item_status == "completed" or (
                status == "incomplete" and item_status == "incomplete"
            )
            if not valid_item_status:
                raise ValueError(
                    "Responses upstream returned non-terminal message item: "
                    f"{item_status or 'missing'}"
                )
            phase = item.get("phase")
            if phase not in (None, "commentary", "final_answer"):
                raise ValueError(
                    f"Responses upstream message phase is unsupported: {phase!r}"
                )
            raw_content = item.get("content")
            if not isinstance(raw_content, list):
                raise ValueError("Responses upstream message content must be an array")
            message_content: list[dict[str, Any]] = []
            message_saw_refusal = False
            for block_index, block in enumerate(raw_content):
                if not isinstance(block, dict):
                    raise ValueError(
                        "Responses upstream message content block must be an object"
                    )
                block_type = block.get("type")
                if block_type == "refusal":
                    if set(block) - {"type", "refusal"}:
                        raise ValueError(
                            "Responses upstream refusal contains unsupported fields"
                        )
                    refusal = block.get("refusal")
                    if not isinstance(refusal, str):
                        raise ValueError("Responses upstream refusal must be a string")
                    saw_refusal = True
                    message_saw_refusal = True
                    text = refusal
                elif block_type == "output_text":
                    if set(block) - {"type", "text", "annotations", "logprobs"}:
                        raise ValueError(
                            "Responses upstream output_text contains unsupported fields"
                        )
                    if block.get("annotations") not in (None, []):
                        raise ValueError(
                            "Responses upstream output_text annotations cannot be "
                            "projected to Anthropic citations"
                        )
                    if block.get("logprobs") not in (None, []):
                        raise ValueError(
                            "Responses upstream output_text logprobs cannot be projected"
                        )
                    text_value = block.get("text")
                    if not isinstance(text_value, str):
                        raise ValueError(
                            "Responses upstream output_text.text must be a string"
                        )
                    text = text_value
                else:
                    raise ValueError(
                        "Responses upstream message content type is unsupported: "
                        f"output[{item_index}].content[{block_index}]={block_type!r}"
                    )
                if text:
                    message_content.append({"type": "text", "text": text})
            content.extend(message_content)
            if phase == "commentary":
                if message_saw_refusal:
                    raise ValueError(
                        "Responses commentary phase cannot contain refusal content"
                    )
                _commentary_visible_input_blocks(item)
                content.append(_commentary_to_redacted_block(item))
        elif item_type == "reasoning":
            item_status = item.get("status")
            if item_status not in (None, "completed"):
                raise ValueError(
                    f"Responses upstream reasoning item is non-terminal: {item_status}"
                )
            redacted = _reasoning_to_redacted_block(item)
            if redacted is None:
                raise ValueError(
                    "Responses upstream reasoning item lacks encrypted_content"
                )
            content.append(redacted)
        elif item_type in {"function_call", "custom_tool_call"}:
            if strict:
                payload_field = (
                    "arguments" if item_type == "function_call" else "input"
                )
                allowed_tool_fields = {
                    "call_id",
                    "caller",
                    "id",
                    "name",
                    "namespace",
                    "status",
                    "type",
                    payload_field,
                }
                unknown_tool_fields = sorted(set(item) - allowed_tool_fields)
                if unknown_tool_fields:
                    raise ValueError(
                        "Responses upstream tool call fields cannot be projected: "
                        + ", ".join(unknown_tool_fields)
                    )
            if strict:
                item_id = item.get("id")
                if not isinstance(item_id, str) or not item_id.strip():
                    raise ValueError("Responses upstream tool call.id is required")
                if "status" not in item:
                    raise ValueError(
                        "Responses upstream tool call.status is required"
                    )
            item_status = str(
                item.get("status") or ("" if strict else "completed")
            )
            if item_status != "completed":
                raise ValueError(
                    f"Responses upstream returned incomplete tool call: {item_status}"
                )
            call_id_value = item.get("call_id")
            name_value = item.get("name")
            call_id = str(call_id_value or "").strip()
            name = str(name_value or "").strip()
            if not call_id or not name:
                raise ValueError(
                    "Responses tool call requires non-empty call_id/id and name"
                )
            if strict and (
                not isinstance(call_id_value, str)
                or call_id_value != call_id
                or not isinstance(name_value, str)
                or name_value != name
            ):
                raise ValueError(
                    "Responses upstream tool call call_id/name must be non-empty "
                    "strings without leading or trailing whitespace"
                )
            raw_arguments = (
                item.get("arguments")
                if item_type == "function_call"
                else item.get("input")
            )
            if strict and not isinstance(raw_arguments, str):
                raise ValueError(
                    f"Responses {item_type}.{payload_field} must be a string"
                )
            tool_use: dict[str, Any] = {
                "type": "tool_use",
                "id": call_id,
                "name": name,
                "input": (
                    _custom_tool_input(raw_arguments)
                    if item_type == "custom_tool_call"
                    else _required_json_object(
                        raw_arguments,
                        "Responses function_call.arguments",
                    )
                ),
            }
            caller = item.get("caller")
            if caller is not None:
                if caller != {"type": "direct"}:
                    raise ValueError(
                        "Responses upstream tool caller cannot be projected to "
                        "Anthropic"
                    )
                tool_use["caller"] = {"type": "direct"}
            namespace = item.get("namespace")
            if namespace is not None:
                if not isinstance(namespace, str) or not namespace:
                    raise ValueError(
                        "Responses upstream tool namespace must be a non-empty string"
                    )
                tool_use["toolset_name"] = namespace
            content.append(tool_use)
            saw_tool_use = True
        else:
            raise ValueError(
                f"Responses upstream output type is unsupported: {item_type!r}"
            )
    incomplete = incomplete_value if isinstance(incomplete_value, dict) else {}
    incomplete_reason = str(incomplete.get("reason") or "")
    if status == "incomplete" and incomplete_reason not in {
        "max_output_tokens",
        "max_tokens",
        "content_filter",
    }:
        raise ValueError(
            "Responses upstream returned incomplete status without a supported "
            f"reason: {incomplete_reason or 'missing'}"
        )
    if not content:
        output_text = str(response.get("output_text") or "")
        if output_text:
            content.append({"type": "text", "text": output_text})
    if not content and status == "completed":
        raise ValueError("Responses upstream returned no completed output")
    if saw_refusal or incomplete_reason == "content_filter":
        stop_reason = "refusal"
    elif incomplete_reason in {"max_output_tokens", "max_tokens"}:
        stop_reason = "max_tokens"
    elif saw_tool_use:
        stop_reason = "tool_use"
    else:
        stop_reason = "end_turn"
    response_service_tier = response.get("service_tier")
    if response_service_tier is not None:
        service_tier_mapping = {
            "default": "standard",
            "priority": "priority",
        }
        if response_service_tier not in service_tier_mapping:
            raise ValueError(
                "Responses upstream service_tier cannot be projected to Anthropic: "
                f"{response_service_tier!r}"
            )
        projected_usage["service_tier"] = service_tier_mapping[
            response_service_tier
        ]
    response_id = str(response.get("id") or uuid.uuid4().hex)
    if response_id.startswith("resp_"):
        response_id = response_id[5:]
    return {
        "id": f"msg_{response_id}",
        "type": "message",
        "role": "assistant",
        "model": str(response.get("model") or fallback_model or "model"),
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": projected_usage,
    }


def _responses_data_source(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    if not value.startswith("data:"):
        return {"type": "url", "url": value}
    header, separator, data = value.partition(",")
    if not separator or ";base64" not in header or not data:
        raise ValueError(f"{field} must use a base64 data URL")
    media_type = header[5:].split(";", 1)[0]
    if not media_type:
        raise ValueError(f"{field} data URL requires a media type")
    return {"type": "base64", "media_type": media_type, "data": data}


def _strict_responses_content_block(
    block: Any,
    *,
    role: str,
    field: str,
) -> dict[str, Any]:
    if isinstance(block, str):
        return {"type": "text", "text": block}
    if not isinstance(block, dict):
        raise ValueError(f"{field} must be a string or content block object")
    block_type = block.get("type")
    if block_type in {"input_text", "output_text", "text"}:
        allowed = {"type", "text"}
        if block_type == "output_text":
            allowed.update({"annotations", "logprobs"})
            if block.get("annotations") not in (None, []):
                raise ValueError(f"{field} annotations cannot be projected")
            if block.get("logprobs") not in (None, []):
                raise ValueError(f"{field} logprobs cannot be projected")
        unknown = sorted(set(block) - allowed)
        if unknown:
            raise ValueError(
                f"{field} fields cannot be projected: {', '.join(unknown)}"
            )
        text = block.get("text")
        if not isinstance(text, str):
            raise ValueError(f"{field}.text must be a string")
        return {"type": "text", "text": text}
    if block_type == "input_image":
        if role != "user":
            raise ValueError(f"{field} image requires user role")
        unknown = sorted(set(block) - {"type", "image_url", "file_id", "detail"})
        if unknown:
            raise ValueError(
                f"{field} fields cannot be projected: {', '.join(unknown)}"
            )
        if block.get("detail") not in (None, "auto"):
            raise ValueError(f"{field}.detail cannot be projected to Anthropic")
        sources = [
            key for key in ("image_url", "file_id") if block.get(key) is not None
        ]
        if len(sources) != 1:
            raise ValueError(f"{field} requires exactly one image source")
        if sources[0] == "file_id":
            file_id = block["file_id"]
            if not isinstance(file_id, str) or not file_id:
                raise ValueError(f"{field}.file_id must be a non-empty string")
            source = {"type": "file", "file_id": file_id}
        else:
            source = _responses_data_source(
                block["image_url"], f"{field}.image_url"
            )
            if (
                source.get("type") == "base64"
                and source.get("media_type") not in _ANTHROPIC_IMAGE_MEDIA_TYPES
            ):
                raise ValueError(
                    f"{field}.image_url media type cannot be projected to Anthropic"
                )
        return {
            "type": "image",
            "source": source,
        }
    if block_type == "input_file":
        if role != "user":
            raise ValueError(f"{field} file requires user role")
        unknown = sorted(
            set(block)
            - {"type", "file_data", "file_url", "file_id", "filename", "detail"}
        )
        if unknown:
            raise ValueError(
                f"{field} fields cannot be projected: {', '.join(unknown)}"
            )
        if block.get("detail") not in (None, "auto"):
            raise ValueError(f"{field}.detail cannot be projected to Anthropic")
        sources = [
            key
            for key in ("file_data", "file_url", "file_id")
            if block.get(key) is not None
        ]
        if len(sources) != 1:
            raise ValueError(f"{field} requires exactly one file source")
        source_key = sources[0]
        if source_key == "file_id":
            file_id = block["file_id"]
            if not isinstance(file_id, str) or not file_id:
                raise ValueError(f"{field}.file_id must be a non-empty string")
            source = {"type": "file", "file_id": file_id}
        elif source_key == "file_url":
            source = _responses_data_source(
                block[source_key], f"{field}.{source_key}"
            )
            if source.get("type") != "url":
                raise ValueError(f"{field}.file_url must be a URL")
        else:
            source = _responses_data_source(
                block[source_key], f"{field}.{source_key}"
            )
            if source.get("type") != "base64":
                raise ValueError(f"{field}.file_data must be a base64 data URL")
            media_type = source.get("media_type")
            if media_type == "text/plain":
                try:
                    decoded = base64.b64decode(
                        str(source.get("data") or ""), validate=True
                    ).decode("utf-8")
                except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
                    raise ValueError(
                        f"{field}.file_data text payload must be valid base64 UTF-8"
                    ) from exc
                source = {
                    "type": "text",
                    "media_type": "text/plain",
                    "data": decoded,
                }
            elif media_type != "application/pdf":
                raise ValueError(
                    f"{field}.file_data media type cannot be projected to Anthropic"
                )
        projected = {"type": "document", "source": source}
        filename = block.get("filename")
        if filename is not None:
            if not isinstance(filename, str):
                raise ValueError(f"{field}.filename must be a string")
            projected["title"] = filename
        return projected
    raise ValueError(
        f"{field} content type cannot be projected to Anthropic: {block_type!r}"
    )


def _strict_responses_controls(body: dict[str, Any]) -> dict[str, Any]:
    supported = {
        "_ciel_remote_bridge_request",
        "background",
        "client_metadata",
        "conversation",
        "include",
        "input",
        "instructions",
        "max_output_tokens",
        "max_tokens",
        "max_tool_calls",
        "metadata",
        "model",
        "moderation",
        "parallel_tool_calls",
        "previous_response_id",
        "prompt",
        "prompt_cache_key",
        "prompt_cache_options",
        "reasoning",
        "safety_identifier",
        "service_tier",
        "store",
        "stream",
        "stream_options",
        "temperature",
        "text",
        "tool_choice",
        "tools",
        "top_p",
        "truncation",
        "user",
    }
    unknown = sorted(set(body) - supported)
    if unknown:
        raise ValueError(
            "Responses fields cannot be projected to Anthropic: "
            + ", ".join(unknown)
        )
    unsupported = {
        "conversation": body.get("conversation"),
        "max_tool_calls": body.get("max_tool_calls"),
        "moderation": body.get("moderation"),
        "previous_response_id": body.get("previous_response_id"),
        "prompt": body.get("prompt"),
        "prompt_cache_options": body.get("prompt_cache_options"),
        "user": body.get("user"),
    }
    present_unsupported = [key for key, value in unsupported.items() if value is not None]
    if present_unsupported:
        raise ValueError(
            "Responses fields cannot be projected to Anthropic: "
            + ", ".join(present_unsupported)
        )
    if body.get("background") not in (None, False):
        raise ValueError("Responses background mode cannot be projected to Anthropic")
    if body.get("store") not in (None, False):
        raise ValueError("Responses store=true cannot be projected to Anthropic")
    if body.get("truncation") not in (None, "disabled"):
        raise ValueError("Responses automatic truncation cannot be projected to Anthropic")
    if body.get("metadata") not in (None, {}):
        raise ValueError("Responses metadata cannot be projected to Anthropic")
    client_metadata = body.get("client_metadata")
    if client_metadata is not None and (
        not isinstance(client_metadata, dict)
        or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in client_metadata.items()
        )
    ):
        raise ValueError("Responses client_metadata must contain string values")
    prompt_cache_key = body.get("prompt_cache_key")
    if prompt_cache_key is not None and (
        not isinstance(prompt_cache_key, str) or not prompt_cache_key
    ):
        raise ValueError("Responses prompt_cache_key must be a non-empty string")
    stream_options = body.get("stream_options")
    sequential_cutoff = False
    if stream_options not in (None, {}):
        if not isinstance(stream_options, dict):
            raise ValueError("Responses stream_options must be an object")
        unknown_stream_options = sorted(
            set(stream_options) - {"reasoning_summary_delivery"}
        )
        if unknown_stream_options:
            raise ValueError(
                "Responses stream_options fields cannot be projected to Anthropic: "
                + ", ".join(unknown_stream_options)
            )
        if (
            stream_options.get("reasoning_summary_delivery")
            != "sequential_cutoff"
        ):
            raise ValueError(
                "unsupported Responses reasoning_summary_delivery: "
                f"{stream_options.get('reasoning_summary_delivery')!r}"
            )
        if body.get("stream", True) is not True:
            raise ValueError(
                "Responses reasoning_summary_delivery requires stream=true"
            )
        # The bridge buffers the Anthropic message and emits each reasoning/output
        # item to the Responses client in source order. That delivery is already
        # sequential, so this exact Codex transport hint is safely consumed.
        sequential_cutoff = True
    include = body.get("include")
    if include is not None:
        if not isinstance(include, list) or any(
            value != "reasoning.encrypted_content" for value in include
        ):
            raise ValueError("Responses include fields cannot be projected to Anthropic")

    controls: dict[str, Any] = {}
    stream = body.get("stream", True)
    if not isinstance(stream, bool):
        raise ValueError("Responses stream must be a boolean")
    controls["stream"] = stream
    for key in ("temperature", "top_p"):
        value = body.get(key)
        if value is not None:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
                or value > 1
            ):
                raise ValueError(f"Responses {key} must be between 0 and 1")
            controls[key] = value
    max_values: list[int] = []
    for key in ("max_output_tokens", "max_tokens"):
        value = body.get(key)
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"Responses {key} must be a positive integer")
            max_values.append(value)
    if len(set(max_values)) > 1:
        raise ValueError("Responses max token fields conflict")
    if max_values:
        controls["max_tokens"] = max_values[0]

    safety_identifier = body.get("safety_identifier")
    if safety_identifier is not None:
        if not isinstance(safety_identifier, str) or not safety_identifier:
            raise ValueError("Responses safety_identifier must be a non-empty string")
        controls["metadata"] = {"user_id": safety_identifier}
    service_tier = body.get("service_tier")
    if service_tier is not None:
        if service_tier not in {"auto", "default"}:
            raise ValueError(
                f"Responses service_tier cannot be projected: {service_tier!r}"
            )
        controls["service_tier"] = (
            "auto" if service_tier == "auto" else "standard_only"
        )

    output_config: dict[str, Any] = {}
    text_value = body.get("text")
    if text_value is not None:
        if not isinstance(text_value, dict):
            raise ValueError("Responses text must be an object")
        unknown_text = sorted(set(text_value) - {"format", "verbosity"})
        if unknown_text:
            raise ValueError(
                "Responses text fields cannot be projected: "
                + ", ".join(unknown_text)
            )
        verbosity = text_value.get("verbosity")
        if verbosity is not None and verbosity not in {"low", "medium", "high"}:
            raise ValueError("Responses text.verbosity must be low, medium, or high")
        format_value = text_value.get("format")
        if format_value is not None:
            if not isinstance(format_value, dict):
                raise ValueError("Responses text.format must be an object")
            format_type = format_value.get("type")
            if format_type == "text":
                if set(format_value) != {"type"}:
                    raise ValueError("Responses text format fields cannot be projected")
            elif format_type == "json_schema":
                if (
                    not isinstance(format_value.get("schema"), dict)
                    or set(format_value) - {"type", "name", "schema", "strict"}
                ):
                    raise ValueError("Responses json_schema format is invalid")
                if format_value.get("strict") not in (None, True):
                    raise ValueError(
                        "Responses non-strict JSON schema cannot be projected to "
                        "Anthropic strict output format"
                    )
                output_config["format"] = {
                    "type": "json_schema",
                    "schema": dict(format_value["schema"]),
                }
            else:
                raise ValueError(
                    f"Responses text format cannot be projected: {format_type!r}"
                )

    reasoning_value = body.get("reasoning")
    if reasoning_value is not None:
        if not isinstance(reasoning_value, dict):
            raise ValueError("Responses reasoning must be an object")
        unknown_reasoning = sorted(
            set(reasoning_value)
            - {"context", "effort", "summary", "generate_summary"}
        )
        if unknown_reasoning:
            raise ValueError(
                "Responses reasoning fields cannot be projected: "
                + ", ".join(unknown_reasoning)
            )
        context = reasoning_value.get("context")
        if context is not None and context != "all_turns":
            raise ValueError(
                "unsupported Responses reasoning.context for Anthropic projection: "
                f"{context!r}"
            )
        # This bridge is stateless and rejects previous_response_id. Signed
        # Anthropic thinking is returned in Ciel encrypted reasoning envelopes,
        # and strict continuation handling restores those envelopes into message
        # history. Consuming all_turns therefore preserves Codex Lite replay
        # semantics without forwarding an invented Anthropic control.
        summary = reasoning_value.get("summary")
        generate_summary = reasoning_value.get("generate_summary")
        if summary is not None and generate_summary is not None and summary != generate_summary:
            raise ValueError("Responses reasoning summary fields conflict")
        summary = summary if summary is not None else generate_summary
        if summary not in (None, "none", "auto", "concise", "detailed"):
            raise ValueError(f"unsupported Responses reasoning summary: {summary!r}")
        effort = reasoning_value.get("effort")
        if effort is not None and not isinstance(effort, str):
            raise ValueError("Responses reasoning.effort must be a string")
        normalized_effort = (
            str(effort).strip().lower() if effort is not None else None
        )
        if normalized_effort == "none":
            if summary not in (None, "none"):
                raise ValueError(
                    "Responses reasoning summary conflicts with effort='none'"
                )
            controls["thinking"] = {"type": "disabled"}
        elif normalized_effort == "minimal":
            raise ValueError(
                "Responses reasoning effort='minimal' has no equivalent "
                "Anthropic effort level"
            )
        elif normalized_effort in {"low", "medium", "high", "xhigh", "max"}:
            output_config["effort"] = normalized_effort
            controls["thinking"] = {"type": "adaptive"}
            if summary == "auto":
                controls["thinking"]["display"] = "summarized"
            elif summary in {None, "none"}:
                controls["thinking"]["display"] = "omitted"
            else:
                raise ValueError(
                    "Responses reasoning summary detail cannot be projected to "
                    f"Anthropic: {summary!r}"
                )
        elif normalized_effort is not None:
            raise ValueError(
                f"unsupported Responses reasoning effort: {normalized_effort!r}"
            )
        elif summary == "auto":
            controls["thinking"] = {
                "type": "adaptive",
                "display": "summarized",
            }
        elif summary not in (None, "none"):
            raise ValueError(
                "Responses reasoning summary detail cannot be projected to "
                f"Anthropic: {summary!r}"
            )
        if sequential_cutoff and summary in (None, "none"):
            raise ValueError(
                "Responses reasoning_summary_delivery requires a reasoning summary"
            )
    elif sequential_cutoff:
        raise ValueError(
            "Responses reasoning_summary_delivery requires a reasoning object"
        )
    if output_config:
        controls["output_config"] = output_config
    return controls


def _validate_codex_internal_chat_metadata(
    item: dict[str, Any],
    *,
    field: str,
) -> None:
    """Validate and consume Codex's Responses-only chat metadata.

    Codex 0.150.1 retains this strongly typed transport metadata only when a
    provider identifies as OpenAI.  It does not affect the model-visible
    message, so a non-native Anthropic projection consumes the known fields
    after validation and continues to reject extensions it cannot identify.
    """

    metadata_field = "internal_chat_message_metadata_passthrough"
    if metadata_field not in item:
        return
    metadata = item.get(metadata_field)
    if not isinstance(metadata, dict):
        raise ValueError(f"{field}.{metadata_field} must be an object")
    unknown = sorted(
        set(metadata) - {"turn_id", "create_time", "content_item_kinds"}
    )
    if unknown:
        raise ValueError(
            f"{field}.{metadata_field} fields cannot be projected: "
            + ", ".join(unknown)
        )
    if "turn_id" in metadata and not isinstance(metadata["turn_id"], str):
        raise ValueError(f"{field}.{metadata_field}.turn_id must be a string")
    if "create_time" in metadata:
        create_time = metadata["create_time"]
        if (
            isinstance(create_time, bool)
            or not isinstance(create_time, (int, float))
            or not math.isfinite(float(create_time))
        ):
            raise ValueError(
                f"{field}.{metadata_field}.create_time must be a finite number"
            )
    if "content_item_kinds" in metadata:
        kinds = metadata["content_item_kinds"]
        if not isinstance(kinds, list) or any(
            not isinstance(kind, str) for kind in kinds
        ):
            raise ValueError(
                f"{field}.{metadata_field}.content_item_kinds must be a string array"
            )
        content = item.get("content")
        if isinstance(content, list) and len(kinds) != len(content):
            raise ValueError(
                f"{field}.{metadata_field}.content_item_kinds must align with content"
            )


def _strict_openai_responses_to_anthropic_messages(
    body: dict[str, Any],
    fallback_model: str,
) -> dict[str, Any]:
    controls = _strict_responses_controls(body)
    instructions = body.get("instructions")
    if instructions is not None and not isinstance(instructions, str):
        raise ValueError("Responses instructions must be a string")
    system_parts = [instructions] if instructions else []
    raw_input = body.get("input", [])
    if isinstance(raw_input, str):
        raw_input = [{"role": "user", "content": raw_input}]
    if not isinstance(raw_input, list):
        raise ValueError("Responses input must be text or an array")

    additional_tool_items = [
        (index, item)
        for index, item in enumerate(raw_input)
        if isinstance(item, dict) and item.get("type") == "additional_tools"
    ]
    effective_tools = body.get("tools")
    if additional_tool_items:
        if len(additional_tool_items) != 1:
            raise ValueError(
                "Responses Lite requires exactly one additional_tools input item"
            )
        additional_index, additional_item = additional_tool_items[0]
        if additional_index != 0:
            raise ValueError(
                "Responses Lite additional_tools must be the first input item"
            )
        unknown_additional_fields = sorted(
            set(additional_item) - {"id", "role", "tools", "type"}
        )
        if unknown_additional_fields:
            raise ValueError(
                "Responses additional_tools fields cannot be projected: "
                + ", ".join(unknown_additional_fields)
            )
        if additional_item.get("role") != "developer":
            raise ValueError(
                "Responses additional_tools role must be 'developer'"
            )
        item_id = additional_item.get("id")
        if item_id is not None and (
            not isinstance(item_id, str) or not item_id.strip()
        ):
            raise ValueError(
                "Responses additional_tools id must be a non-empty string"
            )
        declared_tools = additional_item.get("tools")
        if not isinstance(declared_tools, list):
            raise ValueError("Responses additional_tools.tools must be an array")
        if effective_tools is not None:
            raise ValueError(
                "Responses additional_tools conflicts with top-level tools"
            )
        effective_tools = declared_tools

    messages: list[dict[str, Any]] = []
    saw_conversation = False

    def append_message(role: str, content: list[dict[str, Any]]) -> None:
        nonlocal saw_conversation
        if not content:
            raise ValueError("Responses message content cannot be empty")
        if messages and messages[-1].get("role") == role:
            messages[-1]["content"].extend(content)
        else:
            messages.append({"role": role, "content": content})
        saw_conversation = True

    custom_names = _custom_tool_names(effective_tools)
    pending_calls: dict[str, tuple[str, str | None]] = {}
    seen_call_ids: set[str] = set()
    resolving_calls = False
    for item_index, item in enumerate(raw_input):
        if not isinstance(item, dict):
            raise ValueError(f"Responses input[{item_index}] must be an object")
        item_type = item.get("type")
        call_type = item_type in {"function_call", "custom_tool_call"}
        output_type = item_type in {
            "function_call_output",
            "custom_tool_call_output",
        }
        if pending_calls and not call_type and not output_type:
            raise ValueError(
                "Responses tool calls require immediately following output items"
            )
        if resolving_calls and call_type:
            raise ValueError(
                "Responses tool calls cannot be interleaved with tool outputs"
            )
        if item_type == "additional_tools":
            continue
        _validate_codex_internal_chat_metadata(
            item,
            field=f"Responses input[{item_index}]",
        )
        if item_type in (None, "message"):
            unknown = sorted(
                set(item)
                - {
                    "type",
                    "id",
                    "role",
                    "content",
                    "status",
                    "phase",
                    "internal_chat_message_metadata_passthrough",
                }
            )
            if unknown:
                raise ValueError(
                    f"Responses input[{item_index}] fields cannot be projected: "
                    + ", ".join(unknown)
                )
            if item.get("status") not in (None, "completed"):
                raise ValueError(
                    f"Responses input[{item_index}] message must be completed"
                )
            role_value = item.get("role")
            if role_value not in {"user", "assistant", "system", "developer"}:
                raise ValueError(
                    f"Responses input[{item_index}].role is unsupported: {role_value!r}"
                )
            phase = item.get("phase")
            if role_value == "assistant":
                if phase not in (None, "commentary", "final_answer"):
                    raise ValueError(
                        f"Responses input[{item_index}].phase is unsupported: "
                        f"{phase!r}"
                    )
            elif phase is not None:
                raise ValueError(
                    f"Responses input[{item_index}].phase requires assistant role"
                )
            content_value = item.get("content")
            content_items = (
                content_value if isinstance(content_value, list) else [content_value]
            )
            blocks = [
                _strict_responses_content_block(
                    block,
                    role="user" if role_value in {"system", "developer"} else role_value,
                    field=f"input[{item_index}].content[{block_index}]",
                )
                for block_index, block in enumerate(content_items)
            ]
            if role_value in {"system", "developer"}:
                if saw_conversation:
                    raise ValueError(
                        "late Responses system/developer messages cannot be "
                        "projected to Anthropic"
                    )
                system_parts.extend(str(block.get("text") or "") for block in blocks)
            else:
                append_message(role_value, blocks)
            continue
        if item_type in {"function_call", "custom_tool_call"}:
            allowed = {
                "type",
                "id",
                "call_id",
                "caller",
                "name",
                "namespace",
                "status",
                "internal_chat_message_metadata_passthrough",
            }
            payload_key = "arguments" if item_type == "function_call" else "input"
            allowed.add(payload_key)
            unknown = sorted(set(item) - allowed)
            if unknown or item.get("status") not in (None, "completed"):
                raise ValueError(
                    f"Responses input[{item_index}] tool call is not projectable"
                )
            call_id = item.get("call_id")
            name = item.get("name")
            if (
                not isinstance(call_id, str)
                or not call_id
                or not isinstance(name, str)
                or not name
            ):
                raise ValueError("Responses tool call requires call_id and name")
            if call_id in seen_call_ids:
                raise ValueError(f"Responses tool call_id is duplicated: {call_id}")
            seen_call_ids.add(call_id)
            namespace = item.get("namespace")
            if namespace is not None and (
                not isinstance(namespace, str) or not namespace
            ):
                raise ValueError(
                    "Responses tool call namespace must be a non-empty string"
                )
            namespace_identity = (
                _namespace_tool_identity(
                    effective_tools, _namespace_tool_alias(namespace, name)
                )
                if isinstance(namespace, str)
                else None
            )
            if item_type == "function_call":
                tool_input = _required_json_object(
                    item.get("arguments"),
                    "Responses function_call.arguments",
                )
            else:
                if name not in custom_names and not (
                    namespace_identity is not None
                    and namespace_identity[2] == "custom"
                ):
                    raise ValueError("Responses custom tool call has no matching tool")
                raw_custom = item.get("input")
                if not isinstance(raw_custom, str):
                    raise ValueError("Responses custom tool input must be a string")
                tool_input = {"input": raw_custom}
            tool_use: dict[str, Any] = {
                "type": "tool_use",
                "id": call_id,
                "name": (
                    _namespace_tool_alias(namespace, name)
                    if isinstance(namespace, str)
                    else name
                ),
                "input": tool_input,
            }
            caller = item.get("caller")
            if caller is not None:
                if caller != {"type": "direct"}:
                    raise ValueError(
                        "Responses tool call caller cannot be projected to "
                        "Anthropic"
                    )
                tool_use["caller"] = {"type": "direct"}
            pending_calls[call_id] = (item_type, namespace)
            append_message("assistant", [tool_use])
            continue
        if item_type in {"function_call_output", "custom_tool_call_output"}:
            resolving_calls = True
            unknown = sorted(
                set(item)
                - {
                    "type",
                    "id",
                    "call_id",
                    "output",
                    "status",
                    "internal_chat_message_metadata_passthrough",
                }
            )
            if unknown or item.get("status") not in (None, "completed"):
                raise ValueError(
                    f"Responses input[{item_index}] tool output is not projectable"
                )
            call_id = item.get("call_id")
            if not isinstance(call_id, str) or not call_id:
                raise ValueError("Responses tool output requires call_id")
            expected_call = pending_calls.pop(call_id, None)
            expected_call_type = expected_call[0] if expected_call else None
            expected_output_type = (
                "function_call_output"
                if expected_call_type == "function_call"
                else "custom_tool_call_output"
                if expected_call_type == "custom_tool_call"
                else None
            )
            if expected_output_type != item_type:
                raise ValueError(
                    "Responses tool output has no matching call with the same "
                    f"call_id and type: {call_id}"
                )
            if not pending_calls:
                resolving_calls = False
            output_value = item.get("output", "")
            if isinstance(output_value, str):
                result_content: Any = output_value
            elif isinstance(output_value, list):
                result_content = [
                    _strict_responses_content_block(
                        block,
                        role="user",
                        field=f"input[{item_index}].output[{block_index}]",
                    )
                    for block_index, block in enumerate(output_value)
                ]
            else:
                raise ValueError("Responses tool output must be text or content array")
            tool_result: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": call_id,
                "content": result_content,
            }
            append_message(
                "user",
                [tool_result],
            )
            continue
        if item_type == "reasoning":
            unknown = sorted(
                set(item)
                - {
                    "type",
                    "id",
                    "status",
                    "summary",
                    "encrypted_content",
                    "internal_chat_message_metadata_passthrough",
                }
            )
            if unknown or item.get("status") not in (None, "completed"):
                raise ValueError(
                    f"Responses input[{item_index}] reasoning item is not "
                    "projectable"
                )
            block = _anthropic_reasoning_from_envelope(item)
            if block is None:
                raise ValueError(
                    "Responses reasoning item is not a Ciel Anthropic envelope"
                )
            append_message("assistant", [block])
            continue
        raise ValueError(
            f"Responses input[{item_index}] type cannot be projected: {item_type!r}"
        )

    if pending_calls:
        unresolved = ", ".join(sorted(pending_calls))
        raise ValueError(
            "Responses tool calls require matching output items: " + unresolved
        )
    if not messages:
        raise ValueError("Responses input must contain at least one conversation item")
    model = body.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise ValueError("Responses model must be a non-empty string")
    out: dict[str, Any] = {
        "model": model or fallback_model or "model",
        "messages": messages,
        **controls,
    }
    tools = _tools_to_anthropic(
        effective_tools,
        strict_projection=True,
    )
    if tools:
        out["tools"] = tools
    tool_choice = _tool_choice_to_anthropic(
        body.get("tool_choice"),
        strict_projection=True,
    )
    parallel = body.get("parallel_tool_calls")
    if parallel is not None and not isinstance(parallel, bool):
        raise ValueError("Responses parallel_tool_calls must be a boolean")
    if parallel is False:
        if tool_choice is None:
            tool_choice = {"type": "auto"}
        if tool_choice.get("type") != "none":
            tool_choice = {**tool_choice, "disable_parallel_tool_use": True}
    if tool_choice is not None:
        out["tool_choice"] = tool_choice
    if system_parts:
        out["system"] = [
            {"type": "text", "text": part} for part in system_parts if part
        ]
    return out


def openai_responses_to_anthropic_messages(body: dict[str, Any], fallback_model: str) -> dict[str, Any]:
    if body.get("_ciel_remote_bridge_request") is True:
        return _strict_openai_responses_to_anthropic_messages(body, fallback_model)
    system_parts: list[str] = []
    instructions = str(body.get("instructions") or "").strip()
    if instructions:
        system_parts.append(instructions)
    messages: list[dict[str, Any]] = []
    raw_input = body.get("input", [])
    if isinstance(raw_input, str):
        raw_input = [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": raw_input}]}]
    if isinstance(raw_input, dict):
        raw_input = [raw_input]
    if not isinstance(raw_input, list):
        raw_input = []
    saw_conversation_item = False
    pending_reasoning: list[dict[str, Any]] = []
    pending_tool_role = ""
    for item in raw_input:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "message")
        if item_type == "reasoning":
            encrypted_block = _anthropic_reasoning_from_envelope(item)
            if encrypted_block is not None:
                pending_reasoning.append(encrypted_block)
            else:
                summary = _reasoning_summary_text(item)
                if summary:
                    pending_reasoning.append(
                        {"type": "thinking", "thinking": summary}
                    )
            continue
        if item_type in {"function_call", "custom_tool_call"}:
            call_id = str(item.get("call_id") or item.get("id") or f"call_{len(messages) + 1}")
            content: list[dict[str, Any]] = []
            if pending_reasoning:
                content.extend(pending_reasoning)
                pending_reasoning = []
            content.append(
                {
                    "type": "tool_use",
                    "id": call_id,
                    "name": str(item.get("name") or "tool"),
                    "input": (
                        _custom_tool_input(item.get("input"))
                        if item_type == "custom_tool_call"
                        else _json_object(item.get("arguments"))
                    ),
                }
            )
            if pending_tool_role == "assistant" and messages:
                messages[-1]["content"].extend(content)
            else:
                messages.append(
                    {
                        "role": "assistant",
                        "content": content,
                    }
                )
            pending_tool_role = "assistant"
            saw_conversation_item = True
            continue
        if item_type in {"function_call_output", "custom_tool_call_output"}:
            call_id = str(item.get("call_id") or item.get("id") or "call_tool")
            result = {
                "type": "tool_result",
                "tool_use_id": call_id,
                "content": _content_text(item.get("output")),
            }
            if pending_tool_role == "user" and messages:
                messages[-1]["content"].append(result)
            else:
                messages.append({"role": "user", "content": [result]})
            pending_tool_role = "user"
            saw_conversation_item = True
            continue
        pending_tool_role = ""
        role = str(item.get("role") or "user").strip().lower()
        blocks = _content_blocks(item.get("content", item.get("text", "")))
        if not blocks:
            continue
        if pending_reasoning:
            if role == "assistant":
                blocks = [*pending_reasoning, *blocks]
            else:
                messages.append(
                    {"role": "assistant", "content": list(pending_reasoning)}
                )
                saw_conversation_item = True
            pending_reasoning = []
        if role in ("system", "developer") and not saw_conversation_item:
            system_parts.append(_content_text(blocks))
            continue
        if role in ("system", "developer"):
            role = "user"
            blocks = [{"type": "text", "text": f"[Runtime system context]\n{_content_text(blocks)}"}]
        if role not in ("user", "assistant"):
            role = "user"
        messages.append({"role": role, "content": blocks})
        saw_conversation_item = True
    if pending_reasoning:
        messages.append({"role": "assistant", "content": pending_reasoning})
    if not messages:
        messages.append({"role": "user", "content": [{"type": "text", "text": ""}]})
    out: dict[str, Any] = {
        "model": str(body.get("model") or fallback_model or "model"),
        "messages": messages,
        "stream": bool(body.get("stream", True)),
    }
    tools = _tools_to_anthropic(body.get("tools"))
    if tools:
        out["tools"] = tools
    tool_choice = _tool_choice_to_anthropic(body.get("tool_choice"))
    parallel_tool_calls = body.get("parallel_tool_calls")
    if parallel_tool_calls is not None and not isinstance(parallel_tool_calls, bool):
        raise ValueError("Responses parallel_tool_calls must be a boolean")
    if parallel_tool_calls is False:
        if tool_choice is None:
            tool_choice = {"type": "auto"}
        if isinstance(tool_choice, dict) and tool_choice.get("type") != "none":
            tool_choice = {**tool_choice, "disable_parallel_tool_use": True}
    if tool_choice is not None:
        out["tool_choice"] = tool_choice
    max_tokens = _positive_int(body.get("max_output_tokens")) or _positive_int(body.get("max_tokens"))
    if max_tokens:
        out["max_tokens"] = max_tokens
    for key in ("temperature", "top_p"):
        if body.get(key) is not None:
            out[key] = body[key]
    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict) and reasoning.get("effort") is not None:
        effort = str(reasoning["effort"])
        out["thinking"] = {
            "type": "disabled" if effort.strip().lower() in {"none", "minimal"} else "enabled",
            "effort": effort,
        }
    if system_parts:
        out["system"] = [{"type": "text", "text": part} for part in system_parts if part]
    return out


def _usage_from_anthropic(
    message: dict[str, Any], *, strict: bool = False
) -> dict[str, Any]:
    usage_value = message.get("usage")
    usage = usage_value if isinstance(usage_value, dict) else {}
    if strict:
        if not isinstance(usage_value, dict):
            raise ValueError("Anthropic upstream message.usage is required")
        allowed_usage_fields = {
            "cache_creation",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
            "inference_geo",
            "input_tokens",
            "output_tokens",
            "output_tokens_details",
            "server_tool_use",
            "service_tier",
        }
        unknown_usage_fields = sorted(set(usage) - allowed_usage_fields)
        if unknown_usage_fields:
            raise ValueError(
                "Anthropic upstream usage fields cannot be projected: "
                + ", ".join(unknown_usage_fields)
            )
        for key in ("input_tokens", "output_tokens"):
            if key not in usage:
                raise ValueError(f"Anthropic upstream usage.{key} is required")
        for key in (
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
            "input_tokens",
            "output_tokens",
        ):
            value = usage.get(key, 0)
            if value is None and key in {
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            }:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(
                    f"Anthropic upstream usage.{key} must be a non-negative integer"
                )
        details = usage.get("output_tokens_details")
        if details is not None and (
            not isinstance(details, dict) or set(details) != {"thinking_tokens"}
        ):
            raise ValueError(
                "Anthropic upstream usage.output_tokens_details is invalid"
            )
        thinking_value = details.get("thinking_tokens") if details else 0
        if (
            isinstance(thinking_value, bool)
            or not isinstance(thinking_value, int)
            or thinking_value < 0
            or thinking_value > usage["output_tokens"]
        ):
            raise ValueError(
                "Anthropic upstream usage.output_tokens_details.thinking_tokens "
                "must be between zero and output_tokens"
            )
        cache_creation = usage.get("cache_creation")
        if cache_creation is not None:
            if not isinstance(cache_creation, dict) or set(cache_creation) - {
                "ephemeral_1h_input_tokens",
                "ephemeral_5m_input_tokens",
            }:
                raise ValueError(
                    "Anthropic upstream usage.cache_creation is invalid"
                )
            for key, value in cache_creation.items():
                if (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or value < 0
                ):
                    raise ValueError(
                        f"Anthropic upstream usage.cache_creation.{key} must be "
                        "a non-negative integer"
                    )
        server_tool_use = usage.get("server_tool_use")
        if server_tool_use is not None:
            if not isinstance(server_tool_use, dict) or set(server_tool_use) - {
                "web_fetch_requests",
                "web_search_requests",
            }:
                raise ValueError(
                    "Anthropic upstream usage.server_tool_use is invalid"
                )
            for key, value in server_tool_use.items():
                if (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or value < 0
                ):
                    raise ValueError(
                        f"Anthropic upstream usage.server_tool_use.{key} must be "
                        "a non-negative integer"
                    )
            if any(server_tool_use.values()):
                raise ValueError(
                    "Anthropic upstream server-tool usage cannot be projected to "
                    "a Responses client-tool response"
                )
        inference_geo = usage.get("inference_geo")
        if inference_geo is not None and (
            not isinstance(inference_geo, str) or not inference_geo
        ):
            raise ValueError("Anthropic upstream usage.inference_geo is invalid")
    uncached_input = _positive_int(usage.get("input_tokens")) or 0
    cached_input = _positive_int(usage.get("cache_read_input_tokens")) or 0
    cache_write = _positive_int(usage.get("cache_creation_input_tokens")) or 0
    input_tokens = uncached_input + cached_input + cache_write
    output_tokens = _positive_int(usage.get("output_tokens")) or 0
    output_details = (
        usage.get("output_tokens_details")
        if isinstance(usage.get("output_tokens_details"), dict)
        else {}
    )
    reasoning_tokens = _positive_int(output_details.get("thinking_tokens")) or 0
    return {
        "input_tokens": input_tokens,
        "input_tokens_details": {
            "cache_write_tokens": cache_write,
            "cached_tokens": cached_input,
        },
        "output_tokens": output_tokens,
        "output_tokens_details": {"reasoning_tokens": reasoning_tokens},
        "total_tokens": input_tokens + output_tokens,
    }


def anthropic_message_to_openai_response(
    message: dict[str, Any], source_body: dict[str, Any] | None = None
) -> dict[str, Any]:
    strict = bool((source_body or {}).get("_ciel_remote_bridge_request") is True)
    if strict:
        allowed_message_fields = {
            "container",
            "content",
            "id",
            "model",
            "role",
            "stop_details",
            "stop_reason",
            "stop_sequence",
            "type",
            "usage",
        }
        unknown_message_fields = sorted(set(message) - allowed_message_fields)
        if unknown_message_fields:
            raise ValueError(
                "Anthropic upstream message fields cannot be projected: "
                + ", ".join(unknown_message_fields)
            )
        for field in ("container", "stop_details"):
            if message.get(field) is not None:
                raise ValueError(
                    f"Anthropic upstream message.{field} cannot be projected"
                )
        message_id = message.get("id")
        if not isinstance(message_id, str) or not message_id.strip():
            raise ValueError("Anthropic upstream message.id is required")
        if message.get("type") != "message":
            raise ValueError("Anthropic upstream message.type must be 'message'")
        if message.get("role") != "assistant":
            raise ValueError("Anthropic upstream message.role must be 'assistant'")
        model_value = message.get("model")
        if not isinstance(model_value, str) or not model_value.strip():
            raise ValueError("Anthropic upstream message.model is required")
        projected_usage = _usage_from_anthropic(message, strict=True)
        response_suffix = message_id[4:] if message_id.startswith("msg_") else message_id
        response_id = f"resp_{response_suffix}"
    else:
        projected_usage = _usage_from_anthropic(message)
        response_id = f"resp_{uuid.uuid4().hex}"
    created_at = int(time.time())
    model = str(message.get("model") or (source_body or {}).get("model") or "")
    source_tools = _responses_source_tools(source_body)
    content_value = message.get("content")
    if strict and not isinstance(content_value, list):
        raise ValueError("Anthropic upstream message.content must be an array")
    content_blocks = content_value if isinstance(content_value, list) else []
    stop_reason_value = message.get("stop_reason")
    if strict and (
        not isinstance(stop_reason_value, str) or not stop_reason_value.strip()
    ):
        raise ValueError("Anthropic upstream message.stop_reason is required")
    stop_reason = (
        stop_reason_value.strip().lower()
        if isinstance(stop_reason_value, str)
        else "end_turn"
    )
    supported_stop_reasons = {
        "end_turn",
        "max_tokens",
        "refusal",
        "stop_sequence",
        "tool_use",
    }
    if stop_reason not in supported_stop_reasons:
        raise ValueError(
            f"Anthropic upstream stop_reason cannot be projected: {stop_reason!r}"
        )
    if strict and stop_reason == "stop_sequence":
        raise ValueError(
            "Anthropic upstream stop_sequence termination cannot be represented "
            "by Responses"
        )
    if stop_reason != "stop_sequence" and message.get("stop_sequence") is not None:
        raise ValueError(
            "Anthropic upstream stop_sequence conflicts with stop_reason"
        )
    response_status = "incomplete" if stop_reason == "max_tokens" else "completed"
    output: list[dict[str, Any]] = []
    saw_tool_use = False
    saw_refusal_text = False
    for index, block in enumerate(content_blocks):
        if not isinstance(block, dict):
            if strict:
                raise ValueError(
                    f"Anthropic upstream content[{index}] must be an object"
                )
            continue
        block_type = block.get("type")
        if block_type == "text":
            if strict and set(block) - {"type", "text", "citations"}:
                raise ValueError(
                    f"Anthropic upstream text content[{index}] has unsupported fields"
                )
            if strict and block.get("citations") not in (None, []):
                raise ValueError(
                    "Anthropic upstream text citations cannot be projected to "
                    "Responses annotations"
                )
            text_value = block.get("text", "")
            if strict and not isinstance(text_value, str):
                raise ValueError(
                    f"Anthropic upstream content[{index}].text must be a string"
                )
            text = str(text_value or "")
            part = (
                {"type": "refusal", "refusal": text}
                if stop_reason == "refusal"
                else {"type": "output_text", "text": text, "annotations": []}
            )
            output.append(
                {
                    "id": router_synthesized_item_id("msg", response_id, index),
                    "type": "message",
                    "status": response_status,
                    "role": "assistant",
                    "content": [part],
                }
            )
            saw_refusal_text = saw_refusal_text or stop_reason == "refusal"
        elif block_type == "tool_use":
            toolset_name = block.get("toolset_name")
            if strict:
                allowed_tool_fields = {
                    "caller",
                    "id",
                    "input",
                    "name",
                    "toolset_name",
                    "type",
                }
                if set(block) - allowed_tool_fields:
                    raise ValueError(
                        f"Anthropic upstream tool_use content[{index}] has "
                        "invalid fields"
                    )
                caller = block.get("caller")
                if caller is not None and caller != {"type": "direct"}:
                    raise ValueError(
                        "Anthropic upstream tool_use caller cannot be projected"
                    )
                if toolset_name is not None and (
                    not isinstance(toolset_name, str) or not toolset_name
                ):
                    raise ValueError(
                        "Anthropic upstream tool_use toolset_name must be a "
                        "non-empty string"
                    )
            call_id_value = block.get("id")
            name_value = block.get("name")
            input_value = block.get("input")
            if strict and (
                not isinstance(call_id_value, str)
                or not call_id_value.strip()
                or not isinstance(name_value, str)
                or not name_value.strip()
                or not isinstance(input_value, dict)
            ):
                raise ValueError(
                    "Anthropic upstream tool_use requires non-empty id/name and "
                    "object input"
                )
            if stop_reason == "max_tokens":
                raise ValueError(
                    "Anthropic max_tokens termination cannot contain a completed tool_use"
                )
            call_id = str(call_id_value or f"call_{index + 1}")
            name = str(name_value or "tool")
            namespace_identity = _namespace_tool_identity(
                source_tools, name
            )
            if strict and toolset_name is not None and (
                namespace_identity is None
                or toolset_name != namespace_identity[0]
            ):
                raise ValueError(
                    "Anthropic upstream tool_use toolset_name does not match "
                    "the source Responses namespace declaration"
                )
            output_name = namespace_identity[1] if namespace_identity else name
            custom_source = _custom_tool_source(source_tools, name)
            is_custom = (
                namespace_identity is not None
                and namespace_identity[2] == "custom"
            ) or custom_source is not None
            if is_custom:
                raw_input = _raw_custom_tool_input(input_value, strict=True)
                if strict:
                    if custom_source is None:
                        raise ValueError(
                            "Anthropic custom tool_use does not match a unique "
                            "source Responses custom tool"
                        )
                    definition = _validated_custom_lark_definition(
                        custom_source.get("format"),
                        field="Responses custom tool format",
                    )
                    if definition is not None:
                        _validate_codex_custom_tool_input(
                            raw_input,
                            definition,
                            field="Anthropic custom tool_use input",
                        )
                tool_item: dict[str, Any] = {
                    "id": router_synthesized_item_id("ctc", response_id, index),
                    "type": "custom_tool_call",
                    "status": "completed",
                    "call_id": call_id,
                    "name": output_name,
                    "input": raw_input,
                }
            else:
                tool_item = {
                    "id": router_synthesized_item_id("fc", response_id, index),
                    "type": "function_call",
                    "status": "completed",
                    "call_id": call_id,
                    "name": output_name,
                    "arguments": json.dumps(input_value or {}, ensure_ascii=False),
                }
            if block.get("caller") is not None:
                tool_item["caller"] = {"type": "direct"}
            if namespace_identity is not None:
                tool_item["namespace"] = namespace_identity[0]
            elif not strict and toolset_name is not None:
                tool_item["namespace"] = toolset_name
            output.append(tool_item)
            saw_tool_use = True
        elif block_type == "thinking":
            if strict and (
                set(block) != {"type", "thinking", "signature"}
                or not isinstance(block.get("thinking"), str)
                or not isinstance(block.get("signature"), str)
                or not block["signature"]
            ):
                raise ValueError(
                    "Anthropic upstream thinking requires thinking/signature strings"
                )
            thinking = str(block.get("thinking") or "")
            if thinking:
                item = {
                    "id": router_synthesized_item_id("rs", response_id, index),
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": thinking}],
                }
                if isinstance(block.get("signature"), str) and block["signature"]:
                    item["encrypted_content"] = _anthropic_reasoning_envelope(block)
                output.append(item)
        elif block_type == "redacted_thinking":
            if strict and (
                set(block) != {"type", "data"}
                or not isinstance(block.get("data"), str)
                or not block["data"]
            ):
                raise ValueError(
                    "Anthropic upstream redacted_thinking requires opaque data"
                )
            if isinstance(block.get("data"), str) and block["data"]:
                output.append(
                    {
                        "id": router_synthesized_item_id("rs", response_id, index),
                        "type": "reasoning",
                        "summary": [],
                        "encrypted_content": _anthropic_reasoning_envelope(block),
                    }
                )
        elif strict:
            raise ValueError(
                "Anthropic upstream content type cannot be projected to Responses: "
                f"{block_type!r}"
            )
    if strict:
        if stop_reason == "tool_use" and not saw_tool_use:
            raise ValueError(
                "Anthropic upstream stop_reason tool_use requires a tool_use block"
            )
        if stop_reason != "tool_use" and saw_tool_use:
            raise ValueError(
                "Anthropic upstream tool_use block conflicts with stop_reason"
            )
        if stop_reason == "refusal" and not saw_refusal_text:
            raise ValueError(
                "Anthropic upstream refusal requires a text content block"
            )
        if not output:
            raise ValueError("Anthropic upstream returned no projectable output")
    response: dict[str, Any] = {
        "id": response_id,
        "object": "response",
        "created_at": created_at,
        "status": response_status,
        "model": model,
        "output": output,
        "parallel_tool_calls": bool((source_body or {}).get("parallel_tool_calls", True)),
        "tool_choice": (source_body or {}).get("tool_choice", "auto"),
        "tools": (source_body or {}).get("tools", []),
        "usage": projected_usage,
    }
    anthropic_usage = message.get("usage")
    if isinstance(anthropic_usage, dict):
        metadata: dict[str, str] = {}
        cache_creation = anthropic_usage.get("cache_creation")
        if cache_creation is not None:
            metadata["ciel_anthropic_cache_creation"] = json.dumps(
                cache_creation,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        inference_geo = anthropic_usage.get("inference_geo")
        if inference_geo is not None:
            metadata["ciel_anthropic_inference_geo"] = str(inference_geo)
        if metadata:
            response["metadata"] = metadata
    anthropic_service_tier = (
        message.get("usage", {}).get("service_tier")
        if isinstance(message.get("usage"), dict)
        else None
    )
    if anthropic_service_tier is not None:
        service_tier_mapping = {
            "standard": "default",
            "priority": "priority",
        }
        if anthropic_service_tier not in service_tier_mapping:
            raise ValueError(
                "Anthropic upstream usage.service_tier cannot be projected: "
                f"{anthropic_service_tier!r}"
            )
        response["service_tier"] = service_tier_mapping[anthropic_service_tier]
    if response_status == "incomplete":
        response["incomplete_details"] = {"reason": "max_output_tokens"}
    return response


class OpenAIResponsesProtocolAdapter(MessageProtocolAdapter):
    """Concrete adapter between OpenAI Responses and Anthropic Messages."""

    name = "openai_responses"

    def __init__(self, *, fallback_model: str = "model", source_body: dict[str, Any] | None = None) -> None:
        self._fallback_model = fallback_model
        self._source_body = source_body

    def normalize_request(self, request: Any) -> dict[str, Any]:
        return openai_responses_to_anthropic_messages(dict(request), self._fallback_model)

    def normalize_response(self, response: Any) -> dict[str, Any]:
        return anthropic_message_to_openai_response(dict(response), self._source_body)


__all__ = [
    "OpenAIResponsesProtocolAdapter",
    "anthropic_messages_to_openai_responses",
    "anthropic_message_to_openai_response",
    "openai_response_to_anthropic_message",
    "openai_responses_to_anthropic_messages",
    "strip_openai_responses_reasoning_envelopes",
]
