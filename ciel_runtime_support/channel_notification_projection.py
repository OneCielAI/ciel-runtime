"""Native channel notification protocol projection."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ChannelNotificationConfig:
    notification_method: str
    control_kinds: frozenset[str] | set[str] | tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ChannelNotificationPorts:
    json_safe: Callable[[Any], Any]
    string_list: Callable[[Any], list[str]]
    external_provenance: Callable[[dict[str, Any]], bool]
    wake_noise_reason: Callable[[dict[str, Any]], str | None]
    superseded_ids: Callable[[list[dict[str, Any]]], set[str]]
    log: Callable[[str, str], None]


class ChannelNotificationProjection:
    def __init__(
        self, config: ChannelNotificationConfig, ports: ChannelNotificationPorts
    ) -> None:
        self.config = config
        self.ports = ports

    def meta_value(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        return json.dumps(
            self.ports.json_safe(value),
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )

    def meta(self, message: dict[str, Any]) -> dict[str, str]:
        raw_meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
        meta: dict[str, str] = {}
        for key, value in raw_meta.items():
            name = str(key or "").strip()
            if not name:
                continue
            meta[name] = self.meta_value(value)
        message_recipients = (
            self.ports.string_list(message.get("recipients"))
            if message.get("recipients") is not None
            else None
        )
        base = {
            "ciel_runtime_message_id": message.get("id"),
            "channel": message.get("channel") or "default",
            "sender_id": message.get("sender_id") or "channel",
            "thread_id": message.get("thread_id"),
            "parent_id": message.get("parent_id"),
            "kind": message.get("kind"),
            "recipients": message_recipients,
        }
        for key, value in base.items():
            if value is not None:
                meta[key] = self.meta_value(value)
        if raw_meta:
            meta["ciel_runtime_meta_json"] = json.dumps(
                self.ports.json_safe(raw_meta),
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
        return meta

    def param_value(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (str, bool, int, float)):
            return value
        return self.ports.json_safe(value)

    def notification(self, message: dict[str, Any]) -> dict[str, Any]:
        text = re.sub(r"\s+", " ", str(message.get("message") or "")).strip()
        channel = str(message.get("channel") or "default")
        sender = str(message.get("sender_id") or "channel")
        prefix = f"[{channel}] {sender}"
        content = f"{prefix}: {text}" if text else prefix
        raw_meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
        params: dict[str, Any] = {
            "content": content,
            "message": text,
            "text": text,
            "channel": channel,
            "source": channel,
            "sender_id": sender,
            "meta": self.meta(message),
        }
        for key in ("id", "thread_id", "parent_id", "kind", "time", "recipients"):
            if message.get(key) is not None:
                value = (
                    self.ports.string_list(message.get(key))
                    if key == "recipients"
                    else message.get(key)
                )
                params[key] = self.param_value(value)
        for key in (
            "room_id",
            "room",
            "recipient_id",
            "recipient",
            "conversation_id",
            "dm_id",
        ):
            value = raw_meta.get(key)
            if value is not None and key not in params:
                params[key] = self.param_value(value)
        if "room_id" not in params and channel:
            params["room_id"] = channel
        return {
            "jsonrpc": "2.0",
            "method": self.config.notification_method,
            "params": params,
        }

    def capabilities(
        self,
    ) -> dict[str, Any]:
        return {
            "tools": {"listChanged": False},
            "experimental": {
                "claude/channel": {},
            },
        }

    def skip_reason(self, message: dict[str, Any]) -> str | None:
        visibility = str(message.get("visibility") or "user").strip().lower()
        if visibility in {"hidden", "internal", "transport", "control", "system"}:
            return f"visibility_{visibility}"
        meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
        meta_kind = (
            str(
                meta.get("kind")
                or meta.get("type")
                or meta.get("event_type")
                or meta.get("eventType")
                or meta.get("event")
                or meta.get("status")
                or ""
            )
            .strip()
            .lower()
        )
        if meta_kind in self.config.control_kinds:
            return meta_kind
        recipients = {
            item.strip().lower()
            for item in self.ports.string_list(message.get("recipients"))
        }
        if "internal" in recipients:
            return "recipient_internal"
        delivery = self.ports.string_list(message.get("delivery"))
        if delivery:
            normalized_delivery = {item.strip().lower() for item in delivery}
            if not ({"all", "*", "native", "mcp"} & normalized_delivery):
                return "delivery_not_native"
        wake_reason = self.ports.wake_noise_reason(message)
        if wake_reason:
            return wake_reason
        if not delivery and not self.ports.external_provenance(message):
            return "unscoped_channel_message"
        return None

    def notifications_for_messages(
        self,
        messages: list[dict[str, Any]],
        session: str = "",
    ) -> tuple[int, list[tuple[int, dict[str, Any]]]]:
        last_id = 0
        events: list[tuple[int, dict[str, Any]]] = []
        superseded_ids = self.ports.superseded_ids(messages)
        for message in messages:
            message_id = int(message.get("id") or 0)
            last_id = max(last_id, message_id)
            skip_reason = self.skip_reason(message)
            if skip_reason:
                self.ports.log(
                    "INFO",
                    f"channel_mcp_skipped_noise session={session or '-'} message_id={message.get('id')} channel={message.get('channel')} reason={skip_reason}",
                )
                continue
            if message_id in superseded_ids:
                self.ports.log(
                    "INFO",
                    f"channel_mcp_skipped_noise session={session or '-'} message_id={message.get('id')} channel={message.get('channel')} reason=superseded_channel_notice",
                )
                continue
            notification = self.notification(message)
            events.append((last_id, notification))
            params = (
                notification.get("params") if isinstance(notification, dict) else {}
            )
            room_id = params.get("room_id") if isinstance(params, dict) else None
            recipients = params.get("recipients") if isinstance(params, dict) else None
            self.ports.log(
                "INFO",
                f"channel_mcp_notification_prepared session={session or '-'} message_id={message.get('id')} channel={message.get('channel')} room_id={room_id or '-'} recipients={self.meta_value(recipients)[:120] if recipients is not None else '-'}",
            )
        return last_id, events
