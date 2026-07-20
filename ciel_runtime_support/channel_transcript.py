from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass
import json
from typing import Any, Callable, Iterator


@dataclass(frozen=True)
class ChannelWakeTranscriptServices:
    claim_prompt: Callable[[int], str]
    prompt_references_message_id: Callable[..., bool]
    prompt_message_ids: Callable[[str], set[int]]
    now: Callable[[], float]


@dataclass(frozen=True, slots=True)
class ChannelWakeStateReaderPorts:
    latest_transcript: Callable[[], Any]
    read_tail_text: Callable[[Any], str]
    wake_state_from_text: Callable[..., str]
    queued_age_from_text: Callable[..., float | None]
    stale_seconds: Callable[[], float]


class ChannelWakeStateReader:
    def __init__(self, ports: ChannelWakeStateReaderPorts) -> None:
        self._ports = ports

    @staticmethod
    def message_id(message: dict[str, Any]) -> int:
        try:
            return int(message.get("id") or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def prompt_candidates(
        message: dict[str, Any], prompt: str | None
    ) -> tuple[str, ...]:
        candidates = [prompt] if prompt else []
        body = str(
            message.get("message") if message.get("message") is not None else ""
        )
        if body:
            candidates.append(body)
        return tuple(candidates)

    def state(self, message_id: int) -> str:
        if message_id <= 0:
            return "completed"
        text = self._latest_text()
        return self._ports.wake_state_from_text(message_id, text) if text else "unknown"

    def state_for_message(
        self, message: dict[str, Any], prompt: str | None = None
    ) -> str:
        message_id = self.message_id(message)
        if message_id <= 0:
            return "completed"
        text = self._latest_text()
        if not text:
            return "unknown"
        return self._ports.wake_state_from_text(
            message_id, text, self.prompt_candidates(message, prompt)
        )

    def queued_is_stale(
        self, message: dict[str, Any], prompt: str | None = None
    ) -> bool:
        message_id = self.message_id(message)
        if message_id <= 0:
            return False
        text = self._latest_text()
        if not text:
            return False
        age = self._ports.queued_age_from_text(
            message_id, text, self.prompt_candidates(message, prompt)
        )
        return age is not None and age >= self._ports.stale_seconds()

    def _latest_text(self) -> str:
        path = self._ports.latest_transcript()
        return self._ports.read_tail_text(path) if path is not None else ""


def record_timestamp_seconds(record: dict[str, Any]) -> float | None:
    raw = record.get("timestamp")
    if raw is None and isinstance(record.get("attachment"), dict):
        raw = record["attachment"].get("timestamp")
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts: list[str] = []
        for key in ("text", "content", "input_text", "output_text", "message"):
            raw = value.get(key)
            if isinstance(raw, str):
                parts.append(raw)
            elif isinstance(raw, (dict, list)):
                nested = content_text(raw)
                if nested:
                    parts.append(nested)
        return "\n".join(parts)
    if isinstance(value, list):
        parts = []
        for item in value:
            nested = content_text(item)
            if nested:
                parts.append(nested)
        return "\n".join(parts)
    return ""


def user_text(record: dict[str, Any]) -> str:
    record_type = str(record.get("type") or "")
    message = record.get("message")
    message_obj = message if isinstance(message, dict) else {}
    if record_type == "user" or str(message_obj.get("role") or "") == "user":
        return content_text(message_obj.get("content"))
    payload = record.get("payload")
    payload_obj = payload if isinstance(payload, dict) else {}
    payload_type = str(payload_obj.get("type") or "")
    payload_role = str(payload_obj.get("role") or "")
    if record_type == "response_item" and payload_type == "message" and payload_role == "user":
        return content_text(payload_obj.get("content"))
    if record_type == "event_msg" and payload_type == "user_message":
        return content_text(payload_obj.get("message"))
    return ""


def is_assistant_message(record: dict[str, Any]) -> bool:
    record_type = str(record.get("type") or "")
    message = record.get("message")
    message_obj = message if isinstance(message, dict) else {}
    message_role = str(message_obj.get("role") or "")
    if (
        record_type == "assistant"
        or message_role == "assistant"
        or str(record.get("subtype") or "") == "turn_duration"
    ):
        return True
    payload = record.get("payload")
    payload_obj = payload if isinstance(payload, dict) else {}
    return (
        record_type == "response_item"
        and str(payload_obj.get("type") or "") == "message"
        and str(payload_obj.get("role") or "") == "assistant"
    )


def tool_call_id(value: dict[str, Any]) -> str:
    for key in ("call_id", "id", "tool_call_id"):
        raw = value.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return ""


def message_content_blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content")
    if isinstance(content, dict):
        return [content]
    if isinstance(content, list):
        return [item for item in content if isinstance(item, dict)]
    return []


def tool_use_ids(message: dict[str, Any]) -> set[str]:
    return {
        str(block.get("id") or "").strip()
        for block in message_content_blocks(message)
        if block.get("type") == "tool_use" and str(block.get("id") or "").strip()
    }


def tool_result_ids(message: dict[str, Any]) -> set[str]:
    return {
        str(block.get("tool_use_id") or "").strip()
        for block in message_content_blocks(message)
        if block.get("type") == "tool_result" and str(block.get("tool_use_id") or "").strip()
    }


def active_tool_call_from_text(text: str) -> bool:
    pending_tool_ids: set[str] = set()
    unknown_tool_active = False
    for raw_line in text.splitlines():
        try:
            record = json.loads(raw_line)
        except (TypeError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        record_type = str(record.get("type") or "")
        message = record.get("message")
        message_obj = message if isinstance(message, dict) else {}
        message_role = str(message_obj.get("role") or "")
        payload = record.get("payload")
        payload_obj = payload if isinstance(payload, dict) else {}
        payload_type = str(payload_obj.get("type") or "")
        if record_type == "response_item":
            if payload_type in {"function_call", "custom_tool_call", "local_shell_call"}:
                call_id = tool_call_id(payload_obj)
                if call_id:
                    pending_tool_ids.add(call_id)
                else:
                    unknown_tool_active = True
                continue
            if payload_type in {"function_call_output", "custom_tool_call_output", "local_shell_call_output"}:
                call_id = tool_call_id(payload_obj)
                if call_id:
                    pending_tool_ids.discard(call_id)
                else:
                    pending_tool_ids.clear()
                unknown_tool_active = False
                continue
            if is_assistant_message(record):
                pending_tool_ids.clear()
                unknown_tool_active = False
                continue
        if record_type == "event_msg":
            if payload_type in {"mcp_tool_call_begin", "tool_call_begin"}:
                call_id = tool_call_id(payload_obj)
                if call_id:
                    pending_tool_ids.add(call_id)
                else:
                    unknown_tool_active = True
                continue
            if payload_type in {"mcp_tool_call_end", "tool_call_end"}:
                call_id = tool_call_id(payload_obj)
                if call_id:
                    pending_tool_ids.discard(call_id)
                else:
                    pending_tool_ids.clear()
                unknown_tool_active = False
                continue
        if record_type == "assistant" or message_role == "assistant":
            use_ids = tool_use_ids(message_obj)
            if str(message_obj.get("stop_reason") or "") == "tool_use" or use_ids:
                pending_tool_ids.update(use_ids)
                if not use_ids:
                    unknown_tool_active = True
            else:
                pending_tool_ids.clear()
                unknown_tool_active = False
            continue
        if record_type == "user" or message_role == "user":
            result_ids = tool_result_ids(message_obj)
            if result_ids:
                pending_tool_ids.difference_update(result_ids)
                unknown_tool_active = False
            elif record.get("toolUseResult") is not None:
                pending_tool_ids.clear()
                unknown_tool_active = False
    return bool(pending_tool_ids or unknown_tool_active)


def active_turn_from_text(text: str) -> bool:
    active = False
    for raw_line in text.splitlines():
        try:
            record = json.loads(raw_line)
        except (TypeError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        payload = record.get("payload")
        payload_obj = payload if isinstance(payload, dict) else {}
        event_type = str(payload_obj.get("type") or record.get("type") or "")
        if event_type in {"task_started", "turn_started"}:
            active = True
        elif event_type in {"task_complete", "turn_complete", "turn_aborted"}:
            active = False
    return active


def queued_age_seconds_from_text(
    message_id: int,
    text: str,
    prompt_texts: list[str] | tuple[str, ...] | None,
    services: ChannelWakeTranscriptServices,
    *,
    now: float | None = None,
) -> float | None:
    if message_id <= 0:
        return None
    prompts = [str(item) for item in (prompt_texts or ()) if str(item or "").strip()]
    claimed = services.claim_prompt(message_id)
    if claimed:
        prompts.append(claimed)
    latest_queued_at: float | None = None
    for record in _jsonl_records(text):
        record_type = str(record.get("type") or "")
        candidate = ""
        if record_type == "queue-operation" and record.get("operation") == "enqueue":
            raw = record.get("content")
            candidate = raw if isinstance(raw, str) else ""
        elif record_type == "attachment":
            attachment = record.get("attachment")
            if isinstance(attachment, dict) and attachment.get("type") == "queued_command":
                raw = attachment.get("prompt")
                candidate = raw if isinstance(raw, str) else ""
        actual_user_text = user_text(record)
        if actual_user_text and services.prompt_references_message_id(
            actual_user_text, message_id, prompts
        ):
            return None
        if not candidate or not services.prompt_references_message_id(candidate, message_id, prompts):
            continue
        timestamp = record_timestamp_seconds(record)
        if timestamp is not None:
            latest_queued_at = max(latest_queued_at or 0.0, timestamp)
    if latest_queued_at is None:
        return None
    current = services.now() if now is None else float(now)
    return max(0.0, current - latest_queued_at)


def wake_state_from_text(
    message_id: int,
    text: str,
    prompt_texts: list[str] | tuple[str, ...] | None,
    services: ChannelWakeTranscriptServices,
) -> str:
    if message_id <= 0:
        return "completed"
    prompts = [str(item) for item in (prompt_texts or ()) if str(item or "").strip()]
    claimed = services.claim_prompt(message_id)
    if claimed:
        prompts.append(claimed)
    seen_queued_prompt = False
    seen_real_prompt = False
    for record in _jsonl_records(text):
        record_type = str(record.get("type") or "")
        if seen_real_prompt and is_assistant_message(record):
            return "completed"
        if record_type == "queue-operation" and record.get("operation") == "enqueue":
            raw = record.get("content")
            if isinstance(raw, str) and services.prompt_references_message_id(raw, message_id, prompts):
                seen_queued_prompt = True
            continue
        if record_type == "attachment":
            attachment = record.get("attachment")
            if isinstance(attachment, dict) and attachment.get("type") == "queued_command":
                raw = attachment.get("prompt")
                if isinstance(raw, str) and services.prompt_references_message_id(raw, message_id, prompts):
                    seen_queued_prompt = True
            continue
        if services.prompt_references_message_id(user_text(record), message_id, prompts):
            seen_real_prompt = True
    if seen_real_prompt:
        return "pending"
    return "queued" if seen_queued_prompt else "missing"


def queued_command_ids_from_text(
    text: str, services: ChannelWakeTranscriptServices
) -> set[int]:
    ids: set[int] = set()
    for record in _jsonl_records(text):
        candidate = ""
        if record.get("type") == "queue-operation" and record.get("operation") == "enqueue":
            raw = record.get("content")
            candidate = raw if isinstance(raw, str) else ""
        elif record.get("type") == "attachment":
            attachment = record.get("attachment")
            if isinstance(attachment, dict) and attachment.get("type") == "queued_command":
                raw = attachment.get("prompt")
                candidate = raw if isinstance(raw, str) else ""
        if candidate:
            ids.update(services.prompt_message_ids(candidate))
    return ids


def _jsonl_records(text: str) -> Iterator[dict[str, Any]]:
    for raw_line in text.splitlines():
        try:
            record = json.loads(raw_line)
        except (TypeError, ValueError):
            continue
        if isinstance(record, dict):
            yield record


__all__ = [
    "active_tool_call_from_text",
    "active_turn_from_text",
    "ChannelWakeTranscriptServices",
    "content_text",
    "is_assistant_message",
    "message_content_blocks",
    "record_timestamp_seconds",
    "queued_age_seconds_from_text",
    "queued_command_ids_from_text",
    "tool_call_id",
    "tool_result_ids",
    "tool_use_ids",
    "user_text",
    "wake_state_from_text",
]
