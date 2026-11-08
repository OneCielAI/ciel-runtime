"""Private application-input gateway shared by Web Chat and external events.

The public chat transcript is intentionally not used as the runtime input queue.
That separation prevents non-browser inputs from appearing in Web Chat while all
admitted inputs still share one TUI delivery path.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import secrets
from typing import Any, Callable

from .side_intake_limits import SIDE_INTAKE_PENDING_RECORD_MAX_BYTES, SIDE_INTAKE_TEXT_MAX_CHARS, side_text_fits_pending_record


# Router observers of each admitted external event's exact text (company_prompt_reload).
# A listener handles its own errors: admission has already happened.
EXTERNAL_EVENT_LISTENERS: list[Callable[[str], Any]] = []


def _oversize_notice(meta: dict[str, Any], original: str) -> str:
    """Name an event too large for a side record without carrying its content."""
    encoded = original.encode("utf-8")
    return json.dumps({
        "ciel_runtime_notice": "external_event_too_large_for_side_inbox",
        **{key: str(meta.get(key) or "")[:200] for key in
           ("receiver_id", "event_source", "event_id", "event_type", "transport_event_id")},
        "chars": len(original), "utf8_bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "side_record_limit_bytes": SIDE_INTAKE_PENDING_RECORD_MAX_BYTES,
        "original": "kept unchanged in the runtime external-mail spool; not delivered here",
    }, ensure_ascii=False)


@dataclass(frozen=True, slots=True)
class RuntimeInputGateway:
    append: Callable[[dict[str, Any]], dict[str, Any]]
    project_attachment: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    default_input_transport: Callable[[], str] = lambda: "session_socket"
    status: Any = None
    load_side_settings: Callable[[], dict[str, Any]] = lambda: {}

    def _append(self, payload: dict[str, Any], *, side_fallback: str = "") -> dict[str, Any]:
        meta = dict(payload.get("meta") or {})
        # Side-lane markers are host decisions; a caller's metadata never sets them.
        for name in ("_side_intake_admitted", "side_intake_only", "side_intake_text", "side_intake_fallback"):
            meta.pop(name, None)
        payload = {**payload, "meta": meta}
        settings = self.load_side_settings() or {}
        requested_side = (meta.get("btw_with_mcp") is True or meta.get("message_type") == "btw_with_mcp"
                          or meta.get("event_type") == "btw_with_mcp")
        side_enabled = settings.get("enabled") is True
        external = meta.get('source_kind') == 'external_event'
        fallback_side = external and side_fallback == 'raw_text'
        # A host raw fallback ignores any lane the untrusted event body names.
        explicit_type = None if fallback_side else meta.get('injection_type') or meta.get('message_type')
        invalid_external_lane = (external and side_enabled and explicit_type is not None
                                 and explicit_type not in ('session', 'btw_with_mcp'))
        if explicit_type is not None and explicit_type not in ('session', 'btw_with_mcp') and not invalid_external_lane:
            raise ValueError('injection_type must be session or btw_with_mcp')
        selected_type = ('btw_with_mcp' if invalid_external_lane or fallback_side else explicit_type
                         or ('btw_with_mcp' if requested_side else settings.get('type', 'btw_with_mcp')))
        eligible = requested_side or external or explicit_type is not None
        # Only a live side lane takes side-only rows: with side intake off nothing
        # would consume them and they would hold every later runtime input.
        if side_enabled and eligible and selected_type == 'btw_with_mcp':
            event_key = meta.get("event_id") or meta.get("event_key")
            if not isinstance(event_key, str) or not event_key.strip():
                raise ValueError("btw_with_mcp requires stable meta.event_id or meta.event_key")
            original = payload.get("content", payload.get("message", ""))
            if not isinstance(original, str) or not original.strip():
                raise ValueError("btw_with_mcp requires non-empty text")
            source = str(meta.get("event_source") or meta.get("source") or payload.get("channel") or "external")
            side_key = source + ":" + event_key
            if len(side_key) > 300:
                # The inbox keys at most 300 characters; a longer CloudEvent
                # identity must not become a permanently undeliverable row.
                side_key = "sha256:" + hashlib.sha256(side_key.encode("utf-8")).hexdigest()
            if external:
                # The host, not untrusted event data, owns this lane. Keep the exact
                # event text as data; the verified session socket moves it to the
                # side inbox without parsing it as a slash command. Above the 32K
                # ordinary contract (or for a malformed SSE frame with a synthetic
                # host identity) it is an explicit raw fallback, never trimmed.
                fallback = ("raw_text" if invalid_external_lane or fallback_side
                            or len(original) > SIDE_INTAKE_TEXT_MAX_CHARS else "")
                side_meta = {**meta, "event_key": side_key, "side_intake_only": True,
                             "btw_with_mcp": True, "_side_intake_admitted": True,
                             "input_transport": "session_socket", "raw_preserved": True}
                if not side_text_fits_pending_record(original):
                    # Larger than one side record: the queue row and its
                    # external-mail spool keep the original; the side agent gets
                    # a content-free notice naming it instead of a blocked stream.
                    fallback = "notice"
                    side_meta["side_intake_text"] = _oversize_notice(meta, original)
                side_meta["side_intake_fallback"] = fallback
                payload = {**payload, "message": original, "content": original, "meta": side_meta}
            else:
                if len(original) > SIDE_INTAKE_TEXT_MAX_CHARS:
                    raise ValueError(f"btw_with_mcp requires 1-{SIDE_INTAKE_TEXT_MAX_CHARS} characters")
                command = "/btw_with_mcp " + json.dumps({"event_key": side_key,
                                                        "text": original}, ensure_ascii=False)
                payload = {**payload, "message": command, "content": command, "meta": {**meta, "btw": True,
                           "raw_injection": True, "btw_with_mcp": True, "_side_intake_admitted": True,
                           "input_transport": "session_socket"}}
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

    @staticmethod
    def _btw(body: dict[str, Any], meta: dict[str, Any], content: Any) -> bool:
        """Native Claude `/btw` is a client command, not model text decoration.

        Explicit opt-in and automatic prefix detection converge on raw delivery.
        Only an exact command at the start of the text qualifies; a quoted prefix
        remains ordinary prose.
        """

        for owner in (body, meta):
            value = owner.get("btw")
            if value is None and owner is meta and isinstance(body.get("meta"), dict):
                value = body["meta"].get("btw")
            if value is True:
                return True
            if value is not None and value is not False:
                raise ValueError("btw must be a boolean")
        if not isinstance(content, str):
            return False
        return content == "/btw" or content.startswith(("/btw ", "/btw\n", "/btw\t", "/btw\r"))

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
        if self._btw(inbound, meta, public_message.get("message")):
            meta["btw"] = True
            meta["raw_injection"] = True
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
        if self._btw(body, meta, content):
            meta["btw"] = True
            meta["raw_injection"] = True

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
        if self._btw(body, meta, content):
            meta["btw"] = True
            meta["raw_injection"] = True
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
        if self._btw(body, meta, content):
            meta["btw"] = True
            meta["raw_injection"] = True
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
        injection_type: str = "",
        side_intake_fallback: str = "",
    ) -> dict[str, Any]:
        """Admit decoded event text with an explicit, validated injection lane."""
        if not injection_type and not side_intake_fallback:
            try:
                decoded = json.loads(raw_event)
                data = decoded.get('data', {}) if isinstance(decoded, dict) else {}
                injection_type = data.get('injection_type', '') if isinstance(data, dict) else ''
            except ValueError:
                pass

        admitted = self._append(
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
                    **({'injection_type': injection_type} if injection_type else {}),
                },
                "delivery": ["llm"],
                "visibility": "private_runtime",
            },
            side_fallback=side_intake_fallback,
        )
        for listener in list(EXTERNAL_EVENT_LISTENERS):
            listener(raw_event)
        return admitted

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
