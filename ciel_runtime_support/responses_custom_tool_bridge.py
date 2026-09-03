"""Bridge OpenAI Responses custom tools through function-only providers."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Mapping


def tool_definitions(body: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Return uniquely named top-level function and custom Responses tools."""

    tools = body.get("tools")
    if not isinstance(tools, list):
        return {}
    found: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    for tool in tools:
        if (
            not isinstance(tool, Mapping)
            or tool.get("type") not in {"function", "custom"}
        ):
            continue
        name = str(tool.get("name") or "").strip()
        if not name:
            continue
        if name in found:
            duplicates.add(name)
        else:
            found[name] = deepcopy(dict(tool))
    for name in duplicates:
        found.pop(name, None)
    return found


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
    item: Any, tool_definitions_by_name: Mapping[str, Mapping[str, Any]]
) -> Any:
    if not isinstance(item, Mapping):
        return deepcopy(item)
    projected = deepcopy(dict(item))
    name = str(projected.get("name") or "")
    source_tool = tool_definitions_by_name.get(name)
    if projected.get("type") == "function_call" and isinstance(source_tool, Mapping):
        schema = source_tool.get("parameters")
        projected["arguments"] = _normalized_function_arguments(
            projected.get("arguments"), schema
        )
    if (
        projected.get("type") == "function_call"
        and isinstance(source_tool, Mapping)
        and source_tool.get("type") == "custom"
    ):
        projected["type"] = "custom_tool_call"
        projected["input"] = _raw_custom_input(projected.pop("arguments", ""))
    return projected


def project_response_payload(
    payload: Any,
    tools: Mapping[str, Mapping[str, Any]],
) -> Any:
    """Restore function-enveloped calls to the custom-tool Responses contract."""

    if not isinstance(payload, Mapping) or not tools:
        return deepcopy(payload)
    custom_tools = {
        name: tool for name, tool in tools.items() if tool.get("type") == "custom"
    }
    projected = deepcopy(dict(payload))
    output = projected.get("output")
    if isinstance(output, list):
        projected["output"] = [_project_tool_item(item, tools) for item in output]
    response_tools = projected.get("tools")
    if isinstance(response_tools, list):
        restored: list[Any] = []
        for tool in response_tools:
            if (
                isinstance(tool, Mapping)
                and tool.get("type") == "function"
                and str(tool.get("name") or "") in custom_tools
            ):
                restored.append(deepcopy(dict(custom_tools[str(tool["name"])])))
            else:
                restored.append(deepcopy(tool))
        projected["tools"] = restored
    return projected


class ResponsesCustomToolStreamProjector:
    """Incrementally restore custom-tool item/event shapes in an SSE stream."""

    def __init__(self, tools: Mapping[str, Mapping[str, Any]]) -> None:
        self._tools = {
            str(name): deepcopy(dict(tool)) for name, tool in tools.items()
        }
        self._custom_tools = {
            str(name): deepcopy(dict(tool)) for name, tool in tools.items()
            if tool.get("type") == "custom"
        }
        self._buffer = b""
        self._item_names: dict[str, str] = {}
        self._index_names: dict[int, str] = {}

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
    def _encode_event(event: Mapping[str, Any]) -> bytes:
        event_type = str(event.get("type") or "message")
        data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        return f"event: {event_type}\ndata: {data}\n\n".encode("utf-8")

    def _is_projected_event(self, event: Mapping[str, Any]) -> bool:
        item_id = str(event.get("item_id") or "")
        output_index = event.get("output_index")
        return item_id in self._item_names or (
            isinstance(output_index, int) and output_index in self._index_names
        )

    def _project_event(self, event: Mapping[str, Any]) -> list[dict[str, Any]]:
        event_type = str(event.get("type") or "")
        projected = deepcopy(dict(event))
        names = set(self._tools)

        if event_type in {"response.output_item.added", "response.output_item.done"}:
            item = projected.get("item")
            if (
                isinstance(item, Mapping)
                and item.get("type") == "function_call"
                and str(item.get("name") or "") in names
            ):
                item_id = str(item.get("id") or "")
                output_index = projected.get("output_index")
                tool_name = str(item.get("name") or "")
                if item_id:
                    self._item_names[item_id] = tool_name
                if isinstance(output_index, int):
                    self._index_names[output_index] = tool_name
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
            tool_name = self._item_names.get(item_id) or (
                self._index_names.get(output_index)
                if isinstance(output_index, int)
                else None
            )
            source_tool = self._tools.get(str(tool_name or ""), {})
            arguments = _normalized_function_arguments(
                projected.get("arguments"), source_tool.get("parameters")
            )
            is_custom = source_tool.get("type") == "custom"
            if is_custom:
                raw_value = _raw_custom_input(arguments)
                projected.pop("arguments", None)
                projected["type"] = "response.custom_tool_call_input.done"
                projected["input"] = raw_value
                delta_type = "response.custom_tool_call_input.delta"
                done_field = "input"
            else:
                raw_value = arguments
                projected["arguments"] = arguments
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

    def _project_frame(self, frame: bytes) -> bytes:
        normalized = frame.replace(b"\r\n", b"\n")
        data_lines = [line[6:] for line in normalized.splitlines() if line.startswith(b"data: ")]
        if not data_lines:
            return frame + b"\n\n"
        raw_data = b"\n".join(data_lines)
        if raw_data == b"[DONE]":
            return frame + b"\n\n"
        try:
            event = json.loads(raw_data)
        except (UnicodeDecodeError, ValueError):
            return frame + b"\n\n"
        if not isinstance(event, Mapping):
            return frame + b"\n\n"
        return b"".join(self._encode_event(item) for item in self._project_event(event))

    def feed(self, chunk: bytes) -> bytes:
        self._buffer += chunk
        output = bytearray()
        while (separator := self._separator(self._buffer)) is not None:
            offset, size = separator
            frame = self._buffer[:offset]
            self._buffer = self._buffer[offset + size :]
            output.extend(self._project_frame(frame))
        return bytes(output)

    def finish(self) -> bytes:
        if not self._buffer:
            return b""
        frame, self._buffer = self._buffer, b""
        return self._project_frame(frame)


__all__ = [
    "ResponsesCustomToolStreamProjector",
    "project_response_payload",
    "tool_definitions",
]
