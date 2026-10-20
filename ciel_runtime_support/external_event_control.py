"""Workspace-checked control of the router-owned event receiver; never opens SSE."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from .workspace_router_selection import workspace_identity


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ExternalEventControl:
    def __init__(self, base: str, workspace: str, headers: dict[str, str] | None = None,
                 *, open_request: Callable[..., Any] | None = None) -> None:
        self.base = base.rstrip("/")
        self.workspace = workspace_identity(workspace)
        self.headers = dict(headers or {})
        self.open_request = open_request or urllib.request.build_opener(_NoRedirect()).open

    def _request(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        parsed = urllib.parse.urlsplit(self.base)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("invalid router base")
        request = urllib.request.Request(self.base + path,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={**self.headers, "Content-Type": "application/json"})
        with self.open_request(request, timeout=1.0) as response:
            raw = response.read(262145)
        if len(raw) > 262144:
            raise ValueError("router response too large")
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get("ok") is not True:
            raise ValueError("router request rejected")
        return value

    def call(self, action: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        if action not in {"status", "reconnect", "save"}:
            return {"available": False, "reason": "unsupported_action"}
        try:
            health = self._request("/health")
            if not self.workspace or workspace_identity(health.get("workspace")) != self.workspace:
                return {"available": False, "reason": "workspace_mismatch"}
            if action == "status":
                result = self._request("/ca/events/receivers")
                receivers = result.get("receivers", [])
                receiver = next((r for r in receivers if isinstance(r, dict) and r.get("id") == "default"), {})
                status = receiver.get("status", {})
                return {"available": True, "status": status if isinstance(status, dict) else {}}
            if action == "reconnect":
                result = self._request("/ca/events/receivers/default/reconnect", {})
            else:
                result = self._request("/ca/events/receivers/default", body or {})
            return {"available": True, "accepted": result.get("ok") is True}
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                return {"available": True, "accepted": False, "reason": "receiver_unavailable"}
            return {"available": False, "reason": "router_rejected", "http_status": exc.code}
        except Exception:
            # Arbitrary exception messages can contain credentials or endpoint URLs.
            return {"available": False, "reason": "router_unavailable"}


__all__ = ["ExternalEventControl"]
