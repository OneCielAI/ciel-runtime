from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ChannelSessionLifecycleServices:
    streamable_headers: Callable[..., dict[str, str]]
    http_error_body: Callable[[urllib.error.HTTPError], str]
    session_not_found: Callable[[urllib.error.HTTPError, str], bool]
    records: Callable[[], list[dict[str, Any]]]
    forget: Callable[[str, str, str | None], None]
    log: Callable[[str, str], None]
    urlopen: Callable[..., Any] = urllib.request.urlopen


def delete_channel_session(
    name: str,
    endpoint: str,
    headers: dict[str, str],
    protocol_version: str,
    session_id: str | None,
    reason: str,
    services: ChannelSessionLifecycleServices,
    *,
    default_protocol_version: str,
    timeout: float = 5.0,
) -> bool:
    session = str(session_id or "").strip()
    if not session or not endpoint:
        return True
    request_headers = services.streamable_headers(
        headers,
        protocol_version or default_protocol_version,
        session,
        accept="application/json, text/event-stream",
    )
    try:
        request = urllib.request.Request(endpoint, headers=request_headers, method="DELETE")
        with services.urlopen(request, timeout=max(1.0, min(30.0, timeout))) as response:
            try:
                response.read()
            except (OSError, ValueError) as exc:
                services.log(
                    "WARN",
                    f"channel_http_mcp_session_delete_body_read_failed name={name} session={session} "
                    f"error={type(exc).__name__}: {exc}",
                )
        services.log(
            "INFO",
            f"channel_http_mcp_session_deleted name={name} session={session} reason={reason}",
        )
        services.forget(name, endpoint, session)
        return True
    except urllib.error.HTTPError as exc:
        body_text = services.http_error_body(exc)
        if exc.code in {404, 405} or services.session_not_found(exc, body_text):
            services.log(
                "INFO",
                f"channel_http_mcp_session_delete_not_needed name={name} session={session} "
                f"status={exc.code} reason={reason}",
            )
            services.forget(name, endpoint, session)
            return True
        services.log(
            "WARN",
            f"channel_http_mcp_session_delete_failed name={name} session={session} "
            f"status={exc.code} reason={reason}",
        )
        return False
    except Exception as exc:
        services.log(
            "WARN",
            f"channel_http_mcp_session_delete_failed name={name} session={session} "
            f"error={type(exc).__name__}: {exc} reason={reason}",
        )
        return False


def cleanup_stale_channel_sessions(
    name: str,
    url: str,
    headers: dict[str, str],
    protocol_version: str,
    services: ChannelSessionLifecycleServices,
    *,
    default_protocol_version: str,
    keep_session_id: str | None = None,
) -> None:
    keep = str(keep_session_id or "").strip()
    for record in services.records():
        if str(record.get("url") or "") != str(url):
            continue
        session = str(record.get("session_id") or "").strip()
        if not session or session == keep:
            continue
        record_protocol = str(
            record.get("protocol_version")
            or protocol_version
            or default_protocol_version
        )
        delete_channel_session(
            name,
            url,
            headers,
            record_protocol,
            session,
            "stale_session_cleanup",
            services,
            default_protocol_version=default_protocol_version,
        )
