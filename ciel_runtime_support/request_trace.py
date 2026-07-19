from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class RequestTracePolicy:
    enabled: Callable[[], bool]
    request_path: Path
    response_path: Path
    request_max_bytes: int
    response_max_bytes: int
    response_text_limit: int


@dataclass(frozen=True)
class RequestTraceProjection:
    content_to_text: Callable[[Any], str]
    thinking_block_count: Callable[[dict[str, Any]], int]
    tool_continuation_block_count: Callable[[dict[str, Any]], int]


@dataclass(frozen=True)
class RequestTraceServices:
    policy: RequestTracePolicy
    projection: RequestTraceProjection
    log: Callable[[str, str], None]
    timestamp: Callable[[], str] = lambda: time.strftime("%Y-%m-%dT%H:%M:%S")


def truncate_for_dump(value: Any, max_len: int = 4000) -> Any:
    try:
        text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    except Exception:
        text = str(value)
    if len(text) > max_len:
        return text[:max_len] + f"...<truncated {len(text) - max_len} chars>"
    return value


def summarize_messages_for_trace(
    messages: Any,
    projection: RequestTraceProjection,
    max_messages: int = 30,
) -> list[dict[str, Any]]:
    if not isinstance(messages, list):
        return []
    selected = messages[-max_messages:]
    offset = len(messages) - len(selected)
    summary: list[dict[str, Any]] = []
    for index, message in enumerate(selected, start=offset):
        if not isinstance(message, dict):
            continue
        entry: dict[str, Any] = {"index": index, "role": message.get("role")}
        blocks: list[dict[str, Any]] = []
        content = message.get("content")
        if isinstance(content, str):
            blocks.append({"type": "text", "text": truncate_for_dump(content, 1000)})
        elif isinstance(content, list):
            for block in content:
                projected = _project_trace_block(block, projection)
                if projected is not None:
                    blocks.append(projected)
        else:
            blocks.append(
                {"type": type(content).__name__, "text": truncate_for_dump(content, 1000)}
            )
        entry["content"] = blocks
        summary.append(entry)
    return summary


def _project_trace_block(
    block: Any,
    projection: RequestTraceProjection,
) -> dict[str, Any] | None:
    if isinstance(block, str):
        return {"type": "text", "text": truncate_for_dump(block, 1000)}
    if not isinstance(block, dict):
        return None
    block_type = str(block.get("type") or "")
    if block_type == "text":
        return {"type": "text", "text": truncate_for_dump(block.get("text", ""), 1000)}
    if block_type == "tool_use":
        return {
            "type": "tool_use",
            "id": block.get("id"),
            "name": block.get("name"),
            "input": truncate_for_dump(block.get("input"), 1200),
        }
    if block_type == "tool_result":
        return {
            "type": "tool_result",
            "tool_use_id": block.get("tool_use_id"),
            "is_error": block.get("is_error"),
            "content": truncate_for_dump(
                projection.content_to_text(block.get("content", "")),
                1200,
            ),
        }
    if block_type == "thinking":
        return {
            "type": "thinking",
            "thinking_len": len(str(block.get("thinking") or "")),
            "has_signature": bool(block.get("signature")),
        }
    if block_type == "redacted_thinking":
        return {
            "type": "redacted_thinking",
            "data_len": len(str(block.get("data") or "")),
        }
    return {"type": block_type or "unknown"}


def dump_request_for_trace(
    provider: str,
    path: str,
    body: dict[str, Any],
    services: RequestTraceServices,
) -> None:
    if not services.policy.enabled():
        return
    try:
        _rotate_if_oversized(
            services.policy.request_path,
            services.policy.request_max_bytes,
        )
        record = {
            "time": services.timestamp(),
            "provider": provider,
            "path": path,
            "model": body.get("model"),
            "stream": body.get("stream"),
            "thinking": body.get("thinking"),
            "thinking_blocks": services.projection.thinking_block_count(body),
            "tool_continuation_blocks": services.projection.tool_continuation_block_count(body),
            "messages_count": len(body.get("messages") or []),
            "messages": summarize_messages_for_trace(body.get("messages"), services.projection),
            "system": truncate_for_dump(body.get("system")),
            "tools": body.get("tools"),
        }
        _append_json_line(services.policy.request_path, record)
    except Exception as exc:
        services.log("WARN", f"request_trace_dump_failed error={type(exc).__name__}: {exc}")


def dump_response_for_trace(
    provider: str,
    model: str,
    text_so_far: str,
    tool_calls: list[dict[str, Any]],
    stop_reason: str | None,
    input_tokens: int,
    output_tokens: int,
    services: RequestTraceServices,
    last_chunk: dict[str, Any] | None = None,
) -> None:
    if not services.policy.enabled():
        return
    try:
        _rotate_if_oversized(
            services.policy.response_path,
            services.policy.response_max_bytes,
        )
        text_full_len = len(text_so_far)
        text = text_so_far
        if text_full_len > services.policy.response_text_limit:
            text = (
                text_so_far[: services.policy.response_text_limit]
                + f"...<truncated {text_full_len - services.policy.response_text_limit} chars>"
            )
        tool_summary = []
        for call in tool_calls:
            function = call.get("function") if isinstance(call.get("function"), dict) else {}
            tool_summary.append(
                {
                    "name": function.get("name"),
                    "arguments": function.get("arguments"),
                }
            )
        record = {
            "time": services.timestamp(),
            "provider": provider,
            "model": model,
            "stop_reason": stop_reason,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "text_full_len": text_full_len,
            "tool_call_count": len(tool_calls),
            "text": text,
            "tool_calls": tool_summary,
            "done_reason": (last_chunk or {}).get("done_reason"),
        }
        _append_json_line(services.policy.response_path, record)
    except Exception as exc:
        services.log("WARN", f"response_trace_dump_failed error={type(exc).__name__}: {exc}")


def _rotate_if_oversized(path: Path, max_bytes: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > max_bytes:
        path.replace(path.with_suffix(".jsonl.1"))


def _append_json_line(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
