from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any


_EVENT_IDENTITY_META_KEYS = (
    "stream_id",
    "message_id",
    "source_message_id",
    "event_id",
    "sse_id",
    "cursor",
    "sequence",
    "seq",
)

_STABLE_META_KEYS = (
    "cursor",
    "stream_id",
    "sse_id",
    "event_id",
    "message_id",
    "source_message_id",
    "sequence",
    "seq",
    "rpc_id",
)


def payload_hash(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def message_time_seconds(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return time.mktime(time.strptime(text[:19], "%Y-%m-%dT%H:%M:%S"))
    except (OverflowError, ValueError):
        return 0.0


def _room_key(message: dict[str, Any], sources: list[dict[str, Any]]) -> str:
    room_ids: list[str] = []
    for source in sources:
        rooms = source.get("rooms")
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
            room_id = str(room.get("room_id") or room.get("room") or "").strip()
            if room_id and room_id not in room_ids:
                room_ids.append(room_id)
    if room_ids:
        return ",".join(sorted(room_ids))
    for source in sources:
        room = str(source.get("room_id") or source.get("room") or source.get("channel") or "").strip()
        if room:
            return room
    return str(message.get("channel") or "").strip()


def message_event_identity_key(message: dict[str, Any]) -> tuple[str, ...] | None:
    meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
    envelopes = [meta[key] for key in ("sse_json", "mcp_json") if isinstance(meta.get(key), dict)]
    raw_message = message.get("message")
    if isinstance(raw_message, str) and raw_message.strip().startswith("{"):
        try:
            parsed = json.loads(raw_message.strip())
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            envelopes.append(parsed)

    sources: list[dict[str, Any]] = [meta]
    method = str(meta.get("mcp_method") or meta.get("sse_event") or "").strip()
    content: Any = message.get("message")
    for envelope in envelopes:
        envelope_method = str(envelope.get("method") or "").strip()
        if envelope_method:
            method = envelope_method
        params = envelope.get("params") if isinstance(envelope.get("params"), dict) else {}
        if not params:
            continue
        sources.append(params)
        params_meta = params.get("meta")
        if isinstance(params_meta, dict):
            sources.append(params_meta)
        if params.get("content") is not None:
            content = params.get("content")
    if method and not method.startswith("notifications/"):
        return None

    kind = ""
    for source in sources:
        if not kind:
            kind = str(
                source.get("kind")
                or source.get("type")
                or source.get("event_type")
                or source.get("eventType")
                or ""
            ).strip()
    room = _room_key(message, sources)
    for key in _EVENT_IDENTITY_META_KEYS:
        for source in sources:
            value = source.get(key)
            stable_value = str(value).strip() if value is not None else ""
            if stable_value:
                return ("event", method, room, kind, key, stable_value, payload_hash(content))
    return None


def stable_dedupe_key(message: dict[str, Any]) -> tuple[str, ...] | None:
    event_identity = message_event_identity_key(message)
    if event_identity:
        return ("stable",) + event_identity
    meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
    source = str(
        meta.get("mcp_server")
        or meta.get("sse_source")
        or meta.get("source")
        or message.get("sender_id")
        or ""
    ).strip()
    method = str(meta.get("mcp_method") or "").strip()
    channel = str(
        message.get("channel")
        or meta.get("room_id")
        or meta.get("room")
        or meta.get("channel")
        or ""
    ).strip()
    kind = str(
        message.get("kind")
        or meta.get("kind")
        or meta.get("type")
        or meta.get("event_type")
        or meta.get("eventType")
        or ""
    ).strip()
    body_hash = payload_hash(message.get("message"))
    for key in _STABLE_META_KEYS:
        value = meta.get(key)
        stable_value = str(value).strip() if value is not None else ""
        if stable_value:
            return ("stable", source, method, channel, kind, key, stable_value, body_hash)
    mcp_json = meta.get("mcp_json")
    if method.startswith("notifications/") and isinstance(mcp_json, dict):
        try:
            normalized = json.dumps(
                mcp_json,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        except (TypeError, ValueError, OverflowError):
            normalized = ""
        if normalized:
            digest = hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()
            return ("stable", source, method, channel, kind, "mcp_json", digest, body_hash)
    return None


def fallback_dedupe_key(message: dict[str, Any]) -> tuple[str, ...] | None:
    meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
    method = str(meta.get("mcp_method") or "").strip()
    if not method.startswith("notifications/"):
        return None
    body = re.sub(r"\s+", " ", str(message.get("message") or "")).strip()
    if not body:
        return None
    source = str(
        meta.get("mcp_server")
        or meta.get("sse_source")
        or meta.get("source")
        or message.get("sender_id")
        or ""
    ).strip()
    channel = str(
        message.get("channel")
        or meta.get("room_id")
        or meta.get("room")
        or meta.get("channel")
        or ""
    ).strip()
    sender = str(
        message.get("sender_id") or meta.get("sender_id") or meta.get("agent_id") or ""
    ).strip()
    kind = str(
        message.get("kind")
        or meta.get("kind")
        or meta.get("type")
        or meta.get("event_type")
        or meta.get("eventType")
        or ""
    ).strip()
    return ("fallback", source, method, channel, sender, kind, payload_hash(body))
