"""Private application-input gateway shared by Web Chat and external events.

The public chat transcript is intentionally not used as the runtime input queue.
That separation prevents non-browser inputs from appearing in Web Chat while all
admitted inputs still share one TUI delivery path.
"""

from __future__ import annotations

from dataclasses import dataclass
import secrets
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class RuntimeInputGateway:
    append: Callable[[dict[str, Any]], dict[str, Any]]

    def submit_web_chat(
        self,
        public_message: dict[str, Any],
        inbound: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Mirror one public browser request into the private runtime queue."""

        inbound = inbound if isinstance(inbound, dict) else {}
        meta = dict(public_message.get("meta") or {})
        meta.setdefault("source", "ciel-runtime-web-chat")
        meta.setdefault("source_kind", "web_chat")
        meta["reply_parent_id"] = public_message.get("id")
        meta.setdefault("web_reply_token", secrets.token_urlsafe(24))
        payload = {
            "channel": public_message.get("channel") or inbound.get("channel") or "default",
            "sender_id": public_message.get("sender_id") or inbound.get("sender_id") or "web-user",
            "recipients": public_message.get("recipients") or inbound.get("recipients") or "all",
            "thread_id": public_message.get("thread_id") or inbound.get("thread_id"),
            "parent_id": public_message.get("parent_id"),
            "kind": public_message.get("kind") or "web_chat",
            "message": public_message.get("message") if public_message.get("message") is not None else "",
            "meta": meta,
            "delivery": ["llm"],
            "visibility": "private_runtime",
        }
        return self.append(payload)

    def submit_notification(self, body: dict[str, Any]) -> dict[str, Any]:
        """Admit an explicit Ciel notification without publishing it to chat."""

        params = body.get("params") if isinstance(body.get("params"), dict) else {}
        meta_value = params.get("meta") if isinstance(params.get("meta"), dict) else body.get("meta")
        meta = dict(meta_value) if isinstance(meta_value, dict) else {}
        meta.setdefault("source", "ciel-runtime-notify")
        meta.setdefault("source_kind", "notification")
        content = params.get("content")
        if content is None:
            content = body.get("content", body.get("message", body.get("text", "")))
        return self.append(
            {
                "channel": body.get("channel") or meta.get("channel") or "default",
                "sender_id": body.get("sender_id") or body.get("sender") or body.get("server") or meta.get("source") or "channel",
                "recipients": body.get("recipients", body.get("recipient_id", meta.get("recipients", "all"))),
                "thread_id": body.get("thread_id") or meta.get("thread_id"),
                "parent_id": body.get("parent_id") or meta.get("parent_id"),
                "kind": body.get("kind") or "notification",
                "message": content if content is not None else "",
                "meta": meta,
                "delivery": ["llm"],
                "visibility": "private_runtime",
            }
        )

    def submit_external_event(
        self,
        raw_event: str,
        *,
        receiver_id: str,
        transport: str,
        event_id: str,
        event_type: str,
        event_source: str,
        transport_event_id: str = "",
    ) -> dict[str, Any]:
        """Admit exact decoded event text; never normalize or re-serialize it."""

        return self.append(
            {
                "channel": f"external:{receiver_id}",
                "sender_id": event_source or receiver_id,
                "recipients": ["all"],
                "thread_id": event_id,
                "kind": "external_event",
                "message": raw_event,
                "meta": {
                    "source": "ciel-runtime-external-event",
                    "source_kind": "external_event",
                    "receiver_id": receiver_id,
                    "transport": transport,
                    "event_id": event_id,
                    "event_type": event_type,
                    "event_source": event_source,
                    "transport_event_id": transport_event_id,
                    "raw_preserved": True,
                },
                "delivery": ["llm"],
                "visibility": "private_runtime",
            }
        )


__all__ = ["RuntimeInputGateway"]
