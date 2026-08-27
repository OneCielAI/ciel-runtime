"""Incremental OpenAI Responses SSE to Anthropic Messages SSE projection."""

from __future__ import annotations

import copy
import json
import uuid
from typing import Any, Callable, Iterable

from .protocols.openai_responses import _commentary_to_redacted_block
from .remote_bridge import is_remote_bridge_request


def _iter_events(response: Iterable[Any]) -> Iterable[dict[str, Any]]:
    event_name = ""
    data_lines: list[str] = []
    for raw_line in response:
        line = (
            bytes(raw_line).decode("utf-8")
            if isinstance(raw_line, (bytes, bytearray))
            else str(raw_line)
        ).rstrip("\r\n")
        if not line:
            if data_lines:
                data = "\n".join(data_lines)
                if data != "[DONE]":
                    payload = json.loads(data)
                    if isinstance(payload, dict):
                        payload.setdefault("type", event_name)
                        yield payload
            event_name = ""
            data_lines = []
            continue
        if line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        data = "\n".join(data_lines)
        if data != "[DONE]":
            payload = json.loads(data)
            if isinstance(payload, dict):
                payload.setdefault("type", event_name)
                yield payload


class _StreamProjectionError(ValueError):
    """A provider stream cannot be represented as a successful Anthropic stream."""


class ResponsesAnthropicStreamWriter:
    def __init__(self, to_anthropic: Callable[..., dict[str, Any]]) -> None:
        self._to_anthropic = to_anthropic

    @staticmethod
    def _event(handler: Any, name: str, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        handler.wfile.write(f"event: {name}\ndata: {data}\n\n".encode("utf-8"))
        handler.wfile.flush()

    @staticmethod
    def _response_value(event: dict[str, Any]) -> dict[str, Any]:
        response = event.get("response")
        return response if isinstance(response, dict) else {}

    def forward(
        self,
        handler: Any,
        response: Iterable[Any],
        fallback_model: str,
    ) -> None:
        handler.send_response(200)
        handler.send_header("content-type", "text/event-stream")
        handler.send_header("cache-control", "no-cache")
        handler.send_header("connection", "close")
        handler.end_headers()

        started = False
        terminal = False
        response_id = uuid.uuid4().hex
        model = fallback_model
        upstream_identity: tuple[str, str] | None = None
        next_index = 0
        item_states: dict[str, dict[str, Any]] = {}
        content_states: dict[tuple[str, int, str], dict[str, Any]] = {}
        item_aliases: dict[str, str] = {}
        saw_tool_use = False
        saw_refusal = False
        strict_remote = is_remote_bridge_request(handler)

        def emit_error(message: str) -> None:
            nonlocal terminal
            if terminal:
                return
            self._event(
                handler,
                "error",
                {
                    "type": "error",
                    "error": {"type": "api_error", "message": message},
                },
            )
            terminal = True

        def project(
            value: dict[str, Any], *, strict: bool = False
        ) -> dict[str, Any]:
            try:
                if strict:
                    projected = self._to_anthropic(value, model, strict=True)
                else:
                    projected = self._to_anthropic(value, model)
            except Exception as exc:
                raise _StreamProjectionError(str(exc)) from exc
            if not isinstance(projected, dict):
                raise _StreamProjectionError(
                    "Responses-to-Anthropic projection returned a non-object"
                )
            return projected

        def validate_response_identity(value: dict[str, Any]) -> None:
            nonlocal upstream_identity
            if not strict_remote:
                return
            response_id_value = value.get("id")
            model_value = value.get("model")
            status_value = value.get("status")
            if not isinstance(response_id_value, str) or not response_id_value.strip():
                raise _StreamProjectionError(
                    "Responses stream response.id is required"
                )
            if value.get("object") != "response":
                raise _StreamProjectionError(
                    "Responses stream response.object must be 'response'"
                )
            if not isinstance(model_value, str) or not model_value.strip():
                raise _StreamProjectionError(
                    "Responses stream response.model is required"
                )
            if not isinstance(status_value, str) or not status_value.strip():
                raise _StreamProjectionError(
                    "Responses stream response.status is required"
                )
            current_identity = (response_id_value, model_value)
            if upstream_identity is None:
                upstream_identity = current_identity
            elif upstream_identity != current_identity:
                raise _StreamProjectionError(
                    "Responses stream response id/model changed during the request"
                )

        def start(value: dict[str, Any] | None = None) -> None:
            nonlocal started, response_id, model
            if started:
                return
            value = value or {}
            validate_response_identity(value)
            raw_id = str(value.get("id") or response_id)
            response_id = raw_id[5:] if raw_id.startswith("resp_") else raw_id
            model = str(value.get("model") or model)
            self._event(
                handler,
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": f"msg_{response_id}",
                        "type": "message",
                        "role": "assistant",
                        "model": model,
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                    },
                },
            )
            started = True

        def canonical_item_key(
            event: dict[str, Any],
            item: dict[str, Any],
            fallback_index: int | None = None,
        ) -> str:
            aliases = [
                str(value)
                for value in (event.get("item_id"), item.get("id"))
                if value is not None and str(value)
            ]
            for alias in aliases:
                if alias in item_aliases:
                    return item_aliases[alias]
            raw_index = event.get("output_index", fallback_index)
            if raw_index is not None:
                key = f"output:{raw_index}"
            elif aliases:
                key = f"item:{aliases[0]}"
            else:
                key = f"item:auto:{len(item_states)}"
            for alias in aliases:
                item_aliases[alias] = key
            return key

        @staticmethod
        def new_state(item_key: str, item_type: str) -> dict[str, Any]:
            return {
                "item_key": item_key,
                "index": None,
                "type": item_type,
                "opened": False,
                "closed": False,
                "emitted": "",
                "buffer": "",
                "final_payload": "",
                "raw_final_payload": "",
                "item": {},
                "commentary_envelope_emitted": False,
                "completed_item": None,
            }

        def item_state_for(item_key: str, item_type: str = "message") -> dict[str, Any]:
            state = item_states.get(item_key)
            if state is None:
                state = new_state(item_key, item_type)
                item_states[item_key] = state
            elif item_type and state["type"] in {"", "message"}:
                state["type"] = item_type
            return state

        def content_state_for(
            item_key: str, content_index: int, kind: str
        ) -> dict[str, Any]:
            key = (item_key, content_index, kind)
            state = content_states.get(key)
            if state is None:
                state = new_state(item_key, kind)
                content_states[key] = state
            return state

        def allocate_index(state: dict[str, Any]) -> int:
            nonlocal next_index
            if state["index"] is None:
                state["index"] = next_index
                next_index += 1
            return int(state["index"])

        def open_text(state: dict[str, Any]) -> None:
            if state["opened"]:
                return
            index = allocate_index(state)
            self._event(
                handler,
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {"type": "text", "text": ""},
                },
            )
            state["opened"] = True

        def open_tool(
            state: dict[str, Any], *, required: bool, emit: bool = True
        ) -> bool:
            nonlocal saw_tool_use
            item = state.get("item") if isinstance(state.get("item"), dict) else {}
            item_type = str(item.get("type") or state.get("type") or "function_call")
            if strict_remote:
                payload_field = "input" if item_type == "custom_tool_call" else "arguments"
                allowed_fields = {
                    "call_id",
                    "caller",
                    "id",
                    "name",
                    "namespace",
                    "status",
                    "type",
                    payload_field,
                }
                unknown_fields = sorted(set(item) - allowed_fields)
                if unknown_fields:
                    raise _StreamProjectionError(
                        "Responses tool call fields cannot be projected: "
                        + ", ".join(unknown_fields)
                    )
                item_id = item.get("id")
                if not isinstance(item_id, str) or not item_id.strip():
                    raise _StreamProjectionError(
                        "Responses tool call requires a non-empty id"
                    )
                item_status = item.get("status")
                if item_status not in {"in_progress", "completed"}:
                    raise _StreamProjectionError(
                        "Responses tool call requires an in_progress or completed status"
                    )
                if payload_field in item and not isinstance(
                    item.get(payload_field), str
                ):
                    raise _StreamProjectionError(
                        f"Responses {item_type}.{payload_field} must be a string"
                    )
                caller = item.get("caller")
                if caller is not None and caller != {"type": "direct"}:
                    raise _StreamProjectionError(
                        "Responses tool caller cannot be projected to Anthropic"
                    )
                namespace = item.get("namespace")
                if namespace is not None and (
                    not isinstance(namespace, str) or not namespace
                ):
                    raise _StreamProjectionError(
                        "Responses tool namespace must be a non-empty string"
                    )
            call_id = str(
                item.get("call_id")
                or (None if strict_remote else item.get("id"))
                or ""
            ).strip()
            name = str(item.get("name") or "").strip()
            if not call_id or not name:
                if required:
                    raise _StreamProjectionError(
                        "Responses tool call requires non-empty call_id and name"
                    )
                return False
            if not emit:
                return True
            if state["opened"]:
                return True
            index = allocate_index(state)
            content_block: dict[str, Any] = {
                "type": "tool_use",
                "id": call_id,
                "name": name,
                "input": {},
            }
            if item.get("caller") == {"type": "direct"}:
                content_block["caller"] = {"type": "direct"}
            if item.get("namespace") is not None:
                content_block["toolset_name"] = item["namespace"]
            self._event(
                handler,
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": content_block,
                },
            )
            state["opened"] = True
            saw_tool_use = True
            return True

        def close(state: dict[str, Any]) -> None:
            if not state["opened"] or state["closed"]:
                return
            self._event(
                handler,
                "content_block_stop",
                {"type": "content_block_stop", "index": state["index"]},
            )
            state["closed"] = True

        def emit_full_block(state: dict[str, Any], block: dict[str, Any]) -> None:
            if state["closed"]:
                return
            index = allocate_index(state)
            self._event(
                handler,
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": block,
                },
            )
            self._event(
                handler,
                "content_block_stop",
                {"type": "content_block_stop", "index": index},
            )
            state["opened"] = True
            state["closed"] = True

        def emit_text_delta(state: dict[str, Any], delta: str) -> None:
            if not delta:
                return
            if state["closed"]:
                raise _StreamProjectionError(
                    "Responses text arrived after its content block was closed"
                )
            open_text(state)
            self._event(
                handler,
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": state["index"],
                    "delta": {"type": "text_delta", "text": delta},
                },
            )
            state["emitted"] += delta

        def emit_remaining_text(state: dict[str, Any], final_text: str) -> None:
            emitted = str(state.get("emitted") or "")
            if state["closed"]:
                if final_text and final_text != emitted:
                    raise _StreamProjectionError(
                        "Responses completed text did not match streamed text"
                    )
                return
            if not final_text:
                return
            if emitted and not final_text.startswith(emitted):
                raise _StreamProjectionError(
                    "Responses completed text did not match streamed text"
                )
            emit_text_delta(state, final_text[len(emitted) :])

        def emit_input_json_delta(state: dict[str, Any], partial_json: str) -> None:
            if not partial_json:
                return
            if state["closed"]:
                raise _StreamProjectionError(
                    "Responses tool arguments arrived after the tool block was closed"
                )
            if not open_tool(state, required=True):
                return
            self._event(
                handler,
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": state["index"],
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": partial_json,
                    },
                },
            )
            state["emitted"] += partial_json

        @staticmethod
        def custom_tool_payload(value: Any) -> str:
            if isinstance(value, str):
                raw = value
            elif value is None:
                raw = ""
            else:
                raw = json.dumps(value, ensure_ascii=False, sort_keys=True)
            return json.dumps(
                {"input": raw}, ensure_ascii=False, separators=(",", ":")
            )

        @staticmethod
        def validate_function_payload(payload: str) -> None:
            try:
                parsed = json.loads(payload)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise _StreamProjectionError(
                    "Responses function-call arguments were not valid JSON"
                ) from exc
            if not isinstance(parsed, dict):
                raise _StreamProjectionError(
                    "Responses function-call arguments must be a JSON object"
                )

        def emit_completed_tool_payload(
            state: dict[str, Any], value: Any, *, already_serialized: bool = False
        ) -> None:
            item_type = str(state.get("type") or "function_call")
            if already_serialized:
                payload = str(value or "")
            elif item_type == "custom_tool_call":
                payload = custom_tool_payload(value)
            else:
                payload = str(value or "")
            validate_function_payload(payload)
            emitted = str(state.get("emitted") or "")
            if emitted and not payload.startswith(emitted):
                raise _StreamProjectionError(
                    "Responses completed tool arguments did not match streamed arguments"
                )
            emit_input_json_delta(state, payload[len(emitted) :])
            state["final_payload"] = payload

        def complete_tool(
            state: dict[str, Any], item: dict[str, Any], *, emit_output: bool
        ) -> None:
            merged = {
                **(state.get("item") if isinstance(state.get("item"), dict) else {}),
                **item,
            }
            state["item"] = merged
            item_type = str(merged.get("type") or state.get("type") or "function_call")
            state["type"] = item_type
            if strict_remote and "status" not in merged:
                raise _StreamProjectionError(
                    "Responses completed tool call requires status"
                )
            status = str(merged.get("status") or "completed")
            if status != "completed":
                raise _StreamProjectionError(
                    f"Responses upstream returned incomplete tool call: {status}"
                )
            if strict_remote:
                open_tool(state, required=True, emit=False)
            field = "input" if item_type == "custom_tool_call" else "arguments"
            if (
                strict_remote
                and field in merged
                and not isinstance(merged.get(field), str)
            ):
                raise _StreamProjectionError(
                    f"Responses {item_type}.{field} must be a string"
                )
            if field in merged and merged.get(field) is not None:
                raw_value = merged[field]
                raw_payload = (
                    raw_value
                    if isinstance(raw_value, str)
                    else json.dumps(raw_value, ensure_ascii=False, sort_keys=True)
                )
                payload = (
                    custom_tool_payload(raw_value)
                    if item_type == "custom_tool_call"
                    else str(raw_payload)
                )
            elif state.get("raw_final_payload"):
                raw_payload = str(state["raw_final_payload"])
                payload = str(state.get("final_payload") or "")
            elif state.get("buffer"):
                raw_payload = str(state["buffer"])
                payload = (
                    custom_tool_payload(raw_payload)
                    if item_type == "custom_tool_call"
                    else raw_payload
                )
            else:
                raise _StreamProjectionError(
                    "Responses completed tool call did not include arguments"
                )
            if strict_remote:
                buffered = str(state.get("buffer") or "")
                done_payload = str(state.get("raw_final_payload") or "")
                if buffered and buffered != raw_payload:
                    raise _StreamProjectionError(
                        "Responses completed tool arguments did not match streamed arguments"
                    )
                if done_payload and done_payload != raw_payload:
                    raise _StreamProjectionError(
                        "Responses output item arguments did not match arguments.done"
                    )
            validate_function_payload(payload)
            state["final_payload"] = payload
            state["raw_final_payload"] = raw_payload
            if emit_output:
                emit_completed_tool_payload(
                    state, payload, already_serialized=True
                )
            if emit_output and not strict_remote:
                close(state)

        def emit_reasoning(state: dict[str, Any], item: dict[str, Any]) -> None:
            state["item"] = {
                **(state.get("item") if isinstance(state.get("item"), dict) else {}),
                **item,
            }
            if state["closed"]:
                return
            projected = project(
                {
                    "status": "completed",
                    "model": model,
                    "output": [state["item"]],
                }
            )
            blocks = [
                block
                for block in projected.get("content") or []
                if isinstance(block, dict)
            ]
            if not blocks:
                state["closed"] = True
                return
            emit_full_block(state, blocks[0])
            for offset, block in enumerate(blocks[1:], start=1):
                extra = new_state(f"{state['item_key']}:reasoning:{offset}", "reasoning")
                item_states[extra["item_key"]] = extra
                emit_full_block(extra, block)

        def content_index(event: dict[str, Any]) -> int:
            try:
                return max(0, int(event.get("content_index") or 0))
            except (TypeError, ValueError):
                return 0

        def validate_message_item(
            item: dict[str, Any], *, terminal_item: bool
        ) -> None:
            if not strict_remote:
                return
            allowed_fields = {"content", "id", "phase", "role", "status", "type"}
            unknown_fields = sorted(set(item) - allowed_fields)
            if unknown_fields:
                raise _StreamProjectionError(
                    "Responses message fields cannot be projected: "
                    + ", ".join(unknown_fields)
                )
            item_id = item.get("id")
            if not isinstance(item_id, str) or not item_id.strip():
                raise _StreamProjectionError(
                    "Responses message requires a non-empty id"
                )
            if item.get("role") != "assistant":
                raise _StreamProjectionError(
                    "Responses message role must be assistant"
                )
            status_value = item.get("status")
            allowed_statuses = (
                {"completed", "incomplete"}
                if terminal_item
                else {"in_progress", "completed", "incomplete"}
            )
            if status_value not in allowed_statuses:
                raise _StreamProjectionError(
                    "Responses message has an invalid stream status"
                )
            phase = item.get("phase")
            if phase not in (None, "commentary", "final_answer"):
                raise _StreamProjectionError(
                    f"Responses message phase is unsupported: {phase!r}"
                )
            content = item.get("content")
            if not isinstance(content, list):
                raise _StreamProjectionError(
                    "Responses message content must be an array"
                )
            for index, block in enumerate(content):
                if not isinstance(block, dict):
                    raise _StreamProjectionError(
                        f"Responses message content[{index}] must be an object"
                    )
                block_type = block.get("type")
                allowed_block_fields = (
                    {"type", "refusal"}
                    if block_type == "refusal"
                    else {"type", "text", "annotations", "logprobs"}
                )
                if block_type not in {"output_text", "refusal"} or (
                    set(block) - allowed_block_fields
                ):
                    raise _StreamProjectionError(
                        f"Responses message content[{index}] cannot be projected"
                    )
                text_value = (
                    block.get("refusal")
                    if block_type == "refusal"
                    else block.get("text")
                )
                if not isinstance(text_value, str):
                    raise _StreamProjectionError(
                        f"Responses message content[{index}] text must be a string"
                    )
                if block_type == "output_text" and (
                    block.get("annotations") not in (None, [])
                    or block.get("logprobs") not in (None, [])
                ):
                    raise _StreamProjectionError(
                        f"Responses message content[{index}] metadata cannot be projected"
                    )
                if phase == "commentary" and block_type != "output_text":
                    raise _StreamProjectionError(
                        "Responses commentary phase supports output_text content only"
                    )

        def process_message_item(
            item_key: str,
            item: dict[str, Any],
            *,
            emit_output: bool,
        ) -> None:
            nonlocal saw_refusal
            validate_message_item(item, terminal_item=True)
            raw_content = item.get("content")
            blocks = raw_content if isinstance(raw_content, list) else []
            if strict_remote:
                for (owner, index, kind), state in content_states.items():
                    if owner != item_key:
                        continue
                    if index >= len(blocks) or not isinstance(blocks[index], dict):
                        raise _StreamProjectionError(
                            "Responses streamed content is absent from its completed item"
                        )
                    completed_block = blocks[index]
                    completed_type = str(completed_block.get("type") or "")
                    expected_kind = (
                        "refusal" if completed_type == "refusal" else "text"
                    )
                    completed_text = str(
                        completed_block.get("refusal")
                        if expected_kind == "refusal"
                        else completed_block.get("text")
                        or ""
                    )
                    if kind != expected_kind:
                        raise _StreamProjectionError(
                            "Responses streamed content type changed at completion"
                        )
                    buffered = str(state.get("buffer") or "")
                    done_text = str(state.get("final_payload") or "")
                    if buffered and buffered != completed_text:
                        raise _StreamProjectionError(
                            "Responses completed text did not match streamed text"
                        )
                    if done_text and done_text != completed_text:
                        raise _StreamProjectionError(
                            "Responses output item text did not match text.done"
                        )
            if not emit_output:
                return
            for index, block in enumerate(blocks):
                if not isinstance(block, dict):
                    continue
                block_type = str(block.get("type") or "")
                if block_type == "refusal":
                    kind = "refusal"
                    text = str(block.get("refusal") or "")
                    saw_refusal = True
                elif block_type in {"output_text", "text"}:
                    kind = "text"
                    text = str(block.get("text") or "")
                else:
                    continue
                state = content_state_for(item_key, index, kind)
                emit_remaining_text(state, text)
                close(state)
            for (owner, _index, _kind), state in content_states.items():
                if owner == item_key:
                    close(state)
            item_state = item_state_for(item_key, "message")
            if (
                item.get("phase") == "commentary"
                and not item_state["commentary_envelope_emitted"]
            ):
                envelope_state = new_state(
                    f"{item_key}:commentary-envelope", "commentary"
                )
                item_states[envelope_state["item_key"]] = envelope_state
                emit_full_block(
                    envelope_state,
                    _commentary_to_redacted_block(item),
                )
                item_state["commentary_envelope_emitted"] = True

        def process_output_item(
            item: dict[str, Any],
            output_index: int | None = None,
            *,
            authoritative: bool = False,
        ) -> None:
            event = {"output_index": output_index} if output_index is not None else {}
            item_key = canonical_item_key(event, item, output_index)
            item_type = str(item.get("type") or "")
            state = item_state_for(item_key, item_type or "message")
            state["item"] = {
                **(state.get("item") if isinstance(state.get("item"), dict) else {}),
                **item,
            }
            completed_item = state.get("completed_item")
            if strict_remote and completed_item is not None and completed_item != item:
                raise _StreamProjectionError(
                    "Responses completed output item changed before the terminal event"
                )
            emit_output = not strict_remote or authoritative
            if item_type == "message":
                process_message_item(
                    item_key,
                    state["item"],
                    emit_output=emit_output,
                )
            elif item_type == "reasoning":
                if emit_output:
                    emit_reasoning(state, state["item"])
            elif item_type in {"function_call", "custom_tool_call"}:
                complete_tool(
                    state,
                    state["item"],
                    emit_output=emit_output,
                )
            if strict_remote and completed_item is None:
                state["completed_item"] = copy.deepcopy(item)

        def catch_up_output(value: dict[str, Any]) -> None:
            output = value.get("output")
            if not isinstance(output, list):
                return
            for index, item in enumerate(output):
                if isinstance(item, dict):
                    process_output_item(item, index, authoritative=True)

        def validate_terminal_stream_state(value: dict[str, Any]) -> None:
            if not strict_remote:
                return
            output = value.get("output")
            if not isinstance(output, list):
                raise _StreamProjectionError(
                    "Responses terminal output must be an array"
                )
            terminal_items: dict[str, dict[str, Any]] = {}
            for index, item in enumerate(output):
                if not isinstance(item, dict):
                    raise _StreamProjectionError(
                        "Responses terminal output items must be objects"
                    )
                item_key = canonical_item_key(
                    {"output_index": index}, item, index
                )
                if item_key in terminal_items:
                    raise _StreamProjectionError(
                        "Responses terminal output contains a duplicate item"
                    )
                terminal_items[item_key] = item
            for item_key, state in item_states.items():
                if not isinstance(state.get("item"), dict) or not state["item"]:
                    continue
                terminal_item = terminal_items.get(item_key)
                if terminal_item is None:
                    raise _StreamProjectionError(
                        "Responses streamed output item is absent from the terminal response"
                    )
                completed_item = state.get("completed_item")
                if completed_item is not None and completed_item != terminal_item:
                    raise _StreamProjectionError(
                        "Responses completed output item changed before the terminal event"
                    )
            for owner, _index, _kind in content_states:
                if owner not in terminal_items:
                    raise _StreamProjectionError(
                        "Responses streamed content is absent from the terminal response"
                    )

        def finish(value: dict[str, Any], status: str) -> None:
            nonlocal terminal
            if terminal:
                return
            terminal_value = dict(value)
            terminal_value.setdefault("status", status)
            validate_response_identity(terminal_value)
            if strict_remote and terminal_value.get("status") != status:
                raise _StreamProjectionError(
                    "Responses terminal event conflicts with response.status"
                )
            projected = project(terminal_value, strict=strict_remote)
            validate_terminal_stream_state(terminal_value)
            start(terminal_value)
            catch_up_output(terminal_value)
            for state in [*item_states.values(), *content_states.values()]:
                close(state)
            incomplete_reason = ""
            if status == "incomplete":
                details = terminal_value.get("incomplete_details")
                incomplete_reason = str(
                    details.get("reason")
                    if isinstance(details, dict)
                    else ""
                )
                if incomplete_reason not in {"max_output_tokens", "content_filter"}:
                    raise _StreamProjectionError(
                        "Responses upstream returned incomplete status without a "
                        f"supported reason: {incomplete_reason or 'missing'}"
                    )
            if saw_refusal or incomplete_reason == "content_filter":
                stop_reason = "refusal"
            elif incomplete_reason == "max_output_tokens":
                stop_reason = "max_tokens"
            elif saw_tool_use:
                stop_reason = "tool_use"
            else:
                stop_reason = str(projected.get("stop_reason") or "end_turn")
            self._event(
                handler,
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {
                        "stop_reason": stop_reason,
                        "stop_sequence": None,
                    },
                    "usage": projected.get("usage") or {"output_tokens": 0},
                },
            )
            self._event(handler, "message_stop", {"type": "message_stop"})
            terminal = True

        def upstream_error_message(
            event: dict[str, Any], response_value: dict[str, Any]
        ) -> str:
            error = response_value.get("error") or event.get("error")
            if isinstance(error, dict):
                message = str(error.get("message") or error.get("type") or "").strip()
                if message:
                    return message
            elif error:
                return str(error)
            return str(
                event.get("message")
                or response_value.get("message")
                or event.get("code")
                or "upstream stream failed"
            )

        def process_event(event: dict[str, Any]) -> None:
            nonlocal saw_refusal
            event_type = str(event.get("type") or "")
            response_value = self._response_value(event)
            if event_type in {"response.failed", "response.cancelled", "error"}:
                emit_error(upstream_error_message(event, response_value))
                return
            if event_type in {"response.created", "response.in_progress"}:
                validate_response_identity(response_value)
                start(response_value)
                return

            start(response_value)
            item = event.get("item") if isinstance(event.get("item"), dict) else {}
            item_key = canonical_item_key(event, item)
            item_type = str(item.get("type") or "")
            if event_type == "response.output_item.added":
                state = item_state_for(item_key, item_type or "message")
                state["item"] = {
                    **(state.get("item") if isinstance(state.get("item"), dict) else {}),
                    **item,
                }
                if item_type == "message":
                    validate_message_item(state["item"], terminal_item=False)
                elif item_type in {"function_call", "custom_tool_call"}:
                    open_tool(
                        state,
                        required=strict_remote,
                        emit=not strict_remote,
                    )
                return
            if event_type in {
                "response.output_text.delta",
                "response.refusal.delta",
            }:
                kind = "refusal" if event_type == "response.refusal.delta" else "text"
                if kind == "refusal":
                    saw_refusal = True
                if strict_remote:
                    parent_state = item_states.get(item_key)
                    if parent_state is None or parent_state.get("type") != "message":
                        raise _StreamProjectionError(
                            "Responses text delta arrived before its message item"
                        )
                state = content_state_for(item_key, content_index(event), kind)
                delta_value = event.get("delta")
                if strict_remote and not isinstance(delta_value, str):
                    raise _StreamProjectionError(
                        "Responses text delta must be a string"
                    )
                delta = str(delta_value or "")
                if strict_remote:
                    state["buffer"] += delta
                else:
                    emit_text_delta(state, delta)
                return
            if event_type in {
                "response.output_text.done",
                "response.refusal.done",
            }:
                refusal = event_type == "response.refusal.done"
                kind = "refusal" if refusal else "text"
                if refusal:
                    saw_refusal = True
                if strict_remote:
                    parent_state = item_states.get(item_key)
                    if parent_state is None or parent_state.get("type") != "message":
                        raise _StreamProjectionError(
                            "Responses text completion arrived before its message item"
                        )
                state = content_state_for(item_key, content_index(event), kind)
                final_text = str(
                    (event.get("refusal") or "")
                    if refusal
                    else (event.get("text") or "")
                )
                if strict_remote:
                    raw_final = (
                        event.get("refusal") if refusal else event.get("text")
                    )
                    if not isinstance(raw_final, str):
                        raise _StreamProjectionError(
                            "Responses completed text must be a string"
                        )
                    if state.get("buffer") and state["buffer"] != raw_final:
                        raise _StreamProjectionError(
                            "Responses completed text did not match streamed text"
                        )
                    state["final_payload"] = raw_final
                else:
                    emit_remaining_text(state, final_text)
                return
            if event_type in {"response.content_part.added", "response.content_part.done"}:
                part = event.get("part") if isinstance(event.get("part"), dict) else {}
                part_type = str(part.get("type") or "")
                if part_type in {"output_text", "refusal"}:
                    refusal = part_type == "refusal"
                    if refusal:
                        saw_refusal = True
                    if strict_remote:
                        parent_state = item_states.get(item_key)
                        if (
                            parent_state is None
                            or parent_state.get("type") != "message"
                        ):
                            raise _StreamProjectionError(
                                "Responses content part arrived before its message item"
                            )
                    state = content_state_for(
                        item_key,
                        content_index(event),
                        "refusal" if refusal else "text",
                    )
                    final_text = str(
                        (part.get("refusal") or "")
                        if refusal
                        else (part.get("text") or "")
                    )
                    if strict_remote:
                        raw_final = (
                            part.get("refusal") if refusal else part.get("text")
                        )
                        if not isinstance(raw_final, str):
                            raise _StreamProjectionError(
                                "Responses content part text must be a string"
                            )
                        if event_type == "response.content_part.done":
                            if state.get("buffer") and state["buffer"] != raw_final:
                                raise _StreamProjectionError(
                                    "Responses content part did not match streamed text"
                                )
                            state["final_payload"] = raw_final
                    else:
                        emit_remaining_text(state, final_text)
                        if event_type == "response.content_part.done":
                            close(state)
                return
            if event_type in {
                "response.function_call_arguments.delta",
                "response.custom_tool_call_input.delta",
            }:
                custom = event_type == "response.custom_tool_call_input.delta"
                expected_type = "custom_tool_call" if custom else "function_call"
                if strict_remote:
                    parent_state = item_states.get(item_key)
                    if parent_state is None or parent_state.get("type") != expected_type:
                        raise _StreamProjectionError(
                            "Responses tool arguments arrived before their tool item"
                        )
                state = item_state_for(
                    item_key, expected_type
                )
                delta = str(event.get("delta") or "")
                if strict_remote and not isinstance(event.get("delta"), str):
                    raise _StreamProjectionError(
                        "Responses tool argument delta must be a string"
                    )
                state["buffer"] += delta
                if not custom and delta and open_tool(
                    state,
                    required=strict_remote,
                    emit=not strict_remote,
                ):
                    if not strict_remote:
                        emit_input_json_delta(state, delta)
                return
            if event_type in {
                "response.function_call_arguments.done",
                "response.custom_tool_call_input.done",
            }:
                custom = event_type == "response.custom_tool_call_input.done"
                expected_type = "custom_tool_call" if custom else "function_call"
                if strict_remote:
                    parent_state = item_states.get(item_key)
                    if parent_state is None or parent_state.get("type") != expected_type:
                        raise _StreamProjectionError(
                            "Responses completed tool arguments arrived before their item"
                        )
                state = item_state_for(
                    item_key, expected_type
                )
                field = "input" if custom else "arguments"
                value = event[field] if field in event else state.get("buffer") or ""
                if strict_remote and not isinstance(value, str):
                    raise _StreamProjectionError(
                        "Responses completed tool arguments must be a string"
                    )
                if strict_remote:
                    payload = custom_tool_payload(value) if custom else str(value or "")
                    validate_function_payload(payload)
                    state["final_payload"] = payload
                else:
                    emit_completed_tool_payload(state, value)
                return
            if event_type == "response.output_item.done":
                process_output_item(item, event.get("output_index"))
                return
            if event_type == "response.completed":
                finish(response_value, "completed")
                return
            if event_type == "response.incomplete":
                finish(response_value, "incomplete")

        events = iter(_iter_events(response))
        while not terminal:
            try:
                event = next(events)
            except StopIteration:
                break
            except Exception as exc:
                emit_error(f"invalid upstream Responses stream: {exc}")
                break
            try:
                process_event(event)
            except _StreamProjectionError as exc:
                emit_error(str(exc))
                break
        if not terminal:
            emit_error("upstream Responses stream ended before a terminal event")


__all__ = ["ResponsesAnthropicStreamWriter"]
