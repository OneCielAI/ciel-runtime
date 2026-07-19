"""HTTP/SSE transport projection for OpenAI Responses payloads."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class OpenAIResponsesStreamServices:
    to_response: Callable[..., dict[str, Any]]
    write_json: Callable[..., Any]


def write_openai_responses(
    handler: Any,
    message: dict[str, Any],
    source_body: dict[str, Any] | None,
    *,
    stream: bool,
    services: OpenAIResponsesStreamServices,
) -> None:
    response = services.to_response(message, source_body=source_body)
    if not stream:
        services.write_json(handler, response)
        return
    _start_sse(handler, 200)
    _emit(handler, "response.created", {"type": "response.created", "response": {**response, "status": "in_progress", "output": []}})
    for output_index, item in enumerate(response.get("output") or []):
        _emit_output_item(handler, response, output_index, item)
    _emit(handler, "response.completed", {"type": "response.completed", "response": response})
    handler.wfile.flush()


def write_openai_responses_error(
    handler: Any,
    message: str,
    *,
    stream: bool,
    status: int,
    services: OpenAIResponsesStreamServices,
) -> None:
    payload = {"type": "error", "error": {"type": "api_error", "message": message}}
    if not stream:
        services.write_json(handler, payload, status)
        return
    _start_sse(handler, status)
    _emit(handler, "error", payload)
    handler.wfile.flush()


def _emit_output_item(
    handler: Any,
    response: dict[str, Any],
    output_index: int,
    item: dict[str, Any],
) -> None:
    item_added = {**item}
    if item_added.get("type") == "message":
        item_added["content"] = []
    _emit(
        handler,
        "response.output_item.added",
        {
            "type": "response.output_item.added",
            "response_id": response["id"],
            "output_index": output_index,
            "item": item_added,
        },
    )
    if item.get("type") == "message":
        _emit_message_content(handler, response, output_index, item)
    _emit(
        handler,
        "response.output_item.done",
        {
            "type": "response.output_item.done",
            "response_id": response["id"],
            "output_index": output_index,
            "item": item,
        },
    )


def _emit_message_content(
    handler: Any,
    response: dict[str, Any],
    output_index: int,
    item: dict[str, Any],
) -> None:
    content = item.get("content") if isinstance(item.get("content"), list) else []
    for content_index, part in enumerate(content):
        if not isinstance(part, dict) or part.get("type") != "output_text":
            continue
        common = {
            "response_id": response["id"],
            "item_id": item["id"],
            "output_index": output_index,
            "content_index": content_index,
        }
        _emit(
            handler,
            "response.content_part.added",
            {"type": "response.content_part.added", **common, "part": {**part, "text": ""}},
        )
        text = str(part.get("text") or "")
        if text:
            _emit(
                handler,
                "response.output_text.delta",
                {"type": "response.output_text.delta", **common, "delta": text},
            )
        _emit(
            handler,
            "response.output_text.done",
            {"type": "response.output_text.done", **common, "text": text},
        )
        _emit(
            handler,
            "response.content_part.done",
            {"type": "response.content_part.done", **common, "part": part},
        )


def _start_sse(handler: Any, status: int) -> None:
    handler.send_response(status)
    handler.send_header("content-type", "text/event-stream")
    handler.send_header("cache-control", "no-cache")
    handler.send_header("connection", "close")
    handler.end_headers()


def _emit(handler: Any, event_name: str, payload: dict[str, Any]) -> None:
    handler.wfile.write(f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode())
