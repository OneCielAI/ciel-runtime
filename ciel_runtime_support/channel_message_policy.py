from __future__ import annotations

import json
import re
from typing import Any


_EXTERNAL_PROVENANCE_META_KEYS = (
    "mcp_server",
    "mcp_method",
    "mcp_json",
    "sse_source",
    "sse_event",
    "sse_json",
    "stream_id",
    "sse_id",
    "cursor",
    "event_id",
    "message_id",
    "source_message_id",
    "rpc_id",
)

_UNIQUE_REFERENCE_META_KEYS = (
    "message_id",
    "source_message_id",
    "assignment_id",
    "poll_id",
    "task_id",
    "job_id",
    "schedule_id",
    "reminder_id",
)

_EVENT_ORDER_META_KEYS = (
    "stream_id",
    "cursor",
    "sse_id",
    "event_id",
    "sequence",
    "seq",
)


def _metadata(message: dict[str, Any]) -> dict[str, Any]:
    meta = message.get("meta")
    return meta if isinstance(meta, dict) else {}


def string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                parsed = None
            if isinstance(parsed, list):
                return string_list(parsed)
        if text.lower() in ("all", "*"):
            return ["all"]
        return [text]
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            out.extend(string_list(item))
        return out
    text = str(value).strip()
    return [text] if text else []


def message_meta_sources(message: dict[str, Any]) -> list[dict[str, Any]]:
    meta = _metadata(message)
    sources: list[dict[str, Any]] = []
    if meta:
        sources.append(meta)
    for envelope_key in ("mcp_json", "sse_json"):
        envelope = meta.get(envelope_key)
        if not isinstance(envelope, dict):
            continue
        params = envelope.get("params")
        if isinstance(params, dict):
            nested_meta = params.get("meta")
            if isinstance(nested_meta, dict):
                sources.append(nested_meta)
        nested_meta = envelope.get("meta")
        if isinstance(nested_meta, dict):
            sources.append(nested_meta)
    return sources


def message_delivery_targets(message: dict[str, Any]) -> set[str]:
    return {item.strip().lower() for item in string_list(message.get("delivery")) if item.strip()}


def message_is_web_chat_request(message: dict[str, Any]) -> bool:
    meta = _metadata(message)
    source = str(meta.get("source") or "").strip().lower()
    kind = str(message.get("kind") or meta.get("kind") or "").strip().lower()
    return bool(
        source == "ciel-runtime-web-chat"
        or kind == "web_chat"
        or meta.get("reply_channel")
        or meta.get("reply_recipient")
    )


def message_has_external_provenance(message: dict[str, Any]) -> bool:
    meta = _metadata(message)
    if message_is_web_chat_request(message):
        return True
    return any(
        meta.get(key) is not None and str(meta[key]).strip()
        for key in _EXTERNAL_PROVENANCE_META_KEYS
    )


def message_has_unique_reference(message: dict[str, Any]) -> bool:
    meta_sources = message_meta_sources(message)
    if any(
        meta.get(key) is not None and str(meta[key]).strip()
        for key in _UNIQUE_REFERENCE_META_KEYS
        for meta in meta_sources
    ):
        return True
    for meta in meta_sources:
        message_ids = meta.get("message_ids")
        if isinstance(message_ids, (list, tuple)) and any(str(item).strip() for item in message_ids):
            return True
        if isinstance(message_ids, str) and message_ids.strip():
            return True
        rooms = meta.get("rooms")
        if not isinstance(rooms, str) or not rooms.strip():
            continue
        try:
            parsed_rooms = json.loads(rooms)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(parsed_rooms, list):
            continue
        for room in parsed_rooms:
            if not isinstance(room, dict):
                continue
            room_message_ids = room.get("message_ids")
            if isinstance(room_message_ids, (list, tuple)) and any(
                str(item).strip() for item in room_message_ids
            ):
                return True
    return False


def message_source_key(message: dict[str, Any]) -> str:
    meta = _metadata(message)
    return str(meta.get("mcp_server") or meta.get("sse_source") or meta.get("source") or "").strip()


def message_kind_key(message: dict[str, Any]) -> str:
    meta = _metadata(message)
    meta_kind = str(
        meta.get("kind")
        or meta.get("type")
        or meta.get("event_type")
        or meta.get("eventType")
        or meta.get("event")
        or ""
    ).strip()
    return meta_kind or str(message.get("kind") or "").strip()


def message_topic_key(message: dict[str, Any]) -> str:
    meta = _metadata(message)
    return str(
        meta.get("key")
        or meta.get("topic")
        or meta.get("resource")
        or meta.get("target")
        or ""
    ).strip()


def message_order_value(message: dict[str, Any]) -> tuple[int, int, int] | None:
    meta = _metadata(message)
    for key in _EVENT_ORDER_META_KEYS:
        raw = meta.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue
        match = re.fullmatch(r"(\d+)-(\d+)", text)
        if match:
            return (2, int(match.group(1)), int(match.group(2)))
        if text.isdigit():
            return (1, int(text), 0)
    if message_has_external_provenance(message):
        try:
            message_id = int(message.get("id") or 0)
        except (TypeError, ValueError):
            message_id = 0
        if message_id > 0:
            return (3, message_id, 0)
    return None


def message_coalesce_key(message: dict[str, Any]) -> tuple[str, str, str, str, str] | None:
    if message_delivery_targets(message) and not message_has_external_provenance(message):
        return None
    if message_is_web_chat_request(message) or message_has_unique_reference(message):
        return None
    source = message_source_key(message)
    if not source or message_order_value(message) is None:
        return None
    meta = _metadata(message)
    method = str(meta.get("mcp_method") or meta.get("sse_event") or "").strip()
    kind = message_kind_key(message)
    if not method and not kind:
        return None
    channel = str(
        message.get("channel")
        or meta.get("room_id")
        or meta.get("room")
        or meta.get("channel")
        or ""
    ).strip()
    return (source, channel, method, kind, message_topic_key(message))


def superseded_message_ids(messages: list[dict[str, Any]]) -> set[int]:
    latest: dict[tuple[str, str, str, str, str], tuple[tuple[int, int, int], int]] = {}
    superseded: set[int] = set()
    for message in messages:
        try:
            message_id = int(message.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if message_id <= 0:
            continue
        key = message_coalesce_key(message)
        order = message_order_value(message) if key is not None else None
        if key is None or order is None:
            continue
        previous = latest.get(key)
        if previous is None:
            latest[key] = (order, message_id)
            continue
        previous_order, previous_id = previous
        if (order, message_id) >= (previous_order, previous_id):
            superseded.add(previous_id)
            latest[key] = (order, message_id)
        else:
            superseded.add(message_id)
    return superseded
