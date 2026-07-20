"""MCP notification projection, deduplication, and chat persistence."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from typing import Any, Protocol


class LockPort(Protocol):
    def __enter__(self) -> Any: ...

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None: ...


@dataclass(frozen=True, slots=True)
class McpNotificationProjectionPorts:
    json_safe_metadata: Callable[[Any], Any]
    event_meta: Callable[..., dict[str, Any]]
    event_text: Callable[[Any], str]
    pretty_json: Callable[[Any], str]
    semantic_text: Callable[[Any], str]


@dataclass(frozen=True, slots=True)
class McpNotificationEffects:
    append_chat_message: Callable[[dict[str, Any]], dict[str, Any]]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class McpNotificationDedupeState:
    lock: LockPort
    recent: MutableMapping[str, tuple[str, float]]
    ttl_seconds: float
    native_method: str
    clock: Callable[[], float] = time.time


@dataclass(frozen=True, slots=True)
class McpProxyNotificationService:
    projection: McpNotificationProjectionPorts
    effects: McpNotificationEffects
    dedupe: McpNotificationDedupeState

    def notification_payload(
        self, server_name: str, message: dict[str, Any]
    ) -> dict[str, Any] | None:
        method = str(message.get("method") or "").strip()
        if not method.startswith("notifications/"):
            return None
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
        data = params.get("data") if isinstance(params.get("data"), dict) else {}
        event = params.get("event") if isinstance(params.get("event"), dict) else {}
        meta: dict[str, Any] = {
            "mcp_server": server_name,
            "mcp_method": method,
            "mcp_json": self.projection.json_safe_metadata(message),
        }
        if message.get("jsonrpc") is not None:
            meta["jsonrpc"] = message.get("jsonrpc")
        if message.get("id") is not None:
            meta["rpc_id"] = message.get("id")
        meta.update(self.projection.event_meta(message, params, payload, data, event))
        content = (
            self.projection.event_text(params)
            or self.projection.event_text(payload)
            or self.projection.event_text(data)
            or self.projection.event_text(event)
        )
        if not content and params:
            content = json.dumps(
                params,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
        if not content:
            return None
        channel = str(
            meta.get("channel")
            or meta.get("room_id")
            or meta.get("room")
            or server_name
        )
        return {
            "channel": channel,
            "sender_id": str(
                meta.get("sender_id") or meta.get("agent_id") or server_name
            ),
            "recipients": meta.get("recipient_id") or "all",
            "thread_id": meta.get("thread_id"),
            "parent_id": meta.get("parent_id"),
            "kind": method.replace("notifications/claude/", "")
            .replace("notifications/", "")
            .replace("/", "."),
            "message": self.projection.pretty_json(message),
            "meta": meta,
        }

    @staticmethod
    def stable_event_identity(
        chat_payload: dict[str, Any],
    ) -> tuple[str, str] | None:
        meta = (
            chat_payload.get("meta")
            if isinstance(chat_payload.get("meta"), dict)
            else {}
        )
        for key in (
            "stream_id",
            "sse_id",
            "message_id",
            "source_message_id",
            "event_id",
            "cursor",
            "assignment_id",
            "poll_id",
            "task_id",
            "sequence",
            "seq",
        ):
            value = meta.get(key)
            if value is not None and str(value).strip():
                return key, str(value).strip()
        return None

    def dedupe_key(
        self, server_name: str, chat_payload: dict[str, Any]
    ) -> tuple[str, bool]:
        meta = (
            chat_payload.get("meta")
            if isinstance(chat_payload.get("meta"), dict)
            else {}
        )
        body_source = (
            self.projection.semantic_text(meta.get("mcp_json"))
            or self.projection.semantic_text(meta.get("sse_json"))
            or str(chat_payload.get("message") or "")
        )
        body = re.sub(r"\s+", " ", body_source).strip()
        room = str(
            meta.get("room_id")
            or meta.get("room")
            or chat_payload.get("channel")
            or server_name
        )
        kind = str(meta.get("kind") or chat_payload.get("kind") or "")
        stable_identity = self.stable_event_identity(chat_payload)
        if stable_identity:
            stable_key, stable_value = stable_identity
            return (
                json.dumps(
                    ["stable", room, kind, stable_key, stable_value],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                True,
            )
        sender = str(
            chat_payload.get("sender_id")
            or meta.get("sender_id")
            or meta.get("agent_id")
            or server_name
        )
        thread = str(chat_payload.get("thread_id") or meta.get("thread_id") or "")
        parent = str(chat_payload.get("parent_id") or meta.get("parent_id") or "")
        return (
            json.dumps(
                [server_name, room, sender, thread, parent, body],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            False,
        )

    def should_skip_duplicate(
        self, server_name: str, chat_payload: dict[str, Any]
    ) -> tuple[bool, str | None]:
        meta = (
            chat_payload.get("meta")
            if isinstance(chat_payload.get("meta"), dict)
            else {}
        )
        method = str(meta.get("mcp_method") or "")
        if not method.startswith("notifications/"):
            return False, None
        key, has_stable_identity = self.dedupe_key(server_name, chat_payload)
        now = self.dedupe.clock()
        with self.dedupe.lock:
            stale = [
                item_key
                for item_key, (_, seen_at) in self.dedupe.recent.items()
                if now - seen_at > self.dedupe.ttl_seconds
            ]
            for item_key in stale:
                self.dedupe.recent.pop(item_key, None)
            previous = self.dedupe.recent.get(key)
            self.dedupe.recent[key] = (method, now)
        if not previous:
            return False, None
        previous_method, previous_seen_at = previous
        within_ttl = now - previous_seen_at <= self.dedupe.ttl_seconds
        if has_stable_identity and within_ttl:
            return True, previous_method
        is_native_pair = self.dedupe.native_method in {previous_method, method}
        if previous_method != method and is_native_pair and within_ttl:
            return True, previous_method
        return False, None

    def observe_json_message(
        self,
        server_name: str,
        payload: Any,
        *,
        schedule_direct: bool = True,
    ) -> dict[str, Any] | None:
        del schedule_direct
        if not isinstance(payload, dict):
            return None
        chat_payload = self.notification_payload(server_name, payload)
        if not chat_payload:
            return None
        skip_duplicate, previous_method = self.should_skip_duplicate(
            server_name, chat_payload
        )
        if skip_duplicate:
            self.effects.log(
                "INFO",
                "mcp_proxy_notification_skipped_duplicate "
                f"server={server_name} method={payload.get('method')} "
                f"previous_method={previous_method}",
            )
            return None
        try:
            saved = self.effects.append_chat_message(chat_payload)
            if saved.get("_ciel_runtime_duplicate"):
                self.effects.log(
                    "INFO",
                    "mcp_proxy_notification_skipped_duplicate_persisted "
                    f"server={server_name} method={payload.get('method')} "
                    f"existing_id={saved.get('id')}",
                )
                return saved
            self.effects.log(
                "INFO",
                f"mcp_proxy_notification server={server_name} "
                f"method={payload.get('method')} message_id={saved.get('id')}",
            )
            return saved
        except Exception as exc:
            self.effects.log(
                "WARN",
                f"mcp_proxy_notification_failed server={server_name} "
                f"error={type(exc).__name__}: {exc}",
            )
            return None
