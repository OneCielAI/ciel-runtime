"""Private application-input gateway shared by Web Chat and external events.

The public chat transcript is intentionally not used as the runtime input queue.
That separation prevents non-browser inputs from appearing in Web Chat while all
admitted inputs still share one TUI delivery path.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import secrets
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class RuntimeInputGateway:
    append: Callable[[dict[str, Any]], dict[str, Any]]
    project_attachment: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    default_input_transport: Callable[[], str] = lambda: "session_socket"
    status: Any = None

    def _append(self, payload: dict[str, Any]) -> dict[str, Any]:
        message = self.append(payload)
        request_id = int(message.get("id") or 0)
        if (
            request_id > 0
            and not message.get("_ciel_runtime_duplicate")
            and self.status is not None
        ):
            self.status.transition(
                request_id,
                "queued",
                data={
                    "channel": str(message.get("channel") or "default"),
                    "input_transport": str(
                        (message.get("meta") or {}).get("input_transport") or ""
                    ),
                },
            )
        return message

    def _transport(self, value: Any = None) -> str:
        transport = str(value or self.default_input_transport() or "session_socket").strip().lower().replace("-", "_")
        aliases = {
            "socket": "session_socket",
            "claude_socket": "session_socket",
            "messaging_socket": "session_socket",
            "llm": "router",
            "context": "router",
            "terminal": "tty",
            "stdin": "tty",
        }
        transport = aliases.get(transport, transport)
        return transport if transport in {"session_socket", "router", "tty"} else "tty"

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
        meta.setdefault("injection_mode", "structured")
        meta.setdefault(
            "input_transport",
            self._transport(inbound.get("input_transport") or meta.get("input_transport")),
        )
        meta.setdefault("response_mode", "web_chat")
        if meta["response_mode"] == "web_chat":
            meta["reply_parent_id"] = public_message.get("id")
            meta.setdefault("web_reply_token", secrets.token_urlsafe(24))
        attachments = meta.get("attachments")
        if self.project_attachment is not None and isinstance(attachments, list):
            runtime_attachments: list[dict[str, Any]] = []
            for attachment in attachments:
                if not isinstance(attachment, dict):
                    continue
                try:
                    runtime_attachments.append(self.project_attachment(attachment))
                except (FileNotFoundError, OSError, ValueError):
                    continue
            if runtime_attachments:
                meta["runtime_attachments"] = runtime_attachments
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
        return self._append(payload)

    def submit_tty(
        self,
        body: dict[str, Any],
        public_message: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Admit an API message as plain TTY input, outside Web Chat semantics."""

        params = body.get("params") if isinstance(body.get("params"), dict) else {}
        meta_value = params.get("meta") if isinstance(params.get("meta"), dict) else body.get("meta")
        meta = dict(meta_value) if isinstance(meta_value, dict) else {}
        declared_source = str(meta.get("source") or "").strip()
        declared_kind = str(body.get("kind") or meta.get("source_kind") or "").strip()
        response_mode = str(meta.get("response_mode") or "tty").strip().lower()
        for key in (
            "reply_channel",
            "reply_recipient",
            "reply_parent_id",
            "web_reply_token",
            "response_contract",
        ):
            if response_mode != "web_chat":
                meta.pop(key, None)
        if response_mode == "web_chat" and isinstance(public_message, dict):
            meta.setdefault("reply_channel", public_message.get("channel") or body.get("channel") or "default")
            meta.setdefault("reply_recipient", "web")
            meta["reply_parent_id"] = public_message.get("id")
            meta.setdefault("web_reply_token", secrets.token_urlsafe(24))
        if declared_source and declared_source != "ciel-runtime-api-tty":
            meta["declared_source"] = declared_source
        if declared_kind and declared_kind != "tty_input":
            meta["declared_kind"] = declared_kind
        meta["source"] = "ciel-runtime-api-tty"
        meta["source_kind"] = "tty_input"
        meta["injection_mode"] = "tty"
        meta["input_transport"] = self._transport(
            meta.get("input_transport") or body.get("input_transport")
        )
        meta["response_mode"] = response_mode

        content: Any = params.get("content")
        if content is None:
            content = body.get("message", body.get("content", body.get("text", "")))
        if isinstance(content, (dict, list)):
            content = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
            meta["content_type"] = "application/json"

        return self._append(
            {
                "channel": body.get("channel") or meta.get("channel") or "default",
                "sender_id": body.get("sender_id") or body.get("sender") or "api-user",
                "recipients": body.get("recipients", body.get("recipient_id", "all")),
                "thread_id": body.get("thread_id") or meta.get("thread_id"),
                "parent_id": body.get("parent_id") or meta.get("parent_id"),
                "kind": "tty_input",
                "message": content if content is not None else "",
                "meta": meta,
                "delivery": ["llm"],
                "visibility": "private_runtime",
            }
        )

    def submit_notification(self, body: dict[str, Any]) -> dict[str, Any]:
        """Admit an explicit Ciel notification without publishing it to chat."""

        params = body.get("params") if isinstance(body.get("params"), dict) else {}
        meta_value = params.get("meta") if isinstance(params.get("meta"), dict) else body.get("meta")
        meta = dict(meta_value) if isinstance(meta_value, dict) else {}
        meta.setdefault("source", "ciel-runtime-notify")
        meta.setdefault("source_kind", "notification")
        meta.setdefault(
            "input_transport",
            self._transport(body.get("input_transport") or meta.get("input_transport")),
        )
        content = params.get("content")
        if content is None:
            content = body.get("content", body.get("message", body.get("text", "")))
        return self._append(
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

    def submit_stream_input(self, body: dict[str, Any]) -> dict[str, Any]:
        """Admit an external Streamable HTTP/MCP input into the runtime queue."""

        meta = dict(body.get("meta") or {}) if isinstance(body.get("meta"), dict) else {}
        meta.setdefault("source", "ciel-runtime-mcp-streamable")
        meta.setdefault("source_kind", "mcp_streamable_input")
        meta.setdefault("injection_mode", "structured")
        meta["input_transport"] = self._transport(
            body.get("input_transport") or meta.get("input_transport")
        )
        meta.setdefault("response_mode", str(body.get("response_mode") or "tty"))
        content = body.get("message", body.get("content", body.get("text", "")))
        if isinstance(content, (dict, list)):
            content = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
            meta["content_type"] = "application/json"
        return self._append(
            {
                "channel": body.get("channel") or "mcp-streamable",
                "sender_id": body.get("sender_id") or body.get("sender") or "mcp-client",
                "recipients": body.get("recipients", "all"),
                "thread_id": body.get("thread_id"),
                "parent_id": body.get("parent_id"),
                "kind": body.get("kind") or "mcp_streamable_input",
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
        input_transport: str = "",
    ) -> dict[str, Any]:
        """Admit exact decoded event text; never normalize or re-serialize it."""

        return self._append(
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
                    "input_transport": self._transport(input_transport),
                    "raw_preserved": True,
                },
                "delivery": ["llm"],
                "visibility": "private_runtime",
            }
        )

    def submit_telemetry_notice(
        self,
        ranges: list[dict[str, Any]],
        record_count: int,
    ) -> dict[str, Any]:
        """Notify the runtime about durable logs without embedding log bodies."""

        compact_ranges = [
            {
                key: item.get(key)
                for key in (
                    "file",
                    "segment",
                    "line_start",
                    "line_end",
                    "offset_start",
                    "offset_end",
                    "records",
                )
            }
            for item in ranges
            if isinstance(item, dict)
        ]
        locations = "; ".join(
            f"{item.get('file')}#segment={item.get('segment')} "
            f"lines={item.get('line_start')}-{item.get('line_end')} "
            f"offsets={item.get('offset_start')}-{item.get('offset_end')}"
            for item in compact_ranges
        )
        message = (
            f"{int(record_count)} new OpenTelemetry log record(s) were stored locally. "
            "Use the telemetry_logs tool with the supplied file/segment/offset or line cursor "
            "to read only the needed range. This is input-only telemetry; no acknowledgement "
            f"or response is required. Stored ranges: {locations}"
        )
        return self._append(
            {
                "channel": "telemetry",
                "sender_id": "otlp",
                "recipients": ["all"],
                "kind": "telemetry_notice",
                "message": message,
                "meta": {
                    "source": "ciel-runtime-otlp-logs",
                    "source_kind": "telemetry_notice",
                    "record_count": int(record_count),
                    "ranges": compact_ranges,
                    "ack_required": False,
                    "response_expected": False,
                    "logs_embedded": False,
                    "input_transport": self._transport(),
                },
                "delivery": ["llm"],
                "visibility": "private_runtime",
            }
        )


__all__ = ["RuntimeInputGateway"]
