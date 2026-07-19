from __future__ import annotations

import json
import re
from typing import Any

CHANNEL_CONTROL_KINDS = {
    "agent_presence",
    "check-in",
    "checkin",
    "checkins",
    "checked_in",
    "colleague_presence",
    "connection",
    "connected",
    "disconnect",
    "disconnected",
    "endpoint",
    "heartbeat",
    "initialized",
    "init",
    "keepalive",
    "ping",
    "pong",
    "presence",
    "ready",
    "status",
    "system",
    "user_presence",
}


def first_present_dict_value(*sources: Any, keys: tuple[str, ...]) -> Any:
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in keys:
            value = source.get(key)
            if value is not None:
                return value
    return None


def event_payload_text(value: Any, depth: int = 0) -> str | None:
    if value is None or depth > 5:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if not isinstance(value, dict):
        return str(value)
    direct = first_present_dict_value(value, keys=("content", "message", "text", "body", "summary"))
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    if isinstance(direct, dict):
        nested = event_payload_text(direct, depth + 1)
        if nested:
            return nested
    for key in ("data", "event", "payload", "message", "notification", "item"):
        nested = event_payload_text(value.get(key), depth + 1)
        if nested:
            return nested
    event_type = value.get("type") or value.get("event_type") or value.get("kind")
    if event_type:
        payload = value.get("payload") if isinstance(value.get("payload"), dict) else value.get("data")
        if payload is not None:
            return f"{event_type}: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
        return str(event_type)
    return None


def pretty_json_text_or_raw(text: str) -> str:
    try:
        parsed = json.loads(text)
    except Exception:
        return text
    return json.dumps(parsed, ensure_ascii=False, indent=2, default=str)


def pretty_json_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def notification_semantic_text_from_envelope(envelope: Any) -> str | None:
    if not isinstance(envelope, dict):
        return None
    params = envelope.get("params") if isinstance(envelope.get("params"), dict) else {}
    payload = envelope.get("payload") if isinstance(envelope.get("payload"), dict) else {}
    data = params.get("data") if isinstance(params.get("data"), dict) else {}
    event = params.get("event") if isinstance(params.get("event"), dict) else {}
    nested_payload = (
        data.get("payload")
        if isinstance(data.get("payload"), dict)
        else event.get("payload")
        if isinstance(event.get("payload"), dict)
        else {}
    )
    content = event_payload_text(nested_payload) or event_payload_text(payload)
    for source in (data, event):
        direct = first_present_dict_value(source, keys=("content", "message", "text", "body", "summary"))
        content = content or event_payload_text(direct)
    return content or event_payload_text(params) or event_payload_text(envelope)


def event_meta_from_sources(*sources: Any) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    keys = (
        "room_id",
        "room",
        "channel",
        "thread_id",
        "parent_id",
        "message_id",
        "source_message_id",
        "event_id",
        "stream_id",
        "sse_id",
        "cursor",
        "assignment_id",
        "poll_id",
        "task_id",
        "sequence",
        "seq",
        "round_id",
        "conversation_id",
        "session_id",
        "agent_id",
        "agent_name",
        "sender_id",
        "sender",
        "recipient_id",
        "recipient",
        "recipients",
        "target_id",
        "target",
        "type",
        "event_type",
        "eventType",
        "kind",
        "timestamp",
        "created_at",
        "updated_at",
        "status",
        "priority",
        "title",
        "name",
        "model",
        "runtime",
    )
    for source in sources:
        if not isinstance(source, dict):
            continue
        source_meta = source.get("meta")
        if isinstance(source_meta, dict):
            meta.update(source_meta)
        for key in keys:
            value = source.get(key)
            if value is not None and key not in meta:
                meta[key] = value
    return meta


def metadata_key_is_sensitive(key: str) -> bool:
    return bool(re.search(r"(authorization|api[_-]?key|token|secret|password|credential|cookie)", key, re.I))


def json_safe_metadata(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return "<max-depth>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 8000 else value[:8000] + "...<truncated>"
    if isinstance(value, list):
        out = [json_safe_metadata(item, depth + 1) for item in value[:200]]
        if len(value) > 200:
            out.append(f"...<{len(value) - 200} more>")
        return out
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        items = list(value.items())
        for key, item in items[:200]:
            safe_key = str(key)
            out[safe_key] = "[redacted]" if metadata_key_is_sensitive(safe_key) else json_safe_metadata(item, depth + 1)
        if len(items) > 200:
            out["..."] = f"<{len(items) - 200} more>"
        return out
    return str(value)


def compact_json_for_prompt(value: Any, max_chars: int = 2400) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    except Exception:
        text = str(value)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 16] + "...<truncated>"


def channel_event_is_user_visible(
    kind: str,
    method: str,
    event_name: str,
    content: str,
    meta: dict[str, Any],
) -> bool:
    normalized_kind = str(kind or "").strip().lower().replace("_", ".").replace("/", ".")
    normalized_method = str(method or "").strip().lower()
    normalized_event = str(event_name or "").strip().lower()
    meta_type = str(
        meta.get("type")
        or meta.get("event_type")
        or meta.get("eventType")
        or meta.get("kind")
        or meta.get("status")
        or ""
    ).strip().lower()
    if normalized_kind in CHANNEL_CONTROL_KINDS or normalized_event in CHANNEL_CONTROL_KINDS:
        return False
    if meta_type in CHANNEL_CONTROL_KINDS:
        return False
    if meta.get("jsonrpc") is not None:
        if normalized_method.startswith("notifications/claude/"):
            return True
        if normalized_method in {"notifications/message", "notifications/chat", "notifications/event"}:
            return True
        return False
    return bool(content.strip())


def sse_payload_to_chat_payload(
    data_text: str,
    event_name: str,
    defaults: dict[str, Any],
    event_id: str | None = None,
) -> dict[str, Any] | None:
    raw_text = data_text or ""
    stripped_text = raw_text.strip()
    if not stripped_text or stripped_text == "[DONE]":
        return None
    if (event_name or "").strip().lower() == "endpoint":
        return None
    try:
        parsed: Any = json.loads(stripped_text)
    except Exception:
        parsed = None
    meta: dict[str, Any] = {
        "sse_event": event_name or "message",
        "sse_source": defaults.get("name") or "",
    }
    if event_id:
        meta["sse_id"] = event_id
    content = raw_text
    kind = "sse"
    method = str(event_name or "message")
    event_filter = defaults.get("event_filter")
    allowed_events = (
        {str(item).strip() for item in event_filter if str(item).strip()}
        if isinstance(event_filter, list)
        else set()
    )
    if isinstance(parsed, dict):
        method = str(parsed.get("method") or event_name or "message")
        meta["sse_json"] = json_safe_metadata(parsed)
        if parsed.get("jsonrpc") is not None:
            meta["jsonrpc"] = parsed.get("jsonrpc")
        if parsed.get("id") is not None:
            meta["rpc_id"] = parsed.get("id")
        if parsed.get("method") is not None:
            meta["mcp_method"] = parsed.get("method")
        params = parsed.get("params") if isinstance(parsed.get("params"), dict) else {}
        payload = parsed.get("payload") if isinstance(parsed.get("payload"), dict) else {}
        data = params.get("data") if isinstance(params.get("data"), dict) else {}
        event = params.get("event") if isinstance(params.get("event"), dict) else {}
        nested_payload = (
            data.get("payload")
            if isinstance(data.get("payload"), dict)
            else event.get("payload")
            if isinstance(event.get("payload"), dict)
            else {}
        )
        meta.update(event_meta_from_sources(parsed, params, payload, data, event, nested_payload))
        nested_content = event_payload_text(nested_payload) or event_payload_text(payload)
        for source in (data, event):
            direct = first_present_dict_value(
                source,
                keys=("content", "message", "text", "body", "summary"),
            )
            nested_content = nested_content or event_payload_text(direct)
        content = nested_content or event_payload_text(params) or event_payload_text(parsed) or content
        kind = method.replace("notifications/claude/", "").replace("/", ".") if method else "sse"
        if not parsed.get("method") and meta.get("kind"):
            kind = str(meta.get("kind"))
    if allowed_events and method not in allowed_events and (event_name or "message") not in allowed_events:
        return None
    if not channel_event_is_user_visible(kind, method, event_name, content, meta):
        return None
    channel = meta.get("channel") or meta.get("room_id") or meta.get("room") or defaults.get("channel") or "default"
    return {
        "channel": channel,
        "sender_id": meta.get("sender_id") or meta.get("sender") or meta.get("agent_id") or defaults.get("sender_id") or "sse",
        "recipients": meta.get("recipients")
        or meta.get("recipient_id")
        or meta.get("recipient")
        or defaults.get("recipient")
        or defaults.get("recipients")
        or "all",
        "thread_id": meta.get("thread_id"),
        "parent_id": meta.get("parent_id"),
        "kind": kind,
        "message": pretty_json_text_or_raw(raw_text),
        "meta": meta,
        "visibility": "user",
        "delivery": ["llm"],
    }
