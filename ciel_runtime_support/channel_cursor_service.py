"""Application services for durable Channel MCP cursor and resume behavior."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ciel_runtime_support.channel_cursor_repository import ChannelCursorRepository


@dataclass(frozen=True, slots=True)
class ChannelCursorServices:
    repository: ChannelCursorRepository
    lock: Any
    cached: Callable[[], int | None]
    cache: Callable[[int], None]
    scan_tail: Callable[[], int]


class ChannelCursorService:
    def __init__(self, services: ChannelCursorServices) -> None:
        self.services = services

    def read_locked(self) -> int:
        cached = self.services.cached()
        if cached is not None:
            return cached
        persisted = self.services.repository.read()
        cursor = persisted if persisted is not None else max(0, self.services.scan_tail())
        self.services.cache(cursor)
        if persisted is None:
            self.services.repository.write(cursor)
        return cursor

    def ensure_initialized(self) -> int:
        with self.services.lock:
            return self.read_locked()

    def update(self, last_id: int) -> None:
        if last_id < 0:
            return
        with self.services.lock:
            if last_id <= self.read_locked():
                return
            cursor = int(last_id)
            self.services.cache(cursor)
            self.services.repository.write(cursor)


def parse_channel_event_id(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return max(0, int(text))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class ChannelResumeServices:
    query_params: Callable[[Any], Mapping[str, list[str]]]
    first_param: Callable[[Mapping[str, list[str]], str], str | None]
    ensure_cursor: Callable[[], int]
    update_cursor: Callable[[int], None]
    log: Callable[[str, str], None]


class ChannelResumePolicy:
    def __init__(self, services: ChannelResumeServices) -> None:
        self.services = services

    def client_last_event_id(self, handler: Any) -> int | None:
        try:
            event_id = parse_channel_event_id(handler.headers.get("Last-Event-ID"))
            if event_id is not None:
                return event_id
        except (AttributeError, TypeError, ValueError) as exc:
            self.services.log(
                "WARN",
                f"channel_mcp_last_event_header_invalid error={type(exc).__name__}: {exc}",
            )
        try:
            params = self.services.query_params(handler)
            for key in ("lastEventId", "last_event_id", "last_id"):
                event_id = parse_channel_event_id(self.services.first_param(params, key))
                if event_id is not None:
                    return event_id
        except (AttributeError, TypeError, ValueError) as exc:
            self.services.log(
                "WARN",
                f"channel_mcp_last_event_query_invalid error={type(exc).__name__}: {exc}",
            )
        return None

    def session_start_last_id(self, handler: Any) -> int:
        cursor_last_id = self.services.ensure_cursor()
        client_last_id = self.client_last_event_id(handler)
        if client_last_id is None:
            return cursor_last_id
        if client_last_id > cursor_last_id:
            self.services.update_cursor(client_last_id)
        self.services.log(
            "INFO",
            f"channel_mcp_resume client_last_id={client_last_id} "
            f"cursor_last_id={cursor_last_id}",
        )
        return client_last_id


@dataclass(frozen=True, slots=True)
class ChannelDeliveryCursorPorts:
    response_status: Callable[[Any], int | None]
    metadata_enabled: Callable[[dict[str, Any] | None], bool]
    delivery_confirmed: Callable[[Any | None], bool]
    commit_if_newer: Callable[[int | None], None]
    log: Callable[[str, str], None]


class ChannelDeliveryCursorCommitter:
    """Commit a pending Channel cursor only after a confirmed HTTP response."""

    def __init__(self, ports: ChannelDeliveryCursorPorts) -> None:
        self.ports = ports

    @staticmethod
    def _metadata_int(metadata: dict[str, Any], key: str) -> int | None:
        try:
            value = metadata.get(key)
            if value is None or value == "":
                return None
            return max(0, int(value))
        except (TypeError, ValueError):
            return None

    def commit(
        self,
        body: dict[str, Any],
        handler: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not isinstance(metadata, dict):
            body_metadata = body.get("metadata")
            metadata = body_metadata if isinstance(body_metadata, dict) else {}
        if not metadata:
            return
        if handler is not None:
            status = self.ports.response_status(handler)
            if status is None or status < 200 or status >= 400:
                self.ports.log(
                    "INFO",
                    "channel_delivery_cursor_deferred "
                    f"status={status if status is not None else '-'}",
                )
                return
            if (
                self.ports.metadata_enabled(metadata)
                and not self.ports.delivery_confirmed(handler)
            ):
                reason = str(
                    getattr(
                        handler,
                        "_ciel_runtime_channel_delivery_reason",
                        "unconfirmed",
                    )
                    or "unconfirmed"
                )
                self.ports.log(
                    "INFO",
                    f"channel_delivery_cursor_deferred reason={reason}",
                )
                return
        cursor = self._metadata_int(
            metadata,
            "ciel_runtime_channel_cursor_last_id",
        )
        self.ports.commit_if_newer(cursor)
