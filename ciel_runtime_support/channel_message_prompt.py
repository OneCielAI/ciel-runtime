from __future__ import annotations

import json
import re
from typing import Any

from ciel_runtime_support.channel_event_projection import (
    CHANNEL_CONTROL_KINDS,
    metadata_key_is_sensitive,
    pretty_json_value,
)
from ciel_runtime_support.channel_message_policy import (
    message_has_external_provenance,
    message_meta_sources,
    string_list,
)


NATIVE_ROUTER_CHANNEL_NAMES = frozenset({"ciel-runtime-router", "mcp-ciel-runtime-router"})

_PROMPT_META_KEYS = (
    "kind",
    "type",
    "event_type",
    "eventType",
    "status",
    "mcp_server",
    "mcp_method",
    "room_name",
    "room_label",
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
    "sequence",
    "seq",
    "assignment_id",
    "poll_id",
    "task_id",
    "round_id",
    "conversation_id",
    "session_id",
    "agent_id",
    "agent_name",
    "sender_id",
    "sender",
    "sender_name",
    "author_id",
    "author_name",
    "recipient_id",
    "recipient",
    "recipient_name",
    "mentioned_by",
    "key",
    "path",
)


def _metadata(message: dict[str, Any]) -> dict[str, Any]:
    meta = message.get("meta")
    return meta if isinstance(meta, dict) else {}


def prompt_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        return text if text and len(text) <= 240 else None
    if isinstance(value, list):
        out = [scalar for item in value[:10] if (scalar := prompt_scalar(item)) is not None]
        if not out:
            return None
        try:
            encoded = json.dumps(out, ensure_ascii=False, separators=(",", ":"), default=str)
        except (TypeError, ValueError, OverflowError):
            return None
        return out if len(encoded) <= 300 else None
    return None


def prompt_metadata(message: dict[str, Any]) -> str:
    meta = _metadata(message)
    if not meta:
        return ""
    values = {
        key: value
        for key in _PROMPT_META_KEYS
        if key in meta
        and not metadata_key_is_sensitive(key)
        and (value := prompt_scalar(meta.get(key))) is not None
    }
    kept: dict[str, Any] = {}
    for key, value in values.items():
        candidate = {**kept, key: value}
        text = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"), default=str)
        if len(text) <= 900:
            kept = candidate
    return json.dumps(kept, ensure_ascii=False, separators=(",", ":"), default=str) if kept else ""


def format_wake_prompt(message: dict[str, Any]) -> str:
    channel = str(message.get("channel") or "default")
    sender = str(message.get("sender_id") or "channel")
    message_id = str(message.get("id") or "")
    meta = _metadata(message)
    room = str(meta.get("room_id") or meta.get("room") or channel)
    thread = str(message.get("thread_id") or meta.get("thread_id") or "")
    body = re.sub(r"\s+", " ", str(message.get("message") or "")).strip()
    fields = [f"channel={channel}", f"room={room}", f"from={sender}"]
    if message_id:
        fields.append(f"id={message_id}")
    if thread:
        fields.append(f"thread={thread}")
    metadata = prompt_metadata(message)
    if metadata:
        fields.append(f"metadata={metadata}")
    return (
        "[ciel-runtime external channel message] "
        + " ".join(fields)
        + f" text={json.dumps(body, ensure_ascii=False)}"
    )


def _format_web_chat_wake_item(message: dict[str, Any]) -> str:
    channel = str(message.get("channel") or "default")
    meta = _metadata(message)
    reply_channel = str(meta.get("reply_channel") or channel)
    thread = str(message.get("thread_id") or meta.get("thread_id") or reply_channel)
    message_id = str(message.get("id") or "")
    body = re.sub(r"\s+", " ", str(message.get("message") or "")).strip()
    fields = [f"id={message_id}", f"channel={reply_channel}", f"thread={thread}"]
    return " ".join(field for field in fields if not field.endswith("=")) + (
        f" user={json.dumps(body, ensure_ascii=False)}"
    )


def format_web_chat_wake_batch_prompt(messages: list[dict[str, Any]]) -> str:
    items = " ; ".join(_format_web_chat_wake_item(message) for message in messages)
    return f"[ciel-runtime web chat] {len(messages)} browser message(s): {items}"


def wake_message_noise_reason(message: dict[str, Any]) -> str | None:
    body = re.sub(r"\s+", " ", str(message.get("message") or "")).strip().lower()
    kind = str(message.get("kind") or "").strip().lower()
    if not body:
        return "empty"
    if kind in {"connection", "connected", "heartbeat", "keepalive"}:
        return kind
    if re.fullmatch(r"[a-z0-9_.:-]{1,80}\.(ws|sse)\.connected", body):
        return "transport_connected"
    return None


def llm_message_skip_reason(message: dict[str, Any]) -> str | None:
    visibility = str(message.get("visibility") or "user").strip().lower()
    if visibility in {"hidden", "internal", "transport", "control", "system"}:
        return f"visibility_{visibility}"
    recipients = {item.strip().lower() for item in string_list(message.get("recipients"))}
    if "internal" in recipients:
        return "recipient_internal"
    delivery = string_list(message.get("delivery"))
    if delivery and not ({"all", "*", "llm"} & {item.strip().lower() for item in delivery}):
        return "delivery_not_llm"
    noise_reason = wake_message_noise_reason(message)
    if noise_reason:
        return noise_reason
    meta = _metadata(message)
    source = str(meta.get("sse_source") or meta.get("source") or "").strip().lower()
    sender = str(message.get("sender_id") or meta.get("sender_id") or "").strip().lower()
    if source in NATIVE_ROUTER_CHANNEL_NAMES or sender in NATIVE_ROUTER_CHANNEL_NAMES:
        return "native_router_self_echo"
    meta_kind = str(
        meta.get("kind")
        or meta.get("type")
        or meta.get("event_type")
        or meta.get("eventType")
        or meta.get("event")
        or meta.get("status")
        or ""
    ).strip().lower()
    if meta_kind in CHANNEL_CONTROL_KINDS:
        return meta_kind
    if not delivery and not message_has_external_provenance(message):
        return "unscoped_channel_message"
    return None


def wake_message_is_noise(message: dict[str, Any]) -> bool:
    return wake_message_noise_reason(message) is not None


def format_wake_batch_prompt(messages: list[dict[str, Any]]) -> str:
    if len(messages) == 1:
        return format_wake_prompt(messages[0])
    parts: list[str] = []
    for message in messages:
        channel = str(message.get("channel") or "default")
        sender = str(message.get("sender_id") or "channel")
        message_id = str(message.get("id") or "")
        meta = _metadata(message)
        room = str(meta.get("room_id") or meta.get("room") or channel)
        thread = str(message.get("thread_id") or meta.get("thread_id") or "")
        body = str(message.get("message") or "")
        fields = [f"id={message_id}", f"room={room}", f"from={sender}"]
        if thread:
            fields.append(f"thread={thread}")
        metadata = prompt_metadata(message)
        if metadata:
            fields.append(f"metadata={metadata}")
        parts.append("(" + " ".join(fields) + ") " + json.dumps(body, ensure_ascii=False))
    return f"[ciel-runtime external channel messages] {len(messages)} new messages: " + " ; ".join(parts)


def _source_header_scalar(value: Any) -> str:
    if value is None or isinstance(value, (dict, list)):
        return ""
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text if text and len(text) <= 240 else ""


def _first_source_header_value(message: dict[str, Any], keys: tuple[str, ...]) -> str:
    for source in message_meta_sources(message):
        for key in keys:
            if metadata_key_is_sensitive(key):
                continue
            text = _source_header_scalar(source.get(key))
            if text:
                return text
    return ""


def _message_source_header(message: dict[str, Any]) -> str:
    meta = _metadata(message)
    if not any(isinstance(meta.get(key), (dict, list)) for key in ("mcp_json", "sse_json")):
        return ""
    room_name = _first_source_header_value(message, ("room_name", "room_label", "title", "name"))
    room_id = _first_source_header_value(message, ("room_id", "room"))
    channel = _first_source_header_value(message, ("channel",)) or _source_header_scalar(
        message.get("channel")
    )
    source = room_name or room_id or channel
    if not source:
        return ""
    source_text = f"{room_name} (room_id={room_id})" if room_name and room_id != room_name else source
    return f"[Source channel] {source_text}"


def message_llm_display_text(message: dict[str, Any]) -> str:
    meta = _metadata(message)
    for key in ("mcp_json", "sse_json"):
        value = meta.get(key)
        if isinstance(value, (dict, list)):
            text = pretty_json_value(value)
            header = _message_source_header(message)
            return f"{header}\n\n{text}" if header else text
    return str(message.get("message") if message.get("message") is not None else "")


def format_llm_batch_prompt(messages: list[dict[str, Any]]) -> str:
    return "\n\n".join(message_llm_display_text(message) for message in messages)
