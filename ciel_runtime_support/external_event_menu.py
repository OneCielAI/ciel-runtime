"""Prelaunch menu projection for the external event receiver."""

from __future__ import annotations

from typing import Any

from .external_event_receiver import ExternalEventReceiverService


def panel_rows(
    service: ExternalEventReceiverService, router_base: str
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
        "__info__",
        "back",
    ]
    return rows, values


def update_config(
    service: ExternalEventReceiverService, key: str, value: Any
) -> list[str]:
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
    return [
        f"External event receiver updated: enabled={updated.get('enabled')} transport={updated.get('transport')} input_transport={updated.get('input_transport')}.",
        "The router owns receiver connections; the prelaunch configuration process does not open a duplicate stream.",
        "External events use the private Runtime Input Gateway and are never published to Web Chat.",
    ]


__all__ = ["panel_rows", "update_config"]
