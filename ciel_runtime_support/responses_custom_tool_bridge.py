"""Bridge OpenAI Responses custom tools through function-only providers."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Mapping

from .protocols.openai_responses import _namespace_tool_alias


ToolKey = str | tuple[str, str]


def tool_definitions(
    body: Mapping[str, Any], *, include_functions: bool = True
) -> dict[ToolKey, dict[str, Any]]:
    """Index original declarations, retaining only unambiguous identities.

    Resolve explicit namespace/member pairs independently of wire aliases and
    bare names. An exact top-level name owns unqualified calls, even if that
    declaration is duplicated or excluded from the returned custom-only index.
    """

    tools = body.get("tools")
    declarations = list(tools) if isinstance(tools, list) else []
    raw_input = body.get("input")
    if isinstance(raw_input, list):
        for item in raw_input:
            if isinstance(item, Mapping) and item.get("type") == "additional_tools":
                tools = item.get("tools")
                if isinstance(tools, list):
                    declarations.extend(tools)
    candidates: dict[ToolKey, list[dict[str, Any]]] = {}
    top_level: dict[str, list[dict[str, Any]]] = {}

    def collect(tools: list[Any], namespace: str | None = None) -> None:
        for tool in tools:
            if not isinstance(tool, Mapping):
                continue
            name = tool.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            name = name.strip()
            if tool.get("type") == "namespace":
                members = tool.get("tools")
                if isinstance(members, list):
                    collect(members, name)
                continue
            if tool.get("type") not in {"function", "custom"}:
                continue
            definition = deepcopy(dict(tool))
            definition["name"] = name
            if namespace is None:
                top_level.setdefault(name, []).append(definition)
            else:
                definition["namespace"] = namespace
                keys: set[ToolKey] = {
                    (namespace, name), name, _namespace_tool_alias(namespace, name)
                }
                for key in keys:
                    candidates.setdefault(key, []).append(definition)

    collect(declarations)
    candidates.update(top_level)
    return {
        key: matches[0]
        for key, matches in candidates.items()
        if len(matches) == 1
        and (include_functions or matches[0].get("type") == "custom")
    }


def _source_tool(
    item: Mapping[str, Any], tools: Mapping[ToolKey, Mapping[str, Any]]
) -> Mapping[str, Any] | None:
    name = str(item.get("name") or "")
    namespace = item.get("namespace")
    if namespace is not None:
        if not isinstance(namespace, str) or not namespace:
            return None
        return tools.get((namespace, name))
    return tools.get(name)


def _raw_custom_input(arguments: Any) -> str:
    if not isinstance(arguments, str) or not arguments:
        return ""
    try:
        value = json.loads(arguments)
    except (TypeError, ValueError):
        return arguments
    if isinstance(value, Mapping) and isinstance(value.get("input"), str):
        return value["input"]
    return arguments


def _coerce_schema_integers(value: Any, schema: Mapping[str, Any]) -> Any:
    # Meta can serialize whole-number tool arguments as JSON floats even when
    # the receiving Codex field is a Rust integer.  JSON 1000 and 1000.0 have
    # the same numeric value, so remove only a zero fractional component.
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, Mapping):
        properties = schema.get("properties")
        if isinstance(properties, Mapping):
            return {
                key: _coerce_schema_integers(item, properties.get(key, {}))
                if isinstance(properties.get(key), Mapping)
                else deepcopy(item)
                for key, item in value.items()
            }
    if isinstance(value, list) and isinstance(schema.get("items"), Mapping):
        return [_coerce_schema_integers(item, schema["items"]) for item in value]
    return deepcopy(value)


def _normalized_function_arguments(arguments: Any, schema: Any) -> str:
    if not isinstance(arguments, str) or not arguments or not isinstance(schema, Mapping):
        return str(arguments or "")
    try:
        value = json.loads(arguments)
    except (TypeError, ValueError):
        return arguments
    normalized = _coerce_schema_integers(value, schema)
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))


def _project_tool_item(
    item: Any, tools: Mapping[ToolKey, Mapping[str, Any]]
) -> Any:
    if not isinstance(item, Mapping):
        return deepcopy(item)
    projected = deepcopy(dict(item))
    source_tool = _source_tool(projected, tools)
    if projected.get("type") != "function_call" or source_tool is None:
        return projected
    if source_tool.get("namespace"):
        projected["namespace"] = source_tool["namespace"]
        projected["name"] = source_tool["name"]
    if source_tool.get("type") == "custom":
        projected["type"] = "custom_tool_call"
        projected["input"] = _raw_custom_input(projected.pop("arguments", ""))
    else:
        projected["arguments"] = _normalized_function_arguments(
            projected.get("arguments"), source_tool.get("parameters")
        )
    return projected


def project_response_payload(
    payload: Any,
    tools: Mapping[ToolKey, Mapping[str, Any]],
) -> Any:
    """Restore function-enveloped calls to the custom-tool Responses contract."""

    if not isinstance(payload, Mapping) or not tools:
        return deepcopy(payload)
    projected = deepcopy(dict(payload))
    output = projected.get("output")
    if isinstance(output, list):
        projected["output"] = [_project_tool_item(item, tools) for item in output]
    response_tools = projected.get("tools")
    if isinstance(response_tools, list):
        restored: list[Any] = []
        for tool in response_tools:
            source_tool = _source_tool(tool, tools) if isinstance(tool, Mapping) else None
            if (
                isinstance(tool, Mapping)
                and tool.get("type") == "function"
                and source_tool is not None
                and source_tool.get("type") == "custom"
            ):
                restored.append(deepcopy(dict(source_tool)))
            else:
                restored.append(deepcopy(tool))
        projected["tools"] = restored
    return projected


class ResponsesCustomToolStreamProjector:
    """Incrementally restore custom-tool item/event shapes in an SSE stream."""

    def __init__(self, tools: Mapping[ToolKey, Mapping[str, Any]]) -> None:
        self._tools = {key: deepcopy(dict(tool)) for key, tool in tools.items()}
        self._buffer = b""
        self._item_tools: dict[str, Mapping[str, Any]] = {}
        self._index_tools: dict[int, Mapping[str, Any]] = {}

    @staticmethod
    def _separator(buffer: bytes) -> tuple[int, int] | None:
        lf = buffer.find(b"\n\n")
        crlf = buffer.find(b"\r\n\r\n")
        candidates = [
            (offset, size)
            for offset, size in ((lf, 2), (crlf, 4))
            if offset >= 0
        ]
        return min(candidates) if candidates else None

    @staticmethod
    def _encode_event(
        event: Mapping[str, Any], metadata: list[bytes], newline: bytes
    ) -> bytes:
        event_type = str(event.get("type") or "message")
        data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        lines = [
            *metadata,
            f"event: {event_type}".encode("utf-8"),
            f"data: {data}".encode("utf-8"),
            b"", b"",
        ]
        return newline.join(lines)

    def _is_projected_event(self, event: Mapping[str, Any]) -> bool:
        item_id = str(event.get("item_id") or "")
        output_index = event.get("output_index")
        return item_id in self._item_tools or (
            isinstance(output_index, int) and output_index in self._index_tools
        )

    def _project_event(self, event: Mapping[str, Any]) -> list[dict[str, Any]]:
        event_type = str(event.get("type") or "")
        projected = deepcopy(dict(event))
        if event_type in {"response.output_item.added", "response.output_item.done"}:
            item = projected.get("item")
            if isinstance(item, Mapping) and item.get("type") == "function_call":
                source_tool = _source_tool(item, self._tools)
                if source_tool is not None:
                    item_id = str(item.get("id") or "")
                    output_index = projected.get("output_index")
                    if item_id:
                        self._item_tools[item_id] = source_tool
                    if isinstance(output_index, int):
                        self._index_tools[output_index] = source_tool
                    projected["item"] = _project_tool_item(item, self._tools)
            return [projected]

        if event_type == "response.function_call_arguments.delta" and self._is_projected_event(projected):
            # The provider is streaming a JSON object envelope.  Wait for the
            # completed value so partial JSON escaping is never exposed as raw
            # custom-tool input.
            return []

        if event_type == "response.function_call_arguments.done" and self._is_projected_event(projected):
            item_id = str(projected.get("item_id") or "")
            output_index = projected.get("output_index")
            source_tool = self._item_tools.get(item_id) or (
                self._index_tools.get(output_index, {})
                if isinstance(output_index, int)
                else {}
            )
            if source_tool.get("type") == "custom":
                raw_value = _raw_custom_input(projected.get("arguments"))
                projected.pop("arguments", None)
                projected["type"] = "response.custom_tool_call_input.done"
                projected["input"] = raw_value
                delta_type = "response.custom_tool_call_input.delta"
                done_field = "input"
            else:
                raw_value = _normalized_function_arguments(
                    projected.get("arguments"), source_tool.get("parameters")
                )
                projected["arguments"] = raw_value
                delta_type = "response.function_call_arguments.delta"
                done_field = "arguments"
            events: list[dict[str, Any]] = []
            if raw_value:
                delta = {
                    key: deepcopy(value)
                    for key, value in projected.items()
                    if key not in {"type", done_field}
                }
                delta["type"] = delta_type
                delta["delta"] = raw_value
                events.append(delta)
            events.append(projected)
            return events

        response = projected.get("response")
        if isinstance(response, Mapping):
            projected["response"] = project_response_payload(
                response, self._tools
            )
        return [projected]

    def _project_frame(self, frame: bytes, separator: bytes = b"\n\n") -> bytes:
        newline = b"\r\n" if separator == b"\r\n\r\n" else b"\n"
        original = frame + separator
        lines = frame.replace(b"\r\n", b"\n").split(b"\n")
        data_lines: list[bytes] = []
        event_type = b""
        metadata: list[bytes] = []
        for line in lines:
            field, _, value = line.partition(b":")
            if value.startswith(b" "):
                value = value[1:]
            if field == b"data":
                data_lines.append(value)
            elif field == b"event":
                event_type = value
            else:
                metadata.append(line)
        if not data_lines:
            return original
        raw_data = b"\n".join(data_lines)
        if raw_data == b"[DONE]":
            return original
        try:
            event = json.loads(raw_data)
            if not isinstance(event, Mapping):
                return original
            if not event.get("type") and event_type:
                event = {**event, "type": event_type.decode("utf-8")}
        except (UnicodeDecodeError, ValueError):
            return original
        projected = self._project_event(event)
        if projected == [event]:
            return original
        return b"".join(
            self._encode_event(item, metadata, newline) for item in projected
        )

    def feed(self, chunk: bytes) -> bytes:
        self._buffer += chunk
        output = bytearray()
        while (separator := self._separator(self._buffer)) is not None:
            offset, size = separator
            frame = self._buffer[:offset]
            delimiter = self._buffer[offset : offset + size]
            self._buffer = self._buffer[offset + size :]
            output.extend(self._project_frame(frame, delimiter))
        return bytes(output)

    def finish(self) -> bytes:
        if not self._buffer:
            return b""
        frame, self._buffer = self._buffer, b""
        return self._project_frame(frame)


__all__ = [
    "ResponsesCustomToolStreamProjector",
    "ToolKey",
    "project_response_payload",
    "tool_definitions",
]
