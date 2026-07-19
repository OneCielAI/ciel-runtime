"""HTTP response writer and pending channel-delivery guard."""

from __future__ import annotations

import errno
import json
from typing import Any, Callable


CLIENT_DISCONNECT_ERRNOS = {
    errno.EPIPE,
    errno.ECONNRESET,
    getattr(errno, "ECONNABORTED", errno.ECONNRESET),
}


class HttpResponseAdapter:
    def __init__(self, log: Callable[[str, str], None]) -> None:
        self.log = log

    @staticmethod
    def write_json(handler: Any, value: Any, status: int = 200) -> None:
        body = json.dumps(value).encode("utf-8")
        handler.send_response(status)
        handler.send_header("content-type", "application/json")
        handler.send_header("content-length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    @staticmethod
    def is_client_disconnect(error: BaseException) -> bool:
        if isinstance(error, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return True
        return isinstance(error, OSError) and getattr(error, "errno", None) in CLIENT_DISCONNECT_ERRNOS

    def try_write_json(self, handler: Any, value: Any, status: int = 200) -> bool:
        try:
            self.write_json(handler, value, status)
            return True
        except Exception as exc:
            if self.is_client_disconnect(exc):
                self.log(
                    "WARN",
                    f"write_json_client_disconnected status={status} "
                    f"error={type(exc).__name__}: {exc}",
                )
                return False
            raise

    @staticmethod
    def response_status(handler: Any) -> int | None:
        try:
            return int(getattr(handler, "_ciel_runtime_response_status", None))
        except Exception:
            return None

    @staticmethod
    def write_empty(handler: Any, status: int = 202) -> None:
        handler.send_response(status)
        handler.send_header("content-length", "0")
        handler.end_headers()

    @staticmethod
    def write_accepted(handler: Any) -> None:
        body = b"Accepted"
        handler.send_response(202)
        handler.send_header("content-type", "text/plain")
        handler.send_header("content-length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)


class ChannelDeliveryGuard:
    def __init__(self, log: Callable[[str, str], None]) -> None:
        self.log = log

    @staticmethod
    def metadata_enabled(metadata: dict[str, Any] | None) -> bool:
        return isinstance(metadata, dict) and bool(
            metadata.get("ciel_runtime_channel_cursor_last_id")
        )

    def begin(self, handler: Any | None, body: dict[str, Any]) -> None:
        if handler is None:
            return
        metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
        if not self.metadata_enabled(metadata):
            return
        try:
            setattr(handler, "_ciel_runtime_channel_delivery_guard", True)
            setattr(handler, "_ciel_runtime_channel_delivery_ok", False)
            setattr(handler, "_ciel_runtime_channel_delivery_reason", "pending")
        except Exception as exc:
            self.log(
                "WARN",
                f"channel_delivery_guard_begin_failed error={type(exc).__name__}: {exc}",
            )

    def success(self, handler: Any | None, reason: str = "response_complete") -> None:
        if handler is None or not getattr(handler, "_ciel_runtime_channel_delivery_guard", False):
            return
        try:
            setattr(handler, "_ciel_runtime_channel_delivery_ok", True)
            setattr(handler, "_ciel_runtime_channel_delivery_reason", reason)
        except Exception as exc:
            self.log(
                "WARN",
                f"channel_delivery_guard_success_failed reason={reason} "
                f"error={type(exc).__name__}: {exc}",
            )

    def failed(self, handler: Any | None, reason: str = "response_failed") -> None:
        if handler is None or not getattr(handler, "_ciel_runtime_channel_delivery_guard", False):
            return
        try:
            setattr(handler, "_ciel_runtime_channel_delivery_ok", False)
            setattr(handler, "_ciel_runtime_channel_delivery_reason", reason)
        except Exception as exc:
            self.log(
                "WARN",
                f"channel_delivery_guard_failure_failed reason={reason} "
                f"error={type(exc).__name__}: {exc}",
            )

    @staticmethod
    def confirmed(handler: Any | None) -> bool:
        if handler is None or not getattr(handler, "_ciel_runtime_channel_delivery_guard", False):
            return True
        return bool(getattr(handler, "_ciel_runtime_channel_delivery_ok", False))
