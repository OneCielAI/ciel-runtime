"""Prelaunch menu projection for the external event receiver."""

from __future__ import annotations

from typing import Any, Callable

from .external_event_control import ExternalEventControl
from .external_event_receiver import ExternalEventReceiverService
from .runtime_input_delivery_health import delivery_health_text


def panel_rows(
    service: ExternalEventReceiverService, router_base: str,
    *, control: Callable[..., dict[str, Any]] | None = None,
) -> tuple[list[str], list[str]]:
    receiver = service.receiver_configs().get("default", {})
    public_receiver = service.public_receiver("default", receiver)
    environment_references = public_receiver.get("environment_references", {})
    enabled = bool(receiver.get("enabled", False))
    transport = str(receiver.get("transport") or "webhook")
    input_transport = str(receiver.get("input_transport") or "auto")
    event_types = (
        receiver.get("event_types")
        if isinstance(receiver.get("event_types"), list)
        else []
    )
    secret_status = service.vault.status("default")

    def secret_source(field_name: str, stored_key: str) -> str:
        reference = environment_references.get(field_name, {})
        if isinstance(reference, dict) and reference.get("name"):
            availability = "available" if reference.get("available") else "missing"
            return f"env:{reference['name']} {availability}"
        return "stored" if secret_status[stored_key] else "unset"

    def delivery_acks() -> str:
        # Walkie 49269/56190: per-event acknowledgements, read from event-acks.jsonl.
        ack_service = getattr(service, "delivery_ack", None)
        if ack_service is None:
            return "not attached"
        summary = ack_service.summary()
        if not summary.get("events"):
            return "on · none yet"
        last = summary.get("last") or {}
        return (
            f"on · acked {summary['acked']} · waiting {summary['waiting']} · failed {summary['failed']}"
            f" · injected {summary['injected']} · last {last.get('at') or '-'} {last.get('queued_ack') or '-'}"
        )

    live = control("status") if control is not None else {"available": False}
    status = live.get("status") or {}
    state = str(status.get("state") or "unknown") if live.get("available") else "unavailable"
    failure = status.get("last_failure") or {}
    if not isinstance(failure, dict):
        failure = {}
    diagnostic = status.get("diagnostic") or {}
    if not isinstance(diagnostic, dict):
        diagnostic = {}
    detail = diagnostic.get("category") or status.get("error") or "none"
    rows = [
        f"Enabled  [{'on' if enabled else 'off'}]",
        f"Transport  [{transport}]",
        f"Runtime input transport  [{input_transport}]",
        f"SSE URL  [{str(receiver.get('url') or 'unset')}]",
        "SSE content mode  [CloudEvents 1.0 structured JSON]",
        f"Allowed CloudEvent types  [{', '.join(str(value) for value in event_types) if event_types else 'all'}]",
        f"Cursor JSON pointer  [{str(receiver.get('cursor_json_pointer') or 'SSE id field')}]",
        f"Reconnect query parameter  [{str(receiver.get('cursor_query_parameter') or 'Last-Event-ID header')}]",
        f"Webhook signing secret  [{secret_source('webhook_secret', 'stored_webhook_secret')}]",
        f"SSE authorization  [{secret_source('authorization', 'stored_authorization')}]",
        f"SSE idle timeout  [{receiver.get('idle_timeout_seconds') or 'auto (heartbeat/transport timeout)'}]",
        f"Live receiver  [{state} · phase {status.get('phase') or '-'} · error {detail}]",
        f"Recovery  [failures {status.get('consecutive_failures', 0)} · retry {status.get('retry_seconds', 0)}s · recovered {status.get('recovery_count', 0)}]",
        "Refresh receiver status",
        "Reconnect receiver now",
        f"Delivery acks (event-ack/1)  [{delivery_acks()}]",
        f"Webhook endpoint  [{router_base}/ca/events/webhooks/default]",
        "Back",
    ]
    values = [
        "enabled",
        "transport",
        "input_transport",
        "url",
        "__info__",
        "event_types",
        "cursor_json_pointer",
        "cursor_query_parameter",
        "webhook_secret",
        "authorization",
        "idle_timeout_seconds",
        "__info__",
        "__info__",
        "refresh_receiver",
        "reconnect_receiver",
        "__info__",
        "__info__",
        "back",
    ]
    rows.insert(-1, f"Session delivery  [{delivery_health_text(status.get('delivery') or {})}]")
    values.insert(-1, "__info__")
    return rows, values


def update_config(
    service: ExternalEventReceiverService, key: str, value: Any,
    *, control: Callable[..., dict[str, Any]] | None = None,
) -> list[str]:
    if key in {"refresh_receiver", "reconnect_receiver"}:
        action = "status" if key == "refresh_receiver" else "reconnect"
        result = control(action) if control is not None else {"available": False}
        if result.get("available") and result.get("accepted") is False:
            return ["Receiver reconnect rejected (disabled or stopping); no session was restarted."]
        if not result.get("available"):
            return ["Router receiver control unavailable; no stream was opened or session restarted."]
        return ["Receiver status refreshed." if action == "status" else "Receiver reconnect requested; this is not delivery confirmation."]
    current = service.receiver_configs().get("default", {})
    body: dict[str, Any] = {
        "enabled": bool(current.get("enabled", False)),
        "transport": str(current.get("transport") or "webhook"),
        "input_transport": str(current.get("input_transport") or "auto"),
        "url": str(current.get("url") or ""),
        "event_types": current.get("event_types")
        if isinstance(current.get("event_types"), list)
        else [],
        "cursor_json_pointer": str(current.get("cursor_json_pointer") or ""),
        "cursor_query_parameter": str(current.get("cursor_query_parameter") or ""),
    }
    if key == "enabled":
        body["enabled"] = not body["enabled"]
    elif key == "transport":
        body["transport"] = "sse" if body["transport"] == "webhook" else "webhook"
    elif key == "input_transport":
        body["input_transport"] = str(value or "auto")
    elif key == "idle_timeout_seconds":
        from .event_receiver_connection import idle_timeout_seconds
        body[key] = idle_timeout_seconds(value)
    elif key == "event_types":
        body["event_types"] = [
            part.strip() for part in str(value or "").split(",") if part.strip()
        ]
    elif key in {
        "url",
        "cursor_json_pointer",
        "cursor_query_parameter",
        "webhook_secret",
        "authorization",
    }:
        body[key] = str(value or "")
    else:
        raise ValueError(f"unsupported external event option: {key}")
    updated = service.save_receiver("default", body)
    # Secrets were stored locally; they must not be sent over the control request.
    public_body = {name: item for name, item in body.items() if name not in {"webhook_secret", "authorization"}}
    result = control("save", public_body) if control is not None else {"available": False}
    applied = "Router receiver configuration reconciled." if result.get("available") else "Live router unavailable; settings saved for its next receiver start."
    return [
        applied,
        f"External event receiver updated: enabled={updated.get('enabled')} transport={updated.get('transport')} input_transport={updated.get('input_transport')}.",
        "The router owns receiver connections; the prelaunch configuration process does not open a duplicate stream.",
        "External events use the private Runtime Input Gateway and are never published to Web Chat.",
    ]


__all__ = ["ExternalEventControl", "panel_rows", "update_config"]
